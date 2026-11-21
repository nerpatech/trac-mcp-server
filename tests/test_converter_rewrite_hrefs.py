"""``rewrite_hrefs``: the HTML export's link rewriting (Trac #219).

The input comes back with ONLY the values of ``href`` attributes changed;
every other byte is the input's own. The URL decisions are the Markdown
export's (``LinkTargets``): an in-page anchor is ``#frag``, else the
longest matching ``link_map`` prefix is replaced, and ``src`` is never
mapped.
"""

import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import pytest

from trac_mcp_server.converters.link_targets import (
    LinkTargets,
    _StartTags,
    rewrite_hrefs,
)

_DOC = "http://trac.example/t/wiki/Page"
_MAP = {"http://trac.example/t/wiki/": "https://blog.example/"}
_FIXTURES = Path(__file__).parent / "fixtures" / "trac_html"


def _rw(html, link_map=_MAP, document_url=_DOC):
    return rewrite_hrefs(html, link_map, document_url)


# --- LinkTargets: the URL decisions ----------------------------------------


def test_link_targets_map_decides_like_the_markdown_export():
    targets = LinkTargets(
        {"http://a/": "/x/", "http://a/b/": "/y/"}, "http://a/wiki/P"
    )
    assert targets.map("http://a/wiki/P#Sec") == "#Sec"
    assert targets.map("http://a/b/c") == "/y/c"
    assert targets.map("http://a/c") == "/x/c"
    assert targets.map("http://z/c") == "http://z/c"
    assert targets.map("") == ""
    assert targets.map("#frag") == "#frag"


# --- the rewrite -------------------------------------------------------------


def test_an_in_page_anchor_becomes_a_bare_fragment():
    html = f'<p><a class="wiki" href="{_DOC}#Sec">S</a></p>\n'
    assert _rw(html) == '<p><a class="wiki" href="#Sec">S</a></p>\n'


def test_an_in_page_anchor_needs_no_map():
    html = f'<a href="{_DOC}#Sec">S</a>'
    assert rewrite_hrefs(html, None, _DOC) == '<a href="#Sec">S</a>'
    assert rewrite_hrefs(html, {}, _DOC) == '<a href="#Sec">S</a>'


def test_a_mapped_prefix_is_replaced():
    html = '<a href="http://trac.example/t/wiki/WikiStart">s</a>'
    assert _rw(html) == '<a href="https://blog.example/WikiStart">s</a>'


def test_the_longest_prefix_wins_whatever_the_maps_order():
    link_map = {
        "http://trac.example/": "/short/",
        "http://trac.example/t/wiki/": "/long/",
    }
    html = '<a href="http://trac.example/t/wiki/X">x</a>'
    assert _rw(html, link_map) == '<a href="/long/X">x</a>'


def test_an_in_page_anchor_is_never_mapped():
    link_map = {"http://trac.example/t/wiki/Page": "/gone/"}
    html = f'<a href="{_DOC}#Sec">s</a>'
    assert _rw(html, link_map) == '<a href="#Sec">s</a>'


def test_an_unmapped_link_is_byte_identical():
    html = (
        '<a  class="x"\n   href = "http://other/a?b=1&amp;c=2" >o</a>'
    )
    assert _rw(html) == html


def test_an_empty_href_is_left_alone():
    html = "<a href=\"\">e</a> <a href=''>f</a>"
    assert _rw(html, {"": "x"}) == html


def test_a_tag_with_no_href_is_left_alone():
    html = '<a name="top">t</a><a>n</a><img src="http://trac.example/t/wiki/i.png">'
    assert _rw(html) == html


def test_data_href_is_not_an_href():
    html = '<a data-href="http://trac.example/t/wiki/X" id="a">x</a>'
    assert _rw(html) == html


def test_data_href_next_to_a_real_href_leaves_only_the_real_one():
    html = (
        '<a data-href="http://trac.example/t/wiki/X" '
        'href="http://trac.example/t/wiki/Y">x</a>'
    )
    assert _rw(html) == (
        '<a data-href="http://trac.example/t/wiki/X" '
        'href="https://blog.example/Y">x</a>'
    )


