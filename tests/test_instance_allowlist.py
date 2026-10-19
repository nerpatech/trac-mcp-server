"""Ticket #110: a per-identity instance allowlist.

An identity's scope used to be whatever its Trac user's permissions
allowed. The per-user ``XML_RPC`` grant closes the RPC side, but two code
paths make plain HTTP requests with the identity's credentials and
``XML_RPC`` does not gate them: ``ticket_render_check``'s page fetch
(``preview/live.py``) and the cross-instance link-target probes
(``preview/targets.py``). So the allowlist is enforced at the one choke
point every instance-addressed call passes through -- ``resolve()`` --
and, separately, on the probes, whose hrefs come from rendered content
rather than from an ``instance`` argument.

Shape under test:

* ``instances:`` on a ``TRAC_IDENTITIES`` entry, and
  ``TRAC_ALLOWED_INSTANCES`` (comma-separated) for the default identity.
  Absent means unrestricted. Present but empty is a startup error, never
  "deny everything", and so is a default instance that is not on the
  default identity's own allowlist.
* Entries are a ``/path`` on the default host or an absolute URL, stored
  on ``Config.allowed_instances`` as absolute URLs with no trailing slash.
* The check runs on the RESOLVED URL, after every branch of ``resolve()``,
  so neither a declared name nor a full URL routes around it. Exact
  match: a non-canonical spelling (``/trac_test/../auto_pm``) is refused,
  never normalised into something allowed.
* A refused probe href is reported ``skipped`` with reason
  ``instance_not_allowed``, never fetched, and surfaces as the advisory
  ``target_check_not_allowed`` -- not as ``target_check_capped``, which
  blocks writes.

Every refusal test here was run against unchanged ``master`` first and
observed failing (auto_pm:wiki:Rules/testing/SeededDefectFirst).
"""

import asyncio
from unittest.mock import MagicMock, patch

import mcp.types as types
import pytest

from trac_mcp_server.config import Config, load_config
from trac_mcp_server.instances import (
    Identity,
    InstanceNotAllowedError,
    InstanceRegistry,
    InstanceSpec,
    load_identities,
)
from trac_mcp_server.mcp import server as server_module
from trac_mcp_server.mcp.server import handle_call_tool
from trac_mcp_server.mcp.tools.instances import (
    INSTANCE_SPECS,
    set_instance_registry,
)
from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.preview.checks import build_warnings
from trac_mcp_server.preview.facts import extract_facts
from trac_mcp_server.preview.gate import ADVISORY_CODES, is_blocking
from trac_mcp_server.preview.targets import SKIPPED, probe_targets

HOST = "http://192.168.10.4:8000"
TRAC_TEST = f"{HOST}/trac_test"
AUTO_PM = f"{HOST}/auto_pm"

ALLOWED_HREF = f"{TRAC_TEST}/intertrac/wiki%3AWikiStart"
REFUSED_HREF = f"{AUTO_PM}/intertrac/wiki%3AIndex"
REFUSED_TICKET_HREF = f"{AUTO_PM}/intertrac/%23179"
FOREIGN_HREF = "https://trac.edgewall.org/intertrac/%233754"

EXISTING_PAGE_STUB = '<div id="content" class="wiki view narrow"><h1>WikiStart</h1></div>'

CLOUD = Identity(
    name="cloud",
    username="cloud_rpc",
    password="cloud-pw",
    instances=("/trac_test",),
)
UNRESTRICTED = Identity(
    name="alice", username="alice-trac", password="alice-pw"
)


def _config(**overrides) -> Config:
    fields = dict(
        trac_url=TRAC_TEST, username="agent_rpc", password="secret"
    )
    fields.update(overrides)
    return Config(**fields)


@pytest.fixture(autouse=True)
def no_real_config_files():
    with patch(
        "trac_mcp_server.instances.discover_config_files",
        return_value=[],
    ):
        yield


# ---------------------------------------------------------------------------
# Configuration: TRAC_IDENTITIES `instances:` and TRAC_ALLOWED_INSTANCES
# ---------------------------------------------------------------------------


