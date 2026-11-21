"""Markdown flavour profiles of the write leg (Trac /trac_mcp_server #216).

One block per rule. Each rule has a row showing what the flavour does and
a row showing that the default (`commonmark`) leaves the same input exactly
as the converter always did -- the expected strings of those rows were
captured from the converter before this change.

The normaliser rows read `apply_flavor` (Markdown in, Markdown out); the
parser-rule rows read `markdown_to_tracwiki` (Markdown in, TracWiki out).
"""

import asyncio
import sys
import time
from unittest.mock import MagicMock

import pytest

from trac_mcp_server.converters.common import auto_convert
from trac_mcp_server.converters.markdown_to_tracwiki import (
    convert_with_warnings,
    markdown_to_tracwiki,
)

GEMINI_HINT = (
    'Line "Use code with caution." looks like text copied from a '
    'Gemini page; convert with flavor "gemini" (--flavor gemini) to '
    "rebuild its code blocks."
)
REBUILT_ONE = "gemini: rebuilt 1 code block(s) from 'Use code with caution.' markers"


def _flavors():
    from trac_mcp_server.converters import flavors

    return flavors


def _norm(text, flavor):
    return _flavors().apply_flavor(text, flavor)


# -- selection ----------------------------------------------------------------


def test_the_flavors_and_the_default():
    flavors = _flavors()
    assert flavors.FLAVORS == ("commonmark", "chatgpt", "gemini")
    assert flavors.DEFAULT_FLAVOR == "commonmark"


@pytest.mark.parametrize(
    "call",
    [
        lambda: markdown_to_tracwiki("x", flavor="bard"),
        lambda: convert_with_warnings("x", flavor="bard"),
        lambda: _norm("x", "bard"),
        lambda: _flavors().flavor_plugins("bard"),
    ],
    ids=[
        "markdown_to_tracwiki",
        "convert_with_warnings",
        "apply",
        "plugins",
    ],
)
def test_an_unknown_flavor_is_a_value_error_naming_the_valid_ones(call):
    with pytest.raises(ValueError) as err:
        call()
    message = str(err.value)
    assert "bard" in message
    for name in ("commonmark", "chatgpt", "gemini"):
        assert name in message


def test_commonmark_is_the_identity_on_the_source():
    text = "bash\nls\nUse code with caution.\n\u2022 x \\(y\\)\n"
    assert _norm(text, "commonmark") == (text, [])
    assert _flavors().flavor_plugins("commonmark") == []


def test_auto_convert_forwards_the_flavor():
    config = MagicMock()
    result = asyncio.run(
        auto_convert(
            "Energy \\(E=mc^2\\) here",
            config,
            target_format="tracwiki",
            source_format="markdown",
            flavor="chatgpt",
        )
    )
    assert result.text == "Energy `E=mc^2` here"
    default = asyncio.run(
        auto_convert(
            "Energy \\(E=mc^2\\) here",
            config,
            target_format="tracwiki",
            source_format="markdown",
        )
    )
    assert default.text == "Energy (E=mc^2) here"


# -- chatgpt 3a: inline math --------------------------------------------------

INLINE_MATH_ROWS = [
    # (id, source, chatgpt output, commonmark output)
    (
        "single",
        "Energy \\(E=mc^2\\) here",
        "Energy `E=mc^2` here",
        "Energy (E=mc^2) here",
    ),
    (
        "doubled",
        "Energy \\\\(E=mc^2\\\\) here",
        "Energy `E=mc^2` here",
        "Energy \\(E=mc^2\\) here",
    ),
    (
        "doubled unescapes underscore and star",
        "Doubled \\\\(R\\_{\\text{OK1}}\\\\) and \\\\(a\\*b\\\\) end",
        "Doubled `R_{\\text{OK1}}` and `a*b` end",
        "Doubled \\(R_{\\text{OK1}}\\) and \\(a*b\\) end",
    ),
    (
        "single keeps the content as written",
        "Single \\(R\\_{x}\\) keep",
        "Single `R\\_{x}` keep",
        "Single (R_{x}) keep",
    ),
    (
        "a backtick in the formula uses the triple-brace form",
        "Tick \\\\(a`b\\\\) end",
        "Tick {{{a`b}}} end",
        "Tick \\(a`b\\) end",
    ),
    (
        "brackets in the middle of a sentence",
        "Mid \\[y\\] sentence",
        "Mid `y` sentence",
        "Mid [y] sentence",
    ),
    (
        "doubled brackets in the middle of a sentence",
        "Mid \\\\[y\\_1\\\\] sentence",
        "Mid `y_1` sentence",
        "Mid \\[y_1\\] sentence",
    ),
    (
        "table cells",
        "| a | b |\n| - | - |\n| \\\\(x\\_1\\\\) | \\[y\\] |",
        "||=a=||=b=||\n||`x_1`||`y`||",
        "||=a=||=b=||\n||\\(x_1\\)||[y]||",
    ),
]


@pytest.mark.parametrize(
    ("source", "chatgpt", "commonmark"),
    [row[1:] for row in INLINE_MATH_ROWS],
    ids=[row[0] for row in INLINE_MATH_ROWS],
)
def test_inline_math(source, chatgpt, commonmark):
    assert markdown_to_tracwiki(source, flavor="chatgpt") == chatgpt
    assert (
        markdown_to_tracwiki(source, flavor="commonmark") == commonmark
    )
    assert markdown_to_tracwiki(source) == commonmark


def test_a_lone_opener_is_not_math():
    assert (
        markdown_to_tracwiki("a \\(b c", flavor="chatgpt") == "a (b c"
    )


def test_math_does_not_cross_a_line():
    source = "a \\(b\nc\\) d"
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        markdown_to_tracwiki(source)
    )


# -- chatgpt 3b: display math -------------------------------------------------

DISPLAY_MATH_ROWS = [
    (
        "single, one line",
        "\\[ x = 1 \\]",
        "{{{#!latex\nx = 1\n}}}",
        "[ x = 1 ]",
    ),
    (
        "doubled, one line, unescaped",
        "\\\\[ V\\_{o} = 1 \\\\]",
        "{{{#!latex\nV_{o} = 1\n}}}",
        "\\[ V_{o} = 1 \\]",
    ),
    (
        "doubled, several lines",
        "\\\\[\nV\\_{o} = 1\n  + 2\n\\\\]",
        "{{{#!latex\nV_{o} = 1\n  + 2\n}}}",
        "\\[\nV_{o} = 1\n\n * 2\n   \\]",
    ),
    (
        "between paragraphs",
        "Before.\n\n\\[ a \\]\n\nAfter.",
        "Before.\n\n{{{#!latex\na\n}}}\n\nAfter.",
        "Before.\n\n[ a ]\n\nAfter.",
    ),
    (
        "three spaces of indent",
        "   \\[ a \\]",
        "{{{#!latex\na\n}}}",
        "[ a ]",
    ),
]