def test_href_text_inside_another_attributes_value_is_not_an_href():
    html = (
        "<a title='see href=\"http://trac.example/t/wiki/X\" here' "
        'href="http://trac.example/t/wiki/Y">x</a>'
    )
    assert _rw(html) == (
        "<a title='see href=\"http://trac.example/t/wiki/X\" here' "
        'href="https://blog.example/Y">x</a>'
    )


def test_href_text_in_a_value_with_no_real_href_is_untouched():
    html = (
        "<span title='href=\"http://trac.example/t/wiki/X\"'>x</span>"
    )
    assert _rw(html) == html


def test_a_single_quoted_value_keeps_its_quote():
    html = "<a href='http://trac.example/t/wiki/X'>x</a>"
    assert _rw(html) == "<a href='https://blog.example/X'>x</a>"


def test_an_unquoted_value_is_written_double_quoted():
    html = "<a href=http://trac.example/t/wiki/X class=w>x</a>"
    assert _rw(html) == '<a href="https://blog.example/X" class=w>x</a>'


def test_an_unquoted_in_page_anchor():
    html = f"<a href={_DOC}#Sec>x</a>"
    assert _rw(html) == '<a href="#Sec">x</a>'


def test_the_attribute_name_is_matched_ignoring_case():
    html = '<A HREF="http://trac.example/t/wiki/X">x</A>'
    assert _rw(html) == '<A HREF="https://blog.example/X">x</A>'


def test_an_ampersand_in_an_unmapped_url_is_preserved_as_it_is():
    html = '<a href="http://other/q?a=1&amp;b=2&c=3">q</a>'
    assert _rw(html) == html


def test_an_ampersand_in_a_mapped_url_is_escaped_again():
    html = '<a href="http://trac.example/t/wiki/Q?a=1&amp;b=2">q</a>'
    assert _rw(html) == (
        '<a href="https://blog.example/Q?a=1&amp;b=2">q</a>'
    )


def test_a_mapped_replacement_with_a_quote_is_escaped_for_its_quote():
    link_map = {"http://trac.example/": 'x"y/'}
    html = '<a href="http://trac.example/z">q</a>'
    assert _rw(html, link_map) == '<a href="x&quot;y/z">q</a>'


def test_the_value_is_unescaped_before_it_is_mapped():
    # "&#x2F;" is "/": the prefix matches the decoded value.
    html = '<a href="http://trac.example&#x2F;t/wiki/X">x</a>'
    assert _rw(html) == '<a href="https://blog.example/X">x</a>'


def test_an_in_page_anchor_with_an_escaped_fragment_keeps_its_meaning():
    html = f'<a href="{_DOC}#a&amp;b">x</a>'
    assert _rw(html) == '<a href="#a&amp;b">x</a>'


def test_every_element_carrying_an_href_is_rewritten():
    html = (
        '<link href="http://trac.example/t/wiki/Css" rel="x">'
        '<area href="http://trac.example/t/wiki/Area">'
    )
    assert _rw(html) == (
        '<link href="https://blog.example/Css" rel="x">'
        '<area href="https://blog.example/Area">'
    )


def test_a_self_closing_tag_is_rewritten():
    html = '<a href="http://trac.example/t/wiki/X"/>'
    assert _rw(html) == '<a href="https://blog.example/X"/>'


def test_src_is_never_mapped():
    html = (
        '<img alt="i" src="http://trac.example/t/wiki/i.png">'
        '<img src="http://trac.example/t/wiki/Page#frag">'
    )
    assert _rw(html) == html


def test_an_img_inside_a_link_keeps_its_src_while_the_href_maps():
    html = (
        '<a href="http://trac.example/t/wiki/X">'
        '<img src="http://trac.example/t/wiki/i.png"></a>'
    )
    assert _rw(html) == (
        '<a href="https://blog.example/X">'
        '<img src="http://trac.example/t/wiki/i.png"></a>'
    )


def test_escaped_anchor_text_in_pre_content_is_untouched():
    html = (
        '<pre>&lt;a href="http://trac.example/t/wiki/X"&gt;x&lt;/a&gt;'
        "</pre>"
        '<a href="http://trac.example/t/wiki/Y">y</a>'
    )
    assert _rw(html) == (
        '<pre>&lt;a href="http://trac.example/t/wiki/X"&gt;x&lt;/a&gt;'
        "</pre>"
        '<a href="https://blog.example/Y">y</a>'
    )