class TestIdentityInstancesKey:
    def _load(self, tmp_path, monkeypatch, body: str):
        identities_file = tmp_path / "identities.yml"
        identities_file.write_text(
            "identities:\n"
            "  cloud:\n"
            "    token: tok-cloud\n"
            "    username: cloud_rpc\n"
            "    password: cloud-pw\n" + body
        )
        monkeypatch.setenv("TRAC_IDENTITIES", str(identities_file))
        return load_identities()

    def test_absent_key_is_unrestricted(self, tmp_path, monkeypatch):
        identities = self._load(tmp_path, monkeypatch, "")
        assert identities["tok-cloud"].instances is None

    def test_list_form(self, tmp_path, monkeypatch):
        identities = self._load(
            tmp_path,
            monkeypatch,
            "    instances:\n      - /trac_test\n      - /bcs\n",
        )
        assert identities["tok-cloud"].instances == (
            "/trac_test",
            "/bcs",
        )

    def test_comma_string_form(self, tmp_path, monkeypatch):
        """``${VAR}`` interpolation yields a string, so a list held in
        ``.env`` arrives comma-separated."""
        identities = self._load(
            tmp_path, monkeypatch, "    instances: /trac_test, /bcs\n"
        )
        assert identities["tok-cloud"].instances == (
            "/trac_test",
            "/bcs",
        )

    def test_empty_list_is_a_startup_error(self, tmp_path, monkeypatch):
        with pytest.raises(ValueError, match="cloud"):
            self._load(tmp_path, monkeypatch, "    instances: []\n")

    def test_empty_string_is_a_startup_error(
        self, tmp_path, monkeypatch
    ):
        with pytest.raises(ValueError, match="cloud"):
            self._load(tmp_path, monkeypatch, "    instances: ''\n")

    def test_entry_that_is_neither_path_nor_url_is_an_error(
        self, tmp_path, monkeypatch
    ):
        with pytest.raises(ValueError, match="trac_test"):
            self._load(
                tmp_path, monkeypatch, "    instances: [trac_test]\n"
            )

    @pytest.mark.parametrize(
        "body",
        ["    instances: {/trac_test: true}\n", "    instances: 1\n"],
        ids=["mapping", "scalar"],
    )
    def test_neither_list_nor_string_is_a_value_error(
        self, tmp_path, monkeypatch, body
    ):
        """A mapping must not be read as its keys, and a scalar must be
        the documented startup error, not a TypeError (review, #110)."""
        with pytest.raises(ValueError, match="cloud"):
            self._load(tmp_path, monkeypatch, body)


class TestTracAllowedInstances:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("TRAC_URL", TRAC_TEST)
        monkeypatch.setenv("TRAC_USERNAME", "cloud_rpc")
        monkeypatch.setenv("TRAC_PASSWORD", "cloud-pw")

    def test_unset_is_unrestricted(self, monkeypatch):
        monkeypatch.delenv("TRAC_ALLOWED_INSTANCES", raising=False)
        assert load_config().allowed_instances is None

    def test_paths_normalise_to_absolute_urls(self, monkeypatch):
        monkeypatch.setenv(
            "TRAC_ALLOWED_INSTANCES", " /trac_test/ , /bcs"
        )
        assert load_config().allowed_instances == frozenset(
            {TRAC_TEST, f"{HOST}/bcs"}
        )

    def test_absolute_url_entry_accepted(self, monkeypatch):
        monkeypatch.setenv("TRAC_ALLOWED_INSTANCES", f"{TRAC_TEST}/")
        assert load_config().allowed_instances == frozenset({TRAC_TEST})

    def test_empty_is_a_startup_error(self, monkeypatch):
        monkeypatch.setenv("TRAC_ALLOWED_INSTANCES", " , ")
        with pytest.raises(ValueError, match="TRAC_ALLOWED_INSTANCES"):
            load_config()

    def test_default_outside_its_own_allowlist_is_a_startup_error(
        self, monkeypatch
    ):
        """Fail closed and loudly at startup rather than on every call."""
        monkeypatch.setenv("TRAC_ALLOWED_INSTANCES", "/bcs")
        with pytest.raises(ValueError, match="trac_test"):
            load_config()


# ---------------------------------------------------------------------------
# resolve(): the choke point
# ---------------------------------------------------------------------------