@pytest.mark.parametrize(
    ("source", "chatgpt", "commonmark"),
    [row[1:] for row in DISPLAY_MATH_ROWS],
    ids=[row[0] for row in DISPLAY_MATH_ROWS],
)
def test_display_math(source, chatgpt, commonmark):
    assert markdown_to_tracwiki(source, flavor="chatgpt") == chatgpt
    assert (
        markdown_to_tracwiki(source, flavor="commonmark") == commonmark
    )
    assert markdown_to_tracwiki(source) == commonmark


def test_brackets_that_are_not_a_whole_paragraph_stay_inline():
    source = "Text first\n\\[ a \\]"
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        "Text first\n`a`"
    )


def test_two_bracket_lines_in_a_row_are_not_one_block():
    assert markdown_to_tracwiki(
        "\\[x\\]\n\\[y\\]", flavor="chatgpt"
    ) == ("`x`\n`y`")


@pytest.mark.parametrize(
    "source",
    [
        "```\n\\(x\\) \\\\(y\\\\)\n\\[ z \\]\n```",
        "Span `\\(x\\)` and `\\\\[y\\\\]` here",
        "~~~\n\\\\[\nx\n\\\\]\n~~~",
    ],
    ids=["fence", "code span", "tilde fence"],
)
def test_math_syntax_inside_code_is_untouched(source):
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        markdown_to_tracwiki(source)
    )


def test_a_fenced_block_survives_beside_display_math():
    source = "\\[ a \\]\n\n```\n\\[ b \\]\n```\n"
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        "{{{#!latex\na\n}}}\n\n{{{\n\\[ b \\]\n}}}"
    )


# -- chatgpt 3c: citation residue ---------------------------------------------

FAVICON = (
    "[image](https://www.google.com/s2/favicons"
    "?domain=https://device.report\\&sz=32)"
)


def test_a_favicon_citation_with_a_label_line_becomes_one_link():
    source = f"Text.\n\n{FAVICON}\n\ndevice.report+1\n\nNext para"
    assert _norm(source, "chatgpt") == (
        "Text.\n\n[device.report](https://device.report)\n\nNext para",
        [],
    )


def test_a_label_without_a_count():
    source = f"{FAVICON}\nReuters\n"
    assert _norm(source, "chatgpt")[0] == (
        "[Reuters](https://device.report)\n"
    )


def test_a_label_of_several_words_needs_its_count():
    source = f"{FAVICON}\n\nThe Verge+2\n"
    assert _norm(source, "chatgpt")[0] == (
        "[The Verge](https://device.report)\n"
    )


def test_a_favicon_citation_with_no_label_line_becomes_a_link():
    source = f"{FAVICON}\n\nNext para here is prose"
    assert _norm(source, "chatgpt")[0] == (
        "[device.report](https://device.report)\n\nNext para here is prose"
    )


def test_a_favicon_citation_at_the_end_of_the_text():
    assert _norm(FAVICON, "chatgpt")[0] == (
        "[device.report](https://device.report)"
    )


def test_a_domain_without_a_scheme_gets_one():
    source = (
        "[image](https://www.google.com/s2/favicons"
        "?domain=example.org&sz=32)\n\nexample.org+1"
    )
    assert (
        _norm(source, "chatgpt")[0]
        == "[example.org](https://example.org)"
    )


def test_a_favicon_citation_renders_as_a_labelled_link():
    source = f"Text.\n\n{FAVICON}\n\ndevice.report+1\n\nNext para"
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        "Text.\n\n[https://device.report device.report]\n\nNext para"
    )
    assert markdown_to_tracwiki(source) == (
        "Text.\n\n[https://www.google.com/s2/favicons"
        "?domain=https://device.report&sz=32 image]"
        "\n\ndevice.report+1\n\nNext para"
    )


@pytest.mark.parametrize(
    "source",
    [
        f"```\n{FAVICON}\n\ndevice.report+1\n```",
        f"Inline `{FAVICON}` here",
    ],
    ids=["fence", "code span"],
)
def test_a_favicon_citation_in_code_is_untouched(source):
    assert _norm(source, "chatgpt")[0] == source


def test_an_ordinary_image_link_is_untouched():
    source = "[image](https://example.com/pic.png)\n\nlabel\n"
    assert _norm(source, "chatgpt")[0] == source


# -- chatgpt 3d: utm parameters -----------------------------------------------


def test_utm_parameters_are_removed_from_every_url_form():
    source = (
        "See [x](https://e.com/a?utm_source=chatgpt.com&x=1) and "
        "https://e.com/b?utm_source=a end <https://e.com/c?utm_medium=z>"
    )
    assert _norm(source, "chatgpt")[0] == (
        "See [x](https://e.com/a?x=1) and https://e.com/b end "
        "<https://e.com/c>"
    )


def test_utm_removal_keeps_the_other_parameters_in_order():
    source = (
        "https://e.com/p?a=1&utm_campaign=z&b=2&utm_source=q&c=3#frag"
    )
    assert (
        _norm(source, "chatgpt")[0]
        == "https://e.com/p?a=1&b=2&c=3#frag"
    )


def test_utm_removal_handles_an_escaped_ampersand():
    assert _norm("https://e.com/p?utm_source=z\\&x=1", "chatgpt")[
        0
    ] == ("https://e.com/p?x=1")


def test_a_url_ending_a_sentence_keeps_its_period():
    assert _norm("Go to https://e.com/p?utm_source=z.", "chatgpt")[
        0
    ] == ("Go to https://e.com/p.")


def test_a_url_in_a_closing_parenthesis_keeps_it():
    assert _norm("(see https://e.com/p?utm_source=z)", "chatgpt")[
        0
    ] == ("(see https://e.com/p)")


def test_a_url_without_utm_is_untouched():
    source = "https://e.com/p?a=1&b=2#frag and https://e.com/q"
    assert _norm(source, "chatgpt")[0] == source


@pytest.mark.parametrize(
    "source",
    [
        "```\nhttps://e.com/?utm_source=x\n```",
        "Span `https://e.com/?utm_source=x` here",
    ],
    ids=["fence", "code span"],
)
def test_utm_in_code_is_untouched(source):
    assert _norm(source, "chatgpt")[0] == source


def test_utm_removal_through_the_converter():
    source = "See [x](https://e.com/a?utm_source=chatgpt.com&x=1)."
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        "See [https://e.com/a?x=1 x]."
    )
    assert markdown_to_tracwiki(source) == (
        "See [https://e.com/a?utm_source=chatgpt.com&x=1 x]."
    )


def test_the_default_leaves_utm_alone():
    source = "https://e.com/b?utm_source=a"
    assert _norm(source, "commonmark")[0] == source


