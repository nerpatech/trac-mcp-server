"""Real-substrate tests for per-caller identity over the http transport
(ticket #102), per auto_pm:wiki:Rules/testing/RealSubstrateNotMocks:
transport/lifecycle/concurrency code needs at least one regression test
against the real substrate, not a mock that can only fail in ways its
author already imagined.

Hermetic section: a real uvicorn server on an ephemeral port, driven by
the SDK's real ``streamablehttp_client`` + ``ClientSession`` -- no
Starlette ``TestClient``, no ``AsyncMock`` on the transport. Proves that
concurrent sessions authenticated with different bearer tokens never
observe each other's identity, that the legacy static token still
resolves to no identity, and that identity is read per REQUEST (the
bearer header on that call), not per SESSION (the ``mcp-session-id``).

Live section (``@pytest.mark.live``): the same harness, but the full
production ``trac_mcp_server.mcp.server.server`` (every real tool) against
``/trac_test``, with two real Trac accounts writing concurrently.
"""

import asyncio
import json
import os
import socket
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager

import anyio
import httpx
import pytest
import uvicorn
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server import Server

from trac_mcp_server import __version__
from trac_mcp_server.config import Config
from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.instances import Identity, InstanceRegistry
from trac_mcp_server.mcp import server as server_module
from trac_mcp_server.mcp.http_app import build_http_app

# ---------------------------------------------------------------------------
# Shared harness: a real uvicorn server on an ephemeral port
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Bind to port 0 to let the OS assign a free one, then release it.

    Small bind-then-release race, standard for test harnesses -- and the
    security settings below allow any port on 127.0.0.1 regardless
    (``"127.0.0.1:*"`` is always in ``allowed_hosts``), so a port taken
    between release and uvicorn's own bind would surface as a connection
    error, not a silently-wrong test.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def _running_app(mcp_server: Server, server_config: ServerConfig):
    """Serve ``mcp_server`` over real uvicorn, in its own thread with its
    own event loop, for the duration of the ``with`` block. Yields the
    server's base URL.

    Deliberately a background THREAD with an independent event loop, not
    a second task on the caller's own loop: client and server sharing one
    event loop (both server accept/request handling and the test's own
    client calls as cooperatively-scheduled tasks) produced an
    intermittent, single-event-loop deadlock -- the loop idling in
    ``select()`` with nothing runnable -- that only ever reproduced when
    enough OTHER test modules ran first in the same pytest session to
    shift scheduling, never in a lone-file run, and moved to a different
    one of these tests on each bisection attempt rather than staying
    pinned to one. A real client/server pair over a real socket has no
    business sharing a scheduler in the first place; giving the server
    its own OS thread and event loop (uvicorn's own signal-handling
    already special-cases a non-main thread, see
    ``uvicorn.Server.capture_signals``) removed it. This is also the
    standard pattern other projects use for exactly this kind of
    real-server-in-tests harness.
    """
    app = build_http_app(mcp_server, server_config)
    config = uvicorn.Config(
        app,
        host=server_config.host,
        port=server_config.port,
        log_level="warning",
        log_config=None,
    )
    uv_server = uvicorn.Server(config)
    thread = threading.Thread(
        target=lambda: asyncio.run(uv_server.serve()), daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not uv_server.started:
            if not thread.is_alive():
                raise RuntimeError(
                    "uvicorn server thread exited before starting"
                )
            if time.monotonic() > deadline:
                raise TimeoutError(
                    "uvicorn server did not start within 10s"
                )
            time.sleep(0.01)
        yield f"http://{server_config.host}:{server_config.port}"
    finally:
        uv_server.should_exit = True
        thread.join(timeout=10)


async def _call_tool(base_url, token, tool_name, arguments):
    """One MCP session: connect, initialize, call one tool, return the
    text of its first content block."""
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with streamablehttp_client(
        f"{base_url}/mcp", headers=headers
    ) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments)


# ---------------------------------------------------------------------------
# Hermetic: a minimal probe server whose only tool echoes the resolved
# caller identity, via the production helper (not a copy of it).
# ---------------------------------------------------------------------------


