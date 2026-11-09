"""Unit tests for the Trac HTML -> Markdown translator (Trac #209).

One group per override on small inline HTML; the fixture-wide checks
(golden files and the markdown-it self-check) are in
``test_html_to_markdown_fixtures.py``.
"""

import pytest
from bs4 import BeautifulSoup

from trac_mcp_server.converters.html_to_markdown import (
    trac_html_to_markdown,
)


def conv(html, source=None, **kw):
    return trac_html_to_markdown(html, source, **kw)


def md(html, source=None, **kw):
    return conv(html, source, **kw).text


def code(inner, cls="code"):
    return (
        '<div class="wiki-code"><div class="code"><pre>'
        f"{inner}</pre></div></div>"
    )


# --- the result object ---------------------------------------------


def test_result_fields():
    r = conv("<p>\nhello\n</p>")
    assert r.text == "hello"
    assert r.source_format == "html"
    assert r.target_format == "markdown"
    assert r.converted is True
    assert r.warnings == []


def test_empty_input():
    assert md("") == ""


def test_bad_deflist_value_is_refused():
    with pytest.raises(ValueError, match="deflist"):
        conv("<p>x</p>", deflist="nope")


def test_warnings_are_deduplicated():
    html = (
        '<ol class="loweralpha"><li>a</li></ol><p>x</p>'
        '<ol class="loweralpha"><li>b</li></ol>'
    )
    r = conv(html)
    assert len(r.warnings) == 1


# --- code-block language from the source's openers -----------------


def test_language_comes_from_the_source_opener():
    html = code('<span class="k">def</span> f():\n')
    r = conv(html, "{{{#!python\ndef f():\n}}}")
    assert r.text == "```python\ndef f():\n```"
    assert r.warnings == []


def test_shell_processor_maps_to_bash_like_the_read_leg():
    r = conv(code("ls\n"), "{{{#!sh\nls\n}}}")
    assert r.text == "```bash\nls\n```"


def test_no_source_means_no_language_and_no_warning():
    r = conv(code("ls\n"))
    assert r.text == "```\nls\n```"
    assert r.warnings == []


def test_languages_pair_in_document_order():
    html = code("a = 1\n") + code("ls\n")
    src = "{{{#!python\na = 1\n}}}\n\ntext\n\n{{{#!sh\nls\n}}}\n"
    assert md(html, src) == "```python\na = 1\n```\n\n```bash\nls\n```"


def test_processor_on_the_line_after_the_opener_counts():
    src = "{{{\n#!python\nx = 1\n}}}"
    assert md(code("x = 1\n"), src) == "```python\nx = 1\n```"


def test_path_after_hashbang_is_a_plain_block_not_a_processor():
    # `#!/bin/sh` is not a processor name: Trac renders a pre.wiki.
    src = "{{{#!/bin/sh\necho hi\n}}}\n\n{{{#!python\nx = 1\n}}}"
    html = '<pre class="wiki">#!/bin/sh\necho hi\n</pre>' + code(
        "x = 1\n"
    )
    out = md(html, src)
    assert out == (
        "```\n#!/bin/sh\necho hi\n```\n\n```python\nx = 1\n```"
    )


@pytest.mark.parametrize(
    "name",
    ["comment", "html", "htmlcomment", "default", "Workflow"],
)
def test_processors_that_make_no_code_block_are_not_counted(name):
    src = f"{{{{{{#!{name}\nhidden\n}}}}}}\n\n{{{{{{#!python\nx = 1\n}}}}}}"
    assert md(code("x = 1\n"), src) == "```python\nx = 1\n```"


@pytest.mark.parametrize("name", ["text/html", "rst", "markdown"])
def test_other_names_do_make_a_code_block(name):
    src = f"{{{{{{#!{name}\nbody\n}}}}}}"
    assert md(code("body\n"), src).startswith(f"```{name}\n")


def test_block_inside_a_wiki_processor_counts():
    src = "{{{#!div\n{{{#!python\nx = 1\n}}}\n}}}"
    assert md(code("x = 1\n"), src) == "```python\nx = 1\n```"


def test_block_inside_a_plain_block_is_content_not_a_block():
    src = "{{{\n{{{#!python\nx = 1\n}}}\n}}}\n\n{{{#!sh\nls\n}}}"
    html = '<pre class="wiki">{{{#!python\nx = 1\n}}}\n</pre>' + code(
        "ls\n"
    )
    out = md(html, src)
    assert out.endswith("```bash\nls\n```")
    assert out.startswith("```\n{{{#!python\nx = 1\n}}}\n```")


def test_block_inside_a_code_block_is_content():
    src = "{{{#!text\n{{{#!python\nx\n}}}\n}}}"
    out = md(code("{{{#!python\nx\n}}}\n"), src)
    assert out == "```text\n{{{#!python\nx\n}}}\n```"


