"""Tests for the ticket_render_check / wiki_render_check MCP tools
(ticket #55).

Handler-level tests build a synthetic ticket page (the same `<div
class="description">`/`<div class="change" id="trac-change-N-...">`
shapes confirmed live against tickets #55 and #57 while building this
feature) so the tool wiring -- scoping, pairing, comment filtering,
include_html truncation, error mapping -- is covered without a server.
A live-marked class at the bottom re-runs the ticket's own motivating
scenario against the real daemon (`--run-live`, see conftest.py).
"""

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trac_mcp_server.mcp.tools.render_check import (
    RENDER_CHECK_SPECS,
    RENDER_CHECK_TOOLS,
    TICKET_RENDER_CHECK_TOOL,
    WIKI_RENDER_CHECK_TOOL,
    _handle_ticket_render_check,
    _handle_wiki_render_check,
)

FIXTURES_DIR = (
    Path(__file__).parent.parent.parent / "fixtures" / "render_check"
)
MANIFEST = json.loads((FIXTURES_DIR / "manifest.json").read_text())

BASE_URL = "http://192.168.10.4:8000/trac_mcp_server"


def _fixture_html_body(name: str) -> str:
    """The captured fixture's HTML, unwrapped to a bare inner fragment
    (fixtures are already just the rendered fragment)."""
    return (FIXTURES_DIR / f"{name}.html").read_text()


def _fixture_tracwiki(name: str) -> str:
    return (FIXTURES_DIR / f"{name}.tracwiki.txt").read_text()


def _ticket_page_html(
    description_row: str, comment_rows: list[str]
) -> str:
    changes = "".join(
        f'<div class="change" id="trac-change-{i + 1}-100000000{i}">'
        f'<div class="comment">{_fixture_html_body(row)}</div>'
        "</div>"
        for i, row in enumerate(comment_rows)
    )
    return (
        '<html><body><div class="description"><div class="searchable">'
        f"{_fixture_html_body(description_row)}"
        "</div></div>"
        f"{changes}"
        "</body></html>"
    )


def _mock_ticket_client(
    description_row: str,
    comment_rows: list[str] | None = None,
    status_code: int = 200,
) -> MagicMock:
    comment_rows = comment_rows or []
    client = MagicMock()
    client.config.trac_url = BASE_URL

    page_response = MagicMock()
    page_response.status_code = status_code
    page_response.text = _ticket_page_html(
        description_row, comment_rows
    )
    client.session.get.return_value = page_response

    client.get_ticket.return_value = [
        1,
        "2026-09-01T00:00:00",
        "2026-09-01T00:00:00",
        {"description": _fixture_tracwiki(description_row)},
    ]
    client.get_ticket_changelog.return_value = [
        [
            "2026-09-01T00:00:00",
            "someone",
            "comment",
            str(i + 1),
            _fixture_tracwiki(row),
            True,
        ]
        for i, row in enumerate(comment_rows)
    ]
    return client


def _mock_wiki_client(row_name: str) -> MagicMock:
    client = MagicMock()
    client.config.trac_url = BASE_URL
    client.get_wiki_page_html.return_value = (
        f"<html><body>{_fixture_html_body(row_name)}</body></html>"
    )
    client.get_wiki_page.return_value = _fixture_tracwiki(row_name)
    return client


class TestRenderCheckToolDefinitions(unittest.TestCase):
    def test_two_tools_defined(self):
        self.assertEqual(len(RENDER_CHECK_TOOLS), 2)

    def test_tool_names(self):
        names = {t.name for t in RENDER_CHECK_TOOLS}
        self.assertEqual(
            names, {"ticket_render_check", "wiki_render_check"}
        )

    def test_both_read_only(self):
        for tool in RENDER_CHECK_TOOLS:
            self.assertTrue(tool.annotations.readOnlyHint)

    def test_ticket_required_args(self):
        self.assertEqual(
            TICKET_RENDER_CHECK_TOOL.inputSchema["required"],
            ["ticket_id"],
        )

    def test_wiki_required_args(self):
        self.assertEqual(
            WIKI_RENDER_CHECK_TOOL.inputSchema["required"],
            ["page_name"],
        )

    def test_include_html_defaults_false(self):
        """Opposite of convert_preview's default -- the structured
        result is the point of this tool; raw HTML is the escape hatch."""
        for tool in RENDER_CHECK_TOOLS:
            self.assertFalse(
                tool.inputSchema["properties"]["include_html"][
                    "default"
                ]
            )

    def test_permissions(self):
        by_name = {s.tool.name: s for s in RENDER_CHECK_SPECS}
        self.assertEqual(
            by_name["ticket_render_check"].permissions,
            frozenset({"TICKET_VIEW"}),
        )
        self.assertEqual(
            by_name["wiki_render_check"].permissions,
            frozenset({"WIKI_VIEW"}),
        )


