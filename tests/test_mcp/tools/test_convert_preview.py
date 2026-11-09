"""Tests for the convert_preview MCP tool (ticket #56).

Handler-level tests mock TracClient.wiki_to_html with the same checked-in
fixtures test_preview_checks.py uses, so the tool wiring (format handling,
include_html truncation, check_targets on/off, structuredContent shape) is
covered without a server. A live-marked test at the bottom re-renders
against the real daemon so the fixtures can't silently drift from what
Trac actually emits -- gated behind ``--run-live`` (see conftest.py).
"""

import asyncio
import json
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trac_mcp_server.mcp.tools.convert_preview import (
    CONVERT_PREVIEW_SPECS,
    CONVERT_PREVIEW_TOOLS,
    MAX_HTML_BYTES,
    _handle_convert_preview,
)
from trac_mcp_server.preview.targets import (
    DEFAULT_TARGET_TIMEOUT_SECONDS,
)

FIXTURES_DIR = (
    Path(__file__).parent.parent.parent / "fixtures" / "convert_preview"
)
MANIFEST = json.loads((FIXTURES_DIR / "manifest.json").read_text())


def _fixture_html(name: str) -> str:
    return (FIXTURES_DIR / f"{name}.html").read_text()


def _mock_client(html: str) -> MagicMock:
    client = MagicMock()
    client.wiki_to_html = MagicMock(return_value=html)
    return client


class TestConvertPreviewToolDefinition(unittest.TestCase):
    def test_one_tool_defined(self):
        self.assertEqual(len(CONVERT_PREVIEW_TOOLS), 1)

    def test_tool_name(self):
        self.assertEqual(
            CONVERT_PREVIEW_TOOLS[0].name, "convert_preview"
        )

    def test_read_only_hint(self):
        """Must be readOnlyHint=True -- makes the tool usable in plan
        mode, and it is the whole point of a tool with no write."""
        self.assertTrue(
            CONVERT_PREVIEW_TOOLS[0].annotations.readOnlyHint
        )

    def test_no_permissions_required(self):
        self.assertEqual(
            CONVERT_PREVIEW_SPECS[0].permissions, frozenset()
        )

    def test_description_names_local_captured_tail_links(self):
        """Ticket #163: the captured-punctuation entry covers the local
        `ticket:`/`wiki:` short links too, not only cross-instance."""
        description = CONVERT_PREVIEW_TOOLS[0].description
        self.assertIn("swallowed trailing punctuation", description)
        self.assertIn("local 'ticket:N' or 'wiki:Page'", description)

    def test_content_required(self):
        schema = CONVERT_PREVIEW_TOOLS[0].inputSchema
        self.assertEqual(schema["required"], ["content"])


