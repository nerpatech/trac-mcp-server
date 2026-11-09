"""Live row for ticket #212: front matter on a Markdown pull, and the
push refusal.

The offline tests stand in for Trac with fakes; this row pulls a real
page as Markdown through the real handler and a real client, checks the
front matter names that page and version, then pushes the pulled text
back: refused (the page's version unchanged), then forced (stored,
without the front matter) (Rules/testing/RealSubstrateNotMocks).

Style follows ``test_wiki_file_pull_html_live.py``: a per-run scratch page
on ``/trac_test`` (the client is built for that instance, never for the
default ``TRAC_URL``), deleted in a ``finally``; a failed delete is
reported only once the body has passed.
"""

import asyncio
import sys
import uuid

import pytest
import yaml

from trac_mcp_server.converters.front_matter import split_front_matter
from trac_mcp_server.mcp.tools.wiki_file import (
    _handle_pull,
    _handle_push,
)

_BODY = (
    "= Front matter row =\n"
    "\n"
    "Scratch page for the ticket 212 live row.\n"
)


@pytest.mark.live
def test_pull_front_matter_names_the_page_and_push_refuses_it():
    from tests.live_helpers import trac_test_client

    client = trac_test_client()
    page = f"Scratch/Ticket212FrontMatterLive{uuid.uuid4().hex[:8]}"
    client.put_wiki_page(page, _BODY, "seed for the front matter row")
    cleanup_error = None
    try:
        version = client.get_wiki_page_info(page)["version"]
        pulled = asyncio.run(
            _handle_pull(
                client, {"page_name": page, "format": "markdown"}
            )
        )

        assert not pulled.isError, pulled.content
        content = pulled.structuredContent["content"]
        assert content.startswith("---\n"), content[:200]
        raw, rest = split_front_matter(content)
        meta = yaml.safe_load(raw)
        assert meta["title"] == "Front matter row"
        assert meta["trac_page"] == page
        assert meta["trac_version"] == version
        assert meta["trac_instance"] == "/trac_test"
        assert meta["source"].endswith(f"/trac_test/wiki/{page}")
        assert rest.startswith("# Front matter row")

        # Pushed back as it came: refused, nothing written.
        refused = asyncio.run(
            _handle_push(
                client,
                {
                    "page_name": page,
                    "content": content,
                    "filename": "x.md",
                },
            )
        )

        assert refused.isError
        assert "validation_error" in refused.content[0].text
        assert client.get_wiki_page_info(page)["version"] == version

        # Forced: stored as a new version, without the front matter.
        forced = asyncio.run(
            _handle_push(
                client,
                {
                    "page_name": page,
                    "content": content,
                    "filename": "x.md",
                    "force": True,
                },
            )
        )

        assert not forced.isError, forced.content
        assert client.get_wiki_page_info(page)["version"] == version + 1
        assert "trac_page" not in client.get_wiki_page(page)
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