def _make_identity_probe_server() -> Server:
    probe = Server("identity-probe", version=__version__)

    @probe.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="whoami",
                description="Return the in-flight request's resolved "
                "caller identity name, or 'none'.",
                inputSchema={"type": "object", "properties": {}},
            )
        ]

    @probe.call_tool()
    async def _whoami(
        name: str, arguments: dict
    ) -> list[types.TextContent]:
        # Read through the module, not a bound-at-import-time reference,
        # so the seeded-defect test below (which monkeypatches the
        # module attribute) actually changes what this handler sees.
        identity = server_module._caller_identity()
        text = identity.name if identity is not None else "none"
        return [types.TextContent(type="text", text=text)]

    return probe


_TOKEN_A = "hermetic-token-alice"
_TOKEN_B = "hermetic-token-bob"
_IDENTITY_A = Identity(
    name="alice", username="alice-u", password="alice-p"
)
_IDENTITY_B = Identity(name="bob", username="bob-u", password="bob-p")
_LEGACY_TOKEN = "hermetic-legacy-token"


def _probe_server_config(port: int) -> ServerConfig:
    return ServerConfig(
        transport="http",
        host="127.0.0.1",
        port=port,
        path="/mcp",
        auth_token=_LEGACY_TOKEN,
        identities={_TOKEN_A: _IDENTITY_A, _TOKEN_B: _IDENTITY_B},
    )


