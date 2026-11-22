"""Ticket #267: the link probe does not follow a redirect off the origin
of the href it was asked about.

#118 holds every hop of a *scoped* client inside its allowlist and keeps
credentials on the client's own origin. An UNSCOPED client still followed
a redirect to any host and reported the link from what that third host
answered: an authored ``https://trac.example.org/intertrac/wiki%3AX``
that 302s to ``https://elsewhere.example/...`` came back ``exists`` or
``missing`` on a stranger's say-so.

Now the probe follows hop by hop and refuses, unsent, a hop whose origin
differs from the ORIGINAL href's. The href is then ``skipped`` with reason
``redirect_left_origin`` (advisory ``target_check_redirected``), never a
verdict.

Unit rows drive a real ``TracClient`` session over a scripted transport
adapter (real ``send()``, real ``Response.next``, real relative-Location
resolution); the substrate row uses two real listeners
(auto_pm:wiki:Rules/testing/RealSubstrateNotMocks).
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
import requests.adapters

from trac_mcp_server.config import Config
from trac_mcp_server.core.client import TracClient
from trac_mcp_server.core.scope import MAX_REDIRECTS
from trac_mcp_server.preview.checks import build_warnings
from trac_mcp_server.preview.facts import extract_facts
from trac_mcp_server.preview.gate import ADVISORY_CODES, is_blocking
from trac_mcp_server.preview.targets import (
    ERROR,
    EXISTS,
    MISSING,
    SKIPPED,
    RedirectLeftOrigin,
    _instance_answers,
    probe_targets,
)

BASE = "https://trac.example.org"
OTHER = "https://elsewhere.example"
PAGE = (
    '<html><body><div id="content" class="wiki view narrow">'
    "<h1>X</h1></div></body></html>"
)


class _Script(requests.adapters.BaseAdapter):
    """Answers each URL from ``routes`` as ``(status, headers, body)``,
    records every request that reaches the wire, and 404s the rest."""

    def __init__(self):
        super().__init__()
        self.routes: dict[str, tuple[int, dict[str, str], str]] = {}
        self.sent: list[str] = []
        self.kwargs: list[dict] = []

    def send(self, request, **kwargs):
        self.sent.append(request.url)
        self.kwargs.append(kwargs)
        status, headers, body = self.routes.get(
            request.url, (404, {}, "")
        )
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers)
        response.url = request.url
        response.request = request
        response._content = body.encode()
        return response

    def close(self):
        pass


def _redirect(location: str) -> tuple[int, dict[str, str], str]:
    return (302, {"Location": location}, "")


def _ok(body: str = PAGE) -> tuple[int, dict[str, str], str]:
    return (200, {}, body)


def _scripted(
    allowed: frozenset[str] | None = None,
    trac_url: str = f"{BASE}/trac_test",
) -> tuple[TracClient, _Script]:
    client = TracClient(
        Config(
            trac_url=trac_url,
            username="agent_rpc",
            password="pw",
            allowed_instances=allowed,
        )
    )
    script = _Script()
    make = client._create_session

    def create_with_script():
        # The probe reads `client.session` inside each worker thread,
        # and the session is thread-local, so the adapter goes on every
        # session the client creates, not on the caller's alone.
        session = make()
        session.mount("http://", script)
        session.mount("https://", script)
        return session

    client._create_session = create_with_script
    return client, script


HREF = f"{BASE}/trac_test/intertrac/wiki%3AX"


class TestHopByHop:
    def test_a_same_origin_redirect_is_followed(self):
        client, script = _scripted()
        script.routes[HREF] = _redirect(f"{BASE}/trac_test/wiki/X")
        script.routes[f"{BASE}/trac_test/wiki/X"] = _ok()
        result = probe_targets(client, [HREF], timeout=5)
        assert result[HREF] == {
            "status": EXISTS,
            "resolved_url": f"{BASE}/trac_test/wiki/X",
        }
        assert script.sent == [HREF, f"{BASE}/trac_test/wiki/X"]

    def test_a_relative_location_is_followed(self):
        client, script = _scripted()
        script.routes[HREF] = _redirect("/trac_test/wiki/X")
        script.routes[f"{BASE}/trac_test/wiki/X"] = _ok()
        result = probe_targets(client, [HREF], timeout=5)
        assert result[HREF] == {
            "status": EXISTS,
            "resolved_url": f"{BASE}/trac_test/wiki/X",
        }

    def test_an_http_to_https_upgrade_on_default_ports_is_followed(
        self,
    ):
        href = "http://trac.example.org/trac_test/intertrac/wiki%3AX"
        client, script = _scripted()
        script.routes[href] = _redirect(f"{BASE}/trac_test/wiki/X")
        script.routes[f"{BASE}/trac_test/wiki/X"] = _ok()
        result = probe_targets(client, [href], timeout=5)
        assert result[href]["status"] == EXISTS
        assert result[href]["resolved_url"] == (
            f"{BASE}/trac_test/wiki/X"
        )

    def test_a_cross_origin_redirect_is_refused_unsent(self):
        client, script = _scripted()
        script.routes[HREF] = _redirect(f"{OTHER}/wiki/X")
        script.routes[f"{OTHER}/wiki/X"] = _ok()
        result = probe_targets(client, [HREF], timeout=5)
        assert result[HREF] == {
            "status": SKIPPED,
            "resolved_url": None,
            "reason": "redirect_left_origin",
        }
        assert script.sent == [HREF]

    def test_a_different_port_is_a_different_origin(self):
        client, script = _scripted()
        script.routes[HREF] = _redirect(f"{BASE}:8443/wiki/X")
        result = probe_targets(client, [HREF], timeout=5)
        assert result[HREF]["reason"] == "redirect_left_origin"
        assert script.sent == [HREF]

    def test_a_chain_is_compared_with_the_original_origin(self):
        """A -> A -> B is refused at B, though the second hop's own
        origin is the same as the first's."""
        mid = f"{BASE}/trac_test/mid"
        client, script = _scripted()
        script.routes[HREF] = _redirect(mid)
        script.routes[mid] = _redirect(f"{OTHER}/wiki/X")
        script.routes[f"{OTHER}/wiki/X"] = _ok()
        result = probe_targets(client, [HREF], timeout=5)
        assert result[HREF]["status"] == SKIPPED
        assert result[HREF]["reason"] == "redirect_left_origin"
        assert script.sent == [HREF, mid]

    def test_a_chain_back_to_the_origin_after_leaving_is_not_reached(
        self,
    ):
        """The refusal is at the first hop that leaves; what B would
        have redirected to is never asked."""
        client, script = _scripted()
        script.routes[HREF] = _redirect(f"{OTHER}/back")
        script.routes[f"{OTHER}/back"] = _redirect(
            f"{BASE}/trac_test/wiki/X"
        )
        result = probe_targets(client, [HREF], timeout=5)
        assert result[HREF]["reason"] == "redirect_left_origin"
        assert script.sent == [HREF]

    def test_exactly_max_redirects_hops_are_followed(self):
        client, script = _scripted()
        urls = [HREF] + [
            f"{BASE}/trac_test/hop{i}" for i in range(MAX_REDIRECTS)
        ]
        for here, there in zip(urls, urls[1:], strict=False):
            script.routes[here] = _redirect(there)
        script.routes[urls[-1]] = _ok()
        result = probe_targets(client, [HREF], timeout=5)
        assert result[HREF]["status"] == EXISTS
        assert script.sent == urls

    def test_more_than_max_redirects_hops_is_an_error(self):
        client, script = _scripted()
        urls = [HREF] + [
            f"{BASE}/trac_test/hop{i}" for i in range(MAX_REDIRECTS + 1)
        ]
        for here, there in zip(urls, urls[1:], strict=False):
            script.routes[here] = _redirect(there)
        script.routes[urls[-1]] = _ok()
        result = probe_targets(client, [HREF], timeout=5)
        assert result[HREF] == {"status": ERROR, "resolved_url": None}
        assert len(script.sent) == MAX_REDIRECTS + 1

    def test_the_exception_names_the_hop(self):
        from trac_mcp_server.preview.targets import _get

        client, script = _scripted()
        script.routes[HREF] = _redirect(f"{OTHER}/wiki/X")
        with pytest.raises(RedirectLeftOrigin) as caught:
            _get(client, HREF, 5)
        assert caught.value.url == HREF
        assert caught.value.location == f"{OTHER}/wiki/X"

    def test_a_later_hop_keeps_the_environment_tls_and_proxy_settings(
        self, monkeypatch, tmp_path
    ):
        """``session.send`` does not merge the environment the way
        ``session.request`` does, so a hand-followed hop must be given
        the settings the first request got: a corporate CA bundle named
        in the environment applies to every hop, not just the first."""
        bundle = str(tmp_path / "ca.pem")
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", bundle)
        monkeypatch.setenv("CURL_CA_BUNDLE", bundle)
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        client, script = _scripted()
        script.routes[HREF] = _redirect(f"{BASE}/trac_test/wiki/X")
        script.routes[f"{BASE}/trac_test/wiki/X"] = _ok()
        probe_targets(client, [HREF], timeout=5)
        assert script.sent == [HREF, f"{BASE}/trac_test/wiki/X"]
        first, second = script.kwargs
        assert first["verify"] == bundle
        assert second["verify"] == bundle
        assert second["proxies"] == first["proxies"]
        assert (
            second["proxies"].get("https")
            == "http://proxy.example:3128"
        )
        assert second["cert"] == first["cert"]
        assert second["stream"] is False