def test_source_body_is_emitted_not_the_html_text():
    # Line numbers interleaved by table.code must not leak.
    html = (
        '<div class="wiki-code"><table class="code"><thead><tr>'
        '<th class="lineno">Line</th><th class="content"> </th></tr>'
        '</thead><tbody><tr><th id="L1"><a href="#L1">1</a></th>'
        '<td><span class="n">a</span>\n</td></tr><tr><th id="L2">'
        '<a href="#L2">2</a></th><td><span class="n">b</span>\n</td>'
        "</tr></tbody></table></div>"
    )
    out = md(html, "{{{#!python lineno=1\na\nb\n}}}")
    assert out == "```python\na\nb\n```"


def test_unpaired_table_code_falls_back_to_td_cells_only():
    html = (
        '<div class="wiki-code"><table class="code"><tbody><tr>'
        '<th><a href="#L1">1</a></th><td>a\n</td></tr><tr><th>'
        '<a href="#L2">2</a></th><td>b\n</td></tr></tbody></table>'
        "</div>"
    )
    r = conv(html, "plain text only")
    assert r.text == "```\na\nb\n```"
    assert len(r.warnings) == 1
    assert "code block" in r.warnings[0]


def test_diff_block_emits_source_with_plus_and_minus():
    html = (
        '<div class="wiki-code"><div class="diff"><ul class="entries">'
        '<li class="entry"><table class="trac-diff inline"><tbody>'
        '<tr><th>1</th><td class="l"><span>old</span></td></tr>'
        '<tr><th>1</th><td class="r"><span>new</span></td></tr>'
        "</tbody></table></li></ul></div></div>"
    )
    out = md(html, "{{{#!diff\n-old\n+new\n}}}")
    assert out == "```diff\n-old\n+new\n```"


def test_counts_differ_pairs_by_text():
    # Macro-emitted code (e.g. [[TracIni]]) has no opener in the source.
    html = code("[a]\n") + code("only = here\n") + code("[b]\n")
    src = "{{{#!ini\nonly = here\n}}}"
    r = conv(html, src)
    assert r.text == (
        "```\n[a]\n```\n\n```ini\nonly = here\n```\n\n```\n[b]\n```"
    )
    assert len(r.warnings) == 1
    assert "2 code blocks" in r.warnings[0]


def test_dedents_a_block_indented_in_the_source():
    src = " 1. item\n    {{{#!python\n    x = 1\n    }}}\n"
    assert md(code("x = 1\n"), src) == "```python\nx = 1\n```"


def test_fence_is_longer_than_any_backtick_run_in_the_body():
    out = md(code("```\nx\n```\n"), "{{{#!text\n```\nx\n```\n}}}")
    assert out == "````text\n```\nx\n```\n````"


def test_pre_wiki_is_a_fence_with_no_language():
    out = md('<pre class="wiki">a\n  b\n</pre>')
    assert out == "```\na\n  b\n```"


# --- external-link icon ----------------------------------------------


def test_ext_link_icon_and_its_zero_width_space_are_stripped():
    html = (
        '<a class="ext-link" href="https://x.org/a">'
        '<span class="icon">​</span>the site</a>'
    )
    out = md(html)
    assert out == "[the site](https://x.org/a)"
    assert "​" not in out


def test_ext_link_whose_text_is_its_url_is_an_autolink():
    html = (
        '<a class="ext-link" href="https://x.org/a">'
        '<span class="icon">​</span>https://x.org/a</a>'
    )
    assert md(html) == "<https://x.org/a>"


def test_link_title_is_not_emitted():
    html = '<a class="wiki" href="/w/A" title="A in Proj">A</a>'
    assert md(html) == "[A](/w/A)"


# --- missing links ----------------------------------------------------


@pytest.mark.parametrize(
    "cls",
    [
        "missing wiki",
        "changeset missing",
        "missing ticket",
        "missing source",
        "milestone missing",
        "attachment missing",
    ],
)
def test_missing_link_becomes_plain_text(cls):
    html = f'<p>see <a class="{cls}" href="/x?create">Foo</a> now</p>'
    assert md(html) == "see Foo now"


# --- link_map ---------------------------------------------------------


def test_link_map_rewrites_by_longest_prefix():
    html = '<a href="https://t.example.org/p/wiki/A">A</a>'
    out = md(
        html,
        link_map={
            "https://t.example.org/p/": "trac:/",
            "https://t.example.org/p/wiki/": "wiki:",
        },
    )
    assert out == "[A](wiki:A)"


def test_link_map_applies_to_hrefs_not_images_and_leaves_others():
    # Images and attachments are #215's: a src is never rewritten.
    html = (
        '<img alt="a" src="https://t.example.org/raw/f.png"/>'
        '<a href="https://t.example.org/w/A">A</a>'
        '<a href="https://other.org/x">o</a>'
    )
    out = md(html, link_map={"https://t.example.org/": "/"})
    assert out == (
        "![a](https://t.example.org/raw/f.png)"
        "[A](/w/A)[o](https://other.org/x)"
    )


def test_link_map_longest_prefix_wins_whatever_the_maps_order():
    html = '<a href="https://t.example.org/p/wiki/A">A</a>'
    short = ("https://t.example.org/p/", "trac:/")
    long = ("https://t.example.org/p/wiki/", "wiki:")
    for rules in ((short, long), (long, short)):
        assert md(html, link_map=dict(rules)) == "[A](wiki:A)"