class TestConvertPreviewHandler(unittest.TestCase):
    def _run(self, client, args):
        return asyncio.run(_handle_convert_preview(client, args))

    def test_missing_content_is_validation_error(self):
        client = _mock_client("<p>x</p>")
        result = self._run(client, {})
        self.assertTrue(result.isError)

    def test_invalid_format_is_validation_error(self):
        client = _mock_client("<p>x</p>")
        result = self._run(client, {"content": "x", "format": "html"})
        self.assertTrue(result.isError)

    def test_unrepresentable_footnote_is_a_validation_error(self):
        """A refused conversion must not surface as "unknown_tool".

        Ticket #63 option (c) makes the converter raise ValueError on a
        Markdown footnote it cannot represent.  The dispatcher in
        ``mcp/server.py`` maps a bare ValueError to ``unknown_tool`` with the
        hint "Use list_tools to see available tools" -- which would be
        actively misleading for a content problem the caller can fix, so the
        handler catches it and reports a validation error naming the escape
        hatch instead.
        """
        client = _mock_client("<p>x</p>")
        result = self._run(
            client, {"content": "Text[^1]\n\n[^1]: note\n"}
        )
        self.assertTrue(result.isError)
        payload = json.dumps(
            [c.text for c in result.content if hasattr(c, "text")]
        )
        self.assertIn("validation_error", payload)
        self.assertNotIn("unknown_tool", payload)
        self.assertIn("tracwiki", payload)

    def test_legacy_export_fence_is_a_validation_error(self):
        """A ``tracwiki-unconverted`` fence is refused (#214), reported
        as a validation error and not as "unknown_tool"."""
        client = _mock_client("<p>x</p>")
        result = self._run(
            client, {"content": "```tracwiki-unconverted\n= x =\n```\n"}
        )
        self.assertTrue(result.isError)
        payload = json.dumps(
            [c.text for c in result.content if hasattr(c, "text")]
        )
        self.assertIn("validation_error", payload)
        self.assertNotIn("unknown_tool", payload)
        self.assertIn("tracwiki-unconverted", payload)
        client.wiki_to_html.assert_not_called()

    def test_representable_content_still_converts(self):
        """Recall gate for the check above -- ordinary content is unaffected."""
        client = _mock_client("<p>x</p>")
        result = self._run(
            client, {"content": "Plain **text** here.\n"}
        )
        self.assertFalse(result.isError)

    def test_clean_markdown_produces_no_warnings(self):
        html = _fixture_html("row01_intertrac_wiki")
        client = _mock_client(html)
        result = self._run(
            client,
            {
                "content": MANIFEST["row01_intertrac_wiki"][
                    "markdown_input"
                ],
                "check_targets": False,
            },
        )
        self.assertFalse(result.isError)
        # check_targets=False on a row with a probeable target still
        # notes that (target_check_disabled) -- that is not itself a
        # defect warning, so it's the only thing expected here.
        codes = [
            w["code"] for w in result.structuredContent["warnings"]
        ]
        self.assertEqual(codes, ["target_check_disabled"])
        self.assertEqual(
            result.structuredContent["tracwiki"],
            MANIFEST["row01_intertrac_wiki"]["tracwiki"],
        )

    def test_defective_markdown_surfaces_warning(self):
        html = _fixture_html("row11_code_span_intertrac")
        client = _mock_client(html)
        result = self._run(
            client,
            {
                "content": MANIFEST["row11_code_span_intertrac"][
                    "markdown_input"
                ],
                "check_targets": False,
            },
        )
        codes = [
            w["code"] for w in result.structuredContent["warnings"]
        ]
        self.assertIn("link_ref_in_code_span", codes)

    def test_tracwiki_format_skips_conversion(self):
        client = _mock_client("<p>hi</p>")
        result = self._run(
            client,
            {
                "content": "'''hi'''",
                "format": "tracwiki",
                "check_targets": False,
            },
        )
        self.assertEqual(
            result.structuredContent["tracwiki"], "'''hi'''"
        )
        client.wiki_to_html.assert_called_once_with("'''hi'''")

    def test_include_html_false_omits_html(self):
        html = _fixture_html("row01_intertrac_wiki")
        client = _mock_client(html)
        result = self._run(
            client,
            {
                "content": "text",
                "format": "tracwiki",
                "check_targets": False,
                "include_html": False,
            },
        )
        self.assertIsNone(result.structuredContent["rendered_html"])
        self.assertFalse(result.structuredContent["html_truncated"])

    def test_large_html_is_truncated(self):
        big_html = "<p>" + ("x" * (MAX_HTML_BYTES + 500)) + "</p>"
        client = _mock_client(big_html)
        result = self._run(
            client,
            {
                "content": "text",
                "format": "tracwiki",
                "check_targets": False,
            },
        )
        self.assertTrue(result.structuredContent["html_truncated"])
        self.assertLessEqual(
            len(
                result.structuredContent["rendered_html"].encode(
                    "utf-8"
                )
            ),
            MAX_HTML_BYTES,
        )
        # Warnings must still reflect the FULL render, not the truncated
        # copy -- truncation is a payload-size concern, not an analysis one.
        self.assertEqual(
            result.structuredContent["stats"]["anchors"], 0
        )

    def test_check_targets_false_skips_probe_and_notes_it(self):
        html = _fixture_html("row01_intertrac_wiki")
        client = _mock_client(html)
        result = self._run(
            client,
            {
                "content": MANIFEST["row01_intertrac_wiki"][
                    "markdown_input"
                ],
                "check_targets": False,
            },
        )
        codes = [
            w["code"] for w in result.structuredContent["warnings"]
        ]
        self.assertIn("target_check_disabled", codes)
        self.assertEqual(
            result.structuredContent["stats"]["targets_checked"], 0
        )

    def test_literal_markup_in_render_reaches_the_tool(self):
        """Ticket #77's seed, at the tool boundary.

        The defect was observable HERE -- `convert_preview` returned
        `warnings: []` on content both render-check tools flagged -- so a
        check-level test alone would not have caught it: the check
        existed and passed its own tests throughout. Fixture comes from
        the render_check corpus, since that is where the seed was
        captured from the live renderer.
        """
        render_dir = (
            Path(__file__).parent.parent.parent
            / "fixtures"
            / "render_check"
        )
        name = "row12_markdown_link_unconverted"
        client = _mock_client((render_dir / f"{name}.html").read_text())
        result = self._run(
            client,
            {
                "content": (
                    render_dir / f"{name}.tracwiki.txt"
                ).read_text(),
                "format": "tracwiki",
                "check_targets": False,
            },
        )
        self.assertFalse(result.isError)
        codes = [
            w["code"] for w in result.structuredContent["warnings"]
        ]
        self.assertIn("literal_markup_in_render", codes)

    def test_the_same_markup_in_a_code_block_stays_silent_at_the_tool(
        self,
    ):
        """The over-correction pin at the same boundary: identical
        bytes inside a `{{{ }}}` block, no warning. Without this, the
        test above passes just as well for a check that fires on
        everything."""
        render_dir = (
            Path(__file__).parent.parent.parent
            / "fixtures"
            / "render_check"
        )
        name = "row13_markdown_link_in_code_block_pin"
        client = _mock_client((render_dir / f"{name}.html").read_text())
        result = self._run(
            client,
            {
                "content": (
                    render_dir / f"{name}.tracwiki.txt"
                ).read_text(),
                "format": "tracwiki",
                "check_targets": False,
            },
        )
        self.assertEqual(result.structuredContent["warnings"], [])


