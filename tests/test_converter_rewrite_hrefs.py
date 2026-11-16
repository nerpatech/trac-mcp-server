"""``rewrite_hrefs``: the HTML export's link rewriting (Trac #219).

The input comes back with ONLY the values of ``href`` attributes changed;
every other byte is the input's own. The URL decisions are the Markdown
export's (``LinkTargets``): an in-page anchor is ``#frag``, else the
longest matching ``link_map`` prefix is replaced, and ``src`` is never
mapped.
"""

import re
from pathlib import Path

import pytest

from trac_mcp_server.converters.link_targets import (
    LinkTargets,
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
