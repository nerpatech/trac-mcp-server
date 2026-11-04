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
from unittest.mock import MagicMock, patch

import pytest

from trac_mcp_server.mcp.tools.render_check import (
    RENDER_CHECK_SPECS,
    RENDER_CHECK_TOOLS,
    TICKET_RENDER_CHECK_TOOL,
    WIKI_RENDER_CHECK_TOOL,
    _handle_ticket_render_check,
    _handle_wiki_render_check,
    _missing_links_lines,
    _ticket_refs_line,
)

FIXTURES_DIR = (
    Path(__file__).parent.parent.parent / "fixtures" / "render_check"
)

# Ticket #151. Ticket anchors in the shapes the live daemon rendered
# them: #57 is `row04_bare_ticket_ref.html`'s, #3 is
# `convert_preview/row14_bare_ticket_ref.html`'s, and the dead one is
# `test_write_gate.py`'s captured `missing ticket` (no href).
_TICKET_57_ANCHOR = (
    '<a class="closed ticket" '
    'href="http://192.168.10.4:8000/trac_mcp_server/ticket/57" '
    'title="#57: defect: convert_preview: unconfigured_intertrac_prefix '
    'misses the realm form, ... (closed: fixed)">#57</a>'
)
_TICKET_3_ANCHOR = (
    '<a class="closed ticket" '
    'href="http://192.168.10.4:8000/trac_mcp_server/ticket/3" '
    'title="#3: defect: Expand DEFAULT_TICKET_TYPE docstring comment in '
    'mcp/tools/constants.py (closed: fixed)">#3</a>'
)
_DEAD_TICKET_ANCHOR = '<a class="missing ticket">#99999</a>'
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

    def test_bare_ticket_ref_is_not_a_warning_but_keeps_its_title(self):
        """Ticket #151. A bare `#57` to an existing ticket raises no
        warning, so a correct ticket reads `0 error(s), 0 warning(s)`;
        the resolved summary is still in `links.ticket` and on the
        `Ticket refs` line."""
        client = _mock_ticket_client("row04_bare_ticket_ref")
        result = self._run(
            client, {"ticket_id": 1, "check_targets": False}
        )
        section = result.structuredContent["sections"][0]
        self.assertEqual(section["warnings"], [])
        self.assertEqual(
            result.structuredContent["stats"]["warnings"], 0
        )
        entry = section["links"]["ticket"][0]
        self.assertEqual(entry["text"], "#57")
        self.assertTrue(entry["title"].startswith("#57: defect: "))
        self.assertFalse(entry["missing"])
        text = result.content[0].text
        self.assertIn("0 error(s), 0 warning(s)", text)
        self.assertNotIn("bare_ticket_ref", text)
        self.assertIn("Ticket refs (1): #57 defect: ", text)

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

    def test_backticked_short_link_surfaces_warning(self):
        """Ticket #58, pinned on the verify path and not only on
        `convert_preview`: a backticked InterTrac SHORT LINK
        (`auto_pm:#87`) is a reference meant to be a link, rendered
        inert. The check is shared, and the ticket confirmed the same
        silence reproduced through `wiki_render_check` -- so it is
        asserted on both paths, or a later change could fix one and
        leave the other."""
        client = _mock_wiki_client("row11_code_span_short_link")
        result = self._run(
            client, {"page_name": "Foo", "check_targets": False}
        )
        codes = [
            w["code"]
            for s in result.structuredContent["sections"]
            for w in s["warnings"]
        ]
        self.assertIn("link_ref_in_code_span", codes)

    def test_single_page_section(self):
        client = _mock_wiki_client("row01_clean")
        result = self._run(
            client, {"page_name": "Foo", "check_targets": False}
        )
        sections = result.structuredContent["sections"]
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["kind"], "page")
        self.assertTrue(sections[0]["source_paired"])


