"""``ConversionResult.title`` from Trac's HTML (#212)."""

from pathlib import Path

from trac_mcp_server.converters.html_to_markdown import (
    trac_html_to_markdown,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "trac_html"


def _title(html):
    return trac_html_to_markdown(html).title


class TestTitle:
    def test_first_h1_text(self):
        assert _title('<h1 id="A">Hello</h1><p>x</p>') == "Hello"

    def test_trac_anchor_decorations_are_dropped(self):
        html = (
            '<h1 class="section" id="Hello">Hello Page'
            '<a class="anchor" href="#Hello" title="Link to this section">'
            " ¶</a></h1>"
        )
        assert _title(html) == "Hello Page"

    def test_icon_and_wikianchor_spans_are_dropped(self):
        html = (
            '<h1 id="A"><span class="icon">!</span>'
            '<span class="wikianchor"><a class="anchor" href="#A">x</a>'
            "</span>Title</h1>"
        )
        assert _title(html) == "Title"

    def test_a_later_h1_is_ignored(self):
        assert _title("<h1>First</h1><h1>Second</h1>") == "First"

    def test_an_h1_after_other_headings_is_still_the_first_h1(self):
        assert _title("<h2>Sub</h2><h1>Top</h1>") == "Top"

    def test_h2_only_page_has_no_title(self):
        assert _title("<h2>Sub</h2><p>text</p>") is None

    def test_empty_h1_has_no_title(self):
        assert _title('<h1 id="A"><a class="anchor">¶</a></h1>') is None

    def test_inline_markup_is_flattened(self):
        html = (
            "<h1>The <em>big</em>  <code>x()</code>\n <a href='/p'>"
            "link</a> test</h1>"
        )
        assert _title(html) == "The big x() link test"

    def test_the_markdown_does_not_change_with_the_title_read(self):
        html = "<h1>Hello</h1><p>body</p>"
        assert trac_html_to_markdown(html).text == "# Hello\n\nbody"

    def test_stock_fixture(self):
        html = (FIXTURE_DIR / "stock_TicketQuery.html").read_text(
            encoding="utf-8"
        )
        source = (FIXTURE_DIR / "stock_TicketQuery.tracwiki").read_text(
            encoding="utf-8"
        )

        assert (
            trac_html_to_markdown(html, source).title
            == "TicketQuery Wiki Macro"
        )

    def test_every_fixture_with_an_h1_has_a_title_without_anchor_marks(
        self,
    ):
        for path in sorted(FIXTURE_DIR.glob("*.html")):
            result = trac_html_to_markdown(
                path.read_text(encoding="utf-8")
            )
            if "<h1" in path.read_text(encoding="utf-8"):
                assert result.title, path.name
                assert "¶" not in result.title, path.name