def test_link_map_never_touches_an_in_page_anchor():
    # Even a rule whose prefix is "#", or "" ahead of it, leaves
    # a "#Heading" href alone; the same href elsewhere is mapped.
    html = (
        '<p><a href="#Heading">Heading</a> '
        '<a href="/wiki/Other#Heading">Other</a></p>'
    )
    for rules in ({"#": "/x#"}, {"/": "https://b.example/"}):
        out = md(html, link_map=rules)
        assert "[Heading](#Heading)" in out, rules
    out = md(html, link_map={"/wiki/": "https://b.example/"})
    assert "[Other](https://b.example/Other#Heading)" in out


# In-page anchors. Real Trac renders ``[#Linkrow x]`` as the document's own
# URL plus the fragment, not as a bare "#Linkrow": getPageHTML gives
# "<trac_url>/wiki/<page>#Linkrow", wikiToHtml (no page) "<trac_url>#Linkrow".
_ROOT = "http://trac.example.org:8000/trac_test"
_PAGE_URL = f"{_ROOT}/wiki/Scratch/Ticket213Probe30feed0c"
_BLOG = {f"{_ROOT}/": "https://blog.example/"}


def _anchor(href, label="x"):
    return f'<p><a class="wiki" href="{href}">{label}</a></p>'


def test_page_url_plus_fragment_is_an_in_page_anchor():
    out = md(
        _anchor(f"{_PAGE_URL}#Linkrow"),
        link_map=_BLOG,
        document_url=_PAGE_URL,
    )
    assert out == "[x](#Linkrow)"


def test_instance_root_plus_fragment_is_an_in_page_anchor():
    # wikiToHtml has no page context: the document URL is the root.
    out = md(
        _anchor(f"{_ROOT}#Linkrow"), link_map=_BLOG, document_url=_ROOT
    )
    assert out == "[x](#Linkrow)"


def test_in_page_anchor_is_written_relative_with_no_map_at_all():
    out = md(_anchor(f"{_PAGE_URL}#Linkrow"), document_url=_PAGE_URL)
    assert out == "[x](#Linkrow)"


def test_in_page_anchor_ignores_one_trailing_slash_on_either_side():
    html = _anchor(f"{_PAGE_URL}/#Linkrow")
    assert md(html, document_url=_PAGE_URL) == "[x](#Linkrow)"
    html = _anchor(f"{_PAGE_URL}#Linkrow")
    assert md(html, document_url=f"{_PAGE_URL}/") == "[x](#Linkrow)"


@pytest.mark.parametrize(
    ("href_name", "document_name"),
    [
        ("Scratch/Page%20Two", "Scratch/Page Two"),
        ("Scratch/Page Two", "Scratch/Page%20Two"),
        ("Scratch/Caf%C3%A9", "Scratch/Café"),
        ("Scratch/Café", "Scratch/Caf%C3%A9"),
    ],
)
def test_in_page_anchor_matches_across_percent_encoding(
    href_name, document_name
):
    href = f"{_ROOT}/wiki/{href_name}#Top"
    out = md(
        _anchor(href),
        link_map=_BLOG,
        document_url=f"{_ROOT}/wiki/{document_name}",
    )
    assert out == "[x](#Top)"


def test_same_page_without_a_fragment_is_an_ordinary_link():
    out = md(_anchor(_PAGE_URL), link_map=_BLOG, document_url=_PAGE_URL)
    assert (
        out
        == "[x](https://blog.example/wiki/Scratch/Ticket213Probe30feed0c)"
    )


def test_same_page_with_an_empty_fragment_is_an_ordinary_link():
    out = md(
        _anchor(f"{_PAGE_URL}#"), link_map=_BLOG, document_url=_PAGE_URL
    )
    assert out.startswith("[x](https://blog.example/wiki/")


def test_another_page_with_a_fragment_is_an_ordinary_link():
    # What [wiki:WikiStart#Top other] renders to, in both calls.
    href = f"{_ROOT}/wiki/WikiStart#Top"
    for document_url in (_PAGE_URL, _ROOT):
        out = md(
            _anchor(href, "other"),
            link_map=_BLOG,
            document_url=document_url,
        )
        assert out == "[other](https://blog.example/wiki/WikiStart#Top)"


def test_a_page_whose_name_extends_the_documents_is_another_page():
    href = f"{_PAGE_URL}Two#Top"
    out = md(_anchor(href), link_map=_BLOG, document_url=_PAGE_URL)
    assert (
        out
        == "[x](https://blog.example/wiki/Scratch/Ticket213Probe30feed0cTwo#Top)"
    )


# Trac builds absolute hrefs from its own base URL, which can differ from the
# daemon's TRAC_URL (a LAN address against the public name): only the path
# names the page.
_LAN = "http://10.0.0.5/auto_pm/wiki/P"
_PUBLIC_RULE = {
    "https://trac.example/auto_pm/wiki/": "https://b.example/"
}


def test_in_page_anchor_ignores_scheme_and_host():
    out = md(
        _anchor("https://trac.example/auto_pm/wiki/P#Sec"),
        link_map=_PUBLIC_RULE,
        document_url=_LAN,
    )
    assert out == "[x](#Sec)"


