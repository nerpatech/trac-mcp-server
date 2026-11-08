"""Tests for wiki_conventions (ticket #123).

Offline: the pure ``parse_style_declaration`` parser, and the handler
against a fake client and a fake instance registry.

Live (``@pytest.mark.live``): a probe style page and a probe PageTemplates
page on ``/trac_test``, with WikiStart's declaration line appended and
restored, so the parse runs against text Trac itself stored
(Rules/testing/RealSubstrateNotMocks).
"""

import asyncio
import json
import os
import xmlrpc.client
from unittest.mock import MagicMock, patch

import pytest

from trac_mcp_server.core.client import (
    WikiPageConflict,
    WikiPageStoredReadbackFailed,
)
from trac_mcp_server.instances import (
    InstanceNotAllowedError,
    UnknownInstanceError,
)
from trac_mcp_server.mcp.tools import ALL_SPECS, WIKI_TOOLS
from trac_mcp_server.mcp.tools.wiki_conventions import (
    WIKI_CONVENTIONS_SPECS,
    WIKI_CONVENTIONS_TOOLS,
    StyleDeclaration,
    _handle_conventions,
    parse_style_declaration,
)

AGENT = "'''Wiki style:''' [auto_pm:wiki:Rules/documentation/WikiStyleAgent agent]"
PUBLIC = "'''Wiki style:''' [auto_pm:wiki:Rules/documentation/WikiStylePublic public]"
OWN = (
    "'''Wiki style:''' [wiki:Rules/documentation/WikiStyleAgent agent]"
)


# ---------------------------------------------------------------------------
# parse_style_declaration (pure)
# ---------------------------------------------------------------------------