class TestResolveWithIdentityAllowlist:
    def _registry(self, declared=None) -> InstanceRegistry:
        return InstanceRegistry(_config(), declared or {})

    def test_allowed_path_resolves(self):
        config = self._registry().resolve("/trac_test", CLOUD)
        assert config.trac_url == TRAC_TEST
        assert config.username == "cloud_rpc"

    def test_default_on_the_allowlist_resolves(self):
        assert (
            self._registry().resolve(None, CLOUD).trac_url == TRAC_TEST
        )

    def test_refused_path(self):
        with pytest.raises(InstanceNotAllowedError, match="/trac_test"):
            self._registry().resolve("/auto_pm", CLOUD)

    def test_refused_same_host_url(self):
        with pytest.raises(InstanceNotAllowedError):
            self._registry().resolve(AUTO_PM, CLOUD)

    def test_declared_name_does_not_route_around_it(self):
        registry = self._registry(
            {"apm": InstanceSpec(name="apm", url="/auto_pm")}
        )
        with pytest.raises(InstanceNotAllowedError):
            registry.resolve("apm", CLOUD)

    def test_declared_explicit_credentials_do_not_route_around_it(self):
        """The allowlist is the CALLER's scope; a declared instance with
        its own credentials is still somewhere the caller asked to go."""
        registry = self._registry(
            {
                "apm": InstanceSpec(
                    name="apm",
                    url=AUTO_PM,
                    username="svc",
                    password="svc-pw",
                )
            }
        )
        with pytest.raises(InstanceNotAllowedError):
            registry.resolve("apm", CLOUD)

    def test_default_off_the_identitys_allowlist_is_refused(self):
        registry = InstanceRegistry(_config(trac_url=AUTO_PM), {})
        with pytest.raises(InstanceNotAllowedError):
            registry.resolve(None, CLOUD)

    @pytest.mark.parametrize(
        "spelling",
        ["/trac_test/../auto_pm", "/trac_test/./", "/trac_test?x=1"],
    )
    def test_non_canonical_spellings_are_refused_not_normalised(
        self, spelling
    ):
        with pytest.raises(InstanceNotAllowedError):
            self._registry().resolve(spelling, CLOUD)

    def test_trailing_slash_is_the_same_instance(self):
        config = self._registry().resolve("/trac_test/", CLOUD)
        assert config.trac_url == TRAC_TEST

    def test_resolved_config_carries_the_callers_allowlist(self):
        """So the probes, which see only the client, see the scope."""
        config = self._registry().resolve(None, CLOUD)
        assert config.allowed_instances == frozenset({TRAC_TEST})

    def test_error_is_a_value_error(self):
        """Existing dispatch sites catch ValueError; a refusal must not
        escape them as an unhandled exception."""
        assert issubclass(InstanceNotAllowedError, ValueError)


class TestResolveWithDefaultIdentityAllowlist:
    def _registry(self) -> InstanceRegistry:
        return InstanceRegistry(
            _config(allowed_instances=frozenset({TRAC_TEST})), {}
        )

    def test_default_resolves(self):
        assert self._registry().resolve(None).trac_url == TRAC_TEST

    def test_refused_path(self):
        with pytest.raises(InstanceNotAllowedError):
            self._registry().resolve("/auto_pm")

    def test_unrestricted_identity_is_not_bound_by_the_defaults_list(
        self,
    ):
        """An identity without ``instances:`` is unrestricted -- the
        default identity's list scopes the default credentials only."""
        config = self._registry().resolve("/auto_pm", UNRESTRICTED)
        assert config.trac_url == AUTO_PM
        assert config.allowed_instances is None


class TestNoConfigurationChangesNothing:
    def test_any_same_host_path_still_resolves(self):
        registry = InstanceRegistry(_config(), {})
        assert registry.resolve("/auto_pm").trac_url == AUTO_PM
        assert (
            registry.resolve("/auto_pm", UNRESTRICTED).trac_url
            == AUTO_PM
        )

    def test_default_is_still_the_same_object(self):
        default = _config()
        assert InstanceRegistry(default, {}).resolve(None) is default