def test_a_different_path_on_the_same_host_is_an_ordinary_link():
    out = md(
        _anchor("https://trac.example/auto_pm/wiki/Q#Sec"),
        link_map=_PUBLIC_RULE,
        document_url=_LAN,
    )
    assert out == "[x](https://b.example/Q#Sec)"


def test_the_root_form_also_ignores_the_host():
    out = md(
        _anchor("https://trac.example/trac_test#Sec"),
        link_map={"https://trac.example/": "https://b.example/"},
        document_url="http://10.0.0.5/trac_test",
    )
    assert out == "[x](#Sec)"


def test_the_path_match_still_unquotes_and_ignores_a_trailing_slash():
    out = md(
        _anchor("https://trac.example/auto_pm/wiki/Page%20Two/#Sec"),
        link_map=_PUBLIC_RULE,
        document_url="http://10.0.0.5/auto_pm/wiki/Page Two",
    )
    assert out == "[x](#Sec)"


def test_a_same_path_link_with_a_query_is_another_document():
    out = md(
        _anchor("https://trac.example/auto_pm/wiki/P?version=3#Sec"),
        link_map=_PUBLIC_RULE,
        document_url=_LAN,
    )
    assert out == "[x](https://b.example/P?version=3#Sec)"


def test_a_relative_href_is_compared_whole_not_by_path():
    # Only two absolute URLs are compared by path.
    out = md(
        _anchor("/auto_pm/wiki/P#Sec"),
        link_map={"/auto_pm/": "https://b.example/"},
        document_url=_LAN,
    )
    assert out == "[x](https://b.example/wiki/P#Sec)"


def test_without_document_url_only_a_bare_anchor_counts():
    html = (
        f'<p><a href="{_PAGE_URL}#Linkrow">own</a> '
        '<a href="#Linkrow">bare</a></p>'
    )
    out = md(html, link_map=_BLOG)
    assert "[own](https://blog.example/wiki/Scratch/" in out
    assert "[bare](#Linkrow)" in out


def test_in_page_anchor_in_passed_through_html_is_relative_too():
    html = (
        '<table><tr><td colspan="2">'
        f'<a href="{_PAGE_URL}#Linkrow">a</a> '
        f'<a href="{_ROOT}/wiki/WikiStart#Top">b</a>'
        "</td></tr></table>"
    )
    out = md(html, link_map=_BLOG, document_url=_PAGE_URL)
    assert out.startswith("<table>")
    assert 'href="#Linkrow"' in out
    assert 'href="https://blog.example/wiki/WikiStart#Top"' in out


def test_an_autolink_to_an_in_page_anchor_keeps_its_text():
    url = f"{_PAGE_URL}#Linkrow"
    out = md(_anchor(url, url), document_url=_PAGE_URL)
    assert out == f"[{url}](#Linkrow)"


def test_link_map_leaves_the_link_text_alone():
    # An autolink whose href is rewritten keeps its text: the original
    # URL is the label, the new URL the destination.
    html = '<a href="https://t.example.org/w/A">https://t.example.org/w/A</a>'
    out = md(
        html, link_map={"https://t.example.org/": "https://b.example/"}
    )
    assert out == "[https://t.example.org/w/A](https://b.example/w/A)"
    out = md(
        '<a href="https://t.example.org/w/A">the page</a>',
        link_map={"https://t.example.org/": "https://b.example/"},
    )
    assert out == "[the page](https://b.example/w/A)"


def test_link_map_default_changes_nothing():
    assert md('<a href="https://t/x">x</a>') == "[x](https://t/x)"


# --- tables of contents ----------------------------------------------

TOC = (
    '<div class="wiki-toc"><h4>Table of Contents</h4><ul>'
    '<li><a href="#A">A</a></li></ul></div>'
)


def test_toc_is_dropped_with_one_warning():
    r = conv(f"<p>x</p>{TOC}<p>y</p>")
    assert r.text == "x\n\ny"
    assert len(r.warnings) == 1
    assert "table of contents" in r.warnings[0].lower()


def test_toc_is_kept_on_request_without_a_warning():
    r = conv(f"<p>x</p>{TOC}", keep_toc=True)
    assert "- [A](#A)" in r.text
    assert r.warnings == []


def test_inline_toc_is_dropped_too():
    toc = '<div class="wiki-toc-un"><ol><li><a href="#A">A</a></li></ol></div>'
    r = conv(f"<p>x</p>{toc}")
    assert r.text == "x"
    assert len(r.warnings) == 1


def test_one_toc_warning_per_document():
    r = conv(TOC + "<p>x</p>" + TOC)
    assert len(r.warnings) == 1


def test_trac_guide_toc_with_stringified_false_classes_is_dropped():
    toc = (
        '<div class="wiki-toc"><h4>Contents</h4><ul>'
        '<li class="False"><a class="False" href="/w/A">A</a></li>'
        "</ul></div>"
    )
    r = conv(toc + "<p>x</p>")
    assert r.text == "x"


# --- tables -----------------------------------------------------------


def test_plain_table_is_a_pipe_table_with_header_row():
    html = (
        '<table class="wiki"><tr><th>a</th><th>b</th></tr>'
        "<tr><td>1</td><td>2</td></tr></table>"
    )
    out = md(html)
    assert out == "| a | b |\n| --- | --- |\n| 1 | 2 |"