class TestTicketRenderCheckHandler(unittest.TestCase):
    def _run(self, client, args):
        return asyncio.run(_handle_ticket_render_check(client, args))

    def test_missing_ticket_id_is_validation_error(self):
        client = _mock_ticket_client("row01_clean")
        result = self._run(client, {})
        self.assertTrue(result.isError)

    def test_404_is_not_found(self):
        client = _mock_ticket_client("row01_clean", status_code=404)
        result = self._run(client, {"ticket_id": 999})
        self.assertTrue(result.isError)
        self.assertIn("not_found", result.content[0].text)

    def test_missing_description_div_is_server_error(self):
        client = _mock_ticket_client("row01_clean")
        client.session.get.return_value.text = (
            "<html><body>nothing here</body></html>"
        )
        result = self._run(client, {"ticket_id": 1})
        self.assertTrue(result.isError)

    def test_clean_description_produces_no_warnings(self):
        client = _mock_ticket_client("row01_clean")
        result = self._run(
            client, {"ticket_id": 1, "check_targets": False}
        )
        self.assertFalse(result.isError)
        self.assertEqual(
            result.structuredContent["stats"]["warnings"], 0
        )
        self.assertEqual(
            result.structuredContent["stats"]["sections"], 1
        )

    def test_defective_description_surfaces_warning(self):
        client = _mock_ticket_client("row02_missing_local_target")
        result = self._run(
            client, {"ticket_id": 1, "check_targets": False}
        )
        codes = [
            w["code"]
            for s in result.structuredContent["sections"]
            for w in s["warnings"]
        ]
        self.assertIn("missing_local_target", codes)

    def test_comments_included_by_default(self):
        client = _mock_ticket_client(
            "row01_clean", ["row05_link_ref_in_code_span"]
        )
        result = self._run(
            client, {"ticket_id": 1, "check_targets": False}
        )
        kinds = [
            s["kind"] for s in result.structuredContent["sections"]
        ]
        self.assertEqual(kinds, ["description", "comment"])
        comment_codes = [
            w["code"]
            for w in result.structuredContent["sections"][1]["warnings"]
        ]
        self.assertIn("link_ref_in_code_span", comment_codes)

    def test_include_comments_false_skips_comments(self):
        client = _mock_ticket_client(
            "row01_clean", ["row05_link_ref_in_code_span"]
        )
        result = self._run(
            client,
            {
                "ticket_id": 1,
                "check_targets": False,
                "include_comments": False,
            },
        )
        kinds = [
            s["kind"] for s in result.structuredContent["sections"]
        ]
        self.assertEqual(kinds, ["description"])

    def test_comment_filter_selects_one_comment(self):
        client = _mock_ticket_client(
            "row01_clean",
            [
                "row05_link_ref_in_code_span",
                "row02_missing_local_target",
            ],
        )
        result = self._run(
            client,
            {"ticket_id": 1, "check_targets": False, "comment": 2},
        )
        sections = result.structuredContent["sections"]
        self.assertEqual(
            [(s["kind"], s["ref"]) for s in sections],
            [("description", None), ("comment", "2")],
        )

    def test_comment_filter_unknown_number_is_not_found(self):
        client = _mock_ticket_client(
            "row01_clean", ["row05_link_ref_in_code_span"]
        )
        result = self._run(
            client,
            {"ticket_id": 1, "check_targets": False, "comment": 99},
        )
        self.assertTrue(result.isError)

    def test_include_html_false_omits_html(self):
        client = _mock_ticket_client("row01_clean")
        result = self._run(
            client, {"ticket_id": 1, "check_targets": False}
        )
        section = result.structuredContent["sections"][0]
        self.assertIsNone(section["html"])
        self.assertFalse(section["html_truncated"])

    def test_include_html_true_includes_html(self):
        client = _mock_ticket_client("row01_clean")
        result = self._run(
            client,
            {
                "ticket_id": 1,
                "check_targets": False,
                "include_html": True,
            },
        )
        section = result.structuredContent["sections"][0]
        self.assertIsNotNone(section["html"])

    def test_links_grouped_by_realm(self):
        client = _mock_ticket_client("row04_bare_ticket_ref")
        result = self._run(
            client, {"ticket_id": 1, "check_targets": False}
        )
        links = result.structuredContent["sections"][0]["links"]
        self.assertEqual(len(links["ticket"]), 1)
        self.assertEqual(links["ticket"][0]["text"], "#57")

    def test_unpaired_section_notes_the_skip(self):
        """A comment whose changelog entry can't be found gets an
        explicit source_not_paired warning, never a silent pass
        (Rules/testing/InstrumentDontInfer)."""
        client = _mock_ticket_client(
            "row01_clean", ["row05_link_ref_in_code_span"]
        )
        client.get_ticket_changelog.return_value = []
        result = self._run(
            client, {"ticket_id": 1, "check_targets": False}
        )
        comment_section = result.structuredContent["sections"][1]
        self.assertFalse(comment_section["source_paired"])
        codes = [w["code"] for w in comment_section["warnings"]]
        self.assertIn("source_not_paired", codes)