# -- gemini 4a: code reconstruction -------------------------------------------


def test_a_labelled_block_becomes_a_fence():
    source = "Intro line\nbash\nls -la\nUse code with caution.\nAfter"
    assert _norm(source, "gemini") == (
        "Intro line\n\n```bash\nls -la\n```\nAfter",
        [REBUILT_ONE],
    )


def test_a_block_at_the_start_needs_no_blank_line():
    assert _norm("bash\nls\nUse code with caution.", "gemini")[0] == (
        "```bash\nls\n```"
    )


def test_an_existing_blank_line_is_not_doubled():
    source = "Intro\n\nbash\nls\nUse code with caution."
    assert _norm(source, "gemini")[0] == "Intro\n\n```bash\nls\n```"


def test_the_language_is_matched_case_insensitively_and_written_lowercase():
    assert _norm("Python\nx = 1\nUse code with caution.", "gemini")[
        0
    ] == ("```python\nx = 1\n```")


def test_the_code_is_kept_byte_for_byte():
    code = (
        "# Initialize the extension\n"
        "def main():\n"
        "    if __name__ == '__main__':\n"
        "        run()\n"
        "\n"
        "\t# tab"
    )
    source = f"Step\npython\n{code}\nUse code with caution.\nDone"
    assert _norm(source, "gemini")[0] == (
        f"Step\n\n```python\n{code}\n```\nDone"
    )


def test_the_converted_code_survives_in_the_fence():
    code = "# comment\nclass A:\n    x = __name__\n\n    def f(self):\n        pass"
    source = f"Run:\npython\n{code}\nUse code with caution.\nDone"
    converted = markdown_to_tracwiki(source, flavor="gemini")
    assert (
        converted == f"Run:\n\n{{{{{{#!python\n{code}\n}}}}}}\n\nDone"
    )
    broken = markdown_to_tracwiki(source)
    assert "'''name'''" in broken or "= comment =" in broken


def test_two_blocks_are_counted():
    source = (
        "a\nhtml\n<p>x</p>\nUse code with caution.\n"
        "b\npython\nx = 1\nUse code with caution.\n"
    )
    text, warnings = _norm(source, "gemini")
    assert text == (
        "a\n\n```text/html\n<p>x</p>\n```\nb\n\n```python\nx = 1\n```\n"
    )
    assert warnings == [
        "gemini: rebuilt 2 code block(s) from 'Use code with caution.' "
        "markers"
    ]


def test_an_html_block_is_listed_as_text_html_not_rendered():
    """Trac's `html` processor renders markup; a block of HTML source
    would vanish from the page. `text/html` lists it (checked live in
    test_converter_flavors_fixtures)."""
    source = "html\n<p>x</p>\nUse code with caution."
    assert _norm(source, "gemini")[0] == "```text/html\n<p>x</p>\n```"
    assert markdown_to_tracwiki(source, flavor="gemini") == (
        "{{{#!text/html\n<p>x</p>\n}}}"
    )


def test_a_language_line_with_no_closing_marker_changes_nothing():
    source = "html\n<p>x</p>\nbye\n"
    assert _norm(source, "gemini") == (source, [])


def test_a_marker_before_any_language_line_changes_nothing():
    source = "intro\n\nUse code with caution.\n\nmore"
    assert _norm(source, "gemini") == (source, [])


def test_a_word_that_is_not_a_language_is_not_a_label():
    source = "banana\nx = 1\nUse code with caution.\n"
    assert _norm(source, "gemini") == (source, [])


def test_a_language_word_inside_a_sentence_is_not_a_label():
    source = "use python here\nx = 1\nUse code with caution.\n"
    assert _norm(source, "gemini") == (source, [])


def test_code_holding_a_fence_line_is_not_rebuilt():
    """Review finding 1: a fence line between the label and the marker
    means the label is not above this marker (the code of a rebuilt block
    is never fence-like, so a plain fence always closes it)."""
    for fence in ("```py", "~~~"):
        source = f"markdown\n{fence}\nx\nUse code with caution."
        assert _norm(source, "gemini") == (source, [])


def test_a_label_inside_a_fence_is_untouched():
    source = "```\nbash\nls\nUse code with caution.\n```\n"
    assert _norm(source, "gemini") == (source, [])


def test_the_marker_closes_the_nearest_label_after_it():
    source = (
        "```\nbash\nls\n```\n\npython\nx = 1\nUse code with caution.\n"
    )
    text, warnings = _norm(source, "gemini")
    assert text == ("```\nbash\nls\n```\n\n```python\nx = 1\n```\n")
    assert warnings == [REBUILT_ONE]


# -- gemini 4b: bullets -------------------------------------------------------


def test_a_bullet_character_becomes_a_list_item():
    assert _norm(
        "Intro\n\u2022 one\n  \u2022 two\n\u2022three", "gemini"
    ) == (
        "Intro\n- one\n- two\n- three",
        [],
    )
    assert markdown_to_tracwiki(
        "\u2022 one\n\u2022 two", flavor="gemini"
    ) == (" * one\n * two")
    assert markdown_to_tracwiki("\u2022 one\n\u2022 two") == (
        "\u2022 one\n\u2022 two"
    )


@pytest.mark.parametrize(
    "source",
    [
        "```\n\u2022 x\n```",
        "Use `\u2022 x` as the bullet",
        "A bullet \u2022 mid-line stays",
    ],
    ids=["fence", "code span", "mid-line"],
)
def test_a_bullet_character_elsewhere_is_untouched(source):
    assert _norm(source, "gemini")[0] == source


def test_a_bullet_inside_a_rebuilt_block_is_untouched():
    source = "intro\n\u2022 a\nUse code with caution.\n"
    # No label line: nothing is rebuilt, the bullet becomes a list item.
    assert (
        _norm(source, "gemini")[0]
        == "intro\n- a\nUse code with caution.\n"
    )
    source = "bash\necho \u2022\n\u2022 x\nUse code with caution.\n"
    assert _norm(source, "gemini")[0] == (
        "```bash\necho \u2022\n\u2022 x\n```\n"
    )


# -- gemini 4c: citations -----------------------------------------------------


def test_a_spaced_citation_becomes_a_link():
    assert _norm("see [1] (https://example.com/a) now", "gemini")[
        0
    ] == ("see [1](https://example.com/a) now")
    assert (
        markdown_to_tracwiki(
            "see [1] (https://example.com/a) now", flavor="gemini"
        )
        == "see [https://example.com/a 1] now"
    )
    # Default flavour: no citation rewrite, and the bare `[1]` is a Trac
    # changeset link, so it gets the escape (#249).
    assert markdown_to_tracwiki(
        "see [1] (https://example.com/a) now"
    ) == ("see ![1] (https://example.com/a) now")


