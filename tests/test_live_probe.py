"""Offline tests for the live run's /trac_test probe (Trac
/trac_mcp_server #224).

When ``/trac_test``'s origin is wrong (``TRAC_TEST_URL`` unset, so the
URL is derived from ``TRAC_URL``'s host and port and answers 308 to
another port), ``pytest tests/ --run-live`` produced dozens of failures
and errors and no line naming the cause, because the sweep fixture
swallows the error. ``tests.live_helpers.probe_trac_test`` asks the
instance one XML-RPC question before the run; ``tests.conftest``'s
``pytest_collection_modifyitems`` ends the run with the returned line
and exit code 2 ("not run").

The probe is tested against real local HTTP servers, each answering
one fixed way; the hook is tested with fake config and items.
"""

import http.server
import socket
import threading
import xmlrpc.client

import pytest

import tests.conftest as conftest_module
import tests.live_helpers as live_helpers
from tests.live_helpers import probe_trac_test

PASSWORD = "s3cret-pw"


def _serve(status, headers=(), body=b""):
    """A running server that answers every POST the same way.

    Returns ``(server, seen)``; ``seen`` collects the request bodies.
    """
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            seen.append(self.rfile.read(length))
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.seen = seen
    return server


@pytest.fixture
def serve():
    servers = []

    def start(status, headers=(), body=b""):
        server = _serve(status, headers, body)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _origin(server):
    return f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def env(monkeypatch):
    """Credentials set, TRAC_TEST_URL unset; returns ``set_test_url``."""
    monkeypatch.setenv("TRAC_URL", "http://127.0.0.1:9/some_project")
    monkeypatch.setenv("TRAC_USERNAME", "agent")
    monkeypatch.setenv("TRAC_PASSWORD", PASSWORD)
    monkeypatch.delenv("TRAC_TEST_URL", raising=False)
    monkeypatch.delenv("TRAC_INSECURE", raising=False)

    def set_test_url(value):
        monkeypatch.setenv("TRAC_TEST_URL", value)

    return set_test_url


def _closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# ---------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------


def test_redirect_names_url_code_location_and_variable(serve, env):
    server = serve(
        308,
        [("Location", "http://127.0.0.1:1/trac_test/login/rpc")],
    )
    url = f"{_origin(server)}/trac_test"
    env(url)
    line = probe_trac_test()
    assert line is not None
    assert "\n" not in line
    assert url in line
    assert "308" in line
    assert "http://127.0.0.1:1/trac_test/login/rpc" in line
    assert "TRAC_TEST_URL" in line
    # the probe sent the XML-RPC question, to the client's rpc url
    assert b"system.getAPIVersion" in server.seen[0]


def test_redirect_without_location(serve, env):
    server = serve(301)
    env(f"{_origin(server)}/trac_test")
    line = probe_trac_test()
    assert "301" in line
    assert "(no Location)" in line


def test_redirect_location_userinfo_is_stripped(serve, env):
    server = serve(
        308,
        [("Location", "http://who:hunter2@127.0.0.1:1/trac_test/x")],
    )
    env(f"{_origin(server)}/trac_test")
    line = probe_trac_test()
    assert "hunter2" not in line
    assert "who:" not in line
    assert "http://127.0.0.1:1/trac_test/x" in line


def test_unauthorized(serve, env):
    server = serve(401)
    env(f"{_origin(server)}/trac_test")
    line = probe_trac_test()
    assert line is not None
    assert "401" in line
    assert "TRAC_TEST_URL" in line


def test_html_page_is_not_an_xmlrpc_reply(serve, env):
    server = serve(
        200,
        [("Content-Type", "text/html; charset=utf-8")],
        b"<html><body>hello</body></html>",
    )
    env(f"{_origin(server)}/trac_test")
    line = probe_trac_test()
    assert "not an XML-RPC reply" in line
    assert "text/html; charset=utf-8" in line


def test_html_page_without_content_type(serve, env):
    server = serve(200, [], b"plain, not xml")
    env(f"{_origin(server)}/trac_test")
    line = probe_trac_test()
    assert "not an XML-RPC reply" in line
    assert "Content-Type: none" in line


def test_valid_reply_is_none(serve, env):
    body = xmlrpc.client.dumps(([1, 2, 0],), methodresponse=True)
    server = serve(200, [("Content-Type", "text/xml")], body.encode())
    env(f"{_origin(server)}/trac_test")
    assert probe_trac_test() is None


def test_xmlrpc_fault_still_counts_as_a_server(serve, env):
    body = xmlrpc.client.dumps(
        xmlrpc.client.Fault(1, "no such method"), methodresponse=True
    )
    server = serve(200, [("Content-Type", "text/xml")], body.encode())
    env(f"{_origin(server)}/trac_test")
    assert probe_trac_test() is None