class TestParseStyleDeclaration:
    def test_agent_label(self):
        d = parse_style_declaration(f"= Home =\n\n{AGENT}\n\nbody\n")
        assert d == StyleDeclaration(
            style="agent",
            prefix="auto_pm",
            page="Rules/documentation/WikiStyleAgent",
            label="agent",
            lines=(AGENT,),
        )

    def test_public_label(self):
        d = parse_style_declaration(PUBLIC + "\n")
        assert d.style == "public"
        assert d.label == "public"
        assert d.page == "Rules/documentation/WikiStylePublic"

    def test_same_instance_link_has_no_prefix(self):
        d = parse_style_declaration(OWN + "\n")
        assert d.style == "agent"
        assert d.prefix is None
        assert d.page == "Rules/documentation/WikiStyleAgent"

    def test_intertrac_prefixed_link(self):
        d = parse_style_declaration(AGENT)
        assert d.prefix == "auto_pm"

    def test_marker_with_garbage_is_malformed(self):
        line = AGENT + " and more"
        d = parse_style_declaration(line + "\n")
        assert d.style == "malformed"
        assert d.lines == (line,)
        assert d.label is None and d.page is None

    def test_marker_with_leading_whitespace_is_malformed(self):
        line = " " + AGENT
        d = parse_style_declaration(line + "\n")
        assert d.style == "malformed"
        assert d.lines == (line,)

    def test_missing_label_is_malformed(self):
        line = "'''Wiki style:''' [auto_pm:wiki:Rules/documentation/X]"
        d = parse_style_declaration(line)
        assert d.style == "malformed"

    def test_disagreeing_lines_are_malformed_with_both_listed(self):
        d = parse_style_declaration(f"{AGENT}\ntext\n{PUBLIC}\n")
        assert d.style == "malformed"
        assert d.lines == (AGENT, PUBLIC)

    def test_identical_duplicates_are_accepted(self):
        d = parse_style_declaration(f"{AGENT}\n\n{AGENT}\n")
        assert d.style == "agent"
        assert d.lines == (AGENT, AGENT)

    def test_one_valid_one_malformed_is_malformed(self):
        d = parse_style_declaration(f"{AGENT}\n '''Wiki style:''' x\n")
        assert d.style == "malformed"
        assert len(d.lines) == 2

    def test_valid_line_inside_fence_is_undeclared(self):
        d = parse_style_declaration(
            f"before\n{{{{{{\n{AGENT}\n}}}}}}\nafter\n"
        )
        assert d.style == "undeclared"
        assert d.lines == ()

    def test_malformed_line_inside_fence_is_undeclared(self):
        text = "{{{\n  '''Wiki style:''' nonsense\n}}}\n"
        d = parse_style_declaration(text)
        assert d.style == "undeclared"
        assert d.lines == ()

    def test_fence_does_not_hide_a_declaration_after_it(self):
        d = parse_style_declaration(f"{{{{{{\nx\n}}}}}}\n{AGENT}\n")
        assert d.style == "agent"

    def test_anchor_is_accepted_and_dropped(self):
        line = "'''Wiki style:''' [auto_pm:wiki:Rules/Style#Section-2 agent]"
        d = parse_style_declaration(line + "\n")
        assert d.style == "agent"
        assert d.page == "Rules/Style"
        assert d.prefix == "auto_pm"
        assert d.lines == (line,)

    def test_empty_anchor_is_accepted(self):
        d = parse_style_declaration(
            "'''Wiki style:''' [wiki:Rules/Style# agent]\n"
        )
        assert d.style == "agent"
        assert d.page == "Rules/Style"

    @pytest.mark.parametrize(
        "target", ["Style@3", "Style#sec@3", "Style@3#sec"]
    )
    def test_at_sign_anywhere_in_target_is_malformed(self, target):
        line = f"'''Wiki style:''' [auto_pm:wiki:{target} agent]"
        d = parse_style_declaration(line + "\n")
        assert d.style == "malformed"
        assert d.lines == (line,)

    def test_prefix_is_kept_as_written(self):
        d = parse_style_declaration(
            "'''Wiki style:''' [Auto_PM:wiki:Style agent]\n"
        )
        assert d.prefix == "Auto_PM"

    @pytest.mark.parametrize("label", ["malformed", "undeclared"])
    def test_reserved_status_labels_are_malformed(self, label):
        line = f"'''Wiki style:''' [wiki:Style {label}]"
        d = parse_style_declaration(line + "\n")
        assert d.style == "malformed"
        assert d.label is None and d.page is None
        assert d.lines == (line,)

    def test_empty_text_is_undeclared(self):
        d = parse_style_declaration("")
        assert d == StyleDeclaration(
            style="undeclared",
            prefix=None,
            page=None,
            label=None,
            lines=(),
        )

    def test_crlf_line_endings(self):
        d = parse_style_declaration(f"a\r\n{AGENT}\r\nb\r\n")
        assert d.style == "agent"


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------


class TestToolDefinition:
    def test_registered_read_only_with_wiki_view(self):
        (tool,) = WIKI_CONVENTIONS_TOOLS
        assert tool.name == "wiki_conventions"
        assert tool in WIKI_TOOLS
        a = tool.annotations
        assert a.readOnlyHint is True
        assert a.destructiveHint is False
        assert a.idempotentHint is True
        assert a.openWorldHint is True
        assert tool.inputSchema == {
            "type": "object",
            "properties": {},
            "required": [],
        }
        (spec,) = WIKI_CONVENTIONS_SPECS
        assert spec.permissions == frozenset({"WIKI_VIEW"})
        assert spec.handler is _handle_conventions
        assert spec in ALL_SPECS


# ---------------------------------------------------------------------------
# Handler (fake client + registry)
# ---------------------------------------------------------------------------

TRAC_URL = "https://trac.example.org/trac_test"
STYLE_PAGE = "Rules/documentation/WikiStyleAgent"


