"""Live row for ticket #211: wiki_file_pull ``format=html``.

The document is Trac's own render of the page in a minimal shell. The
offline tests stand in for Trac's renderer with a canned fragment; this
row has the real one render a page with a heading and a wiki link and
checks the result through the real handler and a real client
(Rules/testing/RealSubstrateNotMocks).

Style follows ``test_wiki_file_pull_live.py``: a per-run scratch page on
``/trac_test`` (the client is built for that instance, never for the
default ``TRAC_URL``), deleted in a ``finally``; a failed delete is
reported only once the body has passed.
"""

import asyncio
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