async def _collect_two_sessions(
    base_url: str, calls_per_session: int = 50
) -> dict[str, list[str]]:
    """Run two sessions concurrently to completion, each recording every
    call's resolved identity name -- no assertion runs until both are
    done, so nothing here ever needs to cancel an in-flight network call
    on one session because the other failed. Cancelling a live
    ``call_tool()`` this way, via ``asyncio.gather``'s no-cancel-siblings
    default or even an ``anyio`` task group, was the source of an
    intermittent hang during the seeded-defect run below -- reproducible
    only when this file ran alongside enough other test modules to shift
    the timing, never in a lone-file run. Collecting first and asserting
    after removes the cancellation entirely, for the happy path and the
    seeded-defect path alike."""

    async def _session(token: str, name: str) -> None:
        seen: list[str] = []
        headers = {"Authorization": f"Bearer {token}"}
        async with streamablehttp_client(
            f"{base_url}/mcp", headers=headers
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for _ in range(calls_per_session):
                    result = await session.call_tool("whoami", {})
                    seen.append(result.content[0].text)
        results[name] = seen

    results: dict[str, list[str]] = {}
    async with anyio.create_task_group() as tg:
        tg.start_soon(_session, _TOKEN_A, "alice")
        tg.start_soon(_session, _TOKEN_B, "bob")
    return results


async def _assert_two_sessions_never_cross_identities(
    base_url: str, calls_per_session: int = 50
) -> None:
    results = await _collect_two_sessions(base_url, calls_per_session)
    assert results["alice"] == ["alice"] * calls_per_session, (
        f"alice's session saw: {results['alice']!r}"
    )
    assert results["bob"] == ["bob"] * calls_per_session, (
        f"bob's session saw: {results['bob']!r}"
    )


class TestConcurrentSessionsHermetic:
    """Real uvicorn + real streamablehttp_client, no TestClient/AsyncMock.

    Every test body runs via ``anyio.run()`` from a plain sync ``def``,
    not a bare ``async def`` handed to pytest-asyncio's auto mode: with
    real sockets and an ``anyio`` task group in play, a pytest-asyncio
    function-scoped loop occasionally left one test's connection
    teardown stuck mid-select() when OTHER, unrelated test modules ran
    earlier in the same session -- reproducible standalone, never inside
    a lone-file run, and gone once the loop is owned end-to-end by
    ``anyio.run()`` instead of pytest-asyncio. See the ticket #102 PR
    description for the bisection that pinned this down.
    """

    def test_two_concurrent_sessions_never_cross_identities(self):
        async def _body():
            port = _free_port()
            with _running_app(
                _make_identity_probe_server(),
                _probe_server_config(port),
            ) as base_url:
                await _assert_two_sessions_never_cross_identities(
                    base_url
                )

        anyio.run(_body)

    def test_legacy_static_token_resolves_to_no_identity(self):
        async def _body():
            port = _free_port()
            with _running_app(
                _make_identity_probe_server(),
                _probe_server_config(port),
            ) as base_url:
                result = await _call_tool(
                    base_url, _LEGACY_TOKEN, "whoami", {}
                )
                assert result.content[0].text == "none"

        anyio.run(_body)

    def test_identity_is_per_request_not_per_session(self):
        """A request carrying session A's mcp-session-id but token B's
        Authorization header resolves to B, not A -- identity is read
        per REQUEST, not cached on the session."""

        async def _body():
            port = _free_port()
            with _running_app(
                _make_identity_probe_server(),
                _probe_server_config(port),
            ) as base_url:
                async with streamablehttp_client(
                    f"{base_url}/mcp",
                    headers={"Authorization": f"Bearer {_TOKEN_A}"},
                ) as (read, write, get_session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        # Confirm the session is actually alice's before
                        # trying to hijack it.
                        own_result = await session.call_tool(
                            "whoami", {}
                        )
                        assert own_result.content[0].text == "alice"
                        session_id = get_session_id()
                        assert session_id

                        # A raw request reusing alice's mcp-session-id,
                        # while her ClientSession is still open
                        # (streamablehttp_client terminates the session
                        # with a DELETE on __aexit__, so this must happen
                        # before that, not after). Authenticated as bob.
                        # json_response=False (build_http_app's default)
                        # means the response body is SSE-framed even for
                        # a single request/response -- scan for the
                        # "data:" line.
                        payload = {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {
                                "name": "whoami",
                                "arguments": {},
                            },
                        }
                        headers = {
                            "Content-Type": "application/json",
                            "Accept": "application/json, text/event-stream",
                            "Authorization": f"Bearer {_TOKEN_B}",
                            "mcp-session-id": session_id,
                        }
                        async with httpx.AsyncClient() as client:
                            async with client.stream(
                                "POST",
                                f"{base_url}/mcp",
                                json=payload,
                                headers=headers,
                            ) as response:
                                response.raise_for_status()
                                data_line = None
                                async for (
                                    line
                                ) in response.aiter_lines():
                                    if line.startswith("data:"):
                                        data_line = line[
                                            len("data:") :
                                        ].strip()
                assert data_line is not None
                body = json.loads(data_line)
                text = body["result"]["content"][0]["text"]
                assert text == "bob"

        anyio.run(_body)


# ---------------------------------------------------------------------------
# Seeded defect first (auto_pm:wiki:Rules/testing/SeededDefectFirst): a
# check is not trusted until it has been observed failing on a
# deliberately broken input. Break _caller_identity the way a
# shared-state regression would -- freeze it to whichever identity
# resolved on the FIRST call, across every session -- and confirm the
# concurrency test above then fails.
# ---------------------------------------------------------------------------


def _install_frozen_first_call_identity(monkeypatch) -> None:
    original = server_module._caller_identity
    captured: dict = {}

    def _frozen():
        identity = original()
        if "value" not in captured:
            captured["value"] = identity
        return captured["value"]

    monkeypatch.setattr(server_module, "_caller_identity", _frozen)


class TestSeededDefect:
    def test_concurrency_check_fails_when_identity_is_frozen(
        self, monkeypatch
    ):
        _install_frozen_first_call_identity(monkeypatch)

        async def _body():
            port = _free_port()
            with _running_app(
                _make_identity_probe_server(),
                _probe_server_config(port),
            ) as base_url:
                # Both sessions still run to completion (see
                # _collect_two_sessions), so this is a plain
                # AssertionError raised after the fact, not something a
                # task group had to cancel a live network call to
                # deliver.
                with pytest.raises(AssertionError):
                    await _assert_two_sessions_never_cross_identities(
                        base_url
                    )

        anyio.run(_body)


# ---------------------------------------------------------------------------
# Live: the full production server, real /trac_test writes, two real
# Trac accounts (agent_rpc + auto_pm, per TRAC_USERNAME[_2]/PASSWORD[_2]).
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _running_production_server(server_config: ServerConfig):
    """Wire up the real trac_mcp_server.mcp.server globals (ToolRegistry +
    InstanceRegistry, built the same way main() does) against /trac_test,
    serve them over the shared uvicorn harness, and tear the globals back
    down afterward so this test cannot leak state into any other test in
    the same process."""
    from trac_mcp_server.mcp.server import PING_SPEC
    from trac_mcp_server.mcp.tools import ALL_SPECS, ToolRegistry
    from trac_mcp_server.mcp.tools.instances import (
        set_instance_registry,
    )
    from trac_mcp_server.mcp.tools.registry import (
        with_instance_param,
        with_page_alias,
        with_strict_schema,
    )

    default_config = Config(
        trac_url=os.environ["TRAC_URL"],
        username=os.environ["TRAC_USERNAME"],
        password=os.environ["TRAC_PASSWORD"],
        insecure=os.environ.get("TRAC_INSECURE", "").lower()
        in ("1", "true", "yes"),
    )
    instances = InstanceRegistry(default_config, {})
    all_specs = with_strict_schema(
        with_instance_param(
            with_page_alias([PING_SPEC] + ALL_SPECS), []
        )
    )
    registry = ToolRegistry(all_specs)

    server_module.set_registry(registry)
    server_module.set_instances(instances)
    set_instance_registry(instances)
    try:
        with _running_app(
            server_module.server, server_config
        ) as base_url:
            yield base_url
    finally:
        server_module.set_registry(None)
        server_module.set_instances(None)
        set_instance_registry(None)


async def _write_and_verify_author(
    base_url: str, token: str, page_name: str, expected_username: str
) -> None:
    content = (
        f"Trac ticket 102 live identity test scratch content "
        f"({page_name}). No links, so the write gate has nothing to "
        "probe. Written as 'ticket 102', not a bare hash-number, "
        "because that is TracWiki ticket-link syntax and /trac_test "
        "has no matching ticket for it to resolve to."
    )
    async with streamablehttp_client(
        f"{base_url}/mcp", headers={"Authorization": f"Bearer {token}"}
    ) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()

            create_result = await session.call_tool(
                "wiki_create",
                {
                    "page_name": page_name,
                    "content": content,
                    "instance": "/trac_test",
                },
            )
            assert not create_result.isError, create_result.content

            get_result = await session.call_tool(
                "wiki_get",
                {"page_name": page_name, "instance": "/trac_test"},
            )
            assert not get_result.isError, get_result.content
            author = get_result.structuredContent["author"]
            assert author == expected_username, (
                f"{page_name}: expected author {expected_username!r}, "
                f"got {author!r}"
            )


@pytest.mark.live
class TestLiveTwoIdentities:
    """Requires --run-live and TRAC_URL/USERNAME/PASSWORD plus the second
    account TRAC_USERNAME_2/PASSWORD_2 (ci-live.sh enforces both pairs are
    set before pytest ever runs). Writes only to /trac_test, under
    Scratch/, with a random suffix so reruns never collide."""

    def test_two_identities_write_trac_test_concurrently(self):
        async def _body():
            token_agent = "live-token-agent-rpc"
            token_autopm = "live-token-auto-pm"
            server_config = ServerConfig(
                transport="http",
                host="127.0.0.1",
                port=_free_port(),
                path="/mcp",
                identities={
                    token_agent: Identity(
                        name="agent_rpc",
                        username=os.environ["TRAC_USERNAME"],
                        password=os.environ["TRAC_PASSWORD"],
                    ),
                    token_autopm: Identity(
                        name="auto_pm",
                        username=os.environ["TRAC_USERNAME_2"],
                        password=os.environ["TRAC_PASSWORD_2"],
                    ),
                },
            )

            suffix = uuid.uuid4().hex[:8]
            page_agent = f"Scratch/Ticket102IdentityAgentRpc{suffix}"
            page_autopm = f"Scratch/Ticket102IdentityAutoPm{suffix}"

            async with _running_production_server(
                server_config
            ) as base_url:
                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        _write_and_verify_author,
                        base_url,
                        token_agent,
                        page_agent,
                        os.environ["TRAC_USERNAME"],
                    )
                    tg.start_soon(
                        _write_and_verify_author,
                        base_url,
                        token_autopm,
                        page_autopm,
                        os.environ["TRAC_USERNAME_2"],
                    )

        anyio.run(_body)
