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

import dataclasses
import http.server
import os
import socket
import subprocess
import sys
import threading
import xmlrpc.client
from pathlib import Path

import pytest

import tests.conftest as conftest_module
import tests.live_helpers as live_helpers
from tests.live_helpers import probe_trac_test

PASSWORD = "s3cret-pw"


def _serve(status, headers=(), body=b""):
    """A running server that answers every POST the same way.

    Returns the running server, with ``.seen`` attached: the list of
    request bodies it received.
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


@pytest.mark.parametrize("code", [401, 403])
def test_unauthorized_advises_the_credentials(serve, env, code):
    server = serve(code)
    url = f"{_origin(server)}/trac_test"
    env(url)
    line = probe_trac_test()
    assert line is not None
    assert url in line
    assert f"answered HTTP {code}" in line
    assert "check TRAC_USERNAME/TRAC_PASSWORD" in line
    assert "XML_RPC" in line
    assert "TRAC_TEST_URL" not in line
    assert PASSWORD not in line


@pytest.mark.parametrize("code", [404, 500])
def test_other_status_keeps_the_url_advice(serve, env, code):
    server = serve(code)
    env(f"{_origin(server)}/trac_test")
    line = probe_trac_test()
    assert f"answered HTTP {code}" in line
    assert "set TRAC_TEST_URL in .env" in line
    assert "it is set" in line


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


def test_xmlrpc_fault_is_a_failure_with_credentials_advice(serve, env):
    body = xmlrpc.client.dumps(
        xmlrpc.client.Fault(403, "XML_RPC privileges are required"),
        methodresponse=True,
    )
    server = serve(200, [("Content-Type", "text/xml")], body.encode())
    url = f"{_origin(server)}/trac_test"
    env(url)
    line = probe_trac_test()
    assert line is not None
    assert "\n" not in line
    assert url in line
    assert (
        "answered XML-RPC fault 403: XML_RPC privileges are required"
        in line
    )
    assert "check TRAC_USERNAME/TRAC_PASSWORD" in line


def test_fault_string_is_truncated_to_one_line(serve, env):
    body = xmlrpc.client.dumps(
        xmlrpc.client.Fault(1, "x" * 50 + "\n" + "y" * 500),
        methodresponse=True,
    )
    server = serve(200, [("Content-Type", "text/xml")], body.encode())
    env(f"{_origin(server)}/trac_test")
    line = probe_trac_test()
    assert "\n" not in line
    assert "y" * 60 in line
    assert "y" * 121 not in line


def test_tls_failure_advises_the_certificate(serve, env):
    # an https URL on a plain-http port: the handshake fails for real
    server = serve(200)
    port = server.server_address[1]
    env(f"https://127.0.0.1:{port}/trac_test")
    line = probe_trac_test()
    assert line is not None
    assert "SSLError" in line
    assert "check the certificate or set TRAC_INSECURE" in line
    assert f"https://127.0.0.1:{port}/trac_test" in line


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


def test_userinfo_without_a_host_is_malformed_and_never_echoed(env):
    env("http://u:pw@:8080/trac_test")
    line = probe_trac_test()
    assert line is not None
    assert "TRAC_TEST_URL" in line
    assert "pw" not in line
    assert "u:" not in line


def test_location_with_userinfo_and_no_host_is_stripped(serve, env):
    server = serve(308, [("Location", "http://who:pw@/trac_test")])
    env(f"{_origin(server)}/trac_test")
    line = probe_trac_test()
    assert "308" in line
    assert "pw" not in line
    assert "who" not in line


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
    server = serve(500)
    env(f"{_origin(server)}/trac_test")
    line = probe_trac_test()
    assert "it is set" in line
    assert "unset" not in line


def test_allowlist_refusal_is_one_line(serve, env, monkeypatch):
    """A client whose allowlist excludes /trac_test refuses the probe
    request unsent (``DestinationOutOfScope``, not a RequestException).

    ``trac_test_config()`` sets no allowlist today, so the real session
    refusal is reached by handing the probe a config that has one.
    """
    server = serve(401)
    url = f"{_origin(server)}/trac_test"
    env(url)
    real = live_helpers.trac_test_config()
    monkeypatch.setattr(
        live_helpers,
        "trac_test_config",
        lambda: dataclasses.replace(
            real,
            allowed_instances=frozenset(
                {"http://127.0.0.1:1/elsewhere"}
            ),
        ),
    )
    line = probe_trac_test()
    assert line is not None
    assert "\n" not in line
    assert "allowlist" in line
    assert url in line
    assert "the client's allowed instances exclude it" in line
    assert server.seen == []  # refused before anything was sent


@pytest.mark.parametrize(
    "url, expected",
    [
        ("http://u:p@host:8080/x", "http://host:8080/x"),
        ("https://u@host/x", "https://host/x"),
        ("http://host/x", "http://host/x"),
        ("http://u:p@[::1]:8080/x?q=1", "http://[::1]:8080/x?q=1"),
        ("http://u:p@[::1]/x", "http://[::1]/x"),
        ("http://u:pw@:8080/trac_test", "http://:8080/trac_test"),
        ("http://who:pw@/trac_test", "http:///trac_test"),
        ("http://a@b@host/x", "http://host/x"),
        ("//u:pw@host/x", "//host/x"),
        ("/trac_test/x", "/trac_test/x"),
        ("http://[bad:pw@x/", "(unparseable URL)"),
    ],
)
def test_strip_userinfo(url, expected):
    assert live_helpers.strip_userinfo(url) == expected


# ---------------------------------------------------------------------
# live_credentials_set
# ---------------------------------------------------------------------


def test_live_credentials_set(env, monkeypatch):
    assert live_helpers.live_credentials_set() is True
    for var in ("TRAC_URL", "TRAC_USERNAME", "TRAC_PASSWORD"):
        monkeypatch.setenv(var, "")
        assert live_helpers.live_credentials_set() is False
        monkeypatch.delenv(var)
        assert live_helpers.live_credentials_set() is False
        monkeypatch.setenv(var, "x")


# ---------------------------------------------------------------------
# The conftest hook (fake config and items)
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


class _Session:
    def __init__(self, run_live, items):
        self.config = _Config(run_live)
        self.items = items


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


def test_finish_exits_2_with_the_line(probe, creds):
    probe.answer["line"] = "live suite not run: X answered 308"
    with pytest.raises(pytest.exit.Exception) as caught:
        conftest_module.pytest_collection_finish(
            _Session(True, [_Item(True), _Item(False)])
        )
    assert caught.value.returncode == 2
    assert caught.value.msg == "live suite not run: X answered 308"


def test_finish_continues_when_probe_is_clean(probe, creds):
    conftest_module.pytest_collection_finish(
        _Session(True, [_Item(True), _Item(False)])
    )
    assert probe.calls == [1]


def test_finish_does_not_probe_without_a_live_item(probe, creds):
    probe.answer["line"] = "would exit"
    conftest_module.pytest_collection_finish(
        _Session(True, [_Item(False)])
    )
    assert probe.calls == []


@pytest.mark.parametrize(
    "missing", ["TRAC_URL", "TRAC_USERNAME", "TRAC_PASSWORD"]
)
def test_finish_does_not_probe_without_credentials(
    probe, creds, monkeypatch, missing
):
    probe.answer["line"] = "would exit"
    monkeypatch.delenv(missing)
    conftest_module.pytest_collection_finish(
        _Session(True, [_Item(True)])
    )
    assert probe.calls == []


def test_finish_does_not_probe_without_run_live(probe, creds):
    probe.answer["line"] = "would exit"
    conftest_module.pytest_collection_finish(
        _Session(False, [_Item(True)])
    )
    assert probe.calls == []


def test_modifyitems_never_probes(probe, creds):
    probe.answer["line"] = "would exit"
    live, other = _Item(True), _Item(False)
    conftest_module.pytest_collection_modifyitems(
        _Config(True), [live, other]
    )
    assert probe.calls == []
    assert live.markers == [] and other.markers == []


def test_modifyitems_skips_live_items_without_run_live(probe, creds):
    live, other = _Item(True), _Item(False)
    conftest_module.pytest_collection_modifyitems(
        _Config(False), [live, other]
    )
    assert probe.calls == []
    assert len(live.markers) == 1  # skipped, as before
    assert other.markers == []


# ---------------------------------------------------------------------
# The hook under real pytest: -k / -m are honoured (Trac #224 review)
# ---------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
LIVE_FILE = "tests/test_detection.py"
LIVE_ROW = "test_live_scrape_project_index"
OFFLINE_ROW = "test_strip_userinfo"


def _live_run(*args):
    """``pytest --run-live --collect-only`` in a subprocess whose Trac
    is a closed local port.

    The probe fires at collection finish, so --collect-only reaches it
    without running a row. Every TRAC_* variable is dropped and
    PYTHON_DOTENV_DISABLED is set: conftest calls ``load_dotenv()``
    under --run-live and this checkout may sit inside one that has a
    real .env, whose Trac the probe would then ask.
    """
    port = _closed_port()
    env = {
        k: v for k, v in os.environ.items() if not k.startswith("TRAC_")
    }
    env.update(
        PYTHONPATH=str(REPO / "src"),
        PYTHON_DOTENV_DISABLED="1",
        TRAC_URL=f"http://127.0.0.1:{port}/x",
        TRAC_USERNAME="agent",
        TRAC_PASSWORD="dummy-pw",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--run-live",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *args,
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result, port, result.stdout + result.stderr


def test_k_excluding_every_live_row_does_not_probe():
    result, _port, out = _live_run(
        "-k", OFFLINE_ROW, "tests/test_live_probe.py", LIVE_FILE
    )
    assert result.returncode == 0, out
    assert "live suite not run" not in out
    assert OFFLINE_ROW in out
    assert LIVE_ROW not in out


def test_m_not_live_does_not_probe():
    result, _port, out = _live_run(
        "-m", "not live", "tests/test_live_probe.py", LIVE_FILE
    )
    assert result.returncode == 0, out
    assert "live suite not run" not in out


def test_k_selecting_a_live_row_probes_and_exits_2():
    result, port, out = _live_run(
        "-k", LIVE_ROW, "tests/test_live_probe.py", LIVE_FILE
    )
    assert result.returncode == 2, out
    assert "live suite not run" in out
    # the probe asked the env's Trac, not one a .env named
    assert f"http://127.0.0.1:{port}/trac_test" in out
    assert "unreachable" in out
    assert "dummy-pw" not in out
