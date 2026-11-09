"""Live row for ticket #210: wiki_file_pull translates Trac's own HTML.

``format=markdown`` is the HTML Trac renders for the page, translated.
The offline tests stand in for Trac's renderer with #208's fixtures;
this row has the real one render a page and checks the result through
the real handler and a real client (Rules/testing/RealSubstrateNotMocks).

Style follows ``test_wiki_not_modified_live.py``: a per-run scratch page
on ``/trac_test`` (the client is built for that instance, never for the
default ``TRAC_URL``), deleted in a ``finally``; a failed delete is
reported only once the body has passed.
"""

import asyncio
import re
import sys
import uuid

import pytest

from trac_mcp_server.mcp.tools.wiki_file import _handle_pull

# A nested list and a ``{{{#!python`` block: the two shapes the old
# regex converter got wrong and Trac's HTML gets right. No CamelCase
# word, no link, nothing for Trac to turn into markup besides these.
_BODY = (
    "= Pull row =\n"
    "\n"
    "Scratch page for the ticket 210 live row.\n"
    "\n"
    " * Outer item\n"
    "   * Nested item\n"
    " * Second outer\n"
    "\n"
    "{{{#!python\n"
    "def f(x):\n"
    "    return x + 1\n"
    "}}}\n"
)


@pytest.mark.live
def test_pull_markdown_is_trac_rendered_html_translated():
    from tests.live_helpers import trac_test_client

    client = trac_test_client()
    page = f"Scratch/Ticket210PullLive{uuid.uuid4().hex[:8]}"
    client.put_wiki_page(page, _BODY, "seed for the pull row")
    cleanup_error = None
    try:
        result = asyncio.run(_handle_pull(client, {"page_name": page}))

        assert not result.isError, result.content
        sc = result.structuredContent
        assert sc["format"] == "markdown"
        assert sc["converted"] is True
        assert (
            sc["version"] == client.get_wiki_page_info(page)["version"]
        )
        assert isinstance(sc["warnings"], list)
        content = sc["content"]
        assert "```python\ndef f(x):\n    return x + 1\n```" in content
        assert re.search(
            r"^- Outer item\n +[*-] Nested item$", content, re.M
        ), content
        assert "Second outer" in content

        raw = asyncio.run(
            _handle_pull(
                client, {"page_name": page, "format": "tracwiki"}
            )
        )
        assert raw.structuredContent["content"] == _BODY
        assert raw.structuredContent["warnings"] == []
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
