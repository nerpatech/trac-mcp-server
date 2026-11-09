"""Live row for ticket #213: wiki_file_pull rewrites links with a map.

The offline tests give the translator hand-written HTML; this row has
the real Trac render a page that links to a wiki page and to a heading of
its own page, then pulls it through the real handler with one rule that
maps this instance's ``/wiki/`` URL prefix to ``https://blog.example/``.
The page link must come back rewritten, the in-page heading anchor must
not (Rules/testing/RealSubstrateNotMocks).

Style follows ``test_wiki_file_pull_live.py``: a per-run scratch page on
``/trac_test`` (the client is built for that instance), deleted in a
``finally``; a failed delete is reported only once the body has passed.
"""

import asyncio
import sys
import uuid

import pytest

from trac_mcp_server.mcp.tools.wiki_file import _handle_pull


def _body(page: str) -> str:
    # The page links to itself (a link to a page that does not exist is
    # rendered as plain text, and the translator drops it) and to its own
    # heading. "= Link row =" gets the id "Linkrow" (Trac strips the
    # whitespace and does not lowercase).
    return (
        "= Link row =\n"
        "\n"
        f"Scratch page for the ticket 213 live row: see [wiki:{page} "
        "the page] and [#Linkrow the heading].\n"
    )


@pytest.mark.live
def test_pull_rewrites_the_page_link_and_not_the_heading_anchor():
    from tests.live_helpers import trac_test_client

    client = trac_test_client()
    page = f"Scratch/Ticket213LinkLive{uuid.uuid4().hex[:8]}"
    wiki_prefix = f"{client.config.trac_url.rstrip('/')}/wiki/"
    client.put_wiki_page(page, _body(page), "seed for the link row")
    cleanup_error = None
    try:
        plain = asyncio.run(_handle_pull(client, {"page_name": page}))
        assert not plain.isError, plain.content
        assert plain.structuredContent["link_rewrites_source"] == "none"
        # Precondition: without a map the link points at Trac, so a
        # rewrite below is the map's doing and the prefix is the right one.
        assert (
            f"[the page]({wiki_prefix}{page})"
            in (plain.structuredContent["content"])
        ), plain.structuredContent["content"]

        result = asyncio.run(
            _handle_pull(
                client,
                {
                    "page_name": page,
                    "link_rewrites": [
                        {
                            "from": wiki_prefix,
                            "to": "https://blog.example/",
                        }
                    ],
                },
            )
        )
        assert not result.isError, result.content
        sc = result.structuredContent
        assert sc["link_rewrites_source"] == "call"
        content = sc["content"]
        assert f"[the page](https://blog.example/{page})" in content, (
            content
        )
        assert "[the heading](#Linkrow)" in content, content
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