class TestClientCache:
    def test_scope_is_part_of_the_cache_key(self):
        """Same credentials, different allowlists: one identity's client
        must not be handed to the other, because the client carries the
        scope its probes enforce."""
        scoped = Identity(
            name="a",
            username="u",
            password="p",
            instances=("/trac_test",),
        )
        open_ = Identity(name="b", username="u", password="p")
        registry = InstanceRegistry(_config(), {})
        with patch(
            "trac_mcp_server.instances.TracClient"
        ) as client_cls:
            client_cls.side_effect = lambda config: MagicMock(
                config=config
            )
            first = registry.get_client(None, scoped)
            second = registry.get_client(None, open_)
        assert first is not second


# ---------------------------------------------------------------------------
# Dispatch: an RPC tool and ticket_render_check, refused before any client
# ---------------------------------------------------------------------------


def _dispatch(tool, args, registry, identity):
    """Run ``handle_call_tool`` against a real InstanceRegistry.

    ``TracClient`` and the tool registry are both recording mocks: a
    refusal must happen before either is touched, and an allowed call
    must reach both.
    """
    reached = types.CallToolResult(
        content=[types.TextContent(type="text", text="reached")]
    )
    tool_registry = MagicMock()

    async def fake_call_tool(name, args, client):
        return reached

    tool_registry.call_tool = MagicMock(side_effect=fake_call_tool)
    server_module.set_instances(registry)
    try:
        with (
            patch.object(
                server_module, "_caller_identity", return_value=identity
            ),
            patch.object(
                server_module,
                "get_registry",
                return_value=tool_registry,
            ),
            patch("trac_mcp_server.instances.TracClient") as client_cls,
        ):
            result = asyncio.run(handle_call_tool(tool, args))
        return result, client_cls, tool_registry.call_tool
    finally:
        server_module.set_instances(None)


@pytest.mark.parametrize("tool", ["wiki_get", "ticket_render_check"])
class TestDispatchRefusal:
    def test_identity_refused(self, tool):
        registry = InstanceRegistry(_config(), {})
        result, client_cls, call_tool = _dispatch(
            tool,
            {
                "instance": "/auto_pm",
                "page_name": "Index",
                "ticket_id": 1,
            },
            registry,
            CLOUD,
        )
        call_tool.assert_not_called()
        client_cls.assert_not_called()
        assert result.isError
        text = result.content[0].text
        assert "instance_not_allowed" in text, text
        assert "/trac_test" in text, text

    def test_default_identity_refused(self, tool):
        registry = InstanceRegistry(
            _config(allowed_instances=frozenset({TRAC_TEST})), {}
        )
        result, client_cls, call_tool = _dispatch(
            tool,
            {
                "instance": "/auto_pm",
                "page_name": "Index",
                "ticket_id": 1,
            },
            registry,
            None,
        )
        call_tool.assert_not_called()
        client_cls.assert_not_called()
        assert result.isError
        assert "instance_not_allowed" in result.content[0].text


def test_dispatch_allowed_instance_reaches_the_tool():
    registry = InstanceRegistry(_config(), {})
    result, client_cls, call_tool = _dispatch(
        "wiki_get",
        {"instance": "/trac_test", "page_name": "WikiStart"},
        registry,
        CLOUD,
    )
    assert not result.isError
    client_cls.assert_called_once()
    call_tool.assert_called_once()


# ---------------------------------------------------------------------------
# Probes: an out-of-scope href is skipped, never fetched
# ---------------------------------------------------------------------------


def _probe_client(allowed):
    client = MagicMock()
    client.config = _config(allowed_instances=allowed)
    response = MagicMock(status_code=200, text=EXISTING_PAGE_STUB)
    response.url = ALLOWED_HREF
    client.session.get.return_value = response
    return client


def _fetched(client) -> list[str]:
    return [call.args[0] for call in client.session.get.call_args_list]