def test_a_literal_anchor_in_an_html_comment_is_untouched():
    html = (
        '<!-- <a href="http://trac.example/t/wiki/X">c</a> -->'
        '<a href="http://trac.example/t/wiki/Y">y</a>'
    )
    assert _rw(html) == (
        '<!-- <a href="http://trac.example/t/wiki/X">c</a> -->'
        '<a href="https://blog.example/Y">y</a>'
    )


def test_script_and_style_content_is_untouched():
    html = (
        "<script>var a = '<a href=\"http://trac.example/t/wiki/X\">';"
        "</script>"
        '<style>a[href="http://trac.example/t/wiki/X"] {color: red}'
        "</style>"
        '<a href="http://trac.example/t/wiki/Y">y</a>'
    )
    assert _rw(html) == (
        "<script>var a = '<a href=\"http://trac.example/t/wiki/X\">';"
        "</script>"
        '<style>a[href="http://trac.example/t/wiki/X"] {color: red}'
        "</style>"
        '<a href="https://blog.example/Y">y</a>'
    )


def test_several_links_on_one_line_are_each_rewritten():
    a = "http://trac.example/t/wiki/"
    html = f'<p><a href="{a}A">a</a> <a href="{a}B">b</a><a href="{a}C">c</a></p>'
    assert _rw(html) == (
        '<p><a href="https://blog.example/A">a</a> '
        '<a href="https://blog.example/B">b</a>'
        '<a href="https://blog.example/C">c</a></p>'
    )


def test_offsets_hold_after_non_ascii_text_and_across_lines():
    a = "http://trac.example/t/wiki/"
    html = (
        f'<p>café über 日本 <a href="{a}A">é</a></p>\n'
        f'<p>\U0001f600 <a href="{a}B">b</a> — '
        f'<a href="{a}C">c</a></p>\n'
        "\n\n"
        f'<p>é<a href="{a}D">d</a></p>'
    )
    assert _rw(html) == (
        "<p>café über 日本 "
        '<a href="https://blog.example/A">é</a></p>\n'
        '<p>\U0001f600 <a href="https://blog.example/B">b</a> — '
        '<a href="https://blog.example/C">c</a></p>\n'
        "\n\n"
        '<p>é<a href="https://blog.example/D">d</a></p>'
    )


def test_crlf_and_cr_line_ends_are_preserved():
    a = "http://trac.example/t/wiki/"
    html = f'<p>x</p>\r\n<p>y\r<a href="{a}A">a</a></p>\r\n'
    assert _rw(html) == (
        '<p>x</p>\r\n<p>y\r<a href="https://blog.example/A">a</a></p>\r\n'
    )


def test_no_map_and_no_document_url_changes_nothing():
    html = f'<a href="{_DOC}#Sec">s</a> <a href="/x">x</a>'
    assert rewrite_hrefs(html, None, None) == html


def test_a_bare_fragment_is_never_mapped():
    html = '<a href="#Sec">s</a>'
    assert _rw(html, {"#": "https://x/#"}) == html


def test_a_url_with_a_query_is_another_document():
    html = f'<a href="{_DOC}?v=2#Sec">s</a>'
    assert _rw(html) == (
        '<a href="https://blog.example/Page?v=2#Sec">s</a>'
    )


def test_the_empty_document_is_the_empty_string():
    assert _rw("") == ""


# --- the real Trac captures ----------------------------------------------------

_HREF_VALUE = re.compile(
    r"""(?is)(\shref\s*=\s*)("[^"]*"|'[^']*'|[^\s>]*)"""
)


def _without_href_values(text):
    return _HREF_VALUE.sub(r"\1", text)


_FIXTURE_FILES = sorted(_FIXTURES.glob("*.html"))


def test_the_fixtures_are_there():
    assert len(_FIXTURE_FILES) >= 10


@pytest.mark.parametrize(
    "path", _FIXTURE_FILES, ids=[p.stem for p in _FIXTURE_FILES]
)
def test_a_real_render_is_identical_with_no_map_and_no_document(path):
    html = path.read_text(encoding="utf-8")
    assert rewrite_hrefs(html, None, None) == html