def test_closed_port_is_unreachable(env):
    env(f"http://127.0.0.1:{_closed_port()}/trac_test")
    line = probe_trac_test()
    assert line is not None
    assert "unreachable" in line
    assert "TRAC_TEST_URL" in line


def test_userinfo_in_test_url_never_appears(serve, env):
    server = serve(
        308,
        [("Location", "http://127.0.0.1:1/trac_test/login/rpc")],
    )
    port = server.server_address[1]
    env(f"http://user:s3cret@127.0.0.1:{port}/trac_test")
    line = probe_trac_test()
    assert line is not None
    assert "308" in line
    assert "s3cret" not in line
    assert "user:" not in line
    assert PASSWORD not in line
    assert f"http://127.0.0.1:{port}/trac_test" in line


def test_malformed_test_url_names_variable_not_value(env):
    env("127.0.0.1:9/trac_test")
    line = probe_trac_test()
    assert line is not None
    assert "\n" not in line
    assert "TRAC_TEST_URL" in line
    assert "127.0.0.1:9/trac_test" not in line


def test_unset_test_url_says_derived(serve, env, monkeypatch):
    server = serve(
        308,
        [("Location", "http://127.0.0.1:1/trac_test/login/rpc")],
    )
    monkeypatch.setenv("TRAC_URL", f"{_origin(server)}/some_project")
    line = probe_trac_test()
    assert line is not None
    assert f"{_origin(server)}/trac_test" in line
    assert "308" in line
    assert "unset, derived from TRAC_URL" in line


def test_set_test_url_says_set(serve, env):
    server = serve(401)
    env(f"{_origin(server)}/trac_test")
    line = probe_trac_test()
    assert "it is set" in line
    assert "unset" not in line


@pytest.mark.parametrize(
    "url, expected",
    [
        ("http://u:p@host:8080/x", "http://host:8080/x"),
        ("https://u@host/x", "https://host/x"),
        ("http://host/x", "http://host/x"),
        ("http://u:p@[::1]:8080/x?q=1", "http://[::1]:8080/x?q=1"),
        ("http://u:p@[::1]/x", "http://[::1]/x"),
    ],
)
def test_strip_userinfo(url, expected):
    assert live_helpers.strip_userinfo(url) == expected


# ---------------------------------------------------------------------
# The conftest hook
# ---------------------------------------------------------------------


class _Config:
    def __init__(self, run_live):
        self.run_live = run_live

    def getoption(self, name):
        assert name == "--run-live"
        return self.run_live


class _Item:
    def __init__(self, live):
        self.keywords = {"live": True} if live else {"other": True}
        self.markers = []

    def add_marker(self, marker):
        self.markers.append(marker)


@pytest.fixture
def probe(monkeypatch):
    """Replace the probe; returns the list of its calls."""
    calls = []
    answer = {"line": None}

    def fake():
        calls.append(1)
        return answer["line"]

    monkeypatch.setattr(live_helpers, "probe_trac_test", fake)
    fake.answer = answer
    fake.calls = calls
    return fake


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("TRAC_URL", "http://127.0.0.1:9/p")
    monkeypatch.setenv("TRAC_USERNAME", "agent")
    monkeypatch.setenv("TRAC_PASSWORD", PASSWORD)


def test_hook_exits_2_with_the_line(probe, creds):
    probe.answer["line"] = "live suite not run: X answered 308"
    with pytest.raises(pytest.exit.Exception) as caught:
        conftest_module.pytest_collection_modifyitems(
            _Config(True), [_Item(True), _Item(False)]
        )
    assert caught.value.returncode == 2
    assert caught.value.msg == "live suite not run: X answered 308"


def test_hook_continues_when_probe_is_clean(probe, creds):
    items = [_Item(True), _Item(False)]
    conftest_module.pytest_collection_modifyitems(_Config(True), items)
    assert probe.calls == [1]
    assert all(item.markers == [] for item in items)


def test_hook_does_not_probe_without_a_live_item(probe, creds):
    probe.answer["line"] = "would exit"
    conftest_module.pytest_collection_modifyitems(
        _Config(True), [_Item(False)]
    )
    assert probe.calls == []


@pytest.mark.parametrize(
    "missing", ["TRAC_URL", "TRAC_USERNAME", "TRAC_PASSWORD"]
)
def test_hook_does_not_probe_without_credentials(
    probe, creds, monkeypatch, missing
):
    probe.answer["line"] = "would exit"
    monkeypatch.delenv(missing)
    conftest_module.pytest_collection_modifyitems(
        _Config(True), [_Item(True)]
    )
    assert probe.calls == []


def test_hook_does_not_probe_without_run_live(probe, creds):
    probe.answer["line"] = "would exit"
    live, other = _Item(True), _Item(False)
    conftest_module.pytest_collection_modifyitems(
        _Config(False), [live, other]
    )
    assert probe.calls == []
    assert len(live.markers) == 1  # skipped, as before
    assert other.markers == []