class TestProbeAllowlist:
    def test_refused_wiki_href_skipped_with_reason(self):
        client = _probe_client(frozenset({TRAC_TEST}))
        result = probe_targets(client, [ALLOWED_HREF, REFUSED_HREF])
        assert result[REFUSED_HREF] == {
            "status": SKIPPED,
            "resolved_url": None,
            "reason": "instance_not_allowed",
        }
        assert result[ALLOWED_HREF]["status"] == "exists"
        assert _fetched(client) == [ALLOWED_HREF]

    def test_refused_ticket_href_and_its_control_never_fetched(self):
        client = _probe_client(frozenset({TRAC_TEST}))
        result = probe_targets(client, [REFUSED_TICKET_HREF])
        assert result[REFUSED_TICKET_HREF]["reason"] == (
            "instance_not_allowed"
        )
        assert _fetched(client) == []

    def test_foreign_host_is_outside_any_allowlist(self):
        """The allowlist names what the identity may reach; another host
        is not on it, so it is not fetched with the identity's session."""
        client = _probe_client(frozenset({TRAC_TEST}))
        result = probe_targets(client, [FOREIGN_HREF])
        assert result[FOREIGN_HREF]["reason"] == "instance_not_allowed"
        assert _fetched(client) == []

    def test_refused_hrefs_do_not_consume_the_cap(self):
        client = _probe_client(frozenset({TRAC_TEST}))
        result = probe_targets(
            client, [REFUSED_HREF, ALLOWED_HREF], cap=1
        )
        assert result[ALLOWED_HREF]["status"] == "exists"

    def test_dot_segments_are_judged_where_the_request_goes(self):
        """``requests`` resolves dot segments before sending, so this
        href's written base (``/trac_test``) is not where it is fetched
        (``/auto_pm``). Measured on requests 2.32.5 / urllib3 2.6.3."""
        href = (
            f"{TRAC_TEST}/intertrac/x/../../../auto_pm"
            "/intertrac/wiki%3AIndex"
        )
        client = _probe_client(frozenset({TRAC_TEST}))
        result = probe_targets(client, [href])
        assert result[href]["reason"] == "instance_not_allowed"
        assert _fetched(client) == []

    def test_redirect_out_of_scope_is_not_followed(self):
        """An allowed instance's dispatcher redirects a nested InterTrac
        target onward: measured live, ``/trac_test/intertrac/
        auto_pm%3Awiki%3AIndex`` answers 302 to ``/auto_pm/intertrac/
        wiki%3AIndex``. The query string makes the href probeable."""
        href = (
            f"{TRAC_TEST}/intertrac/auto_pm%3Awiki%3AIndex"
            "?/intertrac/wiki%3A"
        )
        client = _probe_client(frozenset({TRAC_TEST}))
        client.session.get.return_value = MagicMock(
            status_code=302,
            headers={"Location": f"{AUTO_PM}/intertrac/wiki%3AIndex"},
            url=href,
        )
        result = probe_targets(client, [href])
        assert result[href] == {
            "status": SKIPPED,
            "resolved_url": None,
            "reason": "instance_not_allowed",
        }
        assert _fetched(client) == [href]

    def test_redirect_within_scope_is_followed(self):
        page = f"{TRAC_TEST}/wiki/WikiStart"
        responses = {
            ALLOWED_HREF: MagicMock(
                status_code=302,
                headers={"Location": page},
                url=ALLOWED_HREF,
            ),
            page: MagicMock(
                status_code=200, text=EXISTING_PAGE_STUB, url=page
            ),
        }
        client = _probe_client(frozenset({TRAC_TEST}))
        client.session.get.side_effect = lambda url, **kw: responses[
            url
        ]
        result = probe_targets(client, [ALLOWED_HREF])
        assert result[ALLOWED_HREF] == {
            "status": "exists",
            "resolved_url": page,
        }
        assert _fetched(client) == [ALLOWED_HREF, page]

    @pytest.mark.parametrize(
        "href",
        [
            f"{TRAC_TEST}/%2e%2e/auto_pm/intertrac/wiki%3AIndex",
            f"{TRAC_TEST}/%2E%2E/auto_pm/intertrac/wiki%3AIndex",
            f"{TRAC_TEST}/%2e/intertrac/wiki%3AIndex",
        ],
        ids=["encoded-lower", "encoded-upper", "encoded-single"],
    )
    def test_any_dot_segment_is_out_of_scope(self, href):
        """``%2e%2e`` survives request preparation as a literal ``..``,
        and the live server resolves it: measured, ``/trac_test/%2e%2e/
        auto_pm/intertrac/wiki%3AIndex`` answers 302 into ``/auto_pm``
        (review finding 1, #110). Fail closed on any dot segment."""
        client = _probe_client(frozenset({TRAC_TEST}))
        result = probe_targets(client, [href])
        assert result[href]["reason"] == "instance_not_allowed"
        assert _fetched(client) == []

    def test_redirect_to_an_encoded_dot_segment_is_not_followed(self):
        client = _probe_client(frozenset({TRAC_TEST}))
        client.session.get.return_value = MagicMock(
            status_code=302,
            headers={
                "Location": f"{TRAC_TEST}/%2e%2e/auto_pm/wiki/Index"
            },
            url=ALLOWED_HREF,
        )
        result = probe_targets(client, [ALLOWED_HREF])
        assert result[ALLOWED_HREF]["reason"] == "instance_not_allowed"
        assert _fetched(client) == [ALLOWED_HREF]

    def test_allowed_other_host_is_not_probed_with_this_clients_session(
        self,
    ):
        """An allowed URL on another host belongs to a declared instance
        with its own credentials (#102 gap 3). The probe session carries
        THIS client's credentials, so it stays on this client's host
        (review finding 3, #110)."""
        other = "https://other.example/x"
        href = f"{other}/intertrac/wiki%3AY"
        client = _probe_client(frozenset({TRAC_TEST, other}))
        result = probe_targets(client, [href])
        assert result[href]["reason"] == "instance_not_allowed"
        assert _fetched(client) == []

    def test_redirect_to_another_allowed_host_is_not_followed(self):
        other = "https://other.example/x"
        client = _probe_client(frozenset({TRAC_TEST, other}))
        client.session.get.return_value = MagicMock(
            status_code=302,
            headers={"Location": f"{other}/wiki/Y"},
            url=ALLOWED_HREF,
        )
        result = probe_targets(client, [ALLOWED_HREF])
        assert result[ALLOWED_HREF]["reason"] == "instance_not_allowed"
        assert _fetched(client) == [ALLOWED_HREF]

    def test_a_dispatcher_must_be_an_allowed_instance_exactly(self):
        """resolve() matches exactly, so the probe must too: a host-root
        entry allows a Trac AT the root, not every instance below it
        (review finding 5, #110)."""
        client = _probe_client(frozenset({HOST}))
        client.config = _config(
            trac_url=HOST, allowed_instances=frozenset({HOST})
        )
        result = probe_targets(client, [REFUSED_HREF])
        assert result[REFUSED_HREF]["reason"] == "instance_not_allowed"
        assert _fetched(client) == []

    def test_redirect_to_another_instances_dispatcher_is_not_followed(
        self,
    ):
        client = _probe_client(frozenset({HOST}))
        client.config = _config(
            trac_url=HOST, allowed_instances=frozenset({HOST})
        )
        href = f"{HOST}/intertrac/wiki%3AWikiStart"
        client.session.get.return_value = MagicMock(
            status_code=302,
            headers={"Location": REFUSED_HREF},
            url=href,
        )
        result = probe_targets(client, [href])
        assert result[href]["reason"] == "instance_not_allowed"
        assert _fetched(client) == [href]

    def test_no_allowlist_probes_everything(self):
        client = _probe_client(None)
        probe_targets(client, [ALLOWED_HREF, REFUSED_HREF])
        assert sorted(_fetched(client)) == sorted(
            [ALLOWED_HREF, REFUSED_HREF]
        )