def test_cell_text_align_is_dropped_without_a_warning():
    html = (
        '<table class="wiki"><tr><th style="text-align: right">a</th>'
        '<th style="text-align: left">b</th></tr><tr>'
        '<td style="text-align: center">1</td><td align="center">2</td>'
        "</tr></table>"
    )
    r = conv(html)
    assert r.text == "| a | b |\n| --- | --- |\n| 1 | 2 |"
    assert r.warnings == []


def test_pipe_in_a_cell_is_escaped():
    html = "<table><tr><th>a</th></tr><tr><td><code>x|y</code></td></tr></table>"
    assert "x\\|y" in md(html)


@pytest.mark.parametrize("attr", ['colspan="2"', 'rowspan="2"'])
def test_table_with_spans_passes_through_as_html(attr):
    html = (
        '<table class="wiki"><tr><th>a</th><th>b</th></tr>'
        f"<tr><td {attr}>x <em>y</em></td></tr></table>"
    )
    r = conv(html)
    assert r.text.startswith("<table>")  # Trac's wiki class is gone
    assert f"<td {attr}>x <em>y</em></td>" in r.text
    assert r.text.endswith("</table>")
    assert "\n\n" not in r.text
    assert len(r.warnings) == 1
    assert "table" in r.warnings[0] and "span" in r.warnings[0]


def test_table_with_a_list_in_a_cell_passes_through_as_html():
    html = (
        "<table><tr><th>a</th></tr><tr><td><ul><li>x</li></ul></td>"
        "</tr></table>"
    )
    r = conv(html)
    assert r.text.startswith("<table")
    assert "<ul><li>x</li></ul>" in r.text.replace("\n", "")
    assert len(r.warnings) == 1


def test_passed_through_table_carries_its_code_as_plain_pre():
    html = (
        '<table><tr><td colspan="2">'
        + code('<span class="k">x</span> = 1\n\ny\n')
        + "</td></tr></table>"
    )
    r = conv(html, "{{{#!python\nx = 1\n\ny\n}}}")
    assert "<span" not in r.text
    assert "\n\n" not in r.text
    assert "x = 1" in r.text


def test_passed_through_table_links_are_mapped_and_icons_dropped():
    html = (
        '<table><tr><td colspan="2"><a class="ext-link" '
        'href="https://t.example.org/a"><span class="icon">​'
        "</span>t</a></td></tr></table>"
    )
    r = conv(html, link_map={"https://t.example.org/": "/"})
    assert 'href="/a"' in r.text
    assert "​" not in r.text and "icon" not in r.text


# --- sup / sub / underline / del / small -------------------------------


def test_sup_and_sub_are_html():
    assert md("<p>x<sup>2</sup> y<sub>i</sub></p>") == (
        "x<sup>2</sup> y<sub>i</sub>"
    )


def test_underline_span_is_html_u():
    assert md('<p>a <span class="underline">b</span> c</p>') == (
        "a <u>b</u> c"
    )


def test_del_is_strikethrough_and_small_is_plain():
    assert md("<p><del>a</del> <small>b</small></p>") == "~~a~~ b"


# --- definition lists --------------------------------------------------

DL = (
    '<dl class="wiki"><dt>term</dt><dd>the definition</dd>'
    "<dt>two</dt><dd>second</dd></dl>"
)


def test_deflist_extra_is_term_then_colon_definition():
    out = md(DL)
    assert out == "term\n:   the definition\n\ntwo\n:   second"


def test_deflist_bold_is_a_bold_term_fallback():
    out = md(DL, deflist="bold")
    assert out == "**term**\n\nthe definition\n\n**two**\n\nsecond"


# --- lettered and roman lists --------------------------------------------


@pytest.mark.parametrize(
    "cls", ["loweralpha", "upperalpha", "lowerroman", "upperroman"]
)
def test_lettered_and_roman_lists_become_numbered_with_a_warning(cls):
    r = conv(f'<ol class="{cls}"><li>a</li><li>b</li></ol>')
    assert r.text == "1. a\n2. b"
    assert len(r.warnings) == 1
    assert cls in r.warnings[0]


def test_ol_start_is_kept():
    assert md('<ol start="3"><li>a</li><li>b</li></ol>') == "3. a\n4. b"


# --- everything else ------------------------------------------------------


def test_unknown_inline_element_passes_through_with_a_warning_naming_it():
    r = conv('<p>a <span style="color: red">b</span> c</p>')
    assert r.text == 'a <span style="color: red">b</span> c'
    assert len(r.warnings) == 1
    assert "span" in r.warnings[0]


def test_unknown_block_element_passes_through_as_its_own_html():
    r = conv('<div class="important"><p>note <em>x</em></p></div>')
    assert r.text == (
        '<div class="important"><p>note <em>x</em></p></div>'
    )
    assert len(r.warnings) == 1
    assert "div.important" in r.warnings[0]


def test_form_and_input_pass_through_with_warnings():
    r = conv('<form action="/s"><input name="q" type="text"/></form>')
    assert "<form" in r.text and "<input" in r.text
    assert any("form" in w for w in r.warnings)
    assert any("input" in w for w in r.warnings)