def _twelve_distinct_dead_targets() -> str:
    """Twelve DISTINCT dead cross-instance targets, one per line.

    Distinct on purpose: `probe_targets` deduplicates, so repeating one
    href twelve times is two unique targets and tests nothing about the
    cap. The prefix resolves only on this project's own instance, which
    is where the live tests point (`bootstrap_config` reads TRAC_URL);
    on the auto_pm instance `auto_pm:` is not a configured prefix at
    all and these render as plain text.
    """
    return "\n\n".join(
        f"auto_pm:wiki:Rules/trac/DoesNotExistTicket80{n}"
        for n in range(12)
    )


class TestConvertPreviewTarget(unittest.TestCase):
    """Ticket #126: an optional page_name / ticket_id gives the preview
    the context the write gate has, so a bare `attachment:` link to a
    file the target holds is not reported dead and `comment:N` is
    checked against the ticket's comments."""

    # The context-free render of a bare attachment link (#113) and of a
    # bare comment reference (#105); the same shapes the write-gate
    # tests use.
    ATTACHMENT_HTML = (
        '<p>\nSee <a class="missing attachment">x</a> here.\n</p>\n'
    )
    ATTACHMENT_SOURCE = "See [attachment:f.txt x] here."
    COMMENT_HTML = "<p>\nSee comment:99 for context.\n</p>\n"
    COMMENT_SOURCE = "See comment:99 for context."

    def _run(self, client, args):
        return asyncio.run(_handle_convert_preview(client, args))

    def _attachment_client(self):
        client = _mock_client(self.ATTACHMENT_HTML)
        client.list_ticket_attachments.return_value = [
            ["f.txt", "desc", 3, 0, "someone"]
        ]
        return client

    def _args(self, **extra):
        args = {
            "content": self.ATTACHMENT_SOURCE,
            "format": "tracwiki",
            "check_targets": False,
        }
        args.update(extra)
        return args

    def _codes(self, result):
        return [w["code"] for w in result.structuredContent["warnings"]]

    def _text(self, result):
        return "\n".join(
            c.text for c in result.content if hasattr(c, "text")
        )

    def _changelog_client(self):
        client = _mock_client(self.COMMENT_HTML)
        client.get_ticket_changelog.return_value = [
            ("ts", "a", "comment", "1", "one"),
            ("ts", "a", "comment", "2", "two"),
        ]
        return client

    def test_schema_declares_both_args(self):
        props = CONVERT_PREVIEW_TOOLS[0].inputSchema["properties"]
        self.assertEqual(props["page_name"]["type"], "string")
        self.assertEqual(props["ticket_id"]["type"], "integer")
        self.assertEqual(props["ticket_id"]["minimum"], 1)

    def test_ticket_target_clears_a_held_attachment(self):
        client = self._attachment_client()
        result = self._run(client, self._args(ticket_id=126))
        self.assertFalse(result.isError)
        self.assertNotIn("missing_local_target", self._codes(result))
        client.list_ticket_attachments.assert_called_once_with(126)
        self.assertEqual(
            result.structuredContent["target"],
            {
                "realm": "ticket",
                "id": 126,
                "attachments_checked": True,
                "comments_checked": None,
            },
        )
        lines = self._text(result).splitlines()
        self.assertTrue(lines[0].startswith("convert_preview:"))
        self.assertEqual(
            lines[1], "Checked against: ticket 126 (1 attachment(s))"
        )

    def test_no_target_keeps_the_error_and_does_no_lookup(self):
        client = self._attachment_client()
        result = self._run(client, self._args())
        found = [
            w
            for w in result.structuredContent["warnings"]
            if w["code"] == "missing_local_target"
        ]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "error")
        self.assertIsNone(result.structuredContent["target"])
        self.assertNotIn("Checked against", self._text(result))
        client.list_ticket_attachments.assert_not_called()
        client.list_wiki_attachments.assert_not_called()

    def test_file_not_held_stays_an_error(self):
        client = self._attachment_client()
        client.list_ticket_attachments.return_value = [
            ["other.txt", "d", 1, 0, "u"]
        ]
        result = self._run(client, self._args(ticket_id=126))
        found = [
            w
            for w in result.structuredContent["warnings"]
            if w["code"] == "missing_local_target"
        ]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "error")
        self.assertIs(
            result.structuredContent["target"]["attachments_checked"],
            True,
        )

    def test_failed_lookup_stays_an_error_and_says_so(self):
        client = self._attachment_client()
        client.list_ticket_attachments.side_effect = RuntimeError(
            "boom"
        )
        result = self._run(client, self._args(ticket_id=126))
        self.assertFalse(result.isError)
        found = [
            w
            for w in result.structuredContent["warnings"]
            if w["code"] == "missing_local_target"
        ]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "error")
        self.assertIs(
            result.structuredContent["target"]["attachments_checked"],
            False,
        )
        self.assertIn(
            "Checked against: ticket 126 (attachment list unavailable: "
            "bare attachment: links stay errors)",
            self._text(result),
        )

    def test_page_target_clears_a_held_attachment(self):
        client = _mock_client(self.ATTACHMENT_HTML)
        client.list_wiki_attachments.return_value = ["Foo/Bar/f.txt"]
        result = self._run(client, self._args(page_name="Foo/Bar"))
        self.assertNotIn("missing_local_target", self._codes(result))
        client.list_wiki_attachments.assert_called_once_with("Foo/Bar")
        client.list_ticket_attachments.assert_not_called()
        self.assertEqual(
            result.structuredContent["target"],
            {
                "realm": "wiki",
                "id": "Foo/Bar",
                "attachments_checked": True,
                "comments_checked": None,
            },
        )
        self.assertIn(
            "Checked against: wiki page Foo/Bar (1 attachment(s))",
            self._text(result),
        )

    def test_both_targets_is_a_validation_error(self):
        client = self._attachment_client()
        result = self._run(
            client, self._args(page_name="Foo", ticket_id=5)
        )
        self.assertTrue(result.isError)
        self.assertIn("validation_error", self._text(result))
        client.wiki_to_html.assert_not_called()

    def test_empty_page_name_beats_exclusivity(self):
        client = self._attachment_client()
        result = self._run(
            client, self._args(page_name="", ticket_id=5)
        )
        self.assertTrue(result.isError)
        text = self._text(result)
        self.assertIn("page_name must be a non-empty string", text)
        self.assertNotIn("mutually exclusive", text)

    def test_bad_ticket_id_is_a_validation_error(self):
        for bad in (0, -3, True, "5", 1.5):
            client = self._attachment_client()
            result = self._run(client, self._args(ticket_id=bad))
            self.assertTrue(result.isError, bad)
            self.assertIn("validation_error", self._text(result))

    def test_bad_page_name_is_a_validation_error(self):
        for bad in ("", 5, ["Foo"]):
            client = self._attachment_client()
            result = self._run(client, self._args(page_name=bad))
            self.assertTrue(result.isError, bad)
            self.assertIn("validation_error", self._text(result))

    def test_no_attachment_in_content_means_no_attachment_lookup(self):
        client = _mock_client("<p>plain</p>")
        result = self._run(
            client, self._args(content="plain", ticket_id=126)
        )
        client.list_ticket_attachments.assert_not_called()
        self.assertEqual(
            result.structuredContent["target"],
            {
                "realm": "ticket",
                "id": 126,
                "attachments_checked": None,
                "comments_checked": None,
            },
        )
        self.assertIn("Checked against: ticket 126", self._text(result))
        self.assertNotIn("attachment(s)", self._text(result))
        self.assertNotIn("unavailable", self._text(result))

    def test_ticket_target_runs_dangling_comment_ref(self):
        client = self._changelog_client()
        result = self._run(
            client,
            self._args(content=self.COMMENT_SOURCE, ticket_id=126),
        )
        self.assertIn("dangling_comment_ref", self._codes(result))
        client.get_ticket_changelog.assert_called_once_with(126)
        self.assertIs(
            result.structuredContent["target"]["comments_checked"], True
        )

    def test_known_comment_is_not_dangling(self):
        client = self._changelog_client()
        result = self._run(
            client,
            self._args(content="See comment:2 here.", ticket_id=126),
        )
        self.assertNotIn("dangling_comment_ref", self._codes(result))

    def test_no_target_skips_dangling_comment_ref(self):
        client = self._changelog_client()
        result = self._run(
            client, self._args(content=self.COMMENT_SOURCE)
        )
        self.assertNotIn("dangling_comment_ref", self._codes(result))
        client.get_ticket_changelog.assert_not_called()

    def test_changelog_not_fetched_without_comment_in_content(self):
        client = self._changelog_client()
        self._run(
            client, self._args(content="plain text", ticket_id=126)
        )
        client.get_ticket_changelog.assert_not_called()

    def test_page_target_never_fetches_the_changelog(self):
        client = self._changelog_client()
        result = self._run(
            client,
            self._args(content=self.COMMENT_SOURCE, page_name="Foo"),
        )
        client.get_ticket_changelog.assert_not_called()
        self.assertIsNone(
            result.structuredContent["target"]["comments_checked"]
        )
        self.assertNotIn("dangling_comment_ref", self._codes(result))

    def test_failed_comment_lookup_says_so_and_is_not_clean(self):
        client = _mock_client(self.COMMENT_HTML)
        client.get_ticket_changelog.side_effect = RuntimeError("boom")
        result = self._run(
            client,
            self._args(content=self.COMMENT_SOURCE, ticket_id=126),
        )
        self.assertFalse(result.isError)
        self.assertIn("comment list unavailable", self._text(result))
        self.assertIn(
            "(comment list unavailable: comment: links not checked)",
            self._text(result),
        )
        self.assertIs(
            result.structuredContent["target"]["comments_checked"],
            False,
        )
        self.assertNotIn("dangling_comment_ref", self._codes(result))

    def test_both_lookups_failing_emit_both_clauses_in_order(self):
        client = _mock_client(self.ATTACHMENT_HTML)
        client.list_ticket_attachments.side_effect = RuntimeError("a")
        client.get_ticket_changelog.side_effect = RuntimeError("c")
        result = self._run(
            client,
            self._args(
                content=self.ATTACHMENT_SOURCE + " and comment:1",
                ticket_id=126,
            ),
        )
        self.assertIn(
            "Checked against: ticket 126 (attachment list unavailable: "
            "bare attachment: links stay errors) (comment list "
            "unavailable: comment: links not checked)",
            self._text(result),
        )

    def test_unneeded_lookups_are_none_not_false(self):
        client = _mock_client("<p>plain</p>")
        result = self._run(
            client, self._args(content="plain text", ticket_id=126)
        )
        target = result.structuredContent["target"]
        self.assertIsNone(target["attachments_checked"])
        self.assertIsNone(target["comments_checked"])
        self.assertNotIn("unavailable", self._text(result))


