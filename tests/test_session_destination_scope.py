"""Ticket #118: where a ``TracClient`` session may send a request, and
with what credentials -- enforced at the session, against real sockets.

``TracClient`` sets ``session.auth``, so ``requests`` puts
``Authorization: Basic ...`` on every request that session makes,
whatever its host. The link probe fetches hrefs an author wrote, on any
host, so an authored link such as ``http://attacker.example/intertrac/
wiki%3AX`` handed the caller's Trac password to ``attacker.example``.
Measured before the fix: a listener on another port received
``Basic`` decoding to the configured ``username:password``.

Real listeners, not mocks (auto_pm:wiki:Rules/testing/
RealSubstrateNotMocks): the defect lives in what ``requests`` puts on
the wire and in how it follows redirects, which is exactly what a
mocked ``session.get`` cannot show. They are plain ``http.server``
sockets on 127.0.0.1, so the suite stays offline.

Two listeners stand in for two hosts. ``own`` is the client's Trac
(instances ``/trac_test`` and ``/auto_pm`` under one origin, like the
LAN host). ``foreign`` is anything else; a different port is a
different origin, and one test reaches it by a different hostname too.
"""

import asyncio
import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mcp.types as types
import pytest
import requests
import requests.adapters

from trac_mcp_server.config import Config
from trac_mcp_server.core.client import TracClient
from trac_mcp_server.preview.targets import (
    ERROR,
    EXISTS,
    SKIPPED,
    probe_targets,
)

USERNAME = "agent_rpc"
PASSWORD = "s3cret-pw"
BASIC = (
    "Basic "
    + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
)

EXISTING_PAGE = (
    '<html><body><div id="content" class="wiki view narrow">'
    "<h1>WikiStart</h1></div></body></html>"
)


class _Listener:
    """A real HTTP listener that records every request it receives.

    ``routes`` maps a request path (query included) to
    ``(status, headers)``; any other path answers 200 with an existing
    wiki page, which the probe classifies as ``exists``.
    """

    def __init__(self):
        self.requests: list[tuple[str, str | None]] = []
        self.routes: dict[str, tuple[int, dict[str, str]]] = {}
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                listener.requests.append(
                    (self.path, self.headers.get("Authorization"))
                )
                status, headers = listener.routes.get(
                    self.path, (200, {})
                )
                body = EXISTING_PAGE.encode() if status == 200 else b""
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _answer
            do_POST = _answer

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self._thread.start()

    def paths(self) -> list[str]:
        return [path for path, _ in self.requests]

    def auth_for(self, path: str) -> list[str | None]:
        return [auth for p, auth in self.requests if p == path]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def own():
    listener = _Listener()
    yield listener
    listener.close()


@pytest.fixture
def foreign():
    listener = _Listener()
    yield listener
    listener.close()


def _client(own: _Listener, allowed: frozenset[str] | None = None):
    return TracClient(
        Config(
            trac_url=f"{own.base}/trac_test",
            username=USERNAME,
            password=PASSWORD,
            allowed_instances=allowed,
        )
    )


def _scoped(own: _Listener) -> TracClient:
    return _client(own, frozenset({f"{own.base}/trac_test"}))


WIKI_TARGET = "/intertrac/wiki%3AWikiStart"


# ---------------------------------------------------------------------------
# Unscoped callers: credentials never leave the client's own origin
# ---------------------------------------------------------------------------