def test_plain_div_is_transparent():
    assert md("<div><p>a</p><p>b</p></div>") == "a\n\nb"


# --- macro output ---------------------------------------------------------


def test_macro_output_is_translated_with_one_warning_naming_it():
    html = (
        '<div class="trac-subscriberlist"><table class="wiki">'
        "<thead><tr><th>S</th><th>D</th></tr></thead>"
        '<tbody><tr class="even"><td><code>A</code></td><td>x</td>'
        "</tr></tbody></table></div>"
    )
    r = conv(html + html)
    assert "| `A` | x |" in r.text
    assert len(r.warnings) == 1
    assert "SubscriberList" in r.warnings[0]
    assert "exported as rendered, live content" in r.warnings[0]


def test_ticket_query_chrome_is_dropped():
    html = (
        '<div><h2 class="report-result">Results '
        '<span class="numresults">(1 - 3 of 9)</span></h2>'
        '<div class="paging"><span><span class="current">1</span>'
        '</span><span><a href="/q?page=2">2</a></span></div>'
        '<table class="listing tickets"><caption class="trac-group">'
        '</caption><thead class="trac-query-heading"><tr>'
        '<th class="id"><a href="/q?o=id">Ticket</a></th></tr></thead>'
        '<tbody class="trac-query-results"><tr class="odd prio3">'
        '<td class="id"><a class="new" href="/t/1">#1</a></td></tr>'
        '</tbody></table><div class="trac-groupprogress"><table>'
        "</table></div></div>"
    )
    r = conv(html)
    assert "page=2" not in r.text
    assert "| [#1](/t/1) |" in r.text
    assert len([w for w in r.warnings if "TicketQuery" in w]) == 1


def test_system_message_is_dropped_and_its_text_quoted():
    r = conv(
        '<div class="system-message">Enable JavaScript to see it.</div>'
    )
    assert r.text == ""
    assert len(r.warnings) == 1
    assert "Enable JavaScript to see it." in r.warnings[0]


def test_workflow_graph_is_dropped_with_a_warning():
    html = (
        '<div class="trac-workflow-graph trac-noscript" id="g" '
        'style="display:inline-block;width:700px"></div>'
        "<noscript><p>graph fallback</p></noscript>"
    )
    r = conv(html)
    assert r.text == ""
    assert any("workflow" in w.lower() for w in r.warnings)


# --- images ---------------------------------------------------------------


def test_img_is_markdown_with_src_unmapped():
    html = (
        '<p><a href="https://t.example.org/raw/f.png"><img alt="shot" '
        'src="https://t.example.org/raw/f.png" crossorigin="anonymous"/>'
        "</a></p>"
    )
    out = md(html, link_map={"https://t.example.org/": "/"})
    # The enclosing link is a link: mapped. The image is #215's: not.
    assert (
        out == "[![shot](https://t.example.org/raw/f.png)](/raw/f.png)"
    )


# --- anchors, headings, quotes ----------------------------------------------


def test_heading_ids_and_wikianchors_are_not_emitted():
    html = (
        '<h2 class="section" id="Title">Title</h2>'
        '<p><span class="wikianchor" id="here"></span>text</p>'
    )
    assert md(html) == "## Title\n\ntext"


def test_headings_use_atx_at_every_level():
    assert (
        md("<h1>a</h1><h2>b</h2><h4>c</h4>") == "# a\n\n## b\n\n#### c"
    )


def test_citation_and_plain_blockquote_both_become_quotes():
    html = (
        '<blockquote class="citation"><p>a</p></blockquote>'
        "<blockquote><p>b</p></blockquote>"
    )
    assert md(html) == "> a\n\n> b"


def test_nested_lists_keep_their_nesting():
    html = "<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>"
    assert md(html) == "- a\n  * b\n- c"


def test_line_break_in_a_paragraph_is_a_hard_break():
    assert md("<p>a<br/>b</p>") == "a\\\nb"


def test_line_break_at_the_end_of_a_block_is_dropped():
    # A trailing backslash would render as a literal backslash.
    assert md("<p>a<br/></p><p>b</p>") == "a\n\nb"


@pytest.mark.parametrize("tag", ["ul", "ol"])
def test_adjacent_lists_stay_two_lists(tag):
    html = f"<{tag}><li>a</li></{tag}><{tag}><li>b</li></{tag}><p>x</p>"
    out = md(html)
    assert "<!-- -->" in out
    one = "1. a\n1. b" if tag == "ol" else "- a\n- b"
    assert one not in out


def test_hr_is_a_thematic_break():
    assert md("<p>a</p><hr/><p>b</p>") == "a\n\n---\n\nb"


