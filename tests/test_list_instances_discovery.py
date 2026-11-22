"""Trac /trac_mcp_server #274: list_instances discovery and render-check
fetch use the client's own session, off the event loop, with a timeout.

The discovery scrape used to be a bare ``requests.get`` run synchronously
inside the handler's coroutine: it blocked the loop for up to its timeout,
ignored the instance's TLS settings (``verify`` / ``TRAC_INSECURE``) and
sat outside ``ScopedSession``'s scope check. The render check's page fetch
had no timeout at all.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from trac_mcp_server.config import Config
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


async def _call_list(registry):
    set_instance_registry(registry)
    client = MagicMock()
    client.config = _config()
    with patch(
        "trac_mcp_server.mcp.tools.instances.caller_identity",
        return_value=None,
    ):
        return await ToolRegistry(INSTANCE_SPECS).call_tool(
            "list_instances", {}, client
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


class TestSessionReadOnTheWorkerThread:
    def test_the_session_is_obtained_on_the_worker_not_the_loop(self):
        """TracClient sessions are thread-local: reading ``.session`` on
        the loop thread would hand every concurrent call the loop
        thread's one session."""
        reads = []
        session = MagicMock()
        fetched_on = []

        def get(url, **kwargs):
            fetched_on.append(threading.get_ident())
            return _response()

        session.get.side_effect = get

        class FakeClient:
            config = _config()

            @property
            def session(self):
                reads.append(threading.get_ident())
                return session

        registry = InstanceRegistry(_config(), {})
        registry.get_client = MagicMock(return_value=FakeClient())

        async def scenario():
            loop_thread = threading.get_ident()
            _list_in_loop_result = await _call_list(registry)
            return loop_thread, _list_in_loop_result

        loop_thread, result = asyncio.run(scenario())

        assert "discovered" in result.structuredContent
        assert reads, "the client's session was never read"
        assert loop_thread not in reads, (reads, loop_thread)
        assert reads == fetched_on


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

        session.get.assert_called_once_with(HOST, timeout=(10, 10))
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


class TestDiscoveryForAScopedCaller:
    """A caller with an allowlist keeps discovery: the index is fetched
    with its own credentials on the host's origin, and what comes back is
    filtered to the allowlist."""

    def _scoped_client(self):
        from trac_mcp_server.core.client import TracClient

        trac_test = f"{HOST}/trac_test"
        config = _config(
            trac_url=trac_test,
            username="cloud_rpc",
            password="cloud-pw",
            allowed_instances=frozenset({trac_test}),
        )
        return config, TracClient(config)

    def test_the_index_is_fetched_as_the_caller_and_filtered(self):
        config, client = self._scoped_client()
        registry = InstanceRegistry(config, {})
        registry.get_client = MagicMock(return_value=client)
        index = (
            b"<html><body><ul>"
            b'<li><a href="/trac_test">Test</a></li>'
            b'<li><a href="/auto_pm">APM</a></li>'
            b"</ul></body></html>"
        )
        sent = []

        def fake_send(adapter, request, **kwargs):
            sent.append(request)
            response = requests.Response()
            response.status_code = 200
            response._content = index
            response.url = request.url
            response.request = request
            return response

        with patch("requests.adapters.HTTPAdapter.send", new=fake_send):
            result = _list(registry)

        assert [r.url for r in sent] == [f"{HOST}/"]
        assert sent[0].headers["Authorization"].startswith("Basic ")
        import base64

        assert (
            base64.b64decode(sent[0].headers["Authorization"][6:])
            == b"cloud_rpc:cloud-pw"
        )
        structured = result.structuredContent
        assert [e["path"] for e in structured["discovered"]] == [
            "/trac_test"
        ]
        assert "discovery" not in structured
        assert "skipped" not in result.content[0].text

    def test_the_throwaway_session_keeps_the_client_settings(self):
        """verify, cert, proxies, trust_env and auth are copied; the
        client's own session is left alone and the copy is closed."""
        config, client = self._scoped_client()
        # One fixed session, whichever thread asks (the real property is
        # thread-local), carrying settings the copy must keep.
        own = client._create_session()
        own.verify = "/etc/ssl/ca.pem"
        own.cert = "/etc/ssl/client.pem"
        own.proxies = {"https": "http://proxy.example.org:3128"}
        own.trust_env = False
        stub = MagicMock()
        stub.config = config
        stub.session = own
        registry = InstanceRegistry(config, {})
        registry.get_client = MagicMock(return_value=stub)
        seen = []
        closed = []

        original_close = requests.Session.close

        def record_close(session):
            closed.append(session)
            original_close(session)

        def fake_get(session, url, **kwargs):
            seen.append(session)
            return _response()

        with (
            patch.object(requests.Session, "get", new=fake_get),
            patch.object(requests.Session, "close", new=record_close),
        ):
            _list(registry)

        (used,) = seen
        assert used is not own
        assert used.scope is None
        assert used.own_url == own.own_url
        assert used.auth == ("cloud_rpc", "cloud-pw")
        assert used.verify == "/etc/ssl/ca.pem"
        assert used.cert == "/etc/ssl/client.pem"
        assert used.proxies == own.proxies
        assert used.trust_env is False
        assert closed == [used]
        assert own.scope is not None

    def test_an_unscoped_client_keeps_using_its_own_session(self):
        from trac_mcp_server.core.client import TracClient

        config = _config()
        client = TracClient(config)
        own = client._create_session()
        stub = MagicMock()
        stub.config = config
        stub.session = own
        registry = InstanceRegistry(config, {})
        registry.get_client = MagicMock(return_value=stub)
        seen = []
        closed = []

        def fake_get(session, url, **kwargs):
            seen.append(session)
            return _response()

        with (
            patch.object(requests.Session, "get", new=fake_get),
            patch.object(
                requests.Session,
                "close",
                new=lambda session: closed.append(session),
            ),
        ):
            _list(registry)

        assert seen == [own]
        assert closed == []


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
