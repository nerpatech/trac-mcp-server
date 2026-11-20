"""Numeric changeset and log bracket shapes in prose (ticket #249).

**Measured on Trac's own renderer (this host, render only; Trac 1.6,
trac/versioncontrol/web_ui/changeset.py and log.py):** a bracket holding a
number renders as a changeset or log link, so Markdown prose such as
``x[0]`` or ``rows[12]`` becomes one (and the write gate refuses the push).
Rendered as links: ``x[0]``, ``rows[12]``, ``x[1:3]``, ``x[0,1]``,
``x[1-3]``, ``x[deadbeef]``, ``x[12/path]``, ``x[1:3/p]``, ``x[12?a]``,
``x[12#b]`` and ``x[trac 12]`` (a known InterTrac prefix).
Rendered as plain text (not a link): ``x[0, 1]``, ``x[1.5]``, ``x[-1]``,
``x[0:]``, ``x[:3]``, ``x[i+1]``, ``x[.5]``, ``x[n-1:n]``, ``x[cafe]`` (hex
shorter than 8) and, where Trac's regex matches but the link resolver gives
up, ``x[abc 12]``, ``x[ 12]``, ``x[abc12]`` (an unknown prefix).

Trac's ``!`` escape on every shape that matches the pattern renders plain
text with no visible bang (measured for all of the above, the unknown
prefixes and ``x![-1]``, ``x![i+1]``, ``x![.5]`` included), so the rule
escapes every match of the mirrored pattern. Inside a Trac bracket-link
label the escape is NOT consumed (``[http://x a ![0] b]`` renders the
label ``a ![0``) while the bare ``[http://x a [0] b]`` links nothing, so a
label is left alone, unlike the quoted bracket of #241.

**Write leg.** A mistune inline rule (registered before ``link``) turns a
match in prose into a ``numeric_bracket`` token, which the renderer writes
with the ``!``. Real links come first; a scheme run before the shape and a
link label are left as they were.
"""

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)

ESCAPED = {
    "index": ("x[0] y", "x![0] y"),
    "word and number": ("rows[12] y", "rows![12] y"),
    "colon range": ("x[1:3] y", "x![1:3] y"),
    "comma list": ("x[0,1] y", "x![0,1] y"),
    "dash range": ("x[1-3] y", "x![1-3] y"),
    "long hex": ("x[deadbeef] y", "x![deadbeef] y"),
    "number and path": ("x[12/path] y", "x![12/path] y"),
    "range and path": ("x[1:3/p] y", "x![1:3/p] y"),
    "number and query": ("x[12?a] y", "x![12?a] y"),
    "number and fragment": ("x[12#b] y", "x![12#b] y"),
    "known intertrac prefix": ("x[trac 12] y", "x![trac 12] y"),
    # Trac's regex matches these but its link resolver yields text; the
    # escape renders as plain text too, measured.
    "unknown prefix with a space": ("x[abc 12] y", "x![abc 12] y"),
    "leading space": ("x[ 12] y", "x![ 12] y"),
    "unknown prefix, no space": ("x[abc12] y", "x![abc12] y"),
    # These match Trac's pattern but resolve to text, so the escape is
    # harmless noise in the stored text. It is kept so that the regex stays
    # an exact mirror of Trac's (no exclusion rule to justify).
    "match that resolves to text: minus one": ("x[-1] y", "x![-1] y"),
    "match that resolves to text: i plus one": (
        "x[i+1] y",
        "x![i+1] y",
    ),
    "match that resolves to text: dot five": ("x[.5] y", "x![.5] y"),
    "chained": ("m[0][1]", "m![0]![1]"),
    # A `!` in Markdown is a literal character, and Trac reads `!` before a
    # bracket as its escape, so a typed one is not an escape.
    "a typed bang is a literal": ("Wow![0] ok", "Wow!![0] ok"),
    "list item": ("- a[0]", " * a![0]"),
    "table cell": (
        "| h |\n|---|\n| d[0] |",
        "||=h=||\n||d![0]||",
    ),
    "heading": ("# a[0]", "= a![0] ="),
}


@pytest.mark.parametrize(
    ("markdown", "wiki"),
    list(ESCAPED.values()),
    ids=list(ESCAPED),
)
def test_a_numeric_bracket_in_prose_gets_the_escape(markdown, wiki):
    assert markdown_to_tracwiki(markdown) == wiki


UNCHANGED = {
    "comma and space": "x[0, 1] y",
    "decimal": "x[1.5] y",
    "open range at the end": "x[0:] y",
    "open range at the start": "x[:3] y",
    "expression range": "x[n-1:n] y",
    "short hex": "x[cafe] y",
    "code span": "`x[0]`",
    "bare url": "http://e.com/a[0] z",
}


@pytest.mark.parametrize(
    "markdown", list(UNCHANGED.values()), ids=list(UNCHANGED)
)
def test_text_trac_does_not_link_is_unchanged(markdown):
    assert markdown_to_tracwiki(markdown) == markdown


def test_a_fenced_code_block_is_unchanged():
    assert markdown_to_tracwiki("```\nx[0]\n```") == "{{{\nx[0]\n}}}"


# Each expected value below is what master gave before ticket #249: a real
# Markdown link keeps mistune's conversion even when its label or reference
# is a number.
REAL_LINKS = {
    "inline link": ("[1](http://x)", "[http://x 1]"),
    "full reference link": (
        "[1][r]\n\n[r]: http://x",
        "[http://x 1]",
    ),
    "shortcut reference link": (
        "see [1].\n\n[1]: http://x",
        "see [http://x 1].",
    ),
    "image": ("![1](x.png)", "[[Image(x.png)]]"),
}


@pytest.mark.parametrize(
    ("markdown", "wiki"),
    list(REAL_LINKS.values()),
    ids=list(REAL_LINKS),
)
def test_a_real_link_keeps_its_conversion(markdown, wiki):
    assert markdown_to_tracwiki(markdown) == wiki


def test_a_link_label_holding_a_numeric_bracket_gets_no_escape():
    """Pinned to master: the escape would show as a visible `!` inside a
    Trac bracket-link label, and the bare form links nothing."""
    assert markdown_to_tracwiki("[a [0] b](http://x)") == (
        "[http://x a [0] b]"
    )