# --- escaping ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p>2 * 3 * 4</p>", "2 \\* 3 \\* 4"),
        ("<p>snake_case_name</p>", "snake_case_name"),
        ("<p>_lead and trail_</p>", "\\_lead and trail\\_"),
        ("<p>&lt;div&gt; and a &lt; b</p>", "\\<div> and a < b"),
        ("<p>1. not a list</p>", "1\\. not a list"),
        ("<p># not a heading</p>", "\\# not a heading"),
        ("<p>&gt; not a quote</p>", "\\> not a quote"),
        ("<p>- not a bullet</p>", "\\- not a bullet"),
        ("<p>back\\slash\\*</p>", "back\\slash\\\\\\*"),
        ("<p>a `tick` b</p>", "a \\`tick\\` b"),
    ],
)
def test_text_is_escaped_so_it_cannot_become_structure(html, expected):
    assert md(html) == expected


# --- review fixes (Trac #209 review) ------------------------------------
#
# Each test below is the reviewer's reproduction input.


def diff_html(*rows):
    body = "".join(
        f'<tr><th>1</th><td class="{side}"><span>{text}</span></td></tr>'
        for side, text in rows
    )
    return (
        '<div class="wiki-code"><div class="diff"><ul class="entries">'
        '<li class="entry"><table class="trac-diff inline"><tbody>'
        f"{body}</tbody></table></li></ul></div></div>"
    )


def test_equal_counts_with_disagreeing_text_do_not_pair_by_position():
    # [[TracIni]] emits a block the source has no opener for, while the
    # source's one block is not rendered as code at all.
    r = conv(code("[server]\nport = 1\n"), "{{{#!mermaid\ngraph\n}}}")
    assert r.text == "```\n[server]\nport = 1\n```"
    assert any("no matching source block" in w for w in r.warnings)


def test_equal_counts_with_one_disagreeing_pair_pair_by_text_throughout():
    html = code("[a]\n") + code("x = 1\n")
    src = "{{{#!python\nx = 1\n}}}\n\n{{{#!mermaid\ngraph\n}}}"
    r = conv(html, src)
    assert r.text == "```\n[a]\n```\n\n```python\nx = 1\n```"
    assert len(r.warnings) == 1
    assert "1 code block " in r.warnings[0]


def test_equal_counts_pair_by_position_when_text_agrees_modulo_space():
    r = conv(code("a   =   1\n"), "{{{#!python\na = 1\n}}}")
    assert r.text == "```python\na = 1\n```"
    assert r.warnings == []


def test_diff_agrees_with_its_source_without_the_plus_minus_columns():
    html = diff_html(("l", "old"), ("r", "new"), ("l", "same"))
    src = "{{{#!diff\n-old\n+new\n same\n}}}"
    r = conv(html, src)
    assert r.text == "```diff\n-old\n+new\n same\n```"
    assert r.warnings == []


def test_diff_header_lines_may_be_absent_from_the_html():
    html = diff_html(("l", "old"), ("r", "new"))
    src = "{{{#!diff\n--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n}}}"
    r = conv(html, src)
    assert r.text == (
        "```diff\n--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n```"
    )
    assert r.warnings == []


def test_diff_with_disagreeing_text_is_not_paired():
    html = diff_html(("l", "zzz"))
    r = conv(html, "{{{#!diff\n-old\n+new\n}}}")
    assert r.text == "```\nzzz\n```"
    assert any("no matching source block" in w for w in r.warnings)


def test_table_code_is_compared_on_its_td_cells_only():
    html = (
        '<div class="wiki-code"><table class="code"><tbody><tr>'
        "<th>1</th><td>zzz\n</td></tr></tbody></table></div>"
    )
    r = conv(html, "{{{#!python lineno=1\n1\nzzz\n}}}")
    assert r.text == "```\nzzz\n```"
    assert any("no matching source block" in w for w in r.warnings)


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<h1 class="foo">Title</h1>', '<h1 class="foo">Title</h1>'),
        (
            '<ul><li style="color:red">red</li></ul>',
            '<li style="color:red">red</li>',
        ),
        (
            '<p>a <code class="foo">x y</code> b</p>',
            'a <code class="foo">x y</code> b',
        ),
        (
            '<p>a <a href="/x" onclick="f()">link</a> b</p>',
            'a <a href="/x" onclick="f()">link</a> b',
        ),
        (
            '<div class="x"><p>n <em>e</em> <a href="/y">l</a></p></div>',
            '<div class="x"><p>n <em>e</em> <a href="/y">l</a></p></div>',
        ),
    ],
)
def test_passthrough_is_the_original_html_never_converted_markdown(
    html, expected
):
    r = conv(html)
    assert r.text == expected
    assert len(r.warnings) == 1


def test_passthrough_nested_uncovered_elements_still_warn():
    r = conv('<form action="/s"><input name="q"/></form>')
    assert any("form" in w for w in r.warnings)
    assert any("input" in w for w in r.warnings)


def test_passthrough_has_no_blank_line_even_across_dropped_nodes():
    html = (
        '<table><tr><td colspan="2">a\n<span class="wikianchor" '
        'id="x"></span>\nb</td></tr></table>'
    )
    r = conv(html)
    assert "\n\n" not in r.text
    assert "a\nb" in r.text


def test_passthrough_squashes_blank_lines_in_text():
    r = conv('<div class="x">a\n\n\n  \nb</div>')
    assert r.text == '<div class="x">a\nb</div>'


