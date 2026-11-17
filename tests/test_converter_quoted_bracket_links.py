"""Quoted-target bracket links in prose (ticket #241).

**Measured on Trac's own renderer (this host, render only):** a quoted
target in single brackets is a wiki link: ``app.config['SERVER_NAME'] x``,
``d["key"] x``, ``x['a']['b']`` (two links), ``['a b'] x``,
``["wiki:Foo"] x`` and the label form ``x['KEY' label]`` all render a
link to a wiki page (the write gate then refuses the push). Trac's ``!``
escape keeps the text: ``app.config!['SERVER_NAME'] x`` renders
``app.config['SERVER_NAME'] x``. ``y['k'`` (no closing bracket) is not a
link.

**Write leg.** A mistune inline rule (registered before ``link``) turns a
quoted-bracket span in prose into a ``quoted_bracket`` token, which the
renderer writes with the ``!``. Anywhere mistune does not see prose (a
code span or block, a link destination or title, an autolink, a reference
definition) the rule never fires.
"""

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)

ESCAPED = {
    "attribute subscript": (
        "app.config['SERVER_NAME'] x",
        "app.config!['SERVER_NAME'] x",
    ),
    "double quotes": ('d["key"] x', 'd!["key"] x'),
    "chained subscripts": ("x['a']['b']", "x!['a']!['b']"),
    "target with a space": ("['a b'] x", "!['a b'] x"),
    "label form": ("x['KEY' label]", "x!['KEY' label]"),
    # A `!` in Markdown is a literal character, and Trac reads `!` before a
    # bracket as its escape, so a typed one is not an escape: measured,
    # `Wow!!['a'] ok` renders `Wow!['a'] ok`, `Wow!['a'] ok` renders
    # `Wow['a'] ok`.
    "a typed bang is a literal": ("Wow!['a'] ok", "Wow!!['a'] ok"),
    "a typed bang before a key": ("x!['k']", "x!!['k']"),
    "list item": ("- a['k']", " * a!['k']"),
    "table cell": (
        '| h |\n|---|\n| d["k"] |',
        '||=h=||\n||d!["k"]||',
    ),
    "heading": ("# cfg['k']", "= cfg!['k'] ="),
}


@pytest.mark.parametrize(
    ("markdown", "wiki"),
    list(ESCAPED.values()),
    ids=list(ESCAPED),
)
def test_a_quoted_bracket_in_prose_gets_the_escape(markdown, wiki):
    assert markdown_to_tracwiki(markdown) == wiki


UNCHANGED = {
    "no closing bracket": "y['k'",
    "index": "x[0]",
    "name index": "x[i]",
    "apostrophe and bracket": "it's ['",
    "code span": "`x['k']`",
}


@pytest.mark.parametrize(
    "markdown", list(UNCHANGED.values()), ids=list(UNCHANGED)
)
def test_text_that_is_not_a_link_is_unchanged(markdown):
    assert markdown_to_tracwiki(markdown) == markdown


def test_a_fenced_code_block_is_unchanged():
    assert markdown_to_tracwiki("```python\nx['k']\n```") == (
        "{{{#!python\nx['k']\n}}}"
    )


def test_a_markdown_inline_link_with_a_quoted_label_is_unchanged():
    """`(?!\\()` leaves a real Markdown link to mistune."""
    assert markdown_to_tracwiki('["T"](https://e.com)') == (
        '[https://e.com "T"]'
    )