@pytest.mark.parametrize(
    "source",
    [
        "```\n[1] (https://example.com/a)\n```",
        "Span `[1] (https://example.com/a)` here",
        "see [x] (https://example.com/a)",
        "see [1] (not a url)",
    ],
    ids=["fence", "code span", "label not digits", "no url"],
)
def test_other_citation_shapes_are_untouched(source):
    assert _norm(source, "gemini")[0] == source


# -- a profile leaves the other profile's input alone ------------------------


@pytest.mark.parametrize(
    "source",
    [
        f"{FAVICON}\n\ndevice.report+1\n",
        "Energy \\(E=mc^2\\) https://e.com/?utm_source=x\n",
    ],
    ids=["favicon", "math and utm"],
)
def test_gemini_does_not_touch_chatgpt_input(source):
    assert _norm(source, "gemini")[0] == source
    assert _flavors().flavor_plugins("gemini") == []


@pytest.mark.parametrize(
    "source",
    [
        "bash\nls\nUse code with caution.\n",
        "\u2022 one\n[1] (https://example.com/a)\n",
    ],
    ids=["code", "bullets and citations"],
)
def test_chatgpt_does_not_touch_gemini_input(source):
    assert _norm(source, "chatgpt")[0] == source


# -- 5: the hint under the default --------------------------------------------


def test_the_default_hints_at_the_gemini_flavor():
    source = "bash\nls\nUse code with caution.\n"
    result = convert_with_warnings(source)
    assert GEMINI_HINT in result.warnings
    assert convert_with_warnings(
        source, flavor="commonmark"
    ).warnings == [GEMINI_HINT]
    # No automatic switching: the output is today's.
    assert result.text == "bash\nls\nUse code with caution."


def test_the_hint_is_not_given_inside_code():
    source = "```\nUse code with caution.\n```\n"
    assert convert_with_warnings(source).warnings == []


def test_the_hint_is_not_given_for_a_sentence_that_contains_it():
    source = "Please do not say Use code with caution. here\n"
    assert convert_with_warnings(source).warnings == []


def test_the_gemini_flavor_reports_its_rebuilt_blocks_and_no_hint():
    source = "bash\nls\nUse code with caution.\n"
    result = convert_with_warnings(source, flavor="gemini")
    assert result.warnings == [REBUILT_ONE]
    assert result.text == "{{{#!sh\nls\n}}}"


def test_the_chatgpt_flavor_has_no_gemini_hint():
    source = "bash\nls\nUse code with caution.\n"
    assert (
        convert_with_warnings(source, flavor="chatgpt").warnings == []
    )


def test_commonmark_warnings_are_unchanged_without_the_marker():
    result = convert_with_warnings("Some <b>bold</b> text\n")
    assert result.warnings == [
        "HTML tags detected - these may not render correctly in TracWiki."
    ]


# -- review fixes (Trac /trac_mcp_server #216) --------------------------------

# 1. The marker pairs with the NEAREST language line above it.


def test_a_marker_pairs_with_the_nearest_label_not_the_first():
    source = (
        "Intro\n\npython\nprint(1)\n\nSome prose here.\n\n"
        "bash\necho hi\nUse code with caution.\n"
    )
    assert _norm(source, "gemini") == (
        "Intro\n\npython\nprint(1)\n\nSome prose here.\n\n"
        "```bash\necho hi\n```\n",
        [REBUILT_ONE],
    )


def test_a_rebuilt_block_leaves_an_existing_fence_and_text_alone():
    source = (
        "Text\n\n```\npython\n```\n\nbash\nls\nUse code with caution."
    )
    assert _norm(source, "gemini")[0] == (
        "Text\n\n```\npython\n```\n\n```bash\nls\n```"
    )


@pytest.mark.parametrize(
    "source",
    [
        "  python\nx = 1\nUse code with caution.\n",
        "python\nx = 1\n  Use code with caution.\n",
        "\tpython\nx = 1\nUse code with caution.\n",
        "bash\n```\nls\nUse code with caution.\n",
        "bash\n  ~~~\nls\nUse code with caution.\n",
    ],
    ids=[
        "indented label",
        "indented marker",
        "tab label",
        "fence between",
        "indented tilde fence between",
    ],
)
def test_a_block_that_does_not_start_at_column_zero_is_not_rebuilt(
    source,
):
    assert _norm(source, "gemini") == (source, [])


def test_each_marker_takes_one_label_only():
    source = "bash\na\nUse code with caution.\nUse code with caution.\n"
    assert _norm(source, "gemini") == (
        "```bash\na\n```\nUse code with caution.\n",
        [REBUILT_ONE],
    )


# 2. Indented code and multi-line or multi-backtick spans are code.


@pytest.mark.parametrize(
    ("flavor", "source"),
    [
        ("chatgpt", "para\n\n    curl https://a.b/?utm_source=x&q=1"),
        ("chatgpt", "para\n\n\tcurl https://a.b/?utm_source=x&q=1"),
        ("chatgpt", "    https://a.b/?utm_source=x"),
        ("chatgpt", "para\n\n    \\(x\\) \\\\[y\\\\]"),
        ("gemini", "para\n\n    \u2022 x\n    y"),
        ("gemini", "para\n\n    [1] (https://example.com/a)"),
        ("chatgpt", "x ``https://a.b/?utm_source=z`` y"),
        ("chatgpt", "a `x\nhttps://a.b/?utm_source=z` b"),
        ("gemini", "a `x\n\u2022 y` b"),
    ],
    ids=[
        "indented url",
        "tab-indented url",
        "indented url at the start",
        "indented math",
        "indented bullets",
        "indented citation",
        "double-backtick span",
        "span across a line break",
        "bullet in a span across a line break",
    ],
)
def test_indented_code_and_spans_are_untouched(flavor, source):
    assert _norm(source, flavor)[0] == source


def test_an_indented_line_that_continues_a_paragraph_is_not_code():
    assert _norm("para\n    \u2022 x", "gemini")[0] == "para\n- x"
    assert _norm("para\n    https://a.b/?utm_source=x", "chatgpt")[
        0
    ] == ("para\n    https://a.b/")


def test_an_indented_line_in_a_list_item_is_not_code():
    assert _norm(
        "- item\n\n    https://a.b/?utm_source=x&q=1", "chatgpt"
    )[0] == ("- item\n\n    https://a.b/?q=1")
    assert _norm("1. item\n\n    \u2022 x", "gemini")[0] == (
        "1. item\n\n- x"
    )


def test_a_blank_line_ends_a_span_that_would_cross_it():
    source = "a `x\n\nhttps://a.b/?utm_source=z` b"
    assert _norm(source, "chatgpt")[0] == "a `x\n\nhttps://a.b/` b"