@pytest.mark.parametrize(
    "path", _FIXTURE_FILES, ids=[p.stem for p in _FIXTURE_FILES]
)
def test_a_real_render_differs_only_in_its_href_values(path):
    html = path.read_text(encoding="utf-8")
    base = "https://trac.example.org/trac_mcp_server"
    out = rewrite_hrefs(
        html,
        {
            f"{base}/wiki/": "https://blog.example/",
            f"{base}/": "https://other.example/",
            "/trac_mcp_server/": "/rel/",
        },
        f"{base}/wiki/{path.stem}",
    )
    assert _without_href_values(out) == _without_href_values(html)


def test_a_real_render_with_a_map_changes_some_hrefs():
    html = (_FIXTURES / "stock_WikiFormatting.html").read_text(
        encoding="utf-8"
    )
    base = "https://trac.example.org/trac_mcp_server"
    out = rewrite_hrefs(
        html,
        {f"{base}/wiki/": "https://blog.example/"},
        f"{base}/wiki/WikiFormatting",
    )
    assert out != html
    assert "https://blog.example/" in out
    assert f"{base}/wiki/WikiFormatting#" not in out


# --- review fixes (#219) ----------------------------------------------------

_T = "http://t/x/wiki/"
_PUB = {"http://t/x/wiki/": "https://pub/"}


# Finding 1: an attribute value unescapes the HTML5 way.


def test_a_semicolonless_legacy_reference_before_equals_stays_literal():
    # "&not=2" is NOT "\u00ac=2" in an attribute value (HTML5), so the
    # value is the URL it looks like and the map applies to it.
    html = f'<a href="{_T}A?a=1&not=2">x</a>'
    assert (
        rewrite_hrefs(html, _PUB, None)
        == '<a href="https://pub/A?a=1&amp;not=2">x</a>'
    )


def test_a_literal_legacy_reference_in_an_unmapped_url_keeps_its_bytes():
    html = '<a href="https://other/A?a=1&not=2">x</a>'
    assert rewrite_hrefs(html, _PUB, None) == html


@pytest.mark.parametrize(
    "value, decoded",
    [
        ("a=1&copy;b", "a=1\u00a9b"),
        ("a=1&copy b", "a=1\u00a9 b"),
        ("a=1&copy", "a=1\u00a9"),
        ("a=1&amp;b=2", "a=1&b=2"),
        ("a=1&amp b", "a=1& b"),
        ("a=1&amp=2", "a=1&amp=2"),
        ("a=1&#38;b=2", "a=1&b=2"),
        ("a=1&#x26;b=2", "a=1&b=2"),
        ("a=1&not=2", "a=1&not=2"),
        ("a=1&notit;b", "a=1&notit;b"),
        ("a=1&para=2", "a=1&para=2"),
        ("a=1&reg1", "a=1&reg1"),
        ("a=1&not-x", "a=1\u00ac-x"),
        ("a=1&not;=2", "a=1\u00ac=2"),
        ("a=1&bogus;b", "a=1&bogus;b"),
        ("plain", "plain"),
    ],
)
def test_attribute_value_references_follow_html5(value, decoded):
    from trac_mcp_server.converters.link_targets import (
        unescape_attribute,
    )

    assert unescape_attribute(value) == decoded


# Finding 2: the attribute grammar is HTMLParser's own.


def test_a_nameless_equals_attribute_does_not_hide_the_href():
    html = f'<a =foo href="{_T}A">x</a>'
    assert (
        rewrite_hrefs(html, _PUB, None)
        == '<a =foo href="https://pub/A">x</a>'
    )


def _reported_href(html):
    """The href value html.parser reports for ``html`` on THIS
    interpreter (the one the rewrite is specified against)."""
    seen = []

    class _Href(HTMLParser):
        def handle_starttag(self, tag, attrs):
            seen.extend(v for k, v in attrs if k == "href")

    parser = _Href(convert_charrefs=False)
    parser.feed(html)
    parser.close()
    return seen[0]


def test_a_doubled_equals_yields_the_value_htmlparser_reports():
    # What html.parser reports for `href==` depends on the CPython
    # patch release: 3.12.8 and older report the URL after the second
    # `=`, 3.12.9+ and 3.13 (HTML5) report `="URL"`, a value that is
    # not a Trac URL. The rewrite maps whatever is reported, so the
    # expectation is derived from the running interpreter (Trac #262).
    html = f'<a href=="{_T}A">x</a>'
    reported = _reported_href(html)
    if reported == f"{_T}A":
        expected = '<a href=="https://pub/A">x</a>'
    else:
        assert reported == f'="{_T}A"'
        expected = html
    assert rewrite_hrefs(html, _PUB, None) == expected


