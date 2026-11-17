"""Quoted-target bracket links in prose (ticket #241).

**Measured on Trac's own renderer (this host, render only):** a quoted
target in single brackets is a wiki link: ``app.config['SERVER_NAME'] x``,
``d["key"] x``, ``x['a']['b']`` (two links), ``['a b'] x``,
``["wiki:Foo"] x`` and the label form ``x['KEY' label]`` all render a
link to a wiki page (the write gate then refuses the push). Trac's ``!``
escape keeps the text: ``app.config!['SERVER_NAME'] x`` renders
``app.config['SERVER_NAME'] x``. ``y['k'`` (no closing bracket) is not a
link.

**Write leg.** A quoted-bracket span is stashed before mistune parses, so
it stays whole; where it reaches ``text()`` it is prose and gets the ``!``.
In a code span or a code block the stored text is restored as typed.
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
    "already escaped, no double bang": ("x!['k']", "x!['k']"),
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