class TestUnscopedCredentials:
    def test_foreign_href_is_probed_without_credentials(
        self, own, foreign
    ):
        """The ticket's measurement, as a regression: the foreign host
        is still probed (option 1(a)), but anonymously."""
        href = f"{foreign.base}{WIKI_TARGET}"
        result = probe_targets(_client(own), [href], timeout=5)
        assert result[href]["status"] == EXISTS
        assert foreign.auth_for(WIKI_TARGET) == [None]

    def test_foreign_hostname_is_probed_without_credentials(
        self, own, foreign
    ):
        """The same leak by hostname rather than port -- the shape a
        real attacker's link has."""
        href = f"http://localhost:{foreign.port}{WIKI_TARGET}"
        result = probe_targets(_client(own), [href], timeout=5)
        assert result[href]["status"] == EXISTS
        assert foreign.auth_for(WIKI_TARGET) == [None]

    def test_own_origin_href_keeps_credentials(self, own):
        """Control: the instance's own dispatcher needs the caller's
        credentials, exactly as before."""
        path = f"/auto_pm{WIKI_TARGET}"
        href = f"{own.base}{path}"
        result = probe_targets(_client(own), [href], timeout=5)
        assert result[href]["status"] == EXISTS
        assert own.auth_for(path) == [BASIC]

    def test_redirect_to_a_foreign_host_drops_credentials(
        self, own, foreign
    ):
        path = f"/trac_test{WIKI_TARGET}"
        own.routes[path] = (302, {"Location": f"{foreign.base}/wiki/X"})
        probe_targets(_client(own), [f"{own.base}{path}"], timeout=5)
        assert own.auth_for(path) == [BASIC]
        assert foreign.auth_for("/wiki/X") == [None]

    def test_any_session_request_to_a_foreign_host_is_anonymous(
        self, own, foreign
    ):
        """Finding 7: the boundary is the session, not ``probe_targets``.
        A request made on ``client.session`` by any other code path is
        held to it too."""
        _client(own).session.get(f"{foreign.base}/anything", timeout=5)
        assert foreign.auth_for("/anything") == [None]

    def test_rpc_endpoint_keeps_credentials(self, own):
        client = _client(own)
        client.session.post(client.rpc_url, data=b"", timeout=5)
        assert own.auth_for("/trac_test/login/rpc") == [BASIC]

    def test_a_redirect_loop_is_bounded(self, own):
        """The redirect bound the hand-followed loop used to give scoped
        probes now holds for every request; a loop is an unchecked
        target, not a hang."""
        path = f"/trac_test{WIKI_TARGET}"
        own.routes[path] = (302, {"Location": path})
        href = f"{own.base}{path}"
        result = probe_targets(_client(own), [href], timeout=5)
        assert result[href]["status"] == ERROR
        assert len(own.requests) <= 6


# ---------------------------------------------------------------------------
# Scoped callers (#110): out-of-scope destinations are never requested
# ---------------------------------------------------------------------------