# 3. A backtick never belongs to a URL.


def test_a_url_before_a_span_across_a_line_break_matches_commonmark():
    source = "a `x\nhttps://a.b/?utm_source=z` b"
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        markdown_to_tracwiki(source)
    )
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        "a `x https://a.b/?utm_source=z` b"
    )


# 4. Math is protected before the bracket-link stash sees the text.

STASHED_MATH_ROWS = [
    (
        "doubled brackets",
        "The map \\\\[f:A\\\\] here.",
        "The map `f:A` here.",
    ),
    (
        "single brackets",
        "The map \\[f:A\\] here.",
        "The map `f:A` here.",
    ),
    (
        "doubled parens",
        "The map \\\\(f:A\\\\) here.",
        "The map `f:A` here.",
    ),
    ("single parens", "The map \\(f:A\\) here.", "The map `f:A` here."),
    ("single display", "\\[f:A\\]", "{{{#!latex\nf:A\n}}}"),
    ("doubled display", "\\\\[f:A\\\\]", "{{{#!latex\nf:A\n}}}"),
    (
        "multi-line display",
        "\\\\[\nf:A\n  wiki:B\n\\\\]",
        "{{{#!latex\nf:A\n  wiki:B\n}}}",
    ),
    (
        "a bracket form inside",
        "x \\(a[[b]] c [d:e f]\\) y",
        "x `a[[b]] c [d:e f]` y",
    ),
    (
        "in a table cell",
        "| a |\n| - |\n| \\\\(f:A\\\\) |",
        "||=a=||\n||`f:A`||",
    ),
]


@pytest.mark.parametrize(
    ("source", "expected"),
    [row[1:] for row in STASHED_MATH_ROWS],
    ids=[row[0] for row in STASHED_MATH_ROWS],
)
def test_math_holding_a_link_shaped_word_is_math(source, expected):
    assert markdown_to_tracwiki(source, flavor="chatgpt") == expected


def test_a_link_shaped_word_outside_math_is_still_a_link_under_chatgpt():
    source = "See [wiki:Page label] and \\(x\\)."
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        "See [wiki:Page label] and `x`."
    )
    assert markdown_to_tracwiki("See [wiki:Page label].") == (
        markdown_to_tracwiki("See [wiki:Page label].", flavor="chatgpt")
    )


def test_math_in_a_bracket_span_survives_the_stash():
    source = "[[Image(a.png, \\(x\\))]] end"
    out = markdown_to_tracwiki(source, flavor="chatgpt")
    assert "\x00" not in out
    assert out == "[[Image(a.png, \\(x\\))]] end"


def test_the_split_detector_parses_with_the_math_rules():
    from trac_mcp_server.converters.markdown_to_tracwiki import (
        find_list_item_splits,
    )

    source = "- a \\(f:A\\)\n  - sub\n\n  ```\n  x\n  ```\n"
    found = find_list_item_splits(source, flavor="chatgpt")
    assert [f["block"] for f in found] == ["code block"]


# 5. Adversarial input grows linearly (the ratio check of #243).
#
# A wall-clock bound ("under one second") failed whenever the host was busy:
# `many backtick runs` takes 0.68 s quiet, and with 12 busy processes on an
# 8-core host even CPU time rose to 1.01 s. So the test compares an input
# with the same input GROWTH_FACTOR times smaller, in CPU time
# (`time.process_time`), and bounds the ratio of the two. Measured (minimum
# of 3 runs), for all 12 cases below: linear growth gives a ratio of about 8
# (6.8-9.0, quiet and with 12 busy processes), and a deliberately quadratic
# function gives about 62 (61-62 in both conditions).
#
# GROWTH_LIMIT = 16 keeps a margin of about 1.8x over the linear ratios. A
# time of linear + quadratic parts, where the quadratic part is the share r
# of the time at n // 8, has a ratio of (8 + 64 * r) / (1 + r); that stays
# under 16 only while r < 1/6, so a quadratic share of more than about 17%
# at the small size fails, and so does growth faster than about n**1.33
# (8 ** 1.33 = 16). A quadratic term smaller than that is not caught: a
# `s[:i].count("a")` every 50 characters added to `many backtick runs`
# gave a ratio of 17.0, which failed at 16 and passed at the first limit of
# 20.
#
# The ratio cannot see a constant-factor slowdown, so each full-size run
# also has an absolute CPU ceiling. CPU_CEILING_S = 5.0 is about 5 times the
# slowest full-size run measured, 1.01 s for `many backtick runs` with 12
# busy processes, so load stays under it and a slowdown of 5x or more does
# not. A run over the ceiling also ends the repeats of that size.

GROWTH_FACTOR = 8
GROWTH_LIMIT = 16
CPU_CEILING_S = 5.0


def _indented_lines(count):
    return "para\n\n" + "    \u2022 x\n" * count


def _backtick_runs(count):
    return "".join("`" * (i % 40 + 1) + "a " for i in range(count))


# name -> (flavor, build, n). `build` is a function of the repetition count,
# or a string repeated that many times; build(n) is the full-size input.
ADVERSARIAL = {
    "64KB of unclosed inline openers": ("chatgpt", "\\(a ", 16384),
    "8000 unclosed bracket openers": ("chatgpt", "x \\[a ", 8000),
    "8000 unclosed display paragraphs": ("chatgpt", "\\[a\n\n", 8000),
    "8000 display openers in one paragraph": (
        "chatgpt",
        "\\[a\n",
        8000,
    ),
    "doubled openers": ("chatgpt", "\\\\(a ", 8000),
    "10000 lone language lines": ("gemini", "python\n", 10000),
    "10000 languages then markers": (
        "gemini",
        "bash\nUse code with caution.\n",
        5000,
    ),
    "10000 indented lines": ("gemini", _indented_lines, 10000),
    "20000 URL starts": ("chatgpt", "https://", 20000),
    "5000 alternating fences": ("gemini", "~~~\n```\n", 5000),
    "5000 URLs with utm": (
        "chatgpt",
        "https://a.com/?utm_source=x&b=1 ",
        5000,
    ),
    "many backtick runs": ("chatgpt", _backtick_runs, 20000),
}


def _as_build(build):
    if callable(build):
        return build
    return lambda count: build * count


def _cpu_time(fn, source, repeats, stop_over=None):
    """Least CPU time of `repeats` runs of fn(source); with `stop_over`,
    stop repeating as soon as one run takes longer than that."""
    best = None
    for _ in range(repeats):
        started = time.process_time()
        fn(source)
        elapsed = time.process_time() - started
        best = elapsed if best is None else min(best, elapsed)
        if stop_over is not None and elapsed > stop_over:
            break
    return best