def test_blank_line_in_a_passed_through_pre_becomes_a_space_entity():
    r = conv('<div class="x"><pre>a\n\n\nb</pre></div>')
    assert "\n\n" not in r.text
    assert (
        r.text == '<div class="x"><pre>a\n&#32;\n&#32;\nb</pre></div>'
    )


def test_passed_through_pre_has_no_fence_inside_it():
    r = conv('<pre class="other">x = 1\n\ny</pre>')
    assert r.text == '<pre class="other">x = 1\n&#32;\ny</pre>'
    assert "```" not in r.text


@pytest.mark.parametrize(
    "attrs",
    ['style="border:none"', 'border="1"', 'cellspacing="0"'],
)
def test_table_own_attributes_are_checked_too(attrs):
    html = (
        f'<table class="wiki" {attrs}><tr><th>a</th></tr>'
        "<tr><td>1</td></tr></table>"
    )
    r = conv(html)
    assert r.text.startswith("<table")
    assert "|" not in r.text
    assert len(r.warnings) == 1
    assert "table" in r.warnings[0]


def test_table_class_wiki_alone_is_still_a_pipe_table():
    html = '<table class="wiki"><tr><th>a</th></tr><tr><td>1</td></tr></table>'
    r = conv(html)
    assert r.text == "| a |\n| --- |\n| 1 |"
    assert r.warnings == []


def test_link_map_does_not_touch_an_img_in_passed_through_html():
    r = conv(
        '<img src="/trac/raw/a.png" style="float:right">',
        link_map={"/trac/": "https://t/"},
    )
    assert r.text == '<img src="/trac/raw/a.png" style="float:right"/>'


def test_link_map_applies_to_an_href_in_passed_through_html():
    r = conv(
        '<table><tr><td colspan="2"><a href="/trac/w">l</a></td></tr></table>',
        link_map={"/trac/": "https://t/"},
    )
    assert r.text.startswith("<table>")  # raw HTML, not a pipe table
    assert 'href="https://t/w"' in r.text
    assert "/trac/" not in r.text


def test_link_map_in_a_passed_through_subtree_maps_hrefs_only():
    html = (
        '<div class="x"><a href="/trac/w">l</a>'
        '<a href="#top">t</a>'
        '<img src="/trac/raw/a.png" alt="a"/></div>'
    )
    out = md(html, link_map={"/trac/": "https://t/", "#": "/n#"})
    assert 'href="https://t/w"' in out
    assert 'href="#top"' in out
    assert 'src="/trac/raw/a.png"' in out


def test_backslash_before_a_newline_is_escaped_not_a_hard_break():
    assert md("<p>foo \\\nbar</p>") == "foo \\\\\nbar"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# term", "\\# term"),
        ("1. term", "1\\. term"),
        ("- term", "\\- term"),
        ("+ term", "\\+ term"),
        ("* term", "\\* term"),
        ("> term", "\\> term"),
    ],
)
def test_line_start_is_escaped_in_a_definition_term(text, expected):
    html = f'<dl class="wiki"><dt>{text}</dt><dd>d</dd></dl>'
    assert md(html) == f"{expected}\n:   d"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# x", "\\# x"),
        ("1. x", "1\\. x"),
        ("- x", "\\- x"),
        ("+ x", "\\+ x"),
        ("> x", "\\> x"),
    ],
)
def test_line_start_is_escaped_inside_an_inline_only_parent(
    text, expected
):
    assert md(f"<p><span>{text}</span></p>") == expected
    assert md(f"<p>a<br/><span>{text}</span></p>") == f"a\\\n{expected}"


def test_unbalanced_bracket_in_link_text_keeps_the_link():
    assert md('<a href="/wiki/X">a]b</a>') == "[a\\]b](/wiki/X)"


def test_every_bracket_in_text_is_escaped():
    assert md("<p>a [b c] d [e</p>") == "a \\[b c\\] d \\[e"


def test_brackets_in_code_are_left_alone():
    assert md("<p><code>a[0]</code></p>") == "`a[0]`"


def test_tilde_in_text_is_escaped_so_it_is_not_strikethrough():
    assert md("<p>~~x~~</p>") == "\\~\\~x\\~\\~"
    assert md("<p>a ~ b</p>") == "a \\~ b"


def test_tilde_in_code_is_left_alone():
    assert md("<p><code>~/x</code></p>") == "`~/x`"


def test_img_with_dimensions_passes_through_as_html_without_a_warning():
    r = conv(
        '<p><img src="/trac/a.png" alt="a" width="10" height="5"/></p>',
        link_map={"/trac/": "/t/"},
    )
    # the parser writes attributes in its own order
    assert r.text.startswith("<img ") and r.text.endswith("/>")
    assert BeautifulSoup(r.text, "html.parser").img.attrs == {
        "src": "/trac/a.png",
        "alt": "a",
        "width": "10",
        "height": "5",
    }
    assert r.warnings == []


def test_img_title_is_the_markdown_title():
    out = md('<p><img src="/a.png" alt="a" title="The A"/></p>')
    assert out == '![a](/a.png "The A")'


def test_img_title_quotes_and_backslashes_are_escaped():
    out = md('<p><img src="/a.png" alt="a" title=\'say "hi" \\\'/></p>')
    assert out == '![a](/a.png "say \\"hi\\" \\\\")'