@pytest.mark.live
class TestConvertPreviewLive:
    """Re-renders the acceptance-suite rows against the real daemon, so
    the checked-in fixtures can't silently drift from what Trac actually
    emits (`Rules/testing/RealSubstrateNotMocks`). Requires --run-live and
    live Trac credentials (see conftest.py / .env)."""

    def test_row15_missing_cross_instance_target_is_caught_live(self):
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient

        config, _ = bootstrap_config()
        client = TracClient(config)

        result = asyncio.run(
            _handle_convert_preview(
                client,
                {
                    "content": MANIFEST["row15_missing_cross_instance"][
                        "markdown_input"
                    ],
                    "check_targets": True,
                },
            )
        )
        codes = [
            w["code"] for w in result.structuredContent["warnings"]
        ]
        assert "missing_cross_instance_target" in codes

    def test_row01_existing_cross_instance_target_stays_silent_live(
        self,
    ):
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient

        config, _ = bootstrap_config()
        client = TracClient(config)

        result = asyncio.run(
            _handle_convert_preview(
                client,
                {
                    "content": MANIFEST["row01_intertrac_wiki"][
                        "markdown_input"
                    ],
                    "check_targets": True,
                },
            )
        )
        codes = [
            w["code"] for w in result.structuredContent["warnings"]
        ]
        assert "missing_cross_instance_target" not in codes
        assert result.structuredContent["stats"]["targets_checked"] == 1

    def test_many_targets_probed_concurrently_live(self):
        """Ticket #80's concurrency and its raised cap, against the real
        substrate.

        The probe is transport code, and `Rules/testing/
        RealSubstrateNotMocks` is explicit that a mock can only fail in
        ways its author already imagined -- thread-local sessions,
        connection-pool contention and per-request auth are exactly what
        a `MagicMock` cannot get wrong. So this drives real concurrent
        HTTP.

        Twelve DISTINCT dead targets, which matters: `probe_targets`
        deduplicates, so twelve repetitions of one href is two unique
        targets and would have passed against the old cap of 10 as
        well. (Measured -- the first draft of this test did exactly
        that.) Distinct targets put the last two past the old cap, so
        before this ticket they were reported `target_check_capped`
        and never checked.
        """
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient

        config, _ = bootstrap_config()
        client = TracClient(config)

        started = time.monotonic()
        result = asyncio.run(
            _handle_convert_preview(
                client,
                {
                    "content": _twelve_distinct_dead_targets(),
                    "format": "tracwiki",
                    "check_targets": True,
                },
            )
        )
        elapsed = time.monotonic() - started

        codes = [
            w["code"] for w in result.structuredContent["warnings"]
        ]
        assert codes.count("missing_cross_instance_target") == 12, codes
        assert "target_check_capped" not in codes, codes
        assert "target_check_failed" not in codes, codes
        assert (
            result.structuredContent["stats"]["targets_checked"] == 12
        )
        # Concurrency, stated as the property that matters rather than a
        # wall-clock threshold that would flake on a slow day: twelve
        # SEQUENTIAL round trips cannot come in under one request's own
        # timeout budget.
        assert elapsed < DEFAULT_TARGET_TIMEOUT_SECONDS, elapsed

    def test_target_cap_parameter_reaches_the_probe_live(self):
        """The new parameter is new surface, and an unwired parameter
        looks exactly like a wired one until something asks it to bite.

        Same twelve distinct targets with `target_cap=2`: the probe must
        stop after two and say so, which is only observable if the
        argument actually reached `probe_targets`.
        """
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient

        config, _ = bootstrap_config()
        client = TracClient(config)

        result = asyncio.run(
            _handle_convert_preview(
                client,
                {
                    "content": _twelve_distinct_dead_targets(),
                    "format": "tracwiki",
                    "check_targets": True,
                    "target_cap": 2,
                },
            )
        )
        codes = [
            w["code"] for w in result.structuredContent["warnings"]
        ]
        assert "target_check_capped" in codes, codes
        assert codes.count("missing_cross_instance_target") == 2, codes
        assert result.structuredContent["stats"]["targets_checked"] == 2

    # --- Ticket #82: the ticket realm -------------------------------
    #
    # These are live rather than mocked because the whole design rests on
    # transport facts a mock would simply restate: a ticket that exists
    # answers 200 and redirects, one that does not answers a bare 500,
    # and a wiki-realm dispatcher target answers 200 whether or not the
    # page exists. `Rules/testing/RealSubstrateNotMocks`.

    def test_dead_and_live_cross_instance_ticket_refs_live(self):
        """The pair in one document, not the dead row alone.

        Before this ticket both were invisible: the probe matched only
        `/intertrac/wiki%3A`, so a cross-instance TICKET reference was
        never fetched and `missing_cross_instance_target` was
        structurally incapable of firing for it -- 695 references across
        two stores, none checked.

        The live reference is auto_pm ticket 93; if it is ever deleted
        this test fails loudly, which is the correct outcome for a
        regression row that asserts a link resolves.
        """
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient

        config, _ = bootstrap_config()
        client = TracClient(config)

        result = asyncio.run(
            _handle_convert_preview(
                client,
                {
                    "content": (
                        "Live: auto_pm:#93\n\nDead: auto_pm:#999999\n"
                    ),
                    "format": "tracwiki",
                    "check_targets": True,
                },
            )
        )
        warnings = result.structuredContent["warnings"]
        codes = [w["code"] for w in warnings]
        assert codes.count("missing_cross_instance_target") == 1, codes
        assert "target_check_capped" not in codes, codes
        assert "target_check_failed" not in codes, codes
        dead = [
            w
            for w in warnings
            if w["code"] == "missing_cross_instance_target"
        ]
        assert "999999" in dead[0]["evidence"]["href"], dead
        assert result.structuredContent["stats"]["targets_checked"] == 2

    def test_realm_form_ticket_ref_is_probed_live(self):
        """`prefix:ticket:N` renders as `/intertrac/ticket%3AN`, a
        different dispatcher shape from the `prefix:#N` short link. Ten
        references in the measured corpus take it, and a fix keyed on
        the short link alone would leave every one of them unchecked."""
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient

        config, _ = bootstrap_config()
        client = TracClient(config)

        result = asyncio.run(
            _handle_convert_preview(
                client,
                {
                    "content": (
                        "Live: auto_pm:ticket:93\n\n"
                        "Dead: auto_pm:ticket:999999\n"
                    ),
                    "format": "tracwiki",
                    "check_targets": True,
                },
            )
        )
        codes = [
            w["code"] for w in result.structuredContent["warnings"]
        ]
        assert codes.count("missing_cross_instance_target") == 1, codes
        assert result.structuredContent["stats"]["targets_checked"] == 2

    def test_liveness_control_answers_200_for_a_missing_page_live(self):
        """The assumption the whole ticket-realm check rests on.

        A 500 is only evidence of a dead ticket once the instance has
        answered a control, and the control is a WIKI-realm dispatcher
        target chosen precisely because Trac answers 200 for it whether
        or not the page exists. If that ever stopped being true, every
        control would fail, every dead ticket would degrade to
        `target_check_failed`, and the check would go quietly dead --
        indistinguishable from one that always passes, which is what
        `Rules/testing/SeededDefectFirst` is about.

        So it is pinned here against the real dispatcher rather than
        left as a comment.
        """
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient
        from trac_mcp_server.preview.targets import _control_url

        config, _ = bootstrap_config()
        client = TracClient(config)

        base = config.trac_url.rstrip("/")
        control = _control_url(f"{base}/intertrac/%2387")
        assert control is not None
        response = client.session.get(control, timeout=10)
        assert response.status_code == 200, (
            f"{control} answered {response.status_code}; the liveness "
            "control no longer discriminates and the ticket-realm check "
            "is now inert"
        )

    # --- Ticket #163: advice is only advice if it renders clean -----

    @pytest.mark.parametrize(
        "row",
        [
            "row163a_ticket_possessive_local",
            "row163b_ticket_hyphen_local",
            "row163c_wiki_possessive_local",
            "row163d_intertrac_slash_tail",
            "row163e_intertrac_dash_range",
        ],
    )
    def test_captured_tail_suggestions_render_clean_live(self, row):
        """Render the seeded row, take its `evidence.suggestion`,
        substitute it for the resolved text in the source, and render
        again: the second render must carry no error-severity warning.

        This is the live half of the "render each emitted suggestion
        before shipping it as advice" rule. The first pass proves the
        row still fires against what Trac emits NOW; the second proves
        the advice is accepted by the same gate that refused the
        original. Ticket #59's builder once shipped a suggestion that
        rendered silent but linked the wrong project's ticket; a
        silent render is necessary, and the fixtures' anchors are the
        other half of that check.
        """
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient

        config, _ = bootstrap_config()
        client = TracClient(config)
        source = MANIFEST[row]["tracwiki"]

        def render(content):
            return asyncio.run(
                _handle_convert_preview(
                    client,
                    {
                        "content": content,
                        "format": "tracwiki",
                        "check_targets": False,
                    },
                )
            ).structuredContent["warnings"]

        first = [
            w
            for w in render(source)
            if w["code"]
            in (
                "local_target_captured_tail",
                "intertrac_target_captured_punctuation",
            )
        ]
        assert len(first) == 1, first
        evidence = first[0]["evidence"]
        assert evidence["resolved_as"] in source, (source, evidence)
        fixed = source.replace(
            evidence["resolved_as"], evidence["suggestion"], 1
        )
        assert fixed != source

        errors = [
            (w["code"], w["message"])
            for w in render(fixed)
            if w["severity"] == "error"
        ]
        assert errors == [], (fixed, errors)


if __name__ == "__main__":
    unittest.main()