_ODD_TAGS = [
    f'<a href="{_T}A">',
    f"<a href='{_T}A'>",
    f"<a href={_T}A>",
    f'<a =foo href="{_T}A">',
    f'<a href=="{_T}A">',
    f'<a href = "{_T}A" >',
    f'<a href\n=\n"{_T}A">',
    f'<a/href="{_T}A">',
    f'<a x="1"href="{_T}A">',
    f'<a x=\'a"b\' href="{_T}A">',
    "<a href>",
    "<a href=>",
    '<a href="">',
    f'<a HREF="{_T}A" Href="{_T}B">',
    f'<a data-href="{_T}A" href="{_T}B">',
    f'<a title="href=\'{_T}A\'" href="{_T}B">',
    f'<a href="{_T}A?a=1&amp;b=2" id=x/>',
    f"<a href={_T}A/>",
    f'<a href="{_T}A"/>',
    '<a href="unterminated>',
    f"<a href='{_T}A\"B'>",
    f"<a  \t href  =  '{_T}A'  class = c >",
    f'<a ==x href="{_T}A">',
    f'<a x= href="{_T}A">',
]


class _Reference(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.attrs = []

    def handle_starttag(self, tag, attrs):
        self.attrs.append(attrs)

    handle_startendtag = handle_starttag


@pytest.mark.parametrize("html", _ODD_TAGS)
def test_the_tokeniser_sees_the_attributes_htmlparser_reports(html):
    reference = _Reference()
    reference.feed(html + "</a>")
    reference.close()
    from trac_mcp_server.converters.link_targets import _attributes

    tags = _StartTags(html + "</a>")
    tags.feed(html + "</a>")
    tags.close()
    mine = [
        [
            (
                a.name,
                unescape(a.value) if a.value else a.value,
            )
            for a in _attributes(raw)
        ]
        for _, raw in tags.tags
    ]
    assert mine == reference.attrs


# Finding 3: raw-text elements hold text, not tags.


@pytest.mark.parametrize(
    "tag", ["textarea", "title", "xmp", "iframe", "noembed", "noframes"]
)
def test_a_raw_text_element_s_content_is_not_rewritten(tag):
    inside = f'<{tag}><a href="{_T}Q">x</a></{tag}>'
    after = f'<a href="{_T}Q">y</a>'
    out = rewrite_hrefs(inside + after, _PUB, None)
    assert out == inside + '<a href="https://pub/Q">y</a>'


def test_a_raw_text_element_ends_at_its_end_tag_in_any_case():
    html = f'<TEXTAREA><a href="{_T}Q">x</a></TextArea><a href="{_T}Q">y</a>'
    assert rewrite_hrefs(html, _PUB, None) == (
        f'<TEXTAREA><a href="{_T}Q">x</a></TextArea>'
        '<a href="https://pub/Q">y</a>'
    )


def test_a_comment_opener_inside_textarea_does_not_swallow_its_end():
    html = (
        f'<textarea><!-- <a href="{_T}Q">x</a></textarea>'
        f'<a href="{_T}Q">y</a>'
    )
    assert rewrite_hrefs(html, _PUB, None) == (
        f'<textarea><!-- <a href="{_T}Q">x</a></textarea>'
        '<a href="https://pub/Q">y</a>'
    )


def test_plaintext_content_runs_to_the_end_of_the_document():
    html = f'<a href="{_T}Q">a</a><plaintext><a href="{_T}Q">b</a></plaintext>'
    assert rewrite_hrefs(html, _PUB, None) == (
        f'<a href="https://pub/Q">a</a><plaintext>'
        f'<a href="{_T}Q">b</a></plaintext>'
    )


# Finding 5: many edits, one result.


def test_many_links_are_all_rewritten_in_order():
    n = 2000
    html = "".join(
        f'<p>{i}<a href="{_T}P{i}">l{i}</a><a href="http://z/{i}">z</a></p>\n'
        for i in range(n)
    )
    expected = "".join(
        f'<p>{i}<a href="https://pub/P{i}">l{i}</a>'
        f'<a href="http://z/{i}">z</a></p>\n'
        for i in range(n)
    )
    assert rewrite_hrefs(html, _PUB, None) == expected