class TestScopedDestinations:
    def test_foreign_href_is_never_requested(self, own, foreign):
        href = f"{foreign.base}{WIKI_TARGET}"
        result = probe_targets(_scoped(own), [href], timeout=5)
        assert result[href] == {
            "status": SKIPPED,
            "resolved_url": None,
            "reason": "instance_not_allowed",
        }
        assert foreign.requests == []

    def test_redirect_out_of_scope_is_not_requested(self, own):
        """Measured live in #110: ``/trac_test/intertrac/auto_pm%3Awiki
        %3AIndex`` answers 302 to ``/auto_pm/...``. The query string
        makes the href probeable."""
        path = (
            f"/trac_test/intertrac/auto_pm%3Awiki%3AIndex?{WIKI_TARGET}"
        )
        own.routes[path] = (
            302,
            {"Location": f"{own.base}/auto_pm/intertrac/wiki%3AIndex"},
        )
        href = f"{own.base}{path}"
        result = probe_targets(_scoped(own), [href], timeout=5)
        assert result[href] == {
            "status": SKIPPED,
            "resolved_url": None,
            "reason": "instance_not_allowed",
        }
        assert own.paths() == [path]

    def test_redirect_within_scope_is_followed_with_credentials(
        self, own
    ):
        path = f"/trac_test{WIKI_TARGET}"
        page = "/trac_test/wiki/WikiStart"
        own.routes[path] = (302, {"Location": f"{own.base}{page}"})
        href = f"{own.base}{path}"
        result = probe_targets(_scoped(own), [href], timeout=5)
        assert result[href] == {
            "status": EXISTS,
            "resolved_url": f"{own.base}{page}",
        }
        assert own.requests == [(path, BASIC), (page, BASIC)]

    def test_redirect_to_an_encoded_dot_segment_is_not_requested(
        self, own
    ):
        """``%2e%2e`` survives preparation as a literal ``..`` and the
        server resolves it (review finding 1, #110)."""
        path = f"/trac_test{WIKI_TARGET}"
        own.routes[path] = (
            302,
            {
                "Location": f"{own.base}/trac_test/%2e%2e/auto_pm/wiki/Index"
            },
        )
        href = f"{own.base}{path}"
        result = probe_targets(_scoped(own), [href], timeout=5)
        assert result[href]["reason"] == "instance_not_allowed"
        assert own.paths() == [path]

    def test_redirect_to_another_allowed_host_is_not_requested(
        self, own, foreign
    ):
        """An allowed URL on another host is a declared instance with
        its own credentials (#102 gap 3); this client's session must not
        go there (review finding 3, #110)."""
        client = _client(
            own,
            frozenset({f"{own.base}/trac_test", f"{foreign.base}/x"}),
        )
        path = f"/trac_test{WIKI_TARGET}"
        own.routes[path] = (
            302,
            {"Location": f"{foreign.base}/x/wiki/Y"},
        )
        href = f"{own.base}{path}"
        result = probe_targets(client, [href], timeout=5)
        assert result[href]["reason"] == "instance_not_allowed"
        assert foreign.requests == []

    def test_redirect_to_another_instances_dispatcher_is_not_requested(
        self, own
    ):
        """A host-root entry allows the Trac AT the root, not every
        instance below it (review finding 5, #110)."""
        client = TracClient(
            Config(
                trac_url=own.base,
                username=USERNAME,
                password=PASSWORD,
                allowed_instances=frozenset({own.base}),
            )
        )
        path = WIKI_TARGET
        refused = "/auto_pm/intertrac/wiki%3AIndex"
        own.routes[path] = (302, {"Location": f"{own.base}{refused}"})
        href = f"{own.base}{path}"
        result = probe_targets(client, [href], timeout=5)
        assert result[href]["reason"] == "instance_not_allowed"
        assert own.paths() == [path]

    def test_any_session_request_out_of_scope_is_refused_unsent(
        self, own
    ):
        """Finding 7, scoped half: a request made on ``client.session``
        outside ``probe_targets`` is refused before it is sent."""
        from trac_mcp_server.core.scope import DestinationOutOfScope

        with pytest.raises(DestinationOutOfScope):
            _scoped(own).session.get(
                f"{own.base}/auto_pm/wiki/Index", timeout=5
            )
        assert own.requests == []

    def test_rpc_endpoint_is_in_scope(self, own):
        client = _scoped(own)
        client.session.post(client.rpc_url, data=b"", timeout=5)
        assert own.auth_for("/trac_test/login/rpc") == [BASIC]


# ---------------------------------------------------------------------------
# Review findings on #118 (local /code-review high)
# ---------------------------------------------------------------------------


class _Recorder(requests.adapters.BaseAdapter):
    """A transport adapter that records what the session would put on
    the wire and answers 200, so hosts that do not resolve here (an IDN,
    an https name) can still drive the real ``send()`` path."""

    def __init__(self):
        super().__init__()
        self.sent: list[tuple[str, str | None]] = []

    def send(self, request, **kwargs):
        self.sent.append(
            (request.url, request.headers.get("Authorization"))
        )
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response._content = b""
        return response

    def close(self):
        pass


def _recorded(client: TracClient) -> _Recorder:
    recorder = _Recorder()
    client.session.mount("http://", recorder)
    client.session.mount("https://", recorder)
    return recorder