def _growth(fn, build, n, k=GROWTH_FACTOR, repeats=3):
    """Return (ratio, small, big): the least CPU time of `repeats` runs of
    fn(build(n // k)) and of fn(build(n)), and big / small. Inputs are built
    outside the timed region; the big runs stop at one over CPU_CEILING_S."""
    small_input = build(n // k)
    big_input = build(n)
    small = _cpu_time(fn, small_input, repeats)
    big = _cpu_time(fn, big_input, repeats, stop_over=CPU_CEILING_S)
    assert small > 0, (
        f"the small input (repetitions={n // k}) took no measurable CPU "
        "time, so no ratio can be taken; use a larger input"
    )
    return big / small, small, big


@pytest.mark.parametrize(
    ("flavor", "build", "n"),
    list(ADVERSARIAL.values()),
    ids=list(ADVERSARIAL),
)
def test_adversarial_input_grows_linearly(flavor, build, n):
    def convert(source):
        return markdown_to_tracwiki(source, flavor=flavor)

    ratio, small, big = _growth(convert, _as_build(build), n)
    assert ratio < GROWTH_LIMIT, (
        f"CPU time grew {ratio:.1f}x for {GROWTH_FACTOR}x the input "
        f"(limit {GROWTH_LIMIT}x): {small:.4f}s at {n // GROWTH_FACTOR} "
        f"repetitions, {big:.4f}s at {n} repetitions"
    )
    assert big < CPU_CEILING_S, (
        f"the full-size run ({n} repetitions) took {big:.2f}s of CPU time "
        f"(ceiling {CPU_CEILING_S}s); growth was {ratio:.1f}x"
    )


def _quadratic(source):
    for i in range(0, len(source), 4):
        source[:i].count("a")


def _linear(source):
    for _ in range(1000):
        source.count("a")


def _ab(count):
    return "ab" * count


def test_growth_ratio_flags_a_quadratic_function():
    # The small size (4096 repetitions) takes about 5 ms, well above the
    # timer's noise; the big one about 0.3 s.
    ratio, _, _ = _growth(_quadratic, _ab, 8 * 4096)
    assert ratio >= GROWTH_LIMIT


def test_growth_ratio_passes_a_linear_function():
    ratio, _, _ = _growth(_linear, _ab, 8 * 4096)
    assert ratio < GROWTH_LIMIT


def test_growth_refuses_a_ratio_of_an_unmeasurable_input(monkeypatch):
    monkeypatch.setattr(time, "process_time", lambda: 1.0)
    with pytest.raises(AssertionError, match="no measurable CPU time"):
        _growth(lambda source: None, _ab, 80)


def test_growth_stops_repeating_a_run_over_the_ceiling(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "CPU_CEILING_S", 0.0)
    calls = []

    def fn(source):
        calls.append(len(source))
        sum(range(1000))

    _growth(fn, _ab, 80, k=8, repeats=3)
    # three small runs, one big run
    assert calls == [20] * 3 + [160]


def test_a_long_closed_formula_still_converts():
    body = "x + " * 200 + "y"
    assert markdown_to_tracwiki(
        f"a \\({body}\\) b", flavor="chatgpt"
    ) == (f"a `{body}` b")


# 6. Any whitespace-only line before a display paragraph.


@pytest.mark.parametrize(
    "blank", ["", " ", "  ", "\t", " \t ", "      "], ids=repr
)
def test_display_math_after_any_blank_line(blank):
    source = f"x\n{blank}\n\\[\nE = m_c^2\n\\]"
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        "x\n\n{{{#!latex\nE = m_c^2\n}}}"
    )


# 7. A URL that runs into code is cut there.


def test_a_url_running_into_a_code_span_is_cut_at_the_span():
    source = "x https://x.com/?utm_source=a`code`"
    assert _norm(source, "chatgpt")[0] == "x https://x.com/`code`"
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        "x https://x.com/`code`"
    )


# 9. The normalised source rides on the result.


def test_the_result_carries_the_normalised_source():
    source = "bash\nls\nUse code with caution.\n"
    result = convert_with_warnings(source, flavor="gemini")
    assert result.normalised_source == _norm(source, "gemini")[0]
    assert result.normalised_source == "```bash\nls\n```\n"


def test_the_default_result_has_no_normalised_source():
    assert convert_with_warnings("x\n").normalised_source is None
    assert (
        convert_with_warnings(
            "x\n", flavor="commonmark"
        ).normalised_source
        is None
    )


def test_the_default_result_is_otherwise_unchanged():
    result = convert_with_warnings("Some <b>bold</b> text\n")
    assert result.text == "Some <b>bold</b> text"
    assert result.source_format == "markdown"
    assert result.target_format == "tracwiki"
    assert result.converted is True
    assert result.title is None


# -- second review fixes (Trac /trac_mcp_server #216) --------------------------

# 1. A URL ends where the URL ends; text after it is never touched.

URL_BOUNDS = [
    (
        "ideographic stop after a link",
        "See [a](https://a.com/?utm_source=chatgpt.com)\u3002\u4e0b\u4e00\u4e2a",
        "See [a](https://a.com/)\u3002\u4e0b\u4e00\u4e2a",
    ),
    (
        "text glued after a link",
        "See [a](https://a.com/?utm_source=chatgpt.com)abc",
        "See [a](https://a.com/)abc",
    ),
    (
        "text glued after a parenthesised bare URL",
        "(https://a.com/x?id=1&utm_source=chatgpt.com)and more",
        "(https://a.com/x?id=1)and more",
    ),
    (
        "non-ASCII after a bare URL",
        "https://a.com/?utm_source=x\u3002\u4e0b",
        "https://a.com/\u3002\u4e0b",
    ),
    (
        "balanced parentheses in a link target",
        "[w](https://en.wikipedia.org/wiki/A_(b)?utm_source=x)tail",
        "[w](https://en.wikipedia.org/wiki/A_(b))tail",
    ),
    (
        "balanced parentheses in a bare URL",
        "https://en.wikipedia.org/wiki/A_(b)?utm_source=x end",
        "https://en.wikipedia.org/wiki/A_(b) end",
    ),
    (
        "a quote ends a bare URL",
        '"https://a.com/?utm_source=x"tail',
        '"https://a.com/"tail',
    ),
    (
        "a link title is kept",
        '[a](https://a.com/?utm_source=x "t")',
        '[a](https://a.com/ "t")',
    ),
    (
        "an angle autolink",
        "<https://a.com/?utm_source=x>tail",
        "<https://a.com/>tail",
    ),
    (
        "a link target that is not followed by text",
        "[a](https://a.com/p?x=1&utm_source=z)",
        "[a](https://a.com/p?x=1)",
    ),
]


@pytest.mark.parametrize(
    ("source", "expected"),
    [row[1:] for row in URL_BOUNDS],
    ids=[row[0] for row in URL_BOUNDS],
)
def test_a_url_ends_where_the_url_ends(source, expected):
    assert _norm(source, "chatgpt")[0] == expected


