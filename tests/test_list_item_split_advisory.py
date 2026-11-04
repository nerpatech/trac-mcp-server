"""Tests for the `list_item_split` advisory (ticket #125).

Trac cannot continue a list item after a nested list: it ends the list,
renders the following block outside it and starts a new list for the next
item. Since #112 the converter keeps an item's children in source order, so
the shape converts faithfully and then renders split. The detector lives in
`converters.markdown_to_tracwiki` so the preview checks and the
`wiki_file_push` write path reach the same function.
"""

import xmlrpc.client
from unittest.mock import MagicMock

import mcp.types as types
import pytest

from trac_mcp_server.converters import (
    describe_list_item_split,
    find_list_item_splits,
    markdown_to_tracwiki,
)
from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.mcp.tools.wiki_file import WIKI_FILE_SPECS
from trac_mcp_server.preview.checks import build_warnings
from trac_mcp_server.preview.facts import extract_facts
from trac_mcp_server.preview.gate import (
    ADVISORY_CODES,
    BLOCKING_CODES,
    is_blocking,
)

TICKET_INPUT = (
    "- Cause: unknown. Suspects:\n"
    "  - first suspect;\n"
    "  - second suspect.\n"
    "\n"
    "  A paragraph that belongs after the list.\n"
    "- Guard: next item.\n"
)


def _codes(markdown, source_format="markdown"):
    warnings = build_warnings(
        markdown,
        markdown_to_tracwiki(markdown),
        extract_facts("<p>x</p>"),
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    return [w for w in warnings if w["code"] == "list_item_split"]


def test_seeded_defect_one_finding():
    assert find_list_item_splits(TICKET_INPUT) == [
        {
            "item": "Cause: unknown. Suspects:",
            "block": "paragraph",
            "depth": 0,
        }
    ]


def test_seeded_defect_reaches_build_warnings_as_a_warning():
    found = _codes(TICKET_INPUT)
    assert len(found) == 1
    assert found[0]["severity"] == "warning"
    assert "Cause: unknown. Suspects:" in found[0]["message"]
    assert "paragraph" in found[0]["message"]


def test_sublist_with_nothing_after_is_silent():
    text = "- Cause:\n  - a\n  - b\n- Guard: next item.\n"
    assert find_list_item_splits(text) == []


def test_same_input_inside_a_fenced_block_is_silent():
    text = "```\n" + TICKET_INPUT + "```\n"
    assert find_list_item_splits(text) == []


def test_fenced_code_after_sublist():
    text = "- Item:\n  - sub\n\n  ```\n  code\n  ```\n- next\n"
    assert find_list_item_splits(text) == [
        {"item": "Item:", "block": "code block", "depth": 0}
    ]


def test_ordered_parent():
    text = "1. Ordered:\n   1. sub\n\n   para\n2. next\n"
    found = find_list_item_splits(text)
    assert len(found) == 1
    assert found[0]["item"] == "Ordered:"
    assert found[0]["block"] == "paragraph"


def test_nested_item_reports_depth_one():
    text = (
        "- top\n"
        "  - mid\n"
        "    - leaf\n"
        "\n"
        "    after the leaf\n"
        "  - mid two\n"
    )
    assert find_list_item_splits(text) == [
        {"item": "mid", "block": "paragraph", "depth": 1}
    ]


def test_item_text_is_restored_with_no_nul_byte():
    text = "- Use `foo()` and [[PageOutline]] here\n  - sub\n\n  tail\n"
    found = find_list_item_splits(text)
    assert len(found) == 1
    assert found[0]["item"] == "Use foo() and [[PageOutline]] here"
    message = describe_list_item_split(found[0])
    assert "\x00" not in found[0]["item"]
    assert "\x00" not in message
    assert "[[PageOutline]]" in message


def test_item_is_truncated_after_restoring():
    long_code = "x" * 80
    text = f"- `{long_code}`\n  - sub\n\n  tail\n"
    found = find_list_item_splits(text)
    assert found[0]["item"] == "x" * 60


def test_tracwiki_declared_input_is_not_checked():
    assert _codes(TICKET_INPUT, source_format="tracwiki") == []


def test_gate_treats_the_warning_as_advisory():
    found = _codes(TICKET_INPUT)[0]
    assert is_blocking(found) is False
    assert "list_item_split" in ADVISORY_CODES
    assert "list_item_split" not in BLOCKING_CODES


# -- wiki_file_push ----------------------------------------------------

_registry = ToolRegistry(WIKI_FILE_SPECS)


def _make_client():
    client = MagicMock()
    client.config = MagicMock()
    return client


async def _push(tmp_path, text):
    md_file = tmp_path / "page.md"
    md_file.write_text(text)
    client = _make_client()
    client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
        1, "not found"
    )
    client.put_wiki_page.return_value = {"version": 1}
    result = await _registry.call_tool(
        "wiki_file_push",
        {"file_path": str(md_file), "page_name": "TestPage"},
        client,
    )
    return result, client


@pytest.mark.usefixtures("local_file_access")
async def test_push_warns_but_still_writes(tmp_path):
    result, client = await _push(tmp_path, TICKET_INPUT)
    assert isinstance(result, types.CallToolResult)
    assert result.isError is not True
    client.put_wiki_page.assert_called_once()
    warnings = result.structuredContent["warnings"]
    assert any("Cause: unknown. Suspects:" in w for w in warnings)
    assert "Conversion warnings:" in result.content[0].text


@pytest.mark.usefixtures("local_file_access")
async def test_push_without_the_shape_has_no_such_warning(tmp_path):
    result, client = await _push(
        tmp_path, "- Cause:\n  - a\n  - b\n- Guard: next item.\n"
    )
    assert result.isError is not True
    client.put_wiki_page.assert_called_once()
    assert result.structuredContent["warnings"] == []
