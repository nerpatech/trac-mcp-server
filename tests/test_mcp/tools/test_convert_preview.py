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
        # notes that skip (target_check_skipped) -- that is not itself a
        # defect warning, so it's the only thing expected here.
        codes = [
            w["code"] for w in result.structuredContent["warnings"]
        ]
        self.assertEqual(codes, ["target_check_skipped"])
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
        self.assertIn("target_check_skipped", codes)
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
        before this ticket they were reported `target_check_skipped`
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
        assert "target_check_skipped" not in codes, codes
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
        assert "target_check_skipped" in codes, codes
        assert codes.count("missing_cross_instance_target") == 2, codes
        assert result.structuredContent["stats"]["targets_checked"] == 2


if __name__ == "__main__":
    unittest.main()