# 2. Inline math stays hidden from the table splitter, like a code span.


@pytest.mark.parametrize(
    ("cell", "rendered"),
    [
        ("\\(|x|\\)", "`|x|`"),
        ("\\\\(|x|\\\\)", "`|x|`"),
        ("\\\\(a`b|c\\\\)", "{{{a`b|c}}}"),
        ("\\[|y|\\]", "`|y|`"),
    ],
    ids=["single", "doubled", "backtick", "brackets"],
)
def test_a_pipe_in_math_does_not_split_a_table_cell(cell, rendered):
    source = f"| a | b |\n|---|---|\n| {cell} | 2 |\n"
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        f"||=a=||=b=||\n||{rendered}||2||"
    )


def test_the_code_span_equivalent_of_the_pipe_row():
    source = "| a | b |\n|---|---|\n| `|x|` | 2 |\n"
    assert markdown_to_tracwiki(source) == "||=a=||=b=||\n||`|x|`||2||"


def test_display_math_is_still_a_block_beside_a_table():
    source = "| a |\n|---|\n| \\(|x|\\) |\n\n\\[ |y| \\]\n"
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        "||=a=||\n||`|x|`||\n\n{{{#!latex\n|y|\n}}}"
    )


def test_math_in_a_heading_and_emphasis_still_renders():
    assert markdown_to_tracwiki("# H \\(x\\)\n", flavor="chatgpt") == (
        "= H `x` ="
    )
    assert markdown_to_tracwiki(
        "**\\(x\\)** end", flavor="chatgpt"
    ) == ("'''`x`''' end")


# 3. An empty label is skipped: the marker's block needs code.


def test_a_label_directly_above_the_marker_is_code_not_the_label():
    source = "bash\npip install foo\npython\nUse code with caution.\n"
    assert _norm(source, "gemini") == (
        "```bash\npip install foo\npython\n```\n",
        [REBUILT_ONE],
    )
    assert markdown_to_tracwiki(source, flavor="gemini") == (
        "{{{#!sh\npip install foo\npython\n}}}"
    )


def test_a_label_with_only_blank_lines_above_the_marker_is_skipped():
    source = "python\nx = 1\npython\n\nUse code with caution.\n"
    assert _norm(source, "gemini") == (
        "```python\nx = 1\npython\n\n```\n",
        [REBUILT_ONE],
    )


@pytest.mark.parametrize(
    "source",
    [
        "python\nUse code with caution.\n",
        "python\n\nUse code with caution.\n",
        "bash\n  \nUse code with caution.\n",
    ],
    ids=["adjacent", "blank between", "whitespace between"],
)
def test_no_code_no_block(source):
    assert _norm(source, "gemini") == (source, [])


def test_the_warning_counts_only_rebuilt_blocks():
    source = (
        "python\nUse code with caution.\n"
        "bash\nls\nUse code with caution.\n"
    )
    text, warnings = _norm(source, "gemini")
    assert warnings == [REBUILT_ONE]
    assert "```python\n```" not in text
    assert "{{{#!python\n}}}" not in markdown_to_tracwiki(
        source, flavor="gemini"
    )


# 4. One fence scanner for both fence characters.


def test_a_backtick_line_inside_a_tilde_fence_does_not_open_a_fence():
    source = "~~~\n```\n~~~\n\n\u2022 real bullet\n"
    assert (
        _norm(source, "gemini")[0] == "~~~\n```\n~~~\n\n- real bullet\n"
    )
    assert markdown_to_tracwiki(source, flavor="gemini").endswith(
        "\n\n * real bullet"
    )


def test_a_tilde_line_inside_a_backtick_fence_does_not_open_a_fence():
    source = "```\n~~~\n```\n\n\u2022 real bullet\n"
    assert (
        _norm(source, "gemini")[0] == "```\n~~~\n```\n\n- real bullet\n"
    )


def test_a_fence_closes_only_with_its_own_character_and_length():
    source = "````\n```\n\u2022 x\n~~~~\n````\n\n\u2022 y\n"
    assert _norm(source, "gemini")[0] == (
        "````\n```\n\u2022 x\n~~~~\n````\n\n- y\n"
    )


def test_an_unclosed_fence_runs_to_the_end():
    source = "```\n\u2022 x\n\n\u2022 y\n"
    assert _norm(source, "gemini")[0] == source


def test_a_fence_nested_in_a_list_at_any_indent_is_code():
    source = (
        "- a\n    - b\n\n      ```\n      https://a.b/?utm_source=x\n"
        "      ```\n\n- https://a.b/?utm_source=y\n"
    )
    assert _norm(source, "chatgpt")[0] == (
        "- a\n    - b\n\n      ```\n      https://a.b/?utm_source=x\n"
        "      ```\n\n- https://a.b/\n"
    )


def test_the_fence_scanner_is_one_implementation_in_common():
    from trac_mcp_server.converters import common, flavors

    assert not hasattr(flavors, "_blank_tilde_fences")
    text = "a\n~~~\n```\n~~~\nb\n```\n~~~\n```\nc"
    blanked = common.blank_fenced_code(text)
    assert len(blanked) == len(text)
    assert blanked == "a\n   \n   \n   \nb\n   \n   \n   \nc"


def test_blank_code_fences_keeps_its_behaviour_for_other_callers():
    from trac_mcp_server.converters.common import blank_code_fences

    assert blank_code_fences("a\n```x\nb\n```y\nc") == (
        "a\n    \n \n    \nc"
    )


# 5. The hint and the rebuild read the marker the same way.


def test_an_indented_marker_gives_no_hint():
    assert (
        convert_with_warnings(
            "text\n  Use code with caution.\n"
        ).warnings
        == []
    )
    assert (
        convert_with_warnings(
            "text\n\tUse code with caution.\n"
        ).warnings
        == []
    )


def test_a_marker_at_column_zero_with_trailing_space_still_hints():
    source = "text\nUse code with caution. \n"
    assert convert_with_warnings(source).warnings == [GEMINI_HINT]


# 6. A percent-encoded favicon domain is decoded.


def test_a_percent_encoded_favicon_domain_is_decoded():
    favicon = (
        "[image](https://www.google.com/s2/favicons"
        "?domain=https%3A%2F%2Fexample.com\\&sz=32)"
    )
    assert _norm(f"{favicon}\n\nexample.com+1\n", "chatgpt")[0] == (
        "[example.com](https://example.com)\n"
    )
    assert _norm(favicon, "chatgpt")[0] == (
        "[example.com](https://example.com)"
    )


# 8. The hint builds no mask when the marker is absent.