class TestReviewFindings:
    def test_idn_trac_url_keeps_credentials_on_its_own_requests(self):
        """Finding 2: preparation punycodes the host, so the raw
        ``trac_url`` never matched its own requests and every RPC call
        went out anonymous."""
        client = TracClient(
            Config(
                trac_url="http://bücher.example/x",
                username=USERNAME,
                password=PASSWORD,
            )
        )
        recorder = _recorded(client)
        client.session.post(client.rpc_url, data=b"", timeout=5)
        assert recorder.sent == [
            ("http://xn--bcher-kva.example/x/login/rpc", BASIC)
        ]

    def test_scoped_https_upgrade_of_an_http_base_is_in_scope(self):
        """Finding 3: a server that redirects RPC from http to https on
        the default ports must not lock a scoped client out; the
        credential rule already treats that upgrade as the same
        origin."""
        client = _client_at("http://trac.lan/x", {"http://trac.lan/x"})
        recorder = _recorded(client)
        client.session.post(
            "https://trac.lan/x/login/rpc", data=b"", timeout=5
        )
        assert recorder.sent == [
            ("https://trac.lan/x/login/rpc", BASIC)
        ]

    @pytest.mark.parametrize(
        "url",
        [
            "https://trac.lan:8443/x/login/rpc",
            "https://trac.lan/y/login/rpc",
            "https://other.lan/x/login/rpc",
        ],
        ids=["explicit-port", "other-instance", "other-host"],
    )
    def test_the_upgrade_admits_nothing_else(self, url):
        from trac_mcp_server.core.scope import DestinationOutOfScope

        client = _client_at("http://trac.lan/x", {"http://trac.lan/x"})
        recorder = _recorded(client)
        with pytest.raises(DestinationOutOfScope):
            client.session.post(url, data=b"", timeout=5)
        assert recorder.sent == []

    def test_a_refusal_inside_any_tool_is_instance_not_allowed(self):
        """Finding 4: the session can refuse inside any tool, not only
        the probe; the dispatch choke point maps it to the same error
        type #110 gives a refusal up front."""
        from trac_mcp_server.mcp.tools.registry import (
            ToolRegistry,
            ToolSpec,
        )

        client = _client_at("http://trac.lan/x", {"http://trac.lan/x"})
        recorder = _recorded(client)

        async def handler(client, args):
            client.session.get("http://trac.lan/y/ticket/1", timeout=5)

        spec = ToolSpec(
            tool=types.Tool(
                name="probe_tool",
                description="t",
                inputSchema={"type": "object", "properties": {}},
            ),
            permissions=frozenset(),
            handler=handler,
        )
        result = asyncio.run(
            ToolRegistry([spec]).call_tool("probe_tool", {}, client)
        )
        assert result.isError
        assert "instance_not_allowed" in result.content[0].text
        assert recorder.sent == []

    def test_probe_and_session_share_one_scope_derivation(self, own):
        """Finding 5: an allowlist given as a plain set is enforced by
        the session, so the probe's up-front pass must see it too --
        otherwise a refused href costs a cap slot."""
        client = _client(own, {f"{own.base}/trac_test"})
        refused = f"{own.base}/auto_pm{WIKI_TARGET}"
        allowed = f"{own.base}/trac_test{WIKI_TARGET}"
        result = probe_targets(
            client, [refused, allowed], cap=1, timeout=5
        )
        assert result[refused]["reason"] == "instance_not_allowed"
        assert result[allowed]["status"] == EXISTS
        assert own.paths() == [f"/trac_test{WIKI_TARGET}"]


def _client_at(trac_url: str, allowed: set[str]) -> TracClient:
    return TracClient(
        Config(
            trac_url=trac_url,
            username=USERNAME,
            password=PASSWORD,
            allowed_instances=frozenset(allowed),
        )
    )