# ---------------------------------------------------------------------------
# Checks: the skip is advisory, not the blocking `target_check_capped`
# ---------------------------------------------------------------------------


_REFUSED_HTML = (
    '<p><a class="ext-link" href="' + REFUSED_HREF + '"'
    ' title="wiki:Index in Automated Project Manager">auto_pm:wiki:Index'
    "</a></p>"
)


def _warnings(probes):
    return build_warnings(
        markdown_source=None,
        tracwiki="auto_pm:wiki:Index",
        facts=extract_facts(_REFUSED_HTML),
        probes=probes,
        check_targets=True,
        source_format="tracwiki",
    )


def test_not_allowed_skip_is_its_own_advisory_code():
    warnings = _warnings(
        {
            REFUSED_HREF: {
                "status": SKIPPED,
                "resolved_url": None,
                "reason": "instance_not_allowed",
            }
        }
    )
    codes = [w["code"] for w in warnings]
    assert "target_check_not_allowed" in codes, codes
    assert "target_check_capped" not in codes, codes
    (found,) = [
        w for w in warnings if w["code"] == "target_check_not_allowed"
    ]
    assert not is_blocking(found)
    assert found["evidence"]["hrefs"] == [REFUSED_HREF]
    assert "target_check_not_allowed" in ADVISORY_CODES