class TestUnderAnAllowlist:
    def test_a_same_origin_hop_outside_the_allowlist_is_not_allowed(
        self,
    ):
        client, script = _scripted(
            allowed=frozenset({f"{BASE}/trac_test"})
        )
        script.routes[HREF] = _redirect(f"{BASE}/auto_pm/wiki/X")
        result = probe_targets(client, [HREF], timeout=5)
        assert result[HREF] == {
            "status": SKIPPED,
            "resolved_url": None,
            "reason": "instance_not_allowed",
        }
        assert script.sent == [HREF]

    def test_a_cross_origin_hop_under_an_allowlist_is_redirected_not_denied(
        self,
    ):
        """The origin rule runs before the session's scope check, so a
        scoped client reports the same reason an unscoped one does."""
        client, script = _scripted(
            allowed=frozenset({f"{BASE}/trac_test"})
        )
        script.routes[HREF] = _redirect(f"{OTHER}/wiki/X")
        result = probe_targets(client, [HREF], timeout=5)
        assert result[HREF]["reason"] == "redirect_left_origin"
        assert script.sent == [HREF]


class TestTheControl:
    CONTROL = f"{BASE}/trac_test/intertrac/wiki%3ATracMcpServerProbeLivenessControl"
    TICKET = f"{BASE}/trac_test/intertrac/%23999999"

    def test_an_off_origin_redirect_is_no_answer(self):
        client, script = _scripted()
        script.routes[self.CONTROL] = _redirect(f"{OTHER}/wiki/Y")
        script.routes[f"{OTHER}/wiki/Y"] = _ok()
        assert (
            _instance_answers(client, self.CONTROL, 5) == "redirected"
        )
        assert script.sent == [self.CONTROL]

    def test_a_non_200_control_is_no_answer(self):
        client, script = _scripted()
        script.routes[self.CONTROL] = (500, {}, "")
        assert _instance_answers(client, self.CONTROL, 5) == "no_answer"

    def test_a_same_origin_redirect_is_an_answer(self):
        client, script = _scripted()
        script.routes[self.CONTROL] = _redirect("/trac_test/wiki/Y")
        script.routes[f"{BASE}/trac_test/wiki/Y"] = _ok()
        assert _instance_answers(client, self.CONTROL, 5) == "answers"

    def test_a_provisional_missing_degrades_when_the_control_leaves(
        self,
    ):
        client, script = _scripted()
        script.routes[self.TICKET] = (500, {}, "")
        script.routes[self.CONTROL] = _redirect(f"{OTHER}/wiki/Y")
        script.routes[f"{OTHER}/wiki/Y"] = _ok()
        result = probe_targets(client, [self.TICKET], timeout=5)
        assert result[self.TICKET] == {
            "status": SKIPPED,
            "resolved_url": None,
            "reason": "redirect_left_origin",
        }
        assert OTHER + "/wiki/Y" not in script.sent

    def test_a_provisional_missing_with_no_answer_is_still_error(self):
        client, script = _scripted()
        script.routes[self.TICKET] = (500, {}, "")
        script.routes[self.CONTROL] = (503, {}, "")
        result = probe_targets(client, [self.TICKET], timeout=5)
        assert result[self.TICKET] == {
            "status": ERROR,
            "resolved_url": None,
        }

    def test_a_provisional_missing_stands_when_the_control_answers(
        self,
    ):
        client, script = _scripted()
        script.routes[self.TICKET] = (500, {}, "")
        script.routes[self.CONTROL] = _ok()
        result = probe_targets(client, [self.TICKET], timeout=5)
        assert result[self.TICKET] == {
            "status": MISSING,
            "resolved_url": None,
        }

    def test_the_redirected_control_reports_target_check_redirected(
        self,
    ):
        client, script = _scripted()
        script.routes[self.TICKET] = (500, {}, "")
        script.routes[self.CONTROL] = _redirect(f"{OTHER}/wiki/Y")
        probes = probe_targets(client, [self.TICKET], timeout=5)
        anchor = (
            '<p><a class="ext-link" href="' + self.TICKET + '"'
            ' title="ticket 999999">somewhere:#999999</a></p>'
        )
        warnings = build_warnings(
            markdown_source=None,
            tracwiki="somewhere:#999999",
            facts=extract_facts(anchor),
            probes=probes,
            check_targets=True,
            source_format="tracwiki",
        )
        codes = [w["code"] for w in warnings]
        assert "target_check_redirected" in codes, codes
        assert "target_check_failed" not in codes, codes