def test_the_default_conversion_builds_no_code_mask_without_the_marker(
    monkeypatch,
):
    from trac_mcp_server.converters import flavors

    def boom(text):
        raise AssertionError("the mask was built")

    monkeypatch.setattr(flavors, "_code_mask", boom)
    assert convert_with_warnings("plain `text`\n").warnings == []
    assert not flavors.has_gemini_code_marker("plain\n")


# -- third review fixes (Trac /trac_mcp_server #216) ---------------------------

# 1. A marker inside code is not a marker.


@pytest.mark.parametrize(
    "source",
    [
        "python\n{{{\nx=1\nUse code with caution.\n}}}\n",
        "bash\nls `a\nUse code with caution.\nb` c\n",
    ],
    ids=["tracwiki block", "multi-line span"],
)
def test_a_marker_inside_code_is_skipped(source):
    assert _norm(source, "gemini") == (source, [])


# 2. A formula with a backtick and `}}}` has no inline TracWiki form.

UNREPRESENTABLE = "inline \\(a`}}}b\\) x"


def test_an_unrepresentable_formula_is_left_as_written():
    assert markdown_to_tracwiki(UNREPRESENTABLE, flavor="chatgpt") == (
        markdown_to_tracwiki(UNREPRESENTABLE)
    )


def test_an_unrepresentable_formula_is_named_in_a_warning():
    warnings = convert_with_warnings(
        UNREPRESENTABLE, flavor="chatgpt"
    ).warnings
    named = [w for w in warnings if "a`}}}b" in w]
    assert len(named) == 1
    assert named[0].startswith("chatgpt: ")


def test_the_warning_names_the_first_40_characters_only():
    formula = "a`}}}" + "0123456789" * 6
    warnings = convert_with_warnings(
        f"x \\({formula}\\) y", flavor="chatgpt"
    ).warnings
    assert len(warnings) == 1
    assert formula[:40] in warnings[0]
    assert formula[:41] not in warnings[0]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("x \\(a`b\\) y", "x {{{a`b}}} y"),
        ("x \\(a}}}b\\) y", "x `a}}}b` y"),
    ],
    ids=["backtick only", "braces only"],
)
def test_a_representable_formula_converts_without_a_warning(
    source, expected
):
    result = convert_with_warnings(source, flavor="chatgpt")
    assert result.text == expected
    assert result.warnings == []


def test_one_bad_formula_does_not_stop_the_others():
    source = "\\(a`}}}b\\) and \\(c\\)"
    result = convert_with_warnings(source, flavor="chatgpt")
    assert result.text.endswith("and `c`")
    assert len(result.warnings) == 1


# 3. CRLF and lone CR are folded for the flavours, not for the default.


def test_a_crlf_favicon_paste_converts_as_its_lf_version_does():
    lf = f"Text.\n\n{FAVICON}\n\ndevice.report+1\n\nNext para\n"
    for eol in ("\r\n", "\r"):
        crlf = lf.replace("\n", eol)
        assert _norm(crlf, "chatgpt") == _norm(lf, "chatgpt")
        assert markdown_to_tracwiki(crlf, flavor="chatgpt") == (
            markdown_to_tracwiki(lf, flavor="chatgpt")
        )


def test_a_crlf_display_math_paragraph_converts_as_its_lf_version_does():
    lf = "x\n\n\\[\nE = m_c^2\n\\]\n\nafter\n"
    crlf = lf.replace("\n", "\r\n")
    assert markdown_to_tracwiki(crlf, flavor="chatgpt") == (
        "x\n\n{{{#!latex\nE = m_c^2\n}}}\n\nafter"
    )
    assert markdown_to_tracwiki(crlf, flavor="chatgpt") == (
        markdown_to_tracwiki(lf, flavor="chatgpt")
    )


def test_a_crlf_gemini_copy_converts_as_its_lf_version_does():
    lf = "Run:\nbash\nls\nUse code with caution.\n\u2022 a\n"
    crlf = lf.replace("\n", "\r\n")
    assert _norm(crlf, "gemini") == _norm(lf, "gemini")


def test_the_default_flavor_keeps_its_line_endings():
    crlf = "a\r\nb\r\n"
    assert _norm(crlf, "commonmark") == (crlf, [])
    assert markdown_to_tracwiki(crlf) == markdown_to_tracwiki(
        crlf, flavor="commonmark"
    )


# 4. The favicon fallback is a Markdown link, not `<...>` (an HTML tag to
# the converter's own tag check).


def test_the_favicon_fallback_raises_no_html_tag_warning():
    source = (
        "[image](https://www.google.com/s2/favicons?domain=a.com&sz=32)"
        "\n\nThis is a long prose paragraph here.\n"
    )
    result = convert_with_warnings(source, flavor="chatgpt")
    assert result.warnings == []
    assert _norm(source, "chatgpt")[0] == (
        "[a.com](https://a.com)\n\nThis is a long prose paragraph here.\n"
    )


def test_the_favicon_fallback_label_is_the_decoded_domain_without_scheme():
    favicon = (
        "[image](https://www.google.com/s2/favicons"
        "?domain=https%3A%2F%2Fexample.com\\&sz=32)"
    )
    assert (
        _norm(favicon, "chatgpt")[0]
        == "[example.com](https://example.com)"
    )


# 5. A `+N` label is stripped.


def test_a_label_with_a_space_before_its_count_is_stripped():
    source = (
        "[image](https://www.google.com/s2/favicons"
        "?domain=reuters.com&sz=32)\nReuters +2\n"
    )
    assert (
        _norm(source, "chatgpt")[0]
        == "[Reuters](https://reuters.com)\n"
    )
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        "[https://reuters.com Reuters]"
    )


# 6. The fence label is lowercase.


@pytest.mark.parametrize(
    ("label", "fence", "processor"),
    [
        ("Python", "```python", "python"),
        ("JSON", "```json", "json"),
        ("Bash", "```bash", "sh"),
        ("Html", "```text/html", "text/html"),
        ("HTML", "```text/html", "text/html"),
        ("C++", "```c++", "cpp"),
    ],
)
def test_the_fence_label_is_lowercased(label, fence, processor):
    source = f"{label}\nx\nUse code with caution.\n"
    assert _norm(source, "gemini")[0] == f"{fence}\nx\n```\n"
    assert markdown_to_tracwiki(source, flavor="gemini") == (
        f"{{{{{{#!{processor}\nx\n}}}}}}"
    )


@pytest.mark.parametrize(
    "source",
    ["\\( \\)", "\\\\( \\\\) x", "a \\[ \\] b", "\\\\[ \\\\] c"],
    ids=repr,
)
def test_an_empty_formula_reads_as_commonmark_does(source):
    assert markdown_to_tracwiki(source, flavor="chatgpt") == (
        markdown_to_tracwiki(source)
    )