def test_a_plain_skip_is_still_capped():
    """Recall gate: the reason-keyed branch must not swallow the cap."""
    codes = [
        w["code"]
        for w in _warnings(
            {REFUSED_HREF: {"status": SKIPPED, "resolved_url": None}}
        )
    ]
    assert "target_check_capped" in codes, codes


# ---------------------------------------------------------------------------
# list_instances: shows only what the caller may reach
# ---------------------------------------------------------------------------


class TestListInstances:
    def _call(self, registry, identity, client_config):
        set_instance_registry(registry)
        client = MagicMock()
        client.config = client_config
        try:
            with (
                patch(
                    "trac_mcp_server.mcp.tools.instances.caller_identity",
                    return_value=identity,
                ),
                patch(
                    "trac_mcp_server.mcp.tools.instances."
                    "scrape_project_index",
                    return_value=[
                        {
                            "path": "/trac_test",
                            "title": "Test",
                            "description": "",
                            "url": TRAC_TEST,
                        },
                        {
                            "path": "/auto_pm",
                            "title": "APM",
                            "description": "",
                            "url": AUTO_PM,
                        },
                    ],
                ),
            ):
                return asyncio.run(
                    ToolRegistry(INSTANCE_SPECS).call_tool(
                        "list_instances", {}, client
                    )
                )
        finally:
            set_instance_registry(None)

    def test_identity_sees_only_its_allowlist(self):
        registry = InstanceRegistry(
            _config(),
            {"apm": InstanceSpec(name="apm", url="/auto_pm")},
        )
        result = self._call(registry, CLOUD, _config())
        structured = result.structuredContent
        assert structured["allowed"] == [TRAC_TEST]
        assert [e["name"] for e in structured["configured"]] == [
            "default"
        ]
        assert [e["path"] for e in structured["discovered"]] == [
            "/trac_test"
        ]
        assert "auto_pm" not in result.content[0].text

    def test_unrestricted_caller_sees_everything(self):
        registry = InstanceRegistry(
            _config(),
            {"apm": InstanceSpec(name="apm", url="/auto_pm")},
        )
        result = self._call(registry, None, _config())
        structured = result.structuredContent
        assert structured.get("allowed") is None
        assert {e["name"] for e in structured["configured"]} == {
            "default",
            "apm",
        }
        assert len(structured["discovered"]) == 2


def test_list_instances_answers_a_caller_scoped_away_from_the_default():
    """Through dispatch, not the handler: ``handle_call_tool`` resolves
    the default instance before any tool runs, so a caller whose scope
    leaves the default out was refused -- and told to call the very tool
    that refused it (review finding 2, #110)."""
    registry = InstanceRegistry(_config(trac_url=AUTO_PM), {})
    server_module.set_instances(registry)
    set_instance_registry(registry)
    server_module.set_registry(ToolRegistry(INSTANCE_SPECS))
    try:
        with (
            patch.object(
                server_module, "_caller_identity", return_value=CLOUD
            ),
            patch(
                "trac_mcp_server.mcp.tools.instances.caller_identity",
                return_value=CLOUD,
            ),
            patch("trac_mcp_server.instances.TracClient") as client_cls,
        ):
            result = asyncio.run(
                handle_call_tool("list_instances", {"discover": False})
            )
    finally:
        server_module.set_registry(None)
        server_module.set_instances(None)
        set_instance_registry(None)
    assert not result.isError, result.content[0].text
    assert result.structuredContent["allowed"] == [TRAC_TEST]
    assert result.structuredContent["default"] is None
    client_cls.assert_not_called()


def test_list_instances_exemption_is_only_for_list_instances():
    """Recall gate: any other tool is still refused on the default."""
    registry = InstanceRegistry(_config(trac_url=AUTO_PM), {})
    result, client_cls, call_tool = _dispatch(
        "wiki_get", {"page_name": "Index"}, registry, CLOUD
    )
    assert "instance_not_allowed" in result.content[0].text
    call_tool.assert_not_called()
    client_cls.assert_not_called()