class TestWikiRenderCheckHandler(unittest.TestCase):
    def _run(self, client, args):
        return asyncio.run(_handle_wiki_render_check(client, args))

    def test_missing_page_name_is_validation_error(self):
        client = _mock_wiki_client("row01_clean")
        result = self._run(client, {})
        self.assertTrue(result.isError)

    def test_clean_page_produces_no_warnings(self):
        client = _mock_wiki_client("row01_clean")
        result = self._run(
            client, {"page_name": "Foo", "check_targets": False}
        )
        self.assertFalse(result.isError)
        self.assertEqual(
            result.structuredContent["stats"]["warnings"], 0
        )

    def test_defective_page_surfaces_warning(self):
        client = _mock_wiki_client("row02_missing_local_target")
        result = self._run(
            client, {"page_name": "Foo", "check_targets": False}
        )
        codes = [
            w["code"]
            for s in result.structuredContent["sections"]
            for w in s["warnings"]
        ]
        self.assertIn("missing_local_target", codes)

    def test_single_page_section(self):
        client = _mock_wiki_client("row01_clean")
        result = self._run(
            client, {"page_name": "Foo", "check_targets": False}
        )
        sections = result.structuredContent["sections"]
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["kind"], "page")
        self.assertTrue(sections[0]["source_paired"])


@pytest.mark.live
class TestRenderCheckLive:
    """Re-runs ticket #57's own headline scenario -- an InterTrac
    dispatcher link resolving through its redirect -- against the real
    daemon, plus a re-verify of ticket #55 itself, so the checked-in
    fixtures/mocked shapes can't silently drift from what Trac actually
    emits (`Rules/testing/RealSubstrateNotMocks`,
    `Rules/testing/LiveAcceptanceIsForwardDiscovery`). Requires
    --run-live and live Trac credentials (see conftest.py / .env).
    """

    def test_ticket_57_resolves_intertrac_dispatcher_target(self):
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient

        config, _ = bootstrap_config()
        client = TracClient(config)

        result = asyncio.run(
            _handle_ticket_render_check(
                client, {"ticket_id": 57, "check_targets": True}
            )
        )
        assert not result.isError
        resolved = [
            link["resolved_url"]
            for section in result.structuredContent["sections"]
            for link in section["links"]["external"]
            if link["href"] and "/intertrac/wiki%3A" in link["href"]
        ]
        assert resolved, "expected at least one InterTrac wiki link"
        assert any(r and "/intertrac/" not in r for r in resolved), (
            "expected the dispatcher href resolved to a real page URL"
        )

    def test_ticket_55_render_check_runs_clean_end_to_end(self):
        """The ticket's own acceptance bar: one call reproduces what its
        curl+grep workaround produced by hand."""
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient

        config, _ = bootstrap_config()
        client = TracClient(config)

        result = asyncio.run(
            _handle_ticket_render_check(
                client, {"ticket_id": 55, "check_targets": True}
            )
        )
        assert not result.isError
        assert result.structuredContent["stats"]["sections"] >= 1

    def test_wiki_render_check_finds_dead_link_on_project_milestones(
        self,
    ):
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient

        config, _ = bootstrap_config()
        client = TracClient(config)

        result = asyncio.run(
            _handle_wiki_render_check(
                client,
                {
                    "page_name": "ProjectMilestones",
                    "check_targets": False,
                },
            )
        )
        assert not result.isError
        codes = [
            w["code"]
            for s in result.structuredContent["sections"]
            for w in s["warnings"]
        ]
        assert "missing_local_target" in codes


if __name__ == "__main__":
    unittest.main()
