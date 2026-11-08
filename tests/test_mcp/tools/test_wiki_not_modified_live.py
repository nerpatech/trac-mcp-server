"""Live rows for ticket #174: an identical wiki write reports "unchanged".

Trac answers "Page not modified" to a write whose content is
byte-identical to the stored page and stores nothing. That is a no-op,
not an error, so `wiki_update` and `wiki_file_push` report it as
"unchanged" and the page's version does not move. The offline tests
(`test_wiki.py`, `test_wiki_file.py`, `test_client.py`) stand in for the
server's fault; these rows watch the real one.

Style follows `TestWriteGateLive` (`test_write_gate.py`): a per-run
scratch page on `/trac_test`, created before each row and deleted after
it (Trac #159), and the client built explicitly for `/trac_test` through
`InstanceRegistry`, never from the default `TRAC_URL`.
"""

import asyncio

import pytest

from trac_mcp_server.mcp.tools.wiki_file import (
    _handle_push as _handle_wiki_file_push,
)
from trac_mcp_server.mcp.tools.wiki_write import (
    _handle_update as _handle_wiki_update,
)


def _text(result):
    return "\n".join(
        c.text for c in result.content if hasattr(c, "text")
    )


@pytest.mark.live
class TestIdenticalWikiWriteLive:
    """An identical wiki write reports "unchanged" against REAL Trac."""

    BODY = "Seeded for the ticket 174 live row. Nothing to link.\n"

    @pytest.fixture
    def page(self):
        import uuid

        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient
        from trac_mcp_server.instances import InstanceRegistry

        config, _ = bootstrap_config()
        client = TracClient(
            InstanceRegistry(config, {}).resolve("/trac_test")
        )
        name = f"Scratch/Ticket174NotModified{uuid.uuid4().hex[:8]}"
        client.put_wiki_page(name, self.BODY, "seed")
        try:
            yield client, name
        finally:
            try:
                client.delete_wiki_page(name)
            except Exception:
                pass

    def test_identical_wiki_update_is_unchanged_not_an_error(
        self, page
    ):
        client, page = page
        info = client.get_wiki_page_info(page)
        stored = client.get_wiki_page(page)

        result = asyncio.run(
            _handle_wiki_update(
                client,
                {
                    "page_name": page,
                    "content": stored,
                    "version": info["version"],
                },
            )
        )

        assert not result.isError, _text(result)
        assert _text(result).splitlines()[0] == (
            f"Wiki page '{page}' unchanged: content identical to "
            f"version {info['version']}. Trac stored nothing."
        )
        after = client.get_wiki_page_info(page)
        assert after["version"] == info["version"], (
            "an unchanged result, but the version moved"
        )
        assert client.get_wiki_page(page) == stored

    def test_identical_file_push_is_unchanged(self, page):
        client, page = page
        info = client.get_wiki_page_info(page)
        stored = client.get_wiki_page(page)

        result = asyncio.run(
            _handle_wiki_file_push(
                client,
                {
                    "page_name": page,
                    "content": stored,
                    "format": "tracwiki",
                },
            )
        )

        assert not result.isError, _text(result)
        sc = result.structuredContent
        assert sc["action"] == "unchanged"
        assert sc["version"] == info["version"]
        after = client.get_wiki_page_info(page)
        assert after["version"] == info["version"], (
            "an unchanged result, but the version moved"
        )