class TestRenderCheckTextBlock(unittest.TestCase):
    """Ticket #122: text-only MCP clients never see structuredContent,
    so the text block must carry the missing links, code blocks and
    (when requested) the HTML."""

    def _wiki(self, row, **extra):
        client = _mock_wiki_client(row)
        args = {"page_name": "Foo", "check_targets": False, **extra}
        return asyncio.run(_handle_wiki_render_check(client, args))

    def _ticket(self, row, **extra):
        client = _mock_ticket_client(row)
        args = {"ticket_id": 1, "check_targets": False, **extra}
        return asyncio.run(_handle_ticket_render_check(client, args))

    def test_links_line_counts_other_realm(self):
        text = self._wiki("row01_clean").content[0].text
        self.assertRegex(
            text,
            r"Links: \d+ ticket, \d+ wiki, \d+ external, \d+ other\.",
        )

    def test_missing_link_href_in_text(self):
        result = self._wiki("row02_missing_local_target")
        missing = [
            link
            for s in result.structuredContent["sections"]
            for realm in s["links"].values()
            for link in realm
            if link["missing"]
        ]
        self.assertTrue(missing)
        text = result.content[0].text
        self.assertIn("Missing links (", text)
        self.assertIsNotNone(missing[0]["href"])
        self.assertIn(missing[0]["href"], text)
        self.assertIn("[page] wiki: ", text)

    def test_html_total_budget_omits_later_sections(self):
        client = _mock_ticket_client(
            "row01_clean", ["row02_missing_local_target"]
        )
        args = {
            "ticket_id": 1,
            "check_targets": False,
            "include_html": True,
        }
        # Unpatched run first: the budget is sized from the extracted
        # section HTML, which is not byte-identical to the fixture body.
        baseline = asyncio.run(
            _handle_ticket_render_check(client, args)
        )
        first_html = baseline.structuredContent["sections"][0]["html"]
        with patch(
            "trac_mcp_server.mcp.tools.render_check.MAX_TEXT_HTML_BYTES",
            len(first_html.encode("utf-8")) + 10,
        ):
            result = asyncio.run(
                _handle_ticket_render_check(client, args)
            )
        text = result.content[0].text
        sections = result.structuredContent["sections"]
        self.assertIn("HTML [description]:", text)
        self.assertIn(sections[0]["html"], text)
        self.assertNotIn("HTML [comment 1]:", text)
        self.assertNotIn(sections[1]["html"], text)
        self.assertIn(
            "HTML omitted for 1 more section(s) ([comment 1])", text
        )
        self.assertIn("-byte text budget", text)
        self.assertIn("comment=<n>", text)
        # structuredContent keeps every section's html.
        self.assertIsNotNone(sections[1]["html"])

    def test_missing_link_no_href_and_whitespace_collapsed(self):
        section = {
            "kind": "comment",
            "ref": "3",
            "links": {
                "wiki": [
                    {"href": None, "text": "a\nb", "missing": True}
                ]
            },
        }
        self.assertEqual(
            _missing_links_lines([section]),
            [
                "Missing links (1):",
                '  [comment 3] wiki: (no href) "a b"',
            ],
        )

    def test_missing_link_long_text_truncated(self):
        section = {
            "kind": "page",
            "ref": None,
            "links": {
                "wiki": [
                    {"href": "/x", "text": "t" * 200, "missing": True}
                ]
            },
        }
        line = _missing_links_lines([section])[1]
        self.assertTrue(line.endswith('…"'), line)
        self.assertNotIn("t" * 81, line)

    def test_missing_link_long_href_capped(self):
        section = {
            "kind": "page",
            "ref": None,
            "links": {
                "wiki": [
                    {
                        "href": "/" + "h" * 500,
                        "text": "x",
                        "missing": True,
                    }
                ]
            },
        }
        line = _missing_links_lines([section])[1]
        self.assertNotIn("h" * 201, line)

    def test_no_missing_links_says_none(self):
        text = self._wiki("row01_clean").content[0].text
        self.assertIn("Missing links: none.", text)

    def test_ticket_html_in_text_when_requested(self):
        result = self._ticket("row01_clean", include_html=True)
        html = result.structuredContent["sections"][0]["html"]
        self.assertTrue(html)
        text = result.content[0].text
        self.assertIn("HTML [description]:", text)
        self.assertIn(html, text)

    def test_ticket_html_absent_from_text_by_default(self):
        text = self._ticket("row01_clean").content[0].text
        self.assertNotIn("HTML [", text)

    def test_html_truncation_header(self):
        with patch(
            "trac_mcp_server.mcp.tools.render_check.MAX_HTML_BYTES", 50
        ):
            result = self._ticket("row01_clean", include_html=True)
        self.assertTrue(
            result.structuredContent["sections"][0]["html_truncated"]
        )
        self.assertIn(
            "HTML [description] (truncated at 50 bytes):",
            result.content[0].text,
        )

    def test_ticket_refs_listed_once_in_first_seen_order(self):
        """Ticket #151. The same `#57` cited in the description and
        again in a later comment is listed ONCE, and the order is the
        order first seen across sections: #57 (description), #3
        (comment 1) -- not sorted by number, and not once per section.
        A missing ticket is left off this line; it stays an error and
        is listed under `Missing links`. None of it counts toward the
        totals."""
        page = (
            '<html><body><div class="description"><div class="searchable">'
            f"{_fixture_html_body('row04_bare_ticket_ref')}"
            "</div></div>"
            '<div class="change" id="trac-change-1-1000000000">'
            f'<div class="comment"><p>Also {_TICKET_3_ANCHOR}, and '
            f"{_TICKET_57_ANCHOR} again.</p></div></div>"
            '<div class="change" id="trac-change-2-1000000001">'
            f'<div class="comment"><p>Then {_TICKET_57_ANCHOR} and '
            f"{_DEAD_TICKET_ANCHOR}.</p></div></div>"
            "</body></html>"
        )
        client = _mock_ticket_client("row04_bare_ticket_ref")
        client.session.get.return_value.text = page
        client.get_ticket_changelog.return_value = [
            [
                "2026-09-01T00:00:00",
                "someone",
                "comment",
                str(n),
                src,
                True,
            ]
            for n, src in (
                (1, "Also #3, and #57 again."),
                (2, "Then #57 and #99999."),
            )
        ]
        result = asyncio.run(
            _handle_ticket_render_check(
                client, {"ticket_id": 1, "check_targets": False}
            )
        )
        text = result.content[0].text
        refs = [
            line
            for line in text.splitlines()
            if line.startswith("Ticket refs")
        ]
        self.assertEqual(len(refs), 1, text)
        self.assertEqual(
            refs[0],
            "Ticket refs (2): "
            "#57 defect: convert_preview: unconfigured_intertrac_prefix "
            "misses the realm form, ... (closed: fixed); "
            "#3 defect: Expand DEFAULT_TICKET_TYPE docstring comment in "
            "mcp/tools/constants.py (closed: fixed)",
        )
        self.assertIn("Missing links (1):", text)
        # The dead ticket is the one error; the line adds nothing to
        # either total.
        stats = result.structuredContent["stats"]
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(stats["warnings"], 1)
        self.assertIn("1 error(s), 1 warning(s)", text)

    def test_no_ticket_refs_says_none(self):
        text = self._wiki("row01_clean").content[0].text
        self.assertIn("Ticket refs: none.", text)

    def test_ticket_refs_line_comes_right_after_links_line(self):
        lines = self._ticket("row04_bare_ticket_ref").content[0].text
        lines = lines.splitlines()
        at = next(
            i
            for i, line in enumerate(lines)
            if line.startswith("Links:")
        )
        self.assertTrue(lines[at + 1].startswith("Ticket refs (1): "))

    def test_ticket_refs_merge_forms_and_prefer_a_summary_title(self):
        """`#18`, `ticket:18` and a `comment:` link to 18 all name one
        ticket (keyed by the href). The comment link's title is not a
        summary, so a later summary-shaped title replaces it; an entry
        with no title is just its number."""

        def link(href, text, title):
            return {
                "href": href,
                "text": text,
                "title": title,
                "missing": False,
            }

        sections = [
            {
                "links": {
                    "ticket": [
                        link(
                            "http://h/t/ticket/18#comment:2",
                            "comment:2:ticket:18",
                            "Comment 2 for #18: task: Do it (new)",
                        ),
                        link("http://h/t/ticket/7", "#7", None),
                    ]
                }
            },
            {
                "links": {
                    "ticket": [
                        link(
                            "http://h/t/ticket/18",
                            "ticket:18",
                            "#18: task: Do it (new)",
                        ),
                        link(
                            "http://h/t/ticket/18",
                            "#18",
                            "#18: task: something else",
                        ),
                    ]
                }
            },
        ]
        self.assertEqual(
            _ticket_refs_line(sections),
            "Ticket refs (2): #18 task: Do it (new); #7",
        )

    def test_ticket_refs_lone_comment_link_drops_the_title_prefix(self):
        """A ticket cited only through a `comment:` link has no
        `#N: ...` title to fall back on; Trac's `Comment M for #N: `
        prefix is dropped, not shown as the summary."""
        sections = [
            {
                "links": {
                    "ticket": [
                        {
                            "href": "http://h/t/ticket/18#comment:2",
                            "text": "comment:2:ticket:18",
                            "title": "Comment 2 for #18: task: Do it (new)",
                            "missing": False,
                        }
                    ]
                }
            }
        ]
        self.assertEqual(
            _ticket_refs_line(sections),
            "Ticket refs (1): #18 task: Do it (new)",
        )

    @staticmethod
    def _sections_with_tickets(count):
        return [
            {
                "links": {
                    "ticket": [
                        {
                            "href": f"http://h/t/ticket/{n}",
                            "text": f"#{n}",
                            "title": f"#{n}: task: Ticket {n} (new)",
                            "missing": False,
                        }
                        for n in range(1, count + 1)
                    ]
                }
            }
        ]

    def test_ticket_refs_line_is_capped_at_fifty_entries(self):
        """60 distinct tickets: the header keeps the full count, only
        the first 50 (first-seen order) are listed, and the tail says
        how many were left off and where they are."""
        line = _ticket_refs_line(self._sections_with_tickets(60))
        tail = (
            "; ... and 10 more (all in structuredContent links.ticket)"
        )
        self.assertTrue(line.endswith(tail), line)
        head = line[: -len(tail)]
        self.assertTrue(head.startswith("Ticket refs (60): "), line)
        entries = head[len("Ticket refs (60): ") :].split("; ")
        self.assertEqual(len(entries), 50, line)
        self.assertEqual(entries[0], "#1 task: Ticket 1 (new)")
        self.assertEqual(entries[-1], "#50 task: Ticket 50 (new)")

    def test_ticket_refs_line_at_the_cap_has_no_tail(self):
        line = _ticket_refs_line(self._sections_with_tickets(50))
        self.assertTrue(line.startswith("Ticket refs (50): "), line)
        self.assertEqual(len(line.split("; ")), 50, line)
        self.assertNotIn("more", line)

    def test_code_blocks_listed(self):
        result = self._wiki("row09_code_blocks_mixed")
        blocks = result.structuredContent["sections"][0]["code_blocks"]
        self.assertTrue(blocks)
        text = result.content[0].text
        self.assertIn(f"Code blocks ({len(blocks)}):", text)
        self.assertIn("line(s)", text)

    def test_no_code_blocks_says_none(self):
        text = self._wiki("row01_clean").content[0].text
        self.assertIn("Code blocks: none.", text)


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

    def test_project_milestones_reports_its_autolink_as_incidental(
        self,
    ):
        """Live content, and this test has already been inverted once.

        It was written as "finds a dead link on `ProjectMilestones`"
        and passed on `missing_local_target`. Ticket #79 measured what
        that finding actually was: the bare word `GitHub` in a prose
        sentence, which Trac auto-links because it is humped. Nothing
        on the page is broken and nothing was authored as a link -- so
        the old assertion was pinning the false positive, and under
        #64's blocking gate this correct page would have been refused.
        #79 inverted the assertion to require the advisory
        `incidental_wiki_autolink` code instead.

        Ticket #106 inverts it again. The operator is turning on
        `[wiki] ignore_missing_pages` on this instance (auto_pm:#155),
        which makes Trac's bare-word rule return the plain label
        instead of a `missing`-classed anchor -- so `GitHub` now
        renders as ordinary text, with no anchor and nothing for
        `_check_missing_local_target` to see at all. Neither code
        fires; the page reports zero warnings.

        Found by the live suite, which `ci.sh` does not run -- the
        offline suite is green across this change, since the offline
        fixtures pin the pre-option render and are unaffected.
        """
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
                    "include_html": True,
                },
            )
        )
        assert not result.isError
        warnings = [
            w
            for s in result.structuredContent["sections"]
            for w in s["warnings"]
        ]
        codes = [w["code"] for w in warnings]
        assert "incidental_wiki_autolink" not in codes, codes
        assert "missing_local_target" not in codes, codes
        html = "".join(
            s["html"] or ""
            for s in result.structuredContent["sections"]
        )
        assert '/wiki/GitHub"' not in html, html

    def test_ticket_115_replication_string_is_flagged(self):
        """The ticket's own seeded defect, pushed through the real
        renderer: a code span right before the closing bold quotes.
        The offline tests pin hand-copied HTML; this one fails if
        Trac's render of the string ever stops looking like that
        (`Rules/testing/RealSubstrateNotMocks`)."""
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient
        from trac_mcp_server.preview.checks import build_warnings
        from trac_mcp_server.preview.facts import extract_facts

        config, _ = bootstrap_config()
        client = TracClient(config)

        source = "'''label `code'''' then `second` code.\n"
        facts = extract_facts(client.wiki_to_html(source))
        found = [
            w
            for w in build_warnings(
                None,
                source,
                facts,
                probes={},
                check_targets=False,
                source_format="tracwiki",
            )
            if w["code"] == "broken_inline_markup_in_render"
        ]
        assert found, "the replication string rendered clean"
        assert all(w["severity"] == "warning" for w in found)
        # The ticket's shape specifically, not just any of the four.
        assert "code_span_with_quote_run" in {
            w["evidence"]["pattern"] for w in found
        }, found


if __name__ == "__main__":
    unittest.main()