class FakeClient:
    """Pages as {name: (text, version)}; faults mirror the real server."""

    def __init__(self, pages, trac_url=TRAC_URL, faults=None):
        self.pages = pages
        self.config = MagicMock()
        self.config.trac_url = trac_url
        self.faults = faults or {}
        self.reads = []
        self.read_versions = []

    def list_wiki_pages(self):
        return list(self.pages)

    def get_wiki_page(self, name, version=None):
        self.reads.append(name)
        self.read_versions.append((name, version))
        if name in self.faults:
            raise self.faults[name]
        if name not in self.pages:
            raise xmlrpc.client.Fault(
                404, f'Wiki page "{name}" does not exist'
            )
        return self.pages[name][0]

    def get_wiki_page_info(self, name, version=None):
        if name in self.faults:
            raise self.faults[name]
        if name not in self.pages:
            return 0
        return {"name": name, "version": self.pages[name][1]}


def _run(client, registry="unset", identity="ID"):
    patches = [
        patch(
            "trac_mcp_server.mcp.tools.wiki_conventions.caller_identity",
            return_value=identity,
        )
    ]
    if registry != "unset":
        patches.append(
            patch(
                "trac_mcp_server.mcp.tools.wiki_conventions."
                "get_instance_registry",
                return_value=registry,
            )
        )
    for p in patches:
        p.start()
    try:
        result = asyncio.run(_handle_conventions(client, {}))
    finally:
        for p in patches:
            p.stop()
    assert not result.isError
    (content,) = result.content
    assert json.loads(content.text) == result.structuredContent
    assert content.text == json.dumps(
        result.structuredContent, ensure_ascii=False, indent=2
    )
    return result.structuredContent


def _registry(client=None, error=None):
    registry = MagicMock()
    if error is not None:
        registry.get_client.side_effect = error
    else:
        registry.get_client.return_value = client
    return registry


TEMPLATES = {
    "PageTemplates": ("stock help page", 1),
    "PageTemplates/Note": ("= Note =\n\n{{{\nraw\n}}}\n", 3),
    "PageTemplates/Alpha": ("alpha ü\n", 2),
    "PageTemplatesX/Y": ("not a template", 1),
}