# ---------------------------------------------------------------------------
# Checks and gate
# ---------------------------------------------------------------------------

_ANCHOR_HTML = (
    '<p><a class="ext-link" href="' + HREF + '"'
    ' title="wiki:X in Somewhere">somewhere:wiki:X</a></p>'
)


def _warnings(probes):
    return build_warnings(
        markdown_source=None,
        tracwiki="somewhere:wiki:X",
        facts=extract_facts(_ANCHOR_HTML),
        probes=probes,
        check_targets=True,
        source_format="tracwiki",
    )


class TestChecks:
    REDIRECTED = {
        HREF: {
            "status": SKIPPED,
            "resolved_url": None,
            "reason": "redirect_left_origin",
        }
    }

    def test_a_redirect_left_origin_skip_is_its_own_advisory_code(self):
        warnings = _warnings(self.REDIRECTED)
        codes = [w["code"] for w in warnings]
        assert "target_check_redirected" in codes, codes
        assert "target_check_capped" not in codes, codes
        assert "target_check_not_allowed" not in codes, codes
        (found,) = [
            w
            for w in warnings
            if w["code"] == "target_check_redirected"
        ]
        assert found["severity"] == "info"
        assert not is_blocking(found)
        assert found["evidence"]["hrefs"] == [HREF]
        assert "target_check_redirected" in ADVISORY_CODES

    def test_a_plain_skip_is_still_capped(self):
        codes = [
            w["code"]
            for w in _warnings(
                {HREF: {"status": SKIPPED, "resolved_url": None}}
            )
        ]
        assert "target_check_capped" in codes, codes
        assert "target_check_redirected" not in codes, codes

    def test_a_not_allowed_skip_is_still_not_allowed(self):
        codes = [
            w["code"]
            for w in _warnings(
                {
                    HREF: {
                        "status": SKIPPED,
                        "resolved_url": None,
                        "reason": "instance_not_allowed",
                    }
                }
            )
        ]
        assert "target_check_not_allowed" in codes, codes
        assert "target_check_redirected" not in codes, codes