# Each input below holds a `['k']` shape that is NOT prose, and each is
# pinned to what master gave before ticket #241: the change must not touch
# them. A raw-source stash of the shape corrupted all of them (a percent-
# encoded NUL in a destination, a link lost to a title or autolink, a label
# reference that no longer resolved, a printed backslash); the inline rule
# only fires where mistune sees prose.
MASTER_SAME = {
    "link destination": (
        "[docs](http://x/?a=['b'])",
        "[http://x/?a=%5B%27b%27%5D docs]",
    ),
    "image destination": (
        "![a](http://x/['b'].png)",
        "[[Image(http://x/%5B%27b%27%5D.png)]]",
    ),
    "reference definition": (
        "See [t].\n\n[t]: http://x/?q=['a']",
        "See [http://x/?q=%5B%27a%27%5D t].",
    ),
    "autolink": (
        "<http://x/['a']>",
        "[http://x/%5B%27a%27%5D http://x/['a']]",
    ),
    "bare url": (
        "see https://e.com/a['b'] now",
        "see https://e.com/a['b'] now",
    ),
    "full reference link with a quoted label": (
        '["T"][id]\n\n[id]: http://x',
        '[http://x "T"]',
    ),
    "shortcut reference link with a quoted label": (
        'see ["a"] y\n\n["a"]: http://x',
        'see [http://x "a"] y',
    ),
    "collapsed reference link with a quoted label": (
        '["T"][] y\n\n["t"]: http://x',
        '[http://x "T"] y',
    ),
    "link title": ("[t](http://x \"d['k']\")", "[http://x t]"),
    "reference link with a quoted label": (
        'Use [text]["ref"]\n\n["ref"]: http://example.com',
        "Use [http://example.com text]",
    ),
    "backslash-escaped bracket": ("x\\['k']", "x['k']"),
    "code span after an escaped backtick": ("\\`['k']`", "`['k']`"),
}


@pytest.mark.parametrize(
    ("markdown", "wiki"),
    list(MASTER_SAME.values()),
    ids=list(MASTER_SAME),
)
def test_a_quoted_bracket_shape_outside_prose_is_untouched(
    markdown, wiki
):
    assert markdown_to_tracwiki(markdown) == wiki


def test_a_table_header_with_a_quoted_bracket_and_a_pipe():
    """`| x['a' | b] | c |` has three header cells against a two-column
    delimiter row, so it is not a table; it is a paragraph. Pinned."""
    md = "| x['a' | b] | c |\n|---|---|\n| d | e |"
    assert markdown_to_tracwiki(md) == (
        "| x!['a' | b] | c |\n|---|---|\n| d | e |"
    )


def test_a_table_whose_cells_are_split_around_a_quoted_bracket():
    """The block parser splits cells on `|` before any inline rule runs,
    so a quoted bracket cut by a pipe is not a match and the cells are
    intact."""
    md = "| x['a' | b] | c |\n|---|---|---|\n| d | e | f |"
    assert markdown_to_tracwiki(md) == (
        "||=x['a'=||=b]=||=c=||\n||d||e||f||"
    )


def test_a_link_label_holding_a_quoted_bracket():
    """Pinned: the label's shape gets the escape (master wrote it bare:
    `[http://x d["k"] docs]`; Trac's label already ends at the inner `]`
    there). No special handling."""
    assert markdown_to_tracwiki('[d["k"] docs](http://x)') == (
        '[http://x d!["k"] docs]'
    )


# -- Live: the real renderer on /trac_test --------------------------------------


@pytest.mark.live
def test_live_quoted_brackets_render_as_text_not_wiki_links():
    """Ticket #241 acceptance, against Trac's own renderer on /trac_test:
    no missing-page link, and the subscript text is shown. Read-only
    (wiki.wikiToHtml writes nothing)."""
    from tests.live_helpers import TEST_INSTANCE, trac_test_registry
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient

    config, _ = bootstrap_config()
    client = TracClient(
        trac_test_registry(config).resolve(TEST_INSTANCE)
    )

    html = client.wiki_to_html(
        markdown_to_tracwiki(
            "app.config['SERVER_NAME'] and d[\"key\"] and x['a']['b']"
        )
    )

    assert 'class="missing wiki"' not in html, html
    assert "<a " not in html, html
    assert "app.config['SERVER_NAME']" in html, html
    assert "x['a']['b']" in html, html

    # A typed `!` is literal text: `Wow!['a']` is written `Wow!!['a']`,
    # which Trac renders as `Wow!['a']`.
    bang = client.wiki_to_html(markdown_to_tracwiki("Wow!['a'] ok"))
    assert "<a " not in bang, bang
    assert "Wow!['a'] ok" in bang, bang