class TestHandler:
    def test_wikistart_not_found_is_undeclared_templates_still_returned(
        self,
    ):
        client = FakeClient(dict(TEMPLATES))
        out = _run(client)
        assert out["style"] == "undeclared"
        assert out["declaration_lines"] == []
        assert out["style_page"] is None
        assert [t["name"] for t in out["templates"]] == [
            "PageTemplates/Alpha",
            "PageTemplates/Note",
        ]

    def test_templates_filtered_sorted_and_byte_exact(self):
        client = FakeClient(dict(TEMPLATES))
        out = _run(client)
        assert out["templates"] == [
            {
                "name": "PageTemplates/Alpha",
                "version": 2,
                "body": "alpha ü\n",
            },
            {
                "name": "PageTemplates/Note",
                "version": 3,
                "body": "= Note =\n\n{{{\nraw\n}}}\n",
            },
        ]

    def test_no_templates_is_empty_list(self):
        out = _run(FakeClient({"WikiStart": ("plain\n", 1)}))
        assert out["templates"] == []
        assert out["style"] == "undeclared"

    def test_same_instance_style_page_read_from_client_itself(self):
        body = "= Style =\r\nbody with trailing space \n"
        pages = {
            "WikiStart": (OWN + "\n", 4),
            STYLE_PAGE: (body, 7),
            **TEMPLATES,
        }
        client = FakeClient(pages)
        registry = _registry()
        out = _run(client, registry)
        assert out["style"] == "agent"
        assert out["declaration_lines"] == [OWN]
        assert out["style_page"] == {
            "instance": "/trac_test",
            "name": STYLE_PAGE,
            "version": 7,
            "body": body,
        }
        registry.get_client.assert_not_called()

    def test_prefix_equal_to_own_prefix_uses_client_itself(self):
        line = "'''Wiki style:''' [trac_test:wiki:Style probe]"
        pages = {"WikiStart": (line, 1), "Style": ("S", 2)}
        registry = _registry()
        out = _run(FakeClient(pages), registry)
        assert out["style_page"]["body"] == "S"
        assert out["style_page"]["instance"] == "/trac_test"
        registry.get_client.assert_not_called()

    def test_prefixed_link_goes_through_registry_with_identity(self):
        other = FakeClient({STYLE_PAGE: ("agent style\n", 12)})
        registry = _registry(other)
        client = FakeClient(
            {"WikiStart": (AGENT + "\n", 1), **TEMPLATES}
        )
        out = _run(client, registry, identity="WHO")
        registry.get_client.assert_called_once_with(
            "/auto_pm", identity="WHO"
        )
        assert out["style_page"] == {
            "instance": "/auto_pm",
            "name": STYLE_PAGE,
            "version": 12,
            "body": "agent style\n",
        }
        assert STYLE_PAGE not in client.reads
        assert len(out["templates"]) == 2

    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (
                InstanceNotAllowedError("not in allowlist"),
                "instance_not_allowed",
            ),
            (
                UnknownInstanceError("no such instance"),
                "unknown_instance",
            ),
        ],
    )
    def test_registry_refusals_do_not_fail_the_call(
        self, error, reason
    ):
        client = FakeClient(
            {"WikiStart": (AGENT + "\n", 1), **TEMPLATES}
        )
        out = _run(client, _registry(error=error))
        assert out["style"] == "agent"
        assert out["declaration_lines"] == [AGENT]
        assert out["style_page"] == {
            "instance": "/auto_pm",
            "name": STYLE_PAGE,
            "available": False,
            "reason": reason,
            "message": str(error),
        }
        assert len(out["templates"]) == 2

    def test_registry_none_is_unknown_instance(self):
        client = FakeClient({"WikiStart": (AGENT + "\n", 1)})
        out = _run(client, None)
        assert out["style_page"]["available"] is False
        assert out["style_page"]["reason"] == "unknown_instance"
        assert out["style_page"]["instance"] == "/auto_pm"

    def test_style_page_not_found_fault(self):
        other = FakeClient({})
        client = FakeClient({"WikiStart": (AGENT + "\n", 1)})
        out = _run(client, _registry(other))
        assert out["style_page"]["available"] is False
        assert out["style_page"]["reason"] == "not_found"
        assert "does not exist" in out["style_page"]["message"]

    def test_style_page_permission_fault(self):
        other = FakeClient(
            {STYLE_PAGE: ("x", 1)},
            faults={
                STYLE_PAGE: xmlrpc.client.Fault(
                    403, "Permission denied: WIKI_VIEW required"
                )
            },
        )
        client = FakeClient({"WikiStart": (AGENT + "\n", 1)})
        out = _run(client, _registry(other))
        assert out["style_page"]["reason"] == "permission_denied"

    def test_style_page_403_without_permission_wording(self):
        # Trac's PermissionError text names the privilege, not the word
        # "permission"; the 403 fault code alone must classify it.
        other = FakeClient(
            {STYLE_PAGE: ("x", 1)},
            faults={
                STYLE_PAGE: xmlrpc.client.Fault(
                    403,
                    "WIKI_VIEW privileges are required to perform this "
                    "operation on " + STYLE_PAGE,
                )
            },
        )
        client = FakeClient({"WikiStart": (AGENT + "\n", 1)})
        out = _run(client, _registry(other))
        assert out["style_page"]["reason"] == "permission_denied"

    def test_style_page_404_without_not_found_wording(self):
        # ResourceNotFound is code 404 whatever its text, as in
        # translate_xmlrpc_error (Trac #186).
        other = FakeClient(
            {STYLE_PAGE: ("x", 1)},
            faults={
                STYLE_PAGE: xmlrpc.client.Fault(
                    404, "No version 3 for " + STYLE_PAGE
                )
            },
        )
        client = FakeClient({"WikiStart": (AGENT + "\n", 1)})
        out = _run(client, _registry(other))
        assert out["style_page"]["reason"] == "not_found"

    def test_style_page_other_failure_is_error(self):
        other = FakeClient(
            {STYLE_PAGE: ("x", 1)},
            faults={STYLE_PAGE: xmlrpc.client.Fault(500, "boom")},
        )
        client = FakeClient({"WikiStart": (AGENT + "\n", 1)})
        out = _run(client, _registry(other))
        assert out["style_page"]["reason"] == "error"
        assert out["style_page"]["message"]

    def test_style_page_non_dict_info_is_not_found(self):
        # Trac answers getPageInfo with 0 for a missing page (#119).
        class ZeroInfo(FakeClient):
            def get_wiki_page_info(self, name, version=None):
                return 0

        other = ZeroInfo({STYLE_PAGE: ("x", 1)})
        client = FakeClient({"WikiStart": (AGENT + "\n", 1)})
        out = _run(client, _registry(other))
        assert out["style_page"]["available"] is False
        assert out["style_page"]["reason"] == "not_found"
        assert STYLE_PAGE in out["style_page"]["message"]

    def test_failing_templates_are_reported_in_place(self):
        class Flaky(FakeClient):
            def get_wiki_page_info(self, name, version=None):
                if name == "PageTemplates/Gone":
                    return 0
                return super().get_wiki_page_info(name, version)

        pages = {
            "WikiStart": (OWN + "\n", 1),
            STYLE_PAGE: ("style body", 9),
            "PageTemplates/Alpha": ("a", 2),
            "PageTemplates/Boom": ("b", 1),
            "PageTemplates/Gone": ("g", 1),
            "PageTemplates/Zed": ("z", 5),
        }
        client = Flaky(
            pages,
            faults={
                "PageTemplates/Boom": xmlrpc.client.Fault(500, "boom")
            },
        )
        out = _run(client)
        assert out["style"] == "agent"
        assert out["style_page"]["body"] == "style body"
        templates = out["templates"]
        assert [t["name"] for t in templates] == [
            "PageTemplates/Alpha",
            "PageTemplates/Boom",
            "PageTemplates/Gone",
            "PageTemplates/Zed",
        ]
        assert templates[0] == {
            "name": "PageTemplates/Alpha",
            "version": 2,
            "body": "a",
        }
        assert templates[1] == {
            "name": "PageTemplates/Boom",
            "available": False,
            "reason": "error",
            "message": "boom",
        }
        assert templates[2]["available"] is False
        assert templates[2]["reason"] == "not_found"
        assert set(templates[2]) == {
            "name",
            "available",
            "reason",
            "message",
        }
        assert templates[3]["body"] == "z"

    def test_body_and_version_come_from_the_same_pinned_version(self):
        # The page moves on between the two reads: info says 5, the
        # latest is already 6. The body must be version 5's.
        class Moving(FakeClient):
            def get_wiki_page_info(self, name, version=None):
                return {"name": name, "version": 5}

            def get_wiki_page(self, name, version=None):
                self.read_versions.append((name, version))
                if name == "WikiStart":
                    return self.pages[name][0]
                return f"body of v{version if version else 6}"

        client = Moving(
            {"WikiStart": (OWN + "\n", 1), STYLE_PAGE: ("x", 6)}
        )
        out = _run(client)
        assert out["style_page"]["version"] == 5
        assert out["style_page"]["body"] == "body of v5"
        assert (STYLE_PAGE, 5) in client.read_versions

    def test_anchor_is_dropped_from_the_page_read(self):
        line = "'''Wiki style:''' [wiki:Rules/Style#Sec probe]"
        client = FakeClient(
            {"WikiStart": (line, 1), "Rules/Style": ("S", 2)}
        )
        out = _run(client)
        assert out["style"] == "probe"
        assert out["style_page"]["name"] == "Rules/Style"
        assert out["style_page"]["body"] == "S"

    def test_own_prefix_in_another_case_uses_client_itself(self):
        line = "'''Wiki style:''' [Trac_Test:wiki:Style probe]"
        pages = {"WikiStart": (line, 1), "Style": ("S", 2)}
        registry = _registry()
        out = _run(FakeClient(pages), registry)
        assert out["style_page"]["body"] == "S"
        assert out["style_page"]["instance"] == "/trac_test"
        registry.get_client.assert_not_called()

    def test_mixed_case_other_prefix_hits_registry_lower_cased(self):
        line = "'''Wiki style:''' [Auto_PM:wiki:Style probe]"
        other = FakeClient({"Style": ("S", 3)})
        registry = _registry(other)
        out = _run(
            FakeClient({"WikiStart": (line, 1)}), registry, "WHO"
        )
        registry.get_client.assert_called_once_with(
            "/auto_pm", identity="WHO"
        )
        assert out["style_page"]["instance"] == "/auto_pm"
        assert out["style_page"]["body"] == "S"
        assert out["declaration_lines"] == [line]

    def test_malformed_has_lines_and_no_style_page(self):
        bad = "'''Wiki style:''' nonsense"
        client = FakeClient({"WikiStart": (bad + "\n", 1), **TEMPLATES})
        out = _run(client)
        assert out["style"] == "malformed"
        assert out["declaration_lines"] == [bad]
        assert out["style_page"] is None
        assert len(out["templates"]) == 2

    def test_other_wikistart_fault_propagates(self):
        client = FakeClient(
            {"WikiStart": ("x", 1)},
            faults={"WikiStart": xmlrpc.client.Fault(500, "boom")},
        )
        with pytest.raises(xmlrpc.client.Fault):
            asyncio.run(_handle_conventions(client, {}))