# ---------------------------------------------------------------------------
# Real substrate: two listeners on two ports, one client, one probe
# ---------------------------------------------------------------------------


class _Listener:
    """A real listener recording every path it is asked for."""

    def __init__(self, routes=None):
        self.paths: list[str] = []
        self.routes: dict[str, tuple[int, dict[str, str]]] = (
            routes or {}
        )
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                listener.paths.append(self.path)
                status, headers = listener.routes.get(
                    self.path, (200, {})
                )
                body = PAGE.encode() if status == 200 else b""
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(
            target=self.server.serve_forever, daemon=True
        ).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TestRealListeners:
    def test_the_probe_never_reaches_the_other_origin(self):
        b = _Listener()
        a = _Listener()
        try:
            a.routes["/intertrac/wiki%3AX"] = (
                302,
                {"Location": f"{b.base}/wiki/X"},
            )
            a.routes["/intertrac/wiki%3AY"] = (
                302,
                {"Location": f"{a.base}/wiki/Y"},
            )
            client = TracClient(
                Config(
                    trac_url=f"{a.base}/trac_test",
                    username="agent_rpc",
                    password="pw",
                )
            )
            x = f"{a.base}/intertrac/wiki%3AX"
            y = f"{a.base}/intertrac/wiki%3AY"
            result = probe_targets(client, [x, y], timeout=5)
            assert b.paths == []
            assert result[x] == {
                "status": SKIPPED,
                "resolved_url": None,
                "reason": "redirect_left_origin",
            }
            assert result[y] == {
                "status": EXISTS,
                "resolved_url": f"{a.base}/wiki/Y",
            }
            assert "/wiki/Y" in a.paths
        finally:
            a.close()
            b.close()
