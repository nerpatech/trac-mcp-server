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

import time

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    _DEFERRED_LINK_CLOSE,
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


# -- Second review: real links first, Trac's label grammar, scheme runs ----------

# A quoted bracket followed by `(` that is not a Markdown link is still a
# Trac wiki link (measured: `handlers['get'](req, resp)` renders a link
# `get`; `handlers!['get'](req, resp)` renders plain text), so it needs the
# escape. A real inline link keeps mistune's conversion.
LINKS_FIRST = {
    "call on a subscript": (
        "handlers['get'](req, resp)",
        "handlers!['get'](req, resp)",
    ),
    "call with a space": ("f['k'](a b)", "f!['k'](a b)"),
    "real inline link": ("['T'](https://e.com)", "[https://e.com 'T']"),
    # Pinned to the converter before #241 (34eb0a9^1): a real link directly
    # after a `word:` is converted, the scheme-run check applies only to
    # the escape.
    "real inline link after a colon": (
        "Note:['a'](http://x)",
        "Note:[http://x 'a']",
    ),
}


@pytest.mark.parametrize(
    ("markdown", "wiki"),
    list(LINKS_FIRST.values()),
    ids=list(LINKS_FIRST),
)
def test_a_real_link_wins_and_anything_else_gets_the_escape(
    markdown, wiki
):
    assert markdown_to_tracwiki(markdown) == wiki


# Trac's label grammar, measured: whitespace other than a newline (NBSP and
# U+3000 included), then at least one character that is not `]`. The match
# backtracks as Trac does: `x['a' ] y` is plain text, `x['a'  ] y` (two
# spaces) is a link with an empty label, `x!['a' lbl] y` is plain text.
LABELS = {
    "one space, empty label: not a link": ("x['a' ] y", "x['a' ] y"),
    "two spaces: a link with an empty label": (
        "x['a'  ] y",
        "x!['a'  ] y",
    ),
    "plain label": ("x['a' lbl] y", "x!['a' lbl] y"),
    "no-break space": ("x['a' lbl] y", "x!['a' lbl] y"),
    "ideographic space": ("x['a'　lbl] y", "x!['a'　lbl] y"),
}


@pytest.mark.parametrize(
    ("markdown", "wiki"),
    list(LABELS.values()),
    ids=list(LABELS),
)
def test_the_label_follows_tracs_grammar(markdown, wiki):
    assert markdown_to_tracwiki(markdown) == wiki


# Trac never forms a bracket link after a scheme run (measured:
# `https://e.com/a['b']` links `https://e.com/a['b`; `HTTPS://E.COM/['a'] y`
# and `ftps://h/p['a'] y` render as plain text), so an escape there only
# adds a visible `!`. A link or autolink that closes before the shape is
# not a run, and neither is a line break.
SCHEME_RUNS = {
    "upper-case scheme": (
        "HTTPS://E.COM/['a'] y",
        "HTTPS://E.COM/['a'] y",
    ),
    "other scheme": ("ftps://h/p['a'] y", "ftps://h/p['a'] y"),
    "http": (
        "see https://e.com/a['b'] now",
        "see https://e.com/a['b'] now",
    ),
    "after a Markdown link": (
        "[l](http://e.com)['a']",
        "[http://e.com l]!['a']",
    ),
    "after an autolink": (
        "<http://e.com>['a']",
        "[http://e.com http://e.com]!['a']",
    ),
    "url ends the line, shape starts the next": (
        "https://e.com/\n['a']",
        "https://e.com/\n!['a']",
    ),
}


@pytest.mark.parametrize(
    ("markdown", "wiki"),
    list(SCHEME_RUNS.values()),
    ids=list(SCHEME_RUNS),
)
def test_a_scheme_run_before_the_shape_skips_the_escape(markdown, wiki):
    assert markdown_to_tracwiki(markdown) == wiki


# -- mistune 3.4 resolves a link at its `]` (ticket #263) ----------------------
#
# Under mistune 3.2/3.3 `parse_link` answered at the `[`; 3.4 pushes an
# opener and decides at the `]`. These rows pin the shapes where the two
# differ. Each expected value is what the 3.2 code gave, and both versions
# must produce it.
DEFERRED_CLOSE = {
    "inside a real link label": (
        "[x['a'] z](http://e.com)",
        "[http://e.com x!['a'] z]",
    ),
    # The shape inside the label holds a nested bracket, so the label of
    # the outer link is not a plain run of text.
    "nested brackets in a real link label": (
        "['a' [b]](http://e.com)",
        "[http://e.com 'a' [b]]",
    ),
    "a real image after a bang": (
        "![a['k']](http://e.com/i.png)",
        "[[Image(http://e.com/i.png)]]",
    ),
    # `(` follows the shape but starts no destination.
    "a paren that is not a destination": (
        "x['a'](not a dest) y",
        "x!['a'](not a dest) y",
    ),
    "an open paren at the end": ("x['a'](  y", "x!['a'](  y"),
    # A reference definition exists, but not for this shape.
    "a definition that does not match": (
        "see ['a'] y\n\n[b]: http://x",
        "see !['a'] y",
    ),
    "a shape in a bracketed run, no link": (
        "[a ['k'] b]",
        "[a !['k'] b]",
    ),
}