# ---------------------------------------------------------------------------
# Live: /trac_test
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestWikiConventionsLive:
    """Requires --run-live and TRAC_URL/USERNAME/PASSWORD. Writes two
    per-run probe pages on /trac_test and appends one line to WikiStart,
    all restored or deleted afterwards.

    Trac #173: the probe page names carry a per-run suffix, so two
    overlapping runs cannot overwrite or delete each other's pages.
    WikiStart cannot be per-run (the tool reads WikiStart's declaration
    by design), so its writes are version-checked instead: the row
    re-reads WikiStart's version and content right before the append
    and appends with that version (`put_wiki_page(version=...)`), so a
    write that landed in between is refused, not overwritten; the row
    then skips (another run is mid-row). The restore is checked against
    the version the append produced, and reports a cleanup error,
    WikiStart left as found, when anything was written since.
    """

    def test_declared_style_and_template_round_trip(self):
        import uuid

        from trac_mcp_server.config import Config
        from trac_mcp_server.core.client import TracClient
        from trac_mcp_server.instances import InstanceRegistry

        default_config = Config(
            trac_url=os.environ["TRAC_URL"],
            username=os.environ["TRAC_USERNAME"],
            password=os.environ["TRAC_PASSWORD"],
            insecure=os.environ.get("TRAC_INSECURE", "").lower()
            in ("1", "true", "yes"),
        )
        registry = InstanceRegistry(default_config, {})
        client = TracClient(registry.resolve("/trac_test"))

        original = client.get_wiki_page("WikiStart")
        if "'''Wiki style:'''" in original:
            pytest.skip(
                "/trac_test WikiStart already has a Wiki style line"
            )

        run_id = uuid.uuid4().hex[:8]
        template = f"PageTemplates/Probe{run_id}"
        template_body = (
            "= Probe 123 =\n\n  * '''bold''' and ''italic''\n"
            "{{{\nraw\n}}}\n"
        )
        style_page = f"Probe/WikiConventions{run_id}/Style"
        style_body = "= Probe style =\n\nUse probes; ünïcode ok.\n"
        line = f"'''Wiki style:''' [wiki:{style_page} probe]"

        def run():
            result = asyncio.run(_handle_conventions(client, {}))
            assert not result.isError
            return result.structuredContent

        class _Skip(Exception):
            """Another run is mid-row: leave the try, clean up, skip."""

        wrote_wikistart = False
        wikistart_version = None
        skip_reason = None
        cleanup_errors: list[str] = []
        try:
            try:
                client.put_wiki_page(
                    template, template_body, "ticket 123 probe"
                )
                out = run()
                if out["style"] != "undeclared" and (
                    client.get_wiki_page("WikiStart") != original
                ):
                    # Another run appended its declaration after this
                    # run's first read of WikiStart (seen in an overlap
                    # repro, #173): its row, not a defect here.
                    raise _Skip(
                        "/trac_test WikiStart gained a declaration "
                        "since this run read it: another run is "
                        "mid-row"
                    )
                assert out["style"] == "undeclared"
                assert out["style_page"] is None
                probe = [
                    t for t in out["templates"] if t["name"] == template
                ]
                assert len(probe) == 1
                assert probe[0]["body"] == template_body

                client.put_wiki_page(
                    style_page, style_body, "ticket 123 probe"
                )
                sep = "" if original.endswith("\n") else "\n"
                wikistart_written = f"{original}{sep}\n{line}\n"
                # WikiStart is the one shared page. Re-read its version
                # and, at that version, its content, right before the
                # append, then append with that version: the write
                # itself refuses when anything landed since, so there
                # is no gap between the check and the write. Any change
                # since the first read means another run is between its
                # append and its restore; skipping leaves the probe
                # pages to the finally below.
                info = client.get_wiki_page_info("WikiStart")
                read_version = info["version"]
                current = client.get_wiki_page(
                    "WikiStart", version=read_version
                )
                if current != original:
                    raise _Skip(
                        "/trac_test WikiStart changed since this run "
                        "read it: another run is mid-row (this run's "
                        "probe pages are cleaned up first)"
                    )
                try:
                    stored = client.put_wiki_page(
                        "WikiStart",
                        wikistart_written,
                        "ticket 123 probe",
                        version=read_version,
                    )
                except WikiPageConflict:
                    raise _Skip(
                        "/trac_test WikiStart changed between this "
                        "run's re-read and its append: another run is "
                        "mid-row (this run's probe pages are cleaned "
                        "up first)"
                    ) from None
                except WikiPageStoredReadbackFailed:
                    # Stored, version unknown: assume nothing else
                    # wrote in between; the restore is version-checked
                    # and reports if that was wrong.
                    wrote_wikistart = True
                    wikistart_version = read_version + 1
                    raise
                wrote_wikistart = True
                wikistart_version = stored["version"]
                out = run()
                assert out["style"] == "probe"
                assert out["declaration_lines"] == [line]
                assert out["style_page"]["name"] == style_page
                assert out["style_page"]["body"] == style_body
                probe = [
                    t for t in out["templates"] if t["name"] == template
                ]
                assert probe and probe[0]["body"] == template_body
            except _Skip as skip:
                skip_reason = str(skip)
        finally:
            steps = []

            def restore_wikistart():
                # Only undo what this run did: restore at the version
                # this run's append produced. If anything has been
                # written since, the write is refused and not ours to
                # revert.
                try:
                    client.put_wiki_page(
                        "WikiStart",
                        original,
                        "ticket 123 restore",
                        version=wikistart_version,
                    )
                except WikiPageConflict:
                    raise RuntimeError(
                        "WikiStart changed under the run; left as found"
                    ) from None

            if wrote_wikistart:
                steps.append(("restore WikiStart", restore_wikistart))
            steps.append(
                (
                    f"delete {template}",
                    lambda: client.delete_wiki_page(template),
                )
            )
            steps.append(
                (
                    f"delete {style_page}",
                    lambda: client.delete_wiki_page(style_page),
                )
            )
            for label, step in steps:
                try:
                    step()
                except Exception as exc:
                    cleanup_errors.append(f"{label}: {exc!r}")
        # Reached only when the body passed or skipped: an earlier
        # failure is not masked by (and does not wait on) a cleanup
        # report. A skip does not hide a dirty instance, though.
        if cleanup_errors:
            pytest.fail(
                "/trac_test left dirty: "
                + "; ".join(cleanup_errors)
                + (
                    f" (the row also wanted to skip: {skip_reason})"
                    if skip_reason
                    else ""
                )
            )
        if skip_reason:
            pytest.skip(skip_reason)
