"""Live row for ticket #211: wiki_file_pull ``format=html``.

The document is Trac's own render of the page in a minimal shell. The
offline tests stand in for Trac's renderer with a canned fragment; this
row has the real one render a page with a heading and a wiki link and
checks the result through the real handler and a real client
(Rules/testing/RealSubstrateNotMocks).

A second row (ticket #219) has the real renderer produce in-page anchors
(a ``[[PageOutline]]`` table of contents and a ``[#Heading x]`` link) and
a link to another page, and checks the hrefs the export writes.

Style follows ``test_wiki_file_pull_live.py``: a per-run scratch page on
``/trac_test`` (the client is built for that instance, never for the
default ``TRAC_URL``), deleted in a ``finally``; a failed delete is
reported only once the body has passed.
"""

import asyncio
import re
import sys
import uuid

import pytest

from trac_mcp_server.mcp.tools.wiki_file import _handle_pull

# A heading and a link to another wiki page. WikiStart exists on every
# Trac, so the link is not a "missing page" link.
_BODY = (
    "= Html row =\n"
    "\n"
    "Scratch page for the ticket 211 live row, linking to [wiki:WikiStart"
    " the start page].\n"
)


@pytest.mark.live
def test_pull_html_is_a_document_around_trac_render():
    from tests.live_helpers import trac_test_client

    client = trac_test_client()
    page = f"Scratch/Ticket211HtmlLive{uuid.uuid4().hex[:8]}"
    client.put_wiki_page(page, _BODY, "seed for the html pull row")
    cleanup_error = None
    try:
        result = asyncio.run(
            _handle_pull(client, {"page_name": page, "format": "html"})
        )

        assert not result.isError, result.content
        sc = result.structuredContent
        assert sc["format"] == "html"
        assert sc["converted"] is True
        assert sc["link_rewrites_source"] == "none"
        assert (
            sc["version"] == client.get_wiki_page_info(page)["version"]
        )
        assert sc["warnings"] == []
        content = sc["content"]
        assert content.startswith("<!doctype html>\n"), content[:200]
        assert '<meta charset="utf-8">' in content
        assert f"<title>{page}</title>" in content
        assert content.endswith("</body></html>\n")
        # Trac's getPageHTML wraps its render in <html><body>; the
        # document must have exactly one of each, the heading inside.
        assert content.count("<html") == 1, content
        assert content.count("<body") == 1, content
        assert content.index("<body>") < content.index("<h1")
        assert content.index("<h1") < content.index("</body>")
        assert "Html row" in content
        assert 'href="http' in content, content
    finally:
        try:
            client.delete_wiki_page(page)
        except Exception as exc:  # noqa: BLE001
            cleanup_error = exc
            # Said here too, so a body failure (which propagates past
            # the check below) still shows what was left behind.
            print(
                f"page {page} left behind on /trac_test: {exc!r}",
                file=sys.stderr,
            )
    # Reached only when the body passed: a failing body's own assertion
    # is never replaced by a cleanup failure raised from the ``finally``.
    if cleanup_error is not None:
        pytest.fail(
            f"page {page} left behind on /trac_test: {cleanup_error!r}"
        )


# A TOC, two headings, a link to one of them and a link to WikiStart
# (which exists on every Trac). "== Alpha ==" gets the id "Alpha": Trac
# strips the whitespace and does not lowercase.
_HREF_BODY = (
    "[[PageOutline]]\n"
    "\n"
    "== Alpha ==\n"
    "\n"
    "== Beta ==\n"
    "\n"
    "Scratch page for the ticket 219 live row: [#Beta jump] and "
    "[wiki:WikiStart start].\n"
)
_START_LINK = re.compile(r'href="([^"]*?/wiki/)WikiStart"')
_HREF = re.compile(r'\shref="([^"]*)"')


@pytest.mark.live
def test_pull_html_rewrites_anchors_and_maps_the_page_link():
    from tests.live_helpers import trac_test_client

    client = trac_test_client()
    page = f"Scratch/Ticket219HtmlHrefLive{uuid.uuid4().hex[:8]}"
    client.put_wiki_page(page, _HREF_BODY, "seed for the html href row")
    cleanup_error = None
    try:
        plain = asyncio.run(
            _handle_pull(client, {"page_name": page, "format": "html"})
        )
        assert not plain.isError, plain.content
        sc = plain.structuredContent
        assert sc["link_rewrites_source"] == "none"
        content = sc["content"]
        hrefs = _HREF.findall(content)
        # The TOC entries and the [#Beta jump] link are in-page anchors.
        for anchor in ("#Alpha", "#Beta"):
            assert anchor in hrefs, (anchor, hrefs)
        assert hrefs.count("#Beta") >= 2, hrefs  # the TOC and the jump
        assert not any(f"/wiki/{page}#" in h for h in hrefs), hrefs
        # Without a map the other page's link is Trac's own. Its prefix
        # is read off the render: Trac's base URL can differ from
        # TRAC_URL.
        found = _START_LINK.search(content)
        assert found is not None, content
        prefix = found.group(1)

        result = asyncio.run(
            _handle_pull(
                client,
                {
                    "page_name": page,
                    "format": "html",
                    "link_rewrites": [
                        {"from": prefix, "to": "https://example.org/w/"}
                    ],
                },
            )
        )
        assert not result.isError, result.content
        sc = result.structuredContent
        assert sc["link_rewrites_source"] == "call"
        mapped = _HREF.findall(sc["content"])
        assert "https://example.org/w/WikiStart" in mapped, mapped
        assert "#Beta" in mapped, mapped
        assert not any(h.startswith(prefix) for h in mapped), mapped
    finally:
        try:
            client.delete_wiki_page(page)
        except Exception as exc:  # noqa: BLE001
            cleanup_error = exc
            print(
                f"page {page} left behind on /trac_test: {exc!r}",
                file=sys.stderr,
            )
    if cleanup_error is not None:
        pytest.fail(
            f"page {page} left behind on /trac_test: {cleanup_error!r}"
        )