@pytest.mark.parametrize(
    ("markdown", "wiki"),
    list(DEFERRED_CLOSE.values()),
    ids=list(DEFERRED_CLOSE),
)
def test_a_quoted_bracket_where_mistune_decides_at_the_close(
    markdown, wiki
):
    assert markdown_to_tracwiki(markdown) == wiki


def test_an_outer_opener_that_an_inner_link_deactivates_keeps_its_escape():
    """The safety net. `['a' [b](http://u)](http://v)` reads as a link at
    the outer `[` (a `(` destination follows the matching `]`), so the
    rule lets mistune take the opener. mistune 3.4 then forms the inner
    link `[b](http://u)` first and, per CommonMark, deactivates the outer
    opener, so the outer `[` survives as text and Trac would read
    `['a' ...]` as a wiki link: the surviving opener must carry the
    escape. mistune 3.2/3.3 give the outer link the label as raw text, so
    there is no surviving opener and nothing to escape."""
    md = "['a' [b](http://u)](http://v)"
    if _DEFERRED_LINK_CLOSE:
        assert markdown_to_tracwiki(md) == (
            "!['a' [http://u b]](http://v)"
        )
    else:
        assert markdown_to_tracwiki(md) == (
            "[http://v 'a' [b](http://u)]"
        )


# mistune 3.4 leaves the emphasis delimiter in the source that the scheme-run
# scan reads; `x:` is a scheme run all the same (3.2 parses the emphasis
# content as its own source).
def test_a_scheme_run_inside_emphasis_skips_the_escape():
    assert markdown_to_tracwiki("*x:['a']*") == "''x:['a']''"
    assert markdown_to_tracwiki("_x:['a']_") == "''x:['a']''"


# An emphasis delimiter ends the token the scheme-run check reads, as 3.2's
# separate emphasis source does: the token before the shape is the text
# after the delimiter, and empty when the shape follows it directly.
EMPHASIS_BOUNDARY = {
    "shape right after the opening delimiter": (
        "x:*['a']*",
        "x:''!['a']''",
    ),
    "scheme run inside a bracketed label": (
        "[*x:['a']*](http://v)",
        "[http://v ''x:['a']'']",
    ),
    "word before the opening delimiter": (
        "a*x:['a']*",
        "a''x:['a']''",
    ),
    # An intraword `_` is no delimiter (CommonMark), so it ends nothing: the
    # whole token is read, and `path/to_x:` and `1_x:` are no scheme runs.
    "intraword underscore after a path": (
        "path/to_x:['a']",
        "path/to_x:!['a']",
    ),
    "intraword underscore after a digit": (
        "1_x:['a']",
        "1_x:!['a']",
    ),
    "intraword underscore in a scheme run": (
        "snake_x:['a']",
        "snake_x:['a']",
    ),
}


@pytest.mark.parametrize(
    ("markdown", "wiki"),
    list(EMPHASIS_BOUNDARY.values()),
    ids=list(EMPHASIS_BOUNDARY),
)
def test_an_emphasis_delimiter_ends_the_scheme_run_token(
    markdown, wiki
):
    assert markdown_to_tracwiki(markdown) == wiki


def test_a_quoted_bracket_beside_an_inner_link_keeps_its_escape():
    """The shape is escaped inside a label either way; on 3.4 the inner
    link deactivates the outer opener, which carries no escape of its own
    here (it is not a shape)."""
    md = "[x['a'] [b](http://u)](http://v)"
    if _DEFERRED_LINK_CLOSE:
        assert markdown_to_tracwiki(md) == (
            "[x!['a'] [http://u b]](http://v)"
        )
    else:
        assert markdown_to_tracwiki(md) == (
            "[http://v x!['a'] [b](http://u)]"
        )


# Unclosed openers make `[`-matching quadratic if every shape rescans to the
# end of the source (1.3 s at n=100 on mistune 3.4, 8x per doubling).
@pytest.mark.skipif(
    not _DEFERRED_LINK_CLOSE,
    reason="mistune 3.2/3.3 rescan to the end of the source per `[` in "
    "their own link parser (5 s here at n=2000, unchanged from before "
    "#263); the bound is for the 3.4 path's bracket map",
)
@pytest.mark.parametrize(
    "markdown",
    [
        "[" * 2000 + '["a"] ' * 2000,
        "['a' [b] " * 2000,
    ],
    ids=["open run, then shapes", "unclosed nested label"],
)
def test_many_open_brackets_and_shapes_convert_in_linear_time(markdown):
    start = time.perf_counter()
    markdown_to_tracwiki(markdown)
    # 10 s, not tighter: the bracket map takes about 0.05 s at n=2000 and the
    # per-shape rescan about 100 s or more, so the bound keeps a margin of
    # 200x either way and cannot flake on a loaded runner.
    assert time.perf_counter() - start < 10.0


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

    # A call on a subscript: Trac reads `handlers['get'](req, resp)` as a
    # link `get`, so it is escaped; an NBSP label is a label.
    call = client.wiki_to_html(
        markdown_to_tracwiki(
            "handlers['get'](req, resp) and x['a' b] y"
        )
    )
    assert "<a " not in call, call
    assert "handlers['get'](req, resp)" in call, call
