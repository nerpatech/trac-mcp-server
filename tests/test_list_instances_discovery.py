"""Trac /trac_mcp_server #274: list_instances discovery and render-check
fetch use the client's own session, off the event loop, with a timeout.

The discovery scrape used to be a bare ``requests.get`` run synchronously
inside the handler's coroutine: it blocked the loop for up to its timeout,
ignored the instance's TLS settings (``verify`` / ``TRAC_INSECURE``) and
sat outside ``ScopedSession``'s scope check. The render check's page fetch
had no timeout at all.
"""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from trac_mcp_server.config import Config
from trac_mcp_server.core.scope import DestinationOutOfScope
from trac_mcp_server.instances import Identity, InstanceRegistry
from trac_mcp_server.mcp.tools.instances import (
    INSTANCE_SPECS,
    set_instance_registry,
)
from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.preview.live import (
    RenderCheckError,
    fetch_ticket_sections,
)

HOST = "https://trac.example.org"
DEFAULT_URL = f"{HOST}/trac_mcp_server"

INDEX_HTML = (
    b"<html><body><ul>"
    b'<li><a href="/trac_mcp_server" title="T">Trac MCP Server</a></li>'
    b'<li><a href="/bcs">BCS</a></li>'
    b"</ul></body></html>"
)


def _config(**overrides) -> Config:
    fields = dict(
        trac_url=DEFAULT_URL,
        username="agent_rpc",
        password="secret",
        rpc_timeout=42,
    )
    fields.update(overrides)
    return Config(**fields)


def _response(content: bytes = INDEX_HTML) -> MagicMock:
    response = MagicMock()
    response.content = content
    response.raise_for_status = MagicMock()
    return response


@pytest.fixture(autouse=True)
def isolated_registry():
    with patch(
        "trac_mcp_server.instances.discover_config_files",
        return_value=[],
    ):
        yield
    set_instance_registry(None)


def _list(registry, args=None, identity=None):
    set_instance_registry(registry)
    client = MagicMock()
    client.config = _config()
    with patch(
        "trac_mcp_server.mcp.tools.instances.caller_identity",
        return_value=identity,
    ):
        return asyncio.run(
            ToolRegistry(INSTANCE_SPECS).call_tool(
                "list_instances", args or {}, client
            )
        )


def _registry_with_session(session, config=None) -> InstanceRegistry:
    config = config or _config()
    registry = InstanceRegistry(config, {})
    client = MagicMock()
    client.config = config
    client.session = session
    registry.get_client = MagicMock(return_value=client)
    return registry


class TestDiscoveryOffTheLoop:
    def test_a_slow_scrape_does_not_block_the_event_loop(self):
        session = MagicMock()

        def slow_get(url, **kwargs):
            time.sleep(0.3)
            return _response()

        session.get.side_effect = slow_get
        registry = _registry_with_session(session)
        set_instance_registry(registry)
        client = MagicMock()
        client.config = _config()

        ticks = []

        async def ticker(stop: asyncio.Event):
            while not stop.is_set():
                ticks.append(time.monotonic())
                await asyncio.sleep(0.05)

        async def scenario():
            stop = asyncio.Event()
            task = asyncio.create_task(ticker(stop))
            await asyncio.sleep(0)  # let the ticker start
            with patch(
                "trac_mcp_server.mcp.tools.instances.caller_identity",
                return_value=None,
            ):
                result = await ToolRegistry(INSTANCE_SPECS).call_tool(
                    "list_instances", {}, client
                )
            stop.set()
            await task
            return result

        result = asyncio.run(scenario())

        assert "discovered" in result.structuredContent
        # A blocked loop would have ticked once (before the scrape) and
        # once after; a free one ticks about every 0.05 s through 0.3 s.
        assert len(ticks) >= 4, ticks


class TestDiscoveryUsesTheClientSession:
    def test_the_scrape_goes_through_the_session_with_a_timeout(self):
        session = MagicMock()
        session.get.return_value = _response()
        registry = _registry_with_session(session)

        with patch(
            "trac_mcp_server.detection.web_scraper.requests.get",
            side_effect=AssertionError("bare requests.get used"),
        ):
            result = _list(registry)

        session.get.assert_called_once_with(HOST, timeout=(10, 42))
        assert {
            e["path"] for e in result.structuredContent["discovered"]
        } == {
            "/trac_mcp_server",
            "/bcs",
        }

    def test_the_session_comes_from_the_callers_own_resolved_client(
        self,
    ):
        session = MagicMock()
        session.get.return_value = _response()
        registry = _registry_with_session(session)
        alice = Identity(
            name="alice", username="alice-trac", password="alice-pw"
        )

        _list(registry, identity=alice)

        registry.get_client.assert_called_once_with(
            None, identity=alice
        )

    def test_a_session_with_verify_off_is_the_one_that_fetches(self):
        """The TLS settings live on the session; a bare requests.get
        never saw them."""
        real = requests.Session()
        real.verify = False
        seen = {}

        def fake_get(url, **kwargs):
            seen["verify"] = real.verify
            return _response()

        real.get = fake_get  # type: ignore[method-assign]
        registry = _registry_with_session(real)

        _list(registry)

        assert seen == {"verify": False}


class TestDiscoveryOutsideTheCallersScope:
    def test_an_index_outside_the_allowlist_is_skipped_not_a_crash(
        self,
    ):
        session = MagicMock()
        session.get.side_effect = DestinationOutOfScope(HOST)
        registry = _registry_with_session(session)

        result = _list(registry)

        assert not result.isError
        assert (
            result.structuredContent["discovery"]
            == "skipped: the project index is outside this caller's scope"
        )
        assert "discovered" not in result.structuredContent
        assert any(
            "project index is outside this caller's scope" in c.text
            for c in result.content
        )

    def test_a_real_scoped_client_skips_without_sending(self):
        """End to end through the real ScopedSession: the host root is not
        under the allowlisted instance, so nothing is sent."""
        from trac_mcp_server.core.client import TracClient

        config = _config(allowed_instances=frozenset({DEFAULT_URL}))
        client = TracClient(config)
        registry = InstanceRegistry(config, {})
        registry.get_client = MagicMock(return_value=client)

        with patch(
            "requests.adapters.HTTPAdapter.send",
            side_effect=AssertionError("a request was sent"),
        ):
            result = _list(registry)

        assert result.structuredContent["discovery"].startswith(
            "skipped"
        )

    def test_an_unscoped_listing_has_no_discovery_field(self):
        session = MagicMock()
        session.get.return_value = _response()
        result = _list(_registry_with_session(session))
        assert "discovery" not in result.structuredContent


class TestRenderCheckFetchTimeout:
    def test_the_ticket_page_fetch_passes_a_timeout(self):
        client = MagicMock()
        client.config = _config()
        response = MagicMock(status_code=200)
        response.text = "<html><body></body></html>"
        client.session.get.return_value = response

        with pytest.raises(RenderCheckError):
            # The empty page has no description node: the fetch has still
            # happened, which is all this test reads.
            fetch_ticket_sections(client, 5, include_comments=False)

        client.session.get.assert_called_once()
        assert client.session.get.call_args.kwargs["timeout"] == (
            10,
            42,
        )
