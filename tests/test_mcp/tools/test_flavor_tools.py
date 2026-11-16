"""The ``flavor`` parameter of ``wiki_file_push`` and ``convert_preview``
(Trac /trac_mcp_server #216).

A flavour names a Markdown profile, so a non-default one implies a
Markdown source: with format "auto" -- inline content included, which is
otherwise TracWiki -- the text converts as Markdown, and with format
"tracwiki" the call is refused. Ticket tools never convert and take no
``flavor``.
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from tests.test_mcp.tools.test_wiki_file import _fm_push
from trac_mcp_server.mcp.tools.convert_preview import (
    CONVERT_PREVIEW_TOOLS,
    _handle_convert_preview,
)
from trac_mcp_server.mcp.tools.wiki_file import WIKI_FILE_SPECS

pytestmark = pytest.mark.usefixtures("local_file_access")

FLAVORS = ["commonmark", "chatgpt", "gemini"]
GEMINI_COPY = "Run it:\nbash\nls -la\nUse code with caution.\nDone\n"


def _push_tool():
    return next(
        s.tool
        for s in WIKI_FILE_SPECS
        if s.tool.name == "wiki_file_push"
    )


def _error_payload(result) -> str:
    return json.dumps(
        [c.text for c in result.content if hasattr(c, "text")]
    )


# -- the schema ---------------------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    [_push_tool(), CONVERT_PREVIEW_TOOLS[0]],
    ids=["push", "preview"],
)
def test_the_schema_has_the_flavor_enum_with_its_default(tool):
    flavor = tool.inputSchema["properties"]["flavor"]
    assert flavor["type"] == "string"
    assert flavor["enum"] == FLAVORS
    assert flavor["default"] == "commonmark"
    description = flavor["description"]
    assert description.count(".") == 1 or description.endswith(".")
    assert "chatgpt" in description and "gemini" in description
    assert "flavor" not in tool.inputSchema["required"]


def test_ticket_tools_take_no_flavor():
    from trac_mcp_server.mcp.tools import ALL_SPECS

    tickets = [
        s.tool for s in ALL_SPECS if s.tool.name.startswith("ticket_")
    ]
    assert tickets
    for tool in tickets:
        assert "flavor" not in tool.inputSchema.get("properties", {})


# -- wiki_file_push -----------------------------------------------------------


async def test_push_flavor_with_format_tracwiki_is_a_validation_error():
    client, result = await _fm_push(
        "Energy \\(E=mc^2\\)\n",
        name=None,
        format="tracwiki",
        flavor="chatgpt",
    )
    assert result.isError
    assert "validation_error" in _error_payload(result)
    assert "flavor" in _error_payload(result)
    client.put_wiki_page.assert_not_called()


async def test_push_default_flavor_with_format_tracwiki_is_fine():
    client, result = await _fm_push(
        "= T =\n", name=None, format="tracwiki", flavor="commonmark"
    )
    assert not result.isError, result.content
    assert client.put_wiki_page.call_args[0][1] == "= T =\n"


async def test_push_an_unknown_flavor_is_a_validation_error():
    client, result = await _fm_push("x\n", name=None, flavor="bard")
    assert result.isError
    payload = _error_payload(result)
    assert "validation_error" in payload
    assert "bard" in payload and "gemini" in payload
    client.put_wiki_page.assert_not_called()


async def test_push_flavor_with_inline_auto_content_converts_as_markdown():
    client, result = await _fm_push(
        "Energy \\(E=mc^2\\) here\n", name=None, flavor="chatgpt"
    )
    assert not result.isError, result.content
    assert (
        client.put_wiki_page.call_args[0][1] == "Energy `E=mc^2` here"
    )
    assert result.structuredContent["source_format"] == "markdown"
    assert result.structuredContent["converted"] is True


async def test_push_inline_auto_content_without_a_flavor_is_still_tracwiki():
    client, result = await _fm_push(
        "Energy \\(E=mc^2\\) here\n", name=None
    )
    assert not result.isError, result.content
    assert client.put_wiki_page.call_args[0][1] == (
        "Energy \\(E=mc^2\\) here\n"
    )
    assert result.structuredContent["source_format"] == "tracwiki"


async def test_push_flavor_with_a_named_file_converts_as_markdown():
    client, result = await _fm_push(
        GEMINI_COPY, name="copy.txt", flavor="gemini"
    )
    assert not result.isError, result.content
    assert client.put_wiki_page.call_args[0][1] == (
        "Run it:\n\n{{{#!sh\nls -la\n}}}\n\nDone"
    )


async def test_push_reports_the_flavors_warning():
    client, result = await _fm_push(
        GEMINI_COPY, name="copy.md", flavor="gemini"
    )
    assert result.structuredContent["warnings"] == [
        "gemini: rebuilt 1 code block(s) from 'Use code with caution.' "
        "markers"
    ]


async def test_push_the_default_hints_at_the_gemini_flavor():
    client, result = await _fm_push(GEMINI_COPY, name="copy.md")
    warnings = result.structuredContent["warnings"]
    assert len(warnings) == 1
    assert 'flavor "gemini"' in warnings[0]
    assert client.put_wiki_page.call_args[0][1] == (
        "Run it:\nbash\nls -la\nUse code with caution.\nDone"
    )


async def test_push_the_checks_read_the_flavour_normalised_text():
    """The split advisory (#125) must read the text that was converted:
    a `•` item that continues after its nested list is a list item in the
    converted Markdown and a paragraph in the raw copy."""
    copy = "• one\n  - sub\n\n  ```\n  x\n  ```\n"
    client, result = await _fm_push(
        copy, name="copy.md", flavor="gemini"
    )
    warnings = result.structuredContent["warnings"]
    assert any("continues with a code block" in w for w in warnings), (
        warnings
    )
    client2, result2 = await _fm_push(copy, name="copy.md")
    assert not any(
        "continues with a code block" in w
        for w in result2.structuredContent["warnings"]
    )


# -- convert_preview ----------------------------------------------------------


def _preview(args, html="<p>x</p>"):
    client = MagicMock()
    client.wiki_to_html = MagicMock(return_value=html)
    return client, asyncio.run(_handle_convert_preview(client, args))


def test_preview_flavor_with_format_tracwiki_is_a_validation_error():
    client, result = _preview(
        {"content": "x", "format": "tracwiki", "flavor": "gemini"}
    )
    assert result.isError
    payload = _error_payload(result)
    assert "validation_error" in payload and "flavor" in payload
    client.wiki_to_html.assert_not_called()


def test_preview_an_unknown_flavor_is_a_validation_error():
    client, result = _preview({"content": "x", "flavor": "bard"})
    assert result.isError
    payload = _error_payload(result)
    assert "validation_error" in payload and "bard" in payload
    client.wiki_to_html.assert_not_called()


def test_preview_converts_with_the_flavor():
    client, result = _preview(
        {
            "content": "Energy \\\\(E\\_1\\\\) here\n",
            "flavor": "chatgpt",
        }
    )
    assert not result.isError, result.content
    assert result.structuredContent["tracwiki"] == "Energy `E_1` here"
    client.wiki_to_html.assert_called_once_with("Energy `E_1` here")


def test_preview_without_a_flavor_is_as_before():
    client, result = _preview(
        {"content": "Energy \\\\(E\\_1\\\\) here\n"}
    )
    assert (
        result.structuredContent["tracwiki"] == "Energy \\(E_1\\) here"
    )


def test_preview_reports_the_flavors_warning_as_a_conversion_warning():
    client, result = _preview(
        {"content": GEMINI_COPY, "flavor": "gemini"}
    )
    conversion = [
        w
        for w in result.structuredContent["warnings"]
        if w["code"] == "conversion_warning"
    ]
    assert [w["message"] for w in conversion] == [
        "gemini: rebuilt 1 code block(s) from 'Use code with caution.' "
        "markers"
    ]


def test_preview_the_markdown_checks_read_the_normalised_text():
    """TracWiki bold (`'''b'''`) in the code of a Gemini copy is quoted
    code once the block is rebuilt, so `tracwiki_markup_in_markdown` must
    not fire; on the raw copy it is bare text and does."""
    copy = "python\nx = \"'''b'''\"\nUse code with caution.\n"
    _, with_flavor = _preview({"content": copy, "flavor": "gemini"})
    _, without = _preview({"content": copy})
    codes = [
        w["code"] for w in with_flavor.structuredContent["warnings"]
    ]
    assert "tracwiki_markup_in_markdown" not in codes
    codes = [w["code"] for w in without.structuredContent["warnings"]]
    assert "tracwiki_markup_in_markdown" in codes


def test_preview_the_split_advisory_reads_the_normalised_text():
    copy = "• one\n  - sub\n\n  ```\n  x\n  ```\n"
    _, with_flavor = _preview({"content": copy, "flavor": "gemini"})
    _, without = _preview({"content": copy})
    split = [
        w
        for w in with_flavor.structuredContent["warnings"]
        if w["code"] == "list_item_split"
    ]
    assert len(split) == 1
    assert not [
        w
        for w in without.structuredContent["warnings"]
        if w["code"] == "list_item_split"
    ]


# -- the normalisers run once per call (review of #216) -------------------------


@pytest.fixture
def normaliser_calls(monkeypatch):
    from trac_mcp_server.converters import flavors

    calls: list[str] = []

    def counted(normalise):
        def run(text):
            calls.append(normalise.__name__)
            return normalise(text)

        return run

    monkeypatch.setitem(
        flavors._NORMALISERS,
        "gemini",
        tuple(counted(n) for n in flavors._NORMALISERS["gemini"]),
    )
    return calls


async def test_push_normalises_once(normaliser_calls):
    await _fm_push(GEMINI_COPY, name="copy.md", flavor="gemini")
    assert normaliser_calls.count("_gemini_code_blocks") == 1


def test_preview_normalises_once(normaliser_calls):
    _preview({"content": GEMINI_COPY, "flavor": "gemini"})
    assert normaliser_calls.count("_gemini_code_blocks") == 1
