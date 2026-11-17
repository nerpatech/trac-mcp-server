"""End to end: a token Trac rejects is answered 401 on its next request
(Trac /trac_mcp_server #205).

The request Trac refuses cannot itself be answered 401: the daemon uses
``StreamableHTTPSessionManager(json_response=False)``, and the SDK sends
200 plus the SSE headers before it dispatches to the tool, so the 401 only
reaches the client as tool-result text. What the daemon can do is remember
the token and answer the next request that carries it.

Nothing here is mocked between the client and Trac: the real ASGI app with
its lifespan and session manager, the real tool registry, a real
``TracClient`` and a real ``http.server`` standing in for Trac that answers
401 to every POST.
"""

import http.server
import json
import logging
import threading

import pytest
from starlette.testclient import TestClient

from trac_mcp_server.config import Config
from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.mcp.http_app import build_http_app
from trac_mcp_server.mcp.oidc import OidcClientCache
from trac_mcp_server.mcp.server import (
    PING_SPEC,
    oidc_token_rejected,
    server,
    set_instances,
    set_oidc_cache,
    set_registry,
)
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.registry import ToolRegistry

_BASE_URL = "http://127.0.0.1:8080"
_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0"},
    },
}


class _TracRefusesEverything(http.server.BaseHTTPRequestHandler):
    posts = 0

    def do_POST(self):
        type(self).posts += 1
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.fixture
def trac_url():
    _TracRefusesEverything.posts = 0
    stub = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), _TracRefusesEverything
    )
    thread = threading.Thread(target=stub.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{stub.server_port}/login/xmlrpc"
    finally:
        stub.shutdown()
        stub.server_close()


@pytest.fixture
def mcp_client(trac_url):
    """A TestClient on the real app, OIDC per-user mode, against the stub."""
    set_registry(ToolRegistry([PING_SPEC] + ALL_SPECS))
    set_oidc_cache(
        OidcClientCache(
            Config(trac_url=trac_url, username="", password=""),
            trac_url,
        )
    )
    app = build_http_app(
        server,
        ServerConfig(
            transport="http",
            host="127.0.0.1",
            port=8080,
            path="/mcp",
            oidc_rpc_url=trac_url,
            oidc_allow_insecure_http=True,
        ),
    )
    try:
        with TestClient(app, base_url=_BASE_URL) as client:
            yield client
    finally:
        set_oidc_cache(None)
        set_registry(None)
        set_instances(None)


def _rpc(client, token, payload, session_id=None):
    headers = {**_MCP_HEADERS, "Authorization": f"Bearer {token}"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return client.post(
        "/mcp", content=json.dumps(payload), headers=headers
    )


def _open_session(client, token):
    response = _rpc(client, token, _INITIALIZE)
    assert response.status_code == 200
    session_id = response.headers["mcp-session-id"]
    notified = _rpc(
        client,
        token,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session_id,
    )
    assert notified.status_code == 202
    return session_id


def _call_ticket_get(client, token, session_id):
    return _rpc(
        client,
        token,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "ticket_get",
                "arguments": {"ticket_id": 1},
            },
        },
        session_id,
    )


def _result_of(response) -> dict:
    """The JSON-RPC message in an SSE ``data:`` line."""
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    raise AssertionError(f"no data line in {response.text!r}")


class TestRejectedTokenGetsA401OnTheNextRequest:
    def test_the_refused_call_is_200_and_the_next_request_is_401(
        self, mcp_client
    ):
        session_id = _open_session(mcp_client, "tokA")

        refused = _call_ticket_get(mcp_client, "tokA", session_id)

        # The streamed response had started before the tool ran.
        assert refused.status_code == 200
        message = _result_of(refused)
        assert message["result"]["isError"] is True
        assert _TracRefusesEverything.posts == 1

        again = _call_ticket_get(mcp_client, "tokA", session_id)

        assert again.status_code == 401
        challenge = again.headers["www-authenticate"]
        assert 'error="invalid_token"' in challenge
        # The stub was not asked again: the daemon answered it itself.
        assert _TracRefusesEverything.posts == 1

    def test_another_token_is_not_affected(self, mcp_client):
        session_id = _open_session(mcp_client, "tokA")
        _call_ticket_get(mcp_client, "tokA", session_id)

        other = _rpc(mcp_client, "tokB", _INITIALIZE)

        assert other.status_code != 401

    def test_healthz_stays_open(self, mcp_client):
        session_id = _open_session(mcp_client, "tokA")
        _call_ticket_get(mcp_client, "tokA", session_id)

        assert mcp_client.get("/healthz").status_code == 200

    def test_the_rejection_is_logged_without_the_token(
        self, mcp_client, caplog
    ):
        token = "opaque-secret-tokA"
        session_id = _open_session(mcp_client, token)
        _call_ticket_get(mcp_client, token, session_id)

        with caplog.at_level(
            logging.INFO, logger="trac_mcp_server.mcp.http_app"
        ):
            again = _call_ticket_get(mcp_client, token, session_id)

        assert again.status_code == 401
        records = [
            r
            for r in caplog.records
            if r.name == "trac_mcp_server.mcp.http_app"
            and "rejected" in r.getMessage()
        ]
        assert len(records) == 1
        assert records[0].levelno == logging.INFO
        assert token not in caplog.text


class TestOidcTokenRejectedWithoutACache:
    def test_false_when_oidc_mode_is_not_active(self):
        set_oidc_cache(None)
        assert oidc_token_rejected("tokA") is False
