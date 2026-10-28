#!/usr/bin/env python3
"""
Comprehensive MCP Tool Live Testing (v8.0.0)

Tests the Trac MCP server's tools against a live Trac server instance
via MCP stdio protocol. Communicates with trac-mcp-server binary as a
real MCP client (same protocol path as Claude Desktop/Code).

Coverage is intentionally not exhaustive of every registered tool (see
"Executive Summary" / "Tools Tested: N/M" in the generated report for the
live count, fetched from the server itself via list_tools() rather than
hardcoded here). As of v8.0.0 this covers: ping + system + ticket read/write
+ batch ticket + ticket admin (components, enums) + ticket attachments +
wiki read/write/history + wiki files + wiki attachments + milestones.

Features:
- Communicates via MCP stdio protocol (launches trac-mcp-server as subprocess)
- --tools flag selects a subset of tools to test and filters Tool Catalog
- --permissions-file passes permission restrictions to the server subprocess
- Automatic debug logging to ./test_trac_debug.log (--timestamp for unique filenames)
- Covers happy paths, error handling, and edge cases
- Tests format conversions (Markdown <-> TracWiki)
- Tests batch operations with parallel execution
- Generates comprehensive test report
- Automatic cleanup of test resources
- Generates reference output showing tool presentation, call arguments, and full return values
- Tool Catalog section shows complete inputSchema for all tools (LLM tool listing POV)
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from trac_mcp_server import __version__ as PACKAGE_VERSION

VERSION = "8.0.0"

# Number of tickets to create in batch tests.
# Keep small for routine testing; increase for load/stress testing.
BATCH_TEST_SIZE = 10


def _extract_raw_fields(
    raw_result: types.CallToolResult | None,
) -> dict:
    """Extract structured fields from a CallToolResult for CheckResult construction."""
    if raw_result is None:
        return {
            "structured_content": None,
            "is_error": None,
            "raw_text_content": [],
        }
    return {
        "structured_content": raw_result.structuredContent,
        "is_error": raw_result.isError,
        "raw_text_content": [
            c.text
            for c in raw_result.content
            if isinstance(c, types.TextContent)
        ],
    }


@dataclass
class CheckResult:
    """Result of a single test case"""

    tool: str
    test_name: str
    passed: bool
    response: str = ""
    error: str = ""
    notes: str = ""
    call_args: dict = field(default_factory=dict)
    structured_content: dict | None = None
    is_error: bool | None = None
    raw_text_content: list[str] = field(default_factory=list)


@dataclass
class CheckReport:
    """Comprehensive test report"""

    date: str = ""
    server_url: str = ""
    binary_version: str = ""
    results: list[CheckResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)


class ComprehensiveMCPTester:
    """Comprehensive tester for all Trac MCP tools"""

    def __init__(
        self,
        session: ClientSession,
        logger: logging.Logger,
        verbose: bool = False,
        tools_filter: list[str] | None = None,
    ):
        self.session = session
        self.logger = logger
        self.verbose = verbose
        self.report = CheckReport()
        self.report.binary_version = PACKAGE_VERSION
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.tools_filter: set[str] | None = (
            set(tools_filter) if tools_filter else None
        )
        self.available_tools: list[types.Tool] = []

        # Track test resources for cleanup
        self.test_ticket_id: Optional[int] = None
        self.test_wiki_page: Optional[str] = None
        self.test_milestone: Optional[str] = None
        self.test_batch_ticket_ids: list[int] = []
        self.test_component: Optional[str] = None
        self.test_enum: Optional[tuple[str, str]] = None
        self.test_ticket_attachment_filename: Optional[str] = None
        self.test_wiki_attachment_filename: Optional[str] = None

    def _should_test_tool(self, tool_name: str) -> bool:
        """Check if a tool should be tested based on --tools filter."""
        if self.tools_filter is None:
            return True
        return tool_name in self.tools_filter

    def _color(self, text: str) -> str:
        """Return text as-is (no color formatting)."""
        return text

    def _log_result(self, result: CheckResult):
        """Log a test result"""
        status = (
            self._color("PASS")
            if result.passed
            else self._color("FAIL")
        )
        print(f"  [{status}] {result.tool}.{result.test_name}")
        if self.verbose and result.notes:
            print(f"         Notes: {result.notes}")
        if not result.passed and result.error:
            print(f"         Error: {result.error[:100]}")

    async def _call_tool(
        self, tool_name: str, arguments: dict | None = None
    ) -> tuple[bool, str, types.CallToolResult | None]:
        """Call an MCP tool via the client session and return (success, response_text, raw_result)."""
        try:
            result = await self.session.call_tool(
                tool_name, arguments or {}
            )

            # Extract text from CallToolResult.content
            response_text = "\n".join(
                c.text
                for c in result.content
                if isinstance(c, types.TextContent)
            )

            # Check for error indicators in response
            is_error = result.isError or (
                "error_type" in response_text.lower()
                or response_text.startswith("{")
            )
            return not is_error, response_text, result

        except Exception as e:
            return False, str(e), None

    async def fetch_tool_catalog(self):
        """Fetch the tool catalog from the MCP server via list_tools()."""
        result = await self.session.list_tools()
        self.available_tools = result.tools

    async def test_ping(self):
        """Phase 1: Test connectivity"""
        print(f"\n{self._color('=== Phase 1: Connectivity ===')}")

        success, response, raw_result = await self._call_tool("ping")
        result = CheckResult(
            tool="ping",
            test_name="connectivity",
            passed=success and "API version" in response,
            response=response,
            notes="API version: "
            + response.split("API version:")[-1].strip()
            if "API version" in response
            else "",
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        if success and "API version" in response:
            # Server URL not directly available via MCP protocol; note in report
            self.report.server_url = "(via MCP stdio protocol)"

    async def test_system_tools(self):
        """Phase 1b: Test system tools"""
        print(f"\n{self._color('=== Phase 1b: System Tools ===')}")

        # get_server_time
        success, response, raw_result = await self._call_tool(
            "get_server_time"
        )

        # Verify response contains valid timestamp
        passed = False
        notes = ""
        if success and "Server time:" in response:
            try:
                # Extract ISO timestamp from response
                timestamp_str = response.split("Server time:")[
                    -1
                ].strip()
                # Try parsing as datetime to verify format
                from datetime import datetime

                datetime.fromisoformat(timestamp_str)
                passed = True
                notes = f"Valid timestamp: {timestamp_str}"
            except Exception as e:
                notes = f"Invalid timestamp format: {e}"
        else:
            notes = "Response missing 'Server time:' prefix"

        result = CheckResult(
            tool="get_server_time",
            test_name="server_time",
            passed=passed,
            response=response[:200],
            notes=notes,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

    async def test_ticket_read_operations(self):
        """Phase 2a: Test ticket read operations"""
        print(
            f"\n{self._color('=== Phase 2a: Ticket Read Operations ===')}"
        )

        # ticket_search - default query
        _args: dict = {}
        success, response, raw_result = await self._call_tool(
            "ticket_search"
        )
        result = CheckResult(
            tool="ticket_search",
            test_name="default_query",
            passed=success
            and ("Found" in response or "No tickets" in response),
            response=response[:200],
            notes="Returns open tickets by default",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        # ticket_search - custom query with max_results
        _args = {"query": "status=closed", "max_results": 5}
        success, response, raw_result = await self._call_tool(
            "ticket_search",
            _args,
        )
        result = CheckResult(
            tool="ticket_search",
            test_name="custom_query_max_results",
            passed=success,
            response=response[:200],
            notes="Tested status=closed with max_results=5",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        # ticket_get - need a valid ticket ID
        # First get a ticket from search
        search_success, search_response, _ = await self._call_tool(
            "ticket_search", {"max_results": 1}
        )
        ticket_id = None
        if search_success and "#" in search_response:
            try:
                # Extract first ticket ID from response like "- #1: Summary"
                ticket_id = int(
                    search_response.split("#")[1].split(":")[0]
                )
            except (ValueError, IndexError):
                pass

        # If no tickets exist, create a temporary one for read tests
        temp_ticket_id: int | None = None
        if not ticket_id:
            create_success, create_response, _ = await self._call_tool(
                "ticket_create",
                {
                    "summary": f"[MCP READ TEST {self.timestamp}] Temporary ticket for read tests",
                    "description": "Auto-created for ticket read testing. Will be deleted.",
                    "ticket_type": "task",
                    "keywords": "mcp-test,auto-delete",
                },
            )
            if create_success and "Created ticket #" in create_response:
                try:
                    temp_ticket_id = int(
                        create_response.split("#")[1].split(":")[0]
                    )
                    ticket_id = temp_ticket_id
                    print(
                        f"  (created temp ticket #{temp_ticket_id} for read tests)"
                    )
                except (ValueError, IndexError):
                    pass

        if ticket_id:
            # ticket_get - existing ticket
            _args = {"ticket_id": ticket_id}
            success, response, raw_result = await self._call_tool(
                "ticket_get", _args
            )
            result = CheckResult(
                tool="ticket_get",
                test_name="existing_ticket",
                passed=success and f"Ticket #{ticket_id}" in response,
                response=response[:300],
                notes=f"Retrieved ticket #{ticket_id}"
                + (" (temp)" if temp_ticket_id else ""),
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # ticket_get - raw mode
            _args = {"ticket_id": ticket_id, "raw": True}
            success, response, raw_result = await self._call_tool(
                "ticket_get", _args
            )
            result = CheckResult(
                tool="ticket_get",
                test_name="raw_mode",
                passed=success and "(TracWiki)" in response,
                response=response[:300],
                notes="Raw TracWiki format returned",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # ticket_changelog
            _args = {"ticket_id": ticket_id}
            success, response, raw_result = await self._call_tool(
                "ticket_changelog", _args
            )
            result = CheckResult(
                tool="ticket_changelog",
                test_name="existing_ticket",
                passed=success,
                response=response[:300],
                notes="Changelog may be empty for new tickets",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # ticket_changelog - raw mode
            _args = {"ticket_id": ticket_id, "raw": True}
            success, response, raw_result = await self._call_tool(
                "ticket_changelog",
                _args,
            )
            result = CheckResult(
                tool="ticket_changelog",
                test_name="raw_mode",
                passed=success,
                response=response[:200],
                notes="Raw TracWiki format for comments",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # ticket_actions
            _args = {"ticket_id": ticket_id}
            success, response, raw_result = await self._call_tool(
                "ticket_actions", _args
            )
            result = CheckResult(
                tool="ticket_actions",
                test_name="get_workflow_actions",
                passed=success
                and (
                    "actions" in response.lower()
                    or "leave" in response.lower()
                ),
                response=response[:300],
                notes="Retrieved workflow actions for ticket",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # Clean up temp ticket if we created one
            if temp_ticket_id:
                await self._call_tool(
                    "ticket_delete", {"ticket_id": temp_ticket_id}
                )
                print(f"  (deleted temp ticket #{temp_ticket_id})")
        else:
            result = CheckResult(
                tool="ticket_get",
                test_name="existing_ticket",
                passed=False,
                error="No tickets found and could not create temp ticket",
                notes="SKIPPED",
            )
            self.report.results.append(result)
            self._log_result(result)

        # ticket_fields
        success, response, raw_result = await self._call_tool(
            "ticket_fields"
        )
        result = CheckResult(
            tool="ticket_fields",
            test_name="get_fields",
            passed=success and "Ticket Fields" in response,
            response=response[:400],
            notes="Returns standard and custom field definitions",
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

    async def test_wiki_read_operations(self):
        """Phase 2b: Test wiki read operations"""
        print(
            f"\n{self._color('=== Phase 2b: Wiki Read Operations ===')}"
        )

        # wiki_get - WikiStart
        _args = {"page_name": "WikiStart"}
        success, response, raw_result = await self._call_tool(
            "wiki_get", _args
        )
        wiki_version = None
        if success and "Version:" in response:
            try:
                wiki_version = int(
                    response.split("Version:")[1].split()[0]
                )
            except (ValueError, IndexError):
                pass

        result = CheckResult(
            tool="wiki_get",
            test_name="wikistart",
            passed=success and "WikiStart" in response,
            response=response[:300],
            notes=f"Version: {wiki_version}" if wiki_version else "",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        # wiki_get - raw mode
        _args = {"page_name": "WikiStart", "raw": True}
        success, response, raw_result = await self._call_tool(
            "wiki_get", _args
        )
        result = CheckResult(
            tool="wiki_get",
            test_name="raw_mode",
            passed=success and "(TracWiki)" in response,
            response=response[:300],
            notes="Raw TracWiki format returned",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        # wiki_get - specific version (if version > 1)
        if wiki_version and wiki_version > 1:
            _args = {"page_name": "WikiStart", "version": 1}
            success, response, raw_result = await self._call_tool(
                "wiki_get", _args
            )
            result = CheckResult(
                tool="wiki_get",
                test_name="specific_version",
                passed=success and "Version: 1" in response,
                response=response[:200],
                notes="Retrieved historical version 1",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

        # wiki_get_history
        _args = {"page_name": "WikiStart"}
        success, response, raw_result = await self._call_tool(
            "wiki_get_history", _args
        )
        result = CheckResult(
            tool="wiki_get_history",
            test_name="history",
            passed=success and "history" in response.lower(),
            response=response[:300],
            notes="Retrieved WikiStart revision history",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        # wiki_diff - latest against the version before (or the empty
        # page, when WikiStart has only version 1)
        _args = {"page_name": "WikiStart"}
        success, response, raw_result = await self._call_tool(
            "wiki_diff", _args
        )
        result = CheckResult(
            tool="wiki_diff",
            test_name="latest_vs_previous",
            passed=success and "# WikiStart diff:" in response,
            response=response[:300],
            notes="Diffed WikiStart's latest version against the one before",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        # wiki_search
        _args = {"query": "wiki"}
        success, response, raw_result = await self._call_tool(
            "wiki_search", _args
        )
        result = CheckResult(
            tool="wiki_search",
            test_name="basic_search",
            passed=success
            and ("Found" in response or "No wiki" in response),
            response=response[:300],
            notes="Search for 'wiki' keyword",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        # wiki_search - with prefix
        _args = {"query": "trac", "prefix": "Trac"}
        success, response, raw_result = await self._call_tool(
            "wiki_search", _args
        )
        result = CheckResult(
            tool="wiki_search",
            test_name="with_prefix",
            passed=success,
            response=response[:200],
            notes="Filtered by Trac prefix",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        # wiki_recent_changes
        _args = {"days_back": 30}
        success, response, raw_result = await self._call_tool(
            "wiki_recent_changes", _args
        )
        result = CheckResult(
            tool="wiki_recent_changes",
            test_name="recent_changes",
            passed=success
            and (
                "pages" in response.lower()
                or "modified" in response.lower()
                or "no recent" in response.lower()
            ),
            response=response[:300],
            notes="Retrieved wiki pages modified in last 30 days",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

    async def test_milestone_read_operations(self):
        """Phase 2c: Test milestone read operations"""
        print(
            f"\n{self._color('=== Phase 2c: Milestone Read Operations ===')}"
        )

        # milestone_list
        success, response, raw_result = await self._call_tool(
            "milestone_list"
        )
        milestone_name = None
        if (
            success
            and response.strip()
            and "No milestones" not in response
        ):
            milestone_name = response.strip().split("\n")[0]

        result = CheckResult(
            tool="milestone_list",
            test_name="list_all",
            passed=success,
            response=response[:200],
            notes=f"First milestone: {milestone_name}"
            if milestone_name
            else "No milestones found",
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        # milestone_get - if we have a milestone
        if milestone_name:
            _args = {"name": milestone_name}
            success, response, raw_result = await self._call_tool(
                "milestone_get", _args
            )
            result = CheckResult(
                tool="milestone_get",
                test_name="existing_milestone",
                passed=success and "Milestone:" in response,
                response=response[:300],
                notes=f"Retrieved milestone: {milestone_name}",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # milestone_get - raw mode
            _args = {"name": milestone_name, "raw": True}
            success, response, raw_result = await self._call_tool(
                "milestone_get", _args
            )
            result = CheckResult(
                tool="milestone_get",
                test_name="raw_mode",
                passed=success,
                response=response[:200],
                notes="Raw TracWiki format for description",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)
        else:
            result = CheckResult(
                tool="milestone_get",
                test_name="existing_milestone",
                passed=True,  # Not a failure, just no milestones
                notes="SKIPPED - No milestones exist",
            )
            self.report.results.append(result)
            self._log_result(result)

    async def test_ticket_write_operations(self):
        """Phase 3a: Test ticket write operations"""
        print(
            f"\n{self._color('=== Phase 3a: Ticket Write Operations ===')}"
        )

        # ticket_create
        summary = f"[MCP TEST {self.timestamp}] Comprehensive Tool Test"
        description = """## Test Ticket

This is a **Markdown** test.

- Item 1
- Item 2

### Code Example

```python
print("hello world")
```
"""
        _args = {
            "summary": summary,
            "description": description,
            "ticket_type": "task",
            "keywords": "mcp-test,auto-delete",
        }
        success, response, raw_result = await self._call_tool(
            "ticket_create",
            _args,
        )

        if success and "Created ticket #" in response:
            try:
                self.test_ticket_id = int(
                    response.split("#")[1].split(":")[0]
                )
            except (ValueError, IndexError):
                pass

        result = CheckResult(
            tool="ticket_create",
            test_name="create_with_markdown",
            passed=success and "Created ticket" in response,
            response=response,
            notes=f"Created ticket #{self.test_ticket_id}"
            if self.test_ticket_id
            else "Failed to extract ticket ID",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        if self.test_ticket_id:
            # Verify Markdown conversion
            _verify_args = {
                "ticket_id": self.test_ticket_id,
                "raw": True,
            }
            _, verify_response, _verify_raw = await self._call_tool(
                "ticket_get",
                _verify_args,
            )
            # Check for TracWiki markers ('''bold''' instead of **bold**)
            has_tracwiki = (
                "'''" in verify_response
                or "==" in verify_response
                or "{{{" in verify_response
            )
            result = CheckResult(
                tool="ticket_create",
                test_name="markdown_conversion",
                passed=has_tracwiki,
                response=verify_response[:400],
                notes="Verified Markdown converted to TracWiki"
                if has_tracwiki
                else "Conversion may not have occurred",
                call_args=_verify_args,
                **_extract_raw_fields(_verify_raw),
            )
            self.report.results.append(result)
            self._log_result(result)

            # ticket_update - add comment
            _args = {
                "ticket_id": self.test_ticket_id,
                "comment": "### Update Comment\n\nAdding a **formatted** comment.",
            }
            success, response, raw_result = await self._call_tool(
                "ticket_update",
                _args,
            )
            result = CheckResult(
                tool="ticket_update",
                test_name="add_comment",
                passed=success and "Updated ticket" in response,
                response=response,
                notes="Comment with Markdown formatting",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # ticket_update - change fields
            _args = {
                "ticket_id": self.test_ticket_id,
                "priority": "major",  # Use valid Trac priority value
                "keywords": "mcp-test,auto-delete,updated",
            }
            success, response, raw_result = await self._call_tool(
                "ticket_update",
                _args,
            )
            result = CheckResult(
                tool="ticket_update",
                test_name="update_fields",
                passed=success and "Updated ticket" in response,
                response=response,
                notes="Updated priority and keywords",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

    async def test_wiki_write_operations(self):
        """Phase 3b: Test wiki write operations"""
        print(
            f"\n{self._color('=== Phase 3b: Wiki Write Operations ===')}"
        )

        # wiki_create
        self.test_wiki_page = f"MCPTest_{self.timestamp}"
        content = """# Test Page

## Features

- **Bold** text
- *Italic* text
- `Code` text

### Code Block

```python
print('hello')
```

### Links

- [External Link](https://example.com)
- WikiStart (internal link)
"""
        _args = {
            "page_name": self.test_wiki_page,
            "content": content,
            "comment": "MCP test page creation",
        }
        success, response, raw_result = await self._call_tool(
            "wiki_create",
            _args,
        )

        result = CheckResult(
            tool="wiki_create",
            test_name="create_with_markdown",
            passed=success and "Created wiki page" in response,
            response=response,
            notes=f"Created page: {self.test_wiki_page}"
            if success
            else "Creation failed",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        if success:
            # Verify creation
            _, verify_response, _raw = await self._call_tool(
                "wiki_get",
                {"page_name": self.test_wiki_page, "raw": True},
            )
            has_tracwiki = (
                "'''" in verify_response
                or "==" in verify_response
                or "{{{" in verify_response
            )
            result = CheckResult(
                tool="wiki_create",
                test_name="markdown_conversion",
                passed=has_tracwiki,
                response=verify_response[:400],
                notes="Verified Markdown converted to TracWiki",
                call_args={
                    "page_name": self.test_wiki_page,
                    "raw": True,
                },
                **_extract_raw_fields(_raw),
            )
            self.report.results.append(result)
            self._log_result(result)

            # wiki_create - duplicate (should fail)
            _args = {
                "page_name": self.test_wiki_page,
                "content": "Duplicate content",
            }
            success, response, raw_result = await self._call_tool(
                "wiki_create",
                _args,
            )
            result = CheckResult(
                tool="wiki_create",
                test_name="duplicate_error",
                passed="already_exists" in response
                or "already exists" in response.lower(),
                response=response,
                notes="Expected error for duplicate page",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # wiki_update
            _args = {
                "page_name": self.test_wiki_page,
                "content": "# Updated Test Page\n\nThis page was updated.",
                "version": 1,
                "comment": "MCP test page update",
            }
            success, response, raw_result = await self._call_tool(
                "wiki_update",
                _args,
            )
            result = CheckResult(
                tool="wiki_update",
                test_name="update_page",
                passed=success
                and (
                    "Updated wiki page" in response
                    or "version 2" in response
                ),
                response=response,
                notes="Updated to version 2",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # wiki_update - version conflict
            _args = {
                "page_name": self.test_wiki_page,
                "content": "Conflict content",
                "version": 1,
                "comment": "Should conflict",
            }
            success, response, raw_result = await self._call_tool(
                "wiki_update",
                _args,
            )
            # Note: Some Trac versions don't enforce optimistic locking
            result = CheckResult(
                tool="wiki_update",
                test_name="version_conflict",
                passed="version_conflict" in response
                or "Updated wiki page" in response,
                response=response,
                notes="Tested version conflict detection (may not be enforced by server)",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)
        else:
            self.test_wiki_page = None

    async def test_wiki_file_operations(self):
        """Phase 3d: Test wiki file operations"""
        print(
            f"\n{self._color('=== Phase 3d: Wiki File Operations ===')}"
        )

        # wiki_file_detect_format - test with a known file
        # Create a temporary test file first
        import os
        import tempfile

        test_md_path = os.path.join(
            tempfile.gettempdir(), f"mcp_test_{self.timestamp}.md"
        )
        with open(test_md_path, "w") as f:
            f.write("# Test File\n\nThis is **Markdown** content.\n")

        try:
            # wiki_file_detect_format
            _args = {"file_path": test_md_path}
            success, response, raw_result = await self._call_tool(
                "wiki_file_detect_format", _args
            )
            result = CheckResult(
                tool="wiki_file_detect_format",
                test_name="detect_markdown",
                passed=success and "markdown" in response.lower(),
                response=response[:200],
                notes="Detected format of .md file",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # wiki_file_push - push test file to wiki
            test_wiki_file_page = f"MCPFileTest_{self.timestamp}"
            _args = {
                "file_path": test_md_path,
                "page_name": test_wiki_file_page,
                "comment": "MCP file push test",
            }
            success, response, raw_result = await self._call_tool(
                "wiki_file_push",
                _args,
            )
            result = CheckResult(
                tool="wiki_file_push",
                test_name="push_markdown_file",
                passed=success
                and ("Created" in response or "Updated" in response),
                response=response[:200],
                notes=f"Pushed file to wiki page: {test_wiki_file_page}",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            if success:
                # wiki_file_pull - pull it back
                pull_path = os.path.join(
                    tempfile.gettempdir(),
                    f"mcp_pull_{self.timestamp}.md",
                )
                _args = {
                    "page_name": test_wiki_file_page,
                    "file_path": pull_path,
                    "format": "markdown",
                }
                success, response, raw_result = await self._call_tool(
                    "wiki_file_pull",
                    _args,
                )
                result = CheckResult(
                    tool="wiki_file_pull",
                    test_name="pull_to_markdown",
                    passed=success and "Pulled" in response,
                    response=response[:200],
                    notes=f"Pulled wiki page to: {pull_path}",
                    call_args=_args,
                    **_extract_raw_fields(raw_result),
                )
                self.report.results.append(result)
                self._log_result(result)

                # Verify pulled file exists and has content
                if os.path.exists(pull_path):
                    with open(pull_path) as f:
                        pulled_content = f.read()
                    result = CheckResult(
                        tool="wiki_file_pull",
                        test_name="verify_content",
                        passed=len(pulled_content) > 0
                        and "Test File" in pulled_content,
                        response=pulled_content[:200],
                        notes="Verified pulled file has expected content",
                    )
                    self.report.results.append(result)
                    self._log_result(result)
                    os.unlink(pull_path)

                # Clean up the wiki page we created
                await self._call_tool(
                    "wiki_delete", {"page_name": test_wiki_file_page}
                )

        finally:
            # Clean up temp file
            if os.path.exists(test_md_path):
                os.unlink(test_md_path)

    async def test_milestone_write_operations(self):
        """Phase 3c: Test milestone write operations"""
        print(
            f"\n{self._color('=== Phase 3c: Milestone Write Operations ===')}"
        )

        # milestone_create
        self.test_milestone = f"MCP-Test-{self.timestamp}"
        _args = {
            "name": self.test_milestone,
            "attributes": {
                "due": "2026-12-31T23:59:59",
                "description": "Test milestone for MCP validation",
            },
        }
        success, response, raw_result = await self._call_tool(
            "milestone_create",
            _args,
        )

        result = CheckResult(
            tool="milestone_create",
            test_name="create_milestone",
            passed=success and "Created milestone" in response,
            response=response,
            notes=f"Created: {self.test_milestone}"
            if success
            else "Creation failed",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        if success:
            # Verify creation
            _verify_args = {"name": self.test_milestone}
            (
                verify_success,
                verify_response,
                _verify_raw,
            ) = await self._call_tool("milestone_get", _verify_args)
            result = CheckResult(
                tool="milestone_create",
                test_name="verify_creation",
                passed=verify_success
                and self.test_milestone in verify_response,
                response=verify_response[:200],
                notes="Verified milestone exists",
                call_args=_verify_args,
                **_extract_raw_fields(_verify_raw),
            )
            self.report.results.append(result)
            self._log_result(result)

            # milestone_update
            _args = {
                "name": self.test_milestone,
                "attributes": {
                    "description": "Updated description",
                    "completed": "2026-02-04T12:00:00",
                },
            }
            success, response, raw_result = await self._call_tool(
                "milestone_update",
                _args,
            )
            result = CheckResult(
                tool="milestone_update",
                test_name="update_milestone",
                passed=success and "Updated milestone" in response,
                response=response,
                notes="Updated description and completed date",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)
        else:
            self.test_milestone = None

    async def test_ticket_batch_operations(self):
        """Phase 3f: Test batch ticket operations"""
        print(
            f"\n{self._color('=== Phase 3f: Batch Ticket Operations ===')}"
        )

        # --- ticket_batch_create: create BATCH_TEST_SIZE tickets ---
        tickets = [
            {
                "summary": f"[MCP BATCH {self.timestamp}] Ticket {i + 1}/{BATCH_TEST_SIZE}",
                "description": f"Batch test ticket **{i + 1}**. Auto-created, auto-deleted.",
                "ticket_type": "task",
                "keywords": "mcp-batch-test,auto-delete",
            }
            for i in range(BATCH_TEST_SIZE)
        ]

        _args = {"tickets": tickets}
        success, response, raw_result = await self._call_tool(
            "ticket_batch_create", _args
        )

        # Extract created ticket IDs from response lines like "  - #123: ..."
        created_ids = [int(m) for m in re.findall(r"#(\d+):", response)]
        self.test_batch_ticket_ids = created_ids

        result = CheckResult(
            tool="ticket_batch_create",
            test_name="create_batch",
            passed=success
            and f"{BATCH_TEST_SIZE}/{BATCH_TEST_SIZE} succeeded"
            in response,
            response=response[:400],
            notes=f"Created {len(created_ids)} tickets: #{min(created_ids)}..#{max(created_ids)}"
            if created_ids
            else "No tickets created",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        # --- ticket_batch_create: verify a sample ticket exists ---
        if created_ids:
            sample_id = created_ids[0]
            _verify_args = {"ticket_id": sample_id}
            verify_ok, verify_resp, _verify_raw = await self._call_tool(
                "ticket_get", _verify_args
            )
            result = CheckResult(
                tool="ticket_batch_create",
                test_name="verify_created",
                passed=verify_ok
                and f"Ticket #{sample_id}" in verify_resp,
                response=verify_resp[:200],
                notes=f"Spot-checked ticket #{sample_id}",
                call_args=_verify_args,
                **_extract_raw_fields(_verify_raw),
            )
            self.report.results.append(result)
            self._log_result(result)

        # --- ticket_batch_create: partial failure (missing summary) ---
        mixed_tickets = [
            {
                "summary": f"[MCP BATCH {self.timestamp}] Good ticket",
                "description": "Valid ticket",
            },
            {"description": "Missing summary field"},  # should fail
            {
                "summary": f"[MCP BATCH {self.timestamp}] Another good",
                "description": "Also valid",
            },
        ]
        _args = {"tickets": mixed_tickets}
        success, response, raw_result = await self._call_tool(
            "ticket_batch_create", _args
        )

        # Parse any newly created IDs for cleanup
        extra_ids = [int(m) for m in re.findall(r"#(\d+):", response)]
        self.test_batch_ticket_ids.extend(extra_ids)

        result = CheckResult(
            tool="ticket_batch_create",
            test_name="partial_failure",
            passed="2/3 succeeded" in response
            and "1 failed" in response,
            response=response[:400],
            notes="1 ticket missing summary should fail, 2 should succeed",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        # --- ticket_batch_create: empty list validation ---
        _args = {"tickets": []}
        _, response, _raw = await self._call_tool(
            "ticket_batch_create", _args
        )
        result = CheckResult(
            tool="ticket_batch_create",
            test_name="empty_list_error",
            passed="validation_error" in response
            or "required" in response.lower(),
            response=response[:200],
            notes="Expected validation error for empty tickets list",
            call_args=_args,
            **_extract_raw_fields(_raw),
        )
        self.report.results.append(result)
        self._log_result(result)

        # --- ticket_batch_update: update all created tickets ---
        if self.test_batch_ticket_ids:
            updates = [
                {
                    "ticket_id": tid,
                    "keywords": "mcp-batch-test,auto-delete,batch-updated",
                    "comment": f"Batch update test -- ticket **#{tid}**",
                }
                for tid in self.test_batch_ticket_ids
            ]

            _args = {"updates": updates}
            success, response, raw_result = await self._call_tool(
                "ticket_batch_update", _args
            )
            expected_count = len(self.test_batch_ticket_ids)

            result = CheckResult(
                tool="ticket_batch_update",
                test_name="update_batch",
                passed=success
                and f"{expected_count}/{expected_count} succeeded"
                in response,
                response=response[:400],
                notes=f"Updated {expected_count} tickets with keywords + comment",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # Spot-check that update applied
            sample_id = self.test_batch_ticket_ids[0]
            _verify_args = {"ticket_id": sample_id}
            verify_ok, verify_resp, _verify_raw = await self._call_tool(
                "ticket_get", _verify_args
            )
            result = CheckResult(
                tool="ticket_batch_update",
                test_name="verify_updated",
                passed=verify_ok and "batch-updated" in verify_resp,
                response=verify_resp[:300],
                notes=f"Verified keyword added to ticket #{sample_id}",
                call_args=_verify_args,
                **_extract_raw_fields(_verify_raw),
            )
            self.report.results.append(result)
            self._log_result(result)

        # --- ticket_batch_update: empty list validation ---
        _args = {"updates": []}
        _, response, _raw = await self._call_tool(
            "ticket_batch_update", _args
        )
        result = CheckResult(
            tool="ticket_batch_update",
            test_name="empty_list_error",
            passed="validation_error" in response
            or "required" in response.lower(),
            response=response[:200],
            notes="Expected validation error for empty updates list",
            call_args=_args,
            **_extract_raw_fields(_raw),
        )
        self.report.results.append(result)
        self._log_result(result)

        # --- ticket_batch_delete: delete all created tickets ---
        if self.test_batch_ticket_ids:
            _args = {"ticket_ids": self.test_batch_ticket_ids}
            success, response, raw_result = await self._call_tool(
                "ticket_batch_delete",
                _args,
            )
            expected_count = len(self.test_batch_ticket_ids)

            result = CheckResult(
                tool="ticket_batch_delete",
                test_name="delete_batch",
                passed=success
                and f"{expected_count}/{expected_count} succeeded"
                in response,
                response=response[:400],
                notes=f"Deleted {expected_count} tickets",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            if (
                success
                and f"{expected_count}/{expected_count} succeeded"
                in response
            ):
                # Spot-check a ticket is gone
                sample_id = self.test_batch_ticket_ids[0]
                _verify_args = {"ticket_id": sample_id}
                _, verify_resp, _verify_raw = await self._call_tool(
                    "ticket_get", _verify_args
                )
                result = CheckResult(
                    tool="ticket_batch_delete",
                    test_name="verify_deleted",
                    passed="not_found" in verify_resp
                    or "error" in verify_resp.lower(),
                    response=verify_resp[:200],
                    notes=f"Confirmed ticket #{sample_id} no longer exists",
                    call_args=_verify_args,
                    **_extract_raw_fields(_verify_raw),
                )
                self.report.results.append(result)
                self._log_result(result)

                self.test_batch_ticket_ids = []  # All cleaned up

        # --- ticket_batch_delete: empty list validation ---
        _args = {"ticket_ids": []}
        _, response, _raw = await self._call_tool(
            "ticket_batch_delete", _args
        )
        result = CheckResult(
            tool="ticket_batch_delete",
            test_name="empty_list_error",
            passed="validation_error" in response
            or "required" in response.lower(),
            response=response[:200],
            notes="Expected validation error for empty ticket_ids list",
            call_args=_args,
            **_extract_raw_fields(_raw),
        )
        self.report.results.append(result)
        self._log_result(result)

    async def test_ticket_admin_operations(self):
        """Phase 3g: Test ticket admin operations (components, enums)"""
        print(
            f"\n{self._color('=== Phase 3g: Ticket Admin Operations ===')}"
        )

        # ticket_component_create
        self.test_component = f"MCPComponent_{self.timestamp}"
        _args = {
            "name": self.test_component,
            "description": "MCP test component",
        }
        success, response, raw_result = await self._call_tool(
            "ticket_component_create", _args
        )
        result = CheckResult(
            tool="ticket_component_create",
            test_name="create_component",
            passed=success and "created" in response.lower(),
            response=response,
            notes=f"Created: {self.test_component}"
            if success
            else "Creation failed",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        if success:
            # ticket_component_list - verify presence
            success, response, raw_result = await self._call_tool(
                "ticket_component_list"
            )
            result = CheckResult(
                tool="ticket_component_list",
                test_name="verify_created",
                passed=success and self.test_component in response,
                response=response[:300],
                notes="Verified component appears in list",
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # ticket_component_delete
            _args = {"name": self.test_component}
            success, response, raw_result = await self._call_tool(
                "ticket_component_delete", _args
            )
            result = CheckResult(
                tool="ticket_component_delete",
                test_name="delete_component",
                passed=success and "deleted" in response.lower(),
                response=response,
                notes=f"Deleted: {self.test_component}",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)
            if success:
                self.test_component = None

        # ticket_enum_create -- "severity" accepts free-form values without
        # the ordering semantics "version" has, so it's the safest enum
        # type for a throwaway test value.
        self.test_enum = ("severity", f"MCPSeverity{self.timestamp}")
        enum_type, enum_name = self.test_enum
        _args = {"enum_type": enum_type, "name": enum_name}
        success, response, raw_result = await self._call_tool(
            "ticket_enum_create", _args
        )
        result = CheckResult(
            tool="ticket_enum_create",
            test_name="create_enum",
            passed=success and "created" in response.lower(),
            response=response,
            notes=f"Created {enum_type} value: {enum_name}"
            if success
            else "Creation failed",
            call_args=_args,
            **_extract_raw_fields(raw_result),
        )
        self.report.results.append(result)
        self._log_result(result)

        if success:
            # ticket_enum_list - verify presence
            _args = {"enum_type": enum_type}
            success, response, raw_result = await self._call_tool(
                "ticket_enum_list", _args
            )
            result = CheckResult(
                tool="ticket_enum_list",
                test_name="verify_created",
                passed=success and enum_name in response,
                response=response[:300],
                notes="Verified enum value appears in list",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            # ticket_enum_delete
            _args = {"enum_type": enum_type, "name": enum_name}
            success, response, raw_result = await self._call_tool(
                "ticket_enum_delete", _args
            )
            result = CheckResult(
                tool="ticket_enum_delete",
                test_name="delete_enum",
                passed=success and "deleted" in response.lower(),
                response=response,
                notes=f"Deleted {enum_type} value: {enum_name}",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)
            if success:
                self.test_enum = None

    async def test_ticket_attachment_operations(self):
        """Phase 3h: Test ticket attachment operations"""
        print(
            f"\n{self._color('=== Phase 3h: Ticket Attachment Operations ===')}"
        )

        if not self.test_ticket_id:
            print("  (skipped: no test ticket available)")
            return

        import os
        import tempfile

        attach_path = os.path.join(
            tempfile.gettempdir(),
            f"mcp_ticket_attach_{self.timestamp}.txt",
        )
        attach_content = (
            f"MCP ticket attachment test {self.timestamp}\n"
        )
        with open(attach_path, "w") as f:
            f.write(attach_content)

        try:
            filename = f"mcp_test_{self.timestamp}.txt"
            _args = {
                "ticket_id": self.test_ticket_id,
                "file_path": attach_path,
                "filename": filename,
                "description": "MCP attachment test",
            }
            success, response, raw_result = await self._call_tool(
                "ticket_attachment_put", _args
            )
            result = CheckResult(
                tool="ticket_attachment_put",
                test_name="upload",
                passed=success and "Uploaded attachment" in response,
                response=response,
                notes=f"Uploaded: {filename}"
                if success
                else "Upload failed",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            if success:
                self.test_ticket_attachment_filename = filename

                # ticket_attachment_list
                _args = {"ticket_id": self.test_ticket_id}
                success, response, raw_result = await self._call_tool(
                    "ticket_attachment_list", _args
                )
                result = CheckResult(
                    tool="ticket_attachment_list",
                    test_name="list_attachments",
                    passed=success and filename in response,
                    response=response[:300],
                    notes="Verified attachment appears in list",
                    call_args=_args,
                    **_extract_raw_fields(raw_result),
                )
                self.report.results.append(result)
                self._log_result(result)

                # ticket_attachment_get - download and verify byte-identical
                download_path = os.path.join(
                    tempfile.gettempdir(),
                    f"mcp_ticket_download_{self.timestamp}.txt",
                )
                _args = {
                    "ticket_id": self.test_ticket_id,
                    "filename": filename,
                    "output_path": download_path,
                }
                success, response, raw_result = await self._call_tool(
                    "ticket_attachment_get", _args
                )
                result = CheckResult(
                    tool="ticket_attachment_get",
                    test_name="download",
                    passed=success
                    and "Downloaded attachment" in response,
                    response=response,
                    notes="Downloaded attachment to temp file",
                    call_args=_args,
                    **_extract_raw_fields(raw_result),
                )
                self.report.results.append(result)
                self._log_result(result)

                if os.path.exists(download_path):
                    with open(download_path) as f:
                        downloaded_content = f.read()
                    result = CheckResult(
                        tool="ticket_attachment_get",
                        test_name="verify_bytes",
                        passed=downloaded_content == attach_content,
                        response=downloaded_content[:200],
                        notes="Verified byte-identical round-trip",
                    )
                    self.report.results.append(result)
                    self._log_result(result)
                    os.unlink(download_path)

                # ticket_attachment_delete
                _args = {
                    "ticket_id": self.test_ticket_id,
                    "filename": filename,
                }
                success, response, raw_result = await self._call_tool(
                    "ticket_attachment_delete", _args
                )
                result = CheckResult(
                    tool="ticket_attachment_delete",
                    test_name="delete",
                    passed=success and "Deleted attachment" in response,
                    response=response,
                    notes=f"Deleted: {filename}",
                    call_args=_args,
                    **_extract_raw_fields(raw_result),
                )
                self.report.results.append(result)
                self._log_result(result)
                if success:
                    self.test_ticket_attachment_filename = None

        finally:
            if os.path.exists(attach_path):
                os.unlink(attach_path)

    async def test_wiki_attachment_operations(self):
        """Phase 3i: Test wiki attachment operations"""
        print(
            f"\n{self._color('=== Phase 3i: Wiki Attachment Operations ===')}"
        )

        if not self.test_wiki_page:
            print("  (skipped: no test wiki page available)")
            return

        import os
        import tempfile

        attach_path = os.path.join(
            tempfile.gettempdir(),
            f"mcp_wiki_attach_{self.timestamp}.txt",
        )
        attach_content = f"MCP wiki attachment test {self.timestamp}\n"
        with open(attach_path, "w") as f:
            f.write(attach_content)

        try:
            filename = f"mcp_test_{self.timestamp}.txt"
            _args = {
                "page_name": self.test_wiki_page,
                "file_path": attach_path,
                "filename": filename,
                "description": "MCP attachment test",
            }
            success, response, raw_result = await self._call_tool(
                "wiki_attachment_put", _args
            )
            result = CheckResult(
                tool="wiki_attachment_put",
                test_name="upload",
                passed=success and "Uploaded attachment" in response,
                response=response,
                notes=f"Uploaded: {filename}"
                if success
                else "Upload failed",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            if success:
                self.test_wiki_attachment_filename = filename

                # wiki_attachment_list
                _args = {"page_name": self.test_wiki_page}
                success, response, raw_result = await self._call_tool(
                    "wiki_attachment_list", _args
                )
                result = CheckResult(
                    tool="wiki_attachment_list",
                    test_name="list_attachments",
                    passed=success and filename in response,
                    response=response[:300],
                    notes="Verified attachment appears in list",
                    call_args=_args,
                    **_extract_raw_fields(raw_result),
                )
                self.report.results.append(result)
                self._log_result(result)

                # wiki_attachment_get - download and verify byte-identical
                download_path = os.path.join(
                    tempfile.gettempdir(),
                    f"mcp_wiki_download_{self.timestamp}.txt",
                )
                _args = {
                    "page_name": self.test_wiki_page,
                    "filename": filename,
                    "output_path": download_path,
                }
                success, response, raw_result = await self._call_tool(
                    "wiki_attachment_get", _args
                )
                result = CheckResult(
                    tool="wiki_attachment_get",
                    test_name="download",
                    passed=success
                    and "Downloaded attachment" in response,
                    response=response,
                    notes="Downloaded attachment to temp file",
                    call_args=_args,
                    **_extract_raw_fields(raw_result),
                )
                self.report.results.append(result)
                self._log_result(result)

                if os.path.exists(download_path):
                    with open(download_path) as f:
                        downloaded_content = f.read()
                    result = CheckResult(
                        tool="wiki_attachment_get",
                        test_name="verify_bytes",
                        passed=downloaded_content == attach_content,
                        response=downloaded_content[:200],
                        notes="Verified byte-identical round-trip",
                    )
                    self.report.results.append(result)
                    self._log_result(result)
                    os.unlink(download_path)

                # wiki_attachment_delete
                _args = {
                    "page_name": self.test_wiki_page,
                    "filename": filename,
                }
                success, response, raw_result = await self._call_tool(
                    "wiki_attachment_delete", _args
                )
                result = CheckResult(
                    tool="wiki_attachment_delete",
                    test_name="delete",
                    passed=success and "Deleted attachment" in response,
                    response=response,
                    notes=f"Deleted: {filename}",
                    call_args=_args,
                    **_extract_raw_fields(raw_result),
                )
                self.report.results.append(result)
                self._log_result(result)
                if success:
                    self.test_wiki_attachment_filename = None

        finally:
            if os.path.exists(attach_path):
                os.unlink(attach_path)

    async def test_delete_operations(self):
        """Phase 4: Test delete operations"""
        print(f"\n{self._color('=== Phase 4: Delete Operations ===')}")

        # wiki_delete
        if self.test_wiki_page:
            _args = {"page_name": self.test_wiki_page}
            success, response, raw_result = await self._call_tool(
                "wiki_delete", _args
            )
            result = CheckResult(
                tool="wiki_delete",
                test_name="delete_page",
                passed=success and "Deleted" in response,
                response=response,
                notes=f"Deleted: {self.test_wiki_page}",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            if success:
                # Verify deletion
                _verify_args = {"page_name": self.test_wiki_page}
                _, verify_response, _verify_raw = await self._call_tool(
                    "wiki_get", _verify_args
                )
                result = CheckResult(
                    tool="wiki_delete",
                    test_name="verify_deletion",
                    passed="not_found" in verify_response
                    or "does not exist" in verify_response.lower(),
                    response=verify_response[:200],
                    notes="Confirmed page no longer exists",
                    call_args=_verify_args,
                    **_extract_raw_fields(_verify_raw),
                )
                self.report.results.append(result)
                self._log_result(result)
                self.test_wiki_page = None

        # milestone_delete
        if self.test_milestone:
            _args = {"name": self.test_milestone}
            success, response, raw_result = await self._call_tool(
                "milestone_delete", _args
            )
            result = CheckResult(
                tool="milestone_delete",
                test_name="delete_milestone",
                passed=success and "Deleted" in response,
                response=response,
                notes=f"Deleted: {self.test_milestone}",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            if success:
                # Verify deletion
                _verify_args = {"name": self.test_milestone}
                _, verify_response, _verify_raw = await self._call_tool(
                    "milestone_get", _verify_args
                )
                result = CheckResult(
                    tool="milestone_delete",
                    test_name="verify_deletion",
                    passed="not_found" in verify_response
                    or "error" in verify_response.lower(),
                    response=verify_response[:200],
                    notes="Confirmed milestone no longer exists",
                    call_args=_verify_args,
                    **_extract_raw_fields(_verify_raw),
                )
                self.report.results.append(result)
                self._log_result(result)
                self.test_milestone = None

        # ticket_delete
        if self.test_ticket_id:
            _args = {"ticket_id": self.test_ticket_id}
            success, response, raw_result = await self._call_tool(
                "ticket_delete", _args
            )
            result = CheckResult(
                tool="ticket_delete",
                test_name="delete_ticket",
                passed=success
                and (
                    "Deleted" in response
                    or "deleted" in response.lower()
                ),
                response=response[:200],
                notes=f"Deleted test ticket #{self.test_ticket_id}",
                call_args=_args,
                **_extract_raw_fields(raw_result),
            )
            self.report.results.append(result)
            self._log_result(result)

            if success:
                # Verify deletion
                _verify_args = {"ticket_id": self.test_ticket_id}
                _, verify_response, _verify_raw = await self._call_tool(
                    "ticket_get", _verify_args
                )
                result = CheckResult(
                    tool="ticket_delete",
                    test_name="verify_deletion",
                    passed="not_found" in verify_response
                    or "error" in verify_response.lower(),
                    response=verify_response[:200],
                    notes="Confirmed ticket no longer exists",
                    call_args=_verify_args,
                    **_extract_raw_fields(_verify_raw),
                )
                self.report.results.append(result)
                self._log_result(result)
                self.test_ticket_id = (
                    None  # Prevent cleanup from trying to close it
                )

    async def test_error_handling(self):
        """Phase 5: Test error handling"""
        print(f"\n{self._color('=== Phase 5: Error Handling ===')}")

        # ticket_get - non-existent
        _args = {"ticket_id": 99999999}
        _, response, _raw = await self._call_tool("ticket_get", _args)
        result = CheckResult(
            tool="ticket_get",
            test_name="non_existent",
            passed="not_found" in response
            or "error" in response.lower(),
            response=response[:200],
            notes="Expected not_found error",
            call_args=_args,
            **_extract_raw_fields(_raw),
        )
        self.report.results.append(result)
        self._log_result(result)

        # ticket_delete - non-existent
        _args = {"ticket_id": 99999999}
        _, response, _raw = await self._call_tool(
            "ticket_delete", _args
        )
        result = CheckResult(
            tool="ticket_delete",
            test_name="non_existent",
            passed="not_found" in response
            or "error" in response.lower(),
            response=response[:200],
            notes="Expected not_found error",
            call_args=_args,
            **_extract_raw_fields(_raw),
        )
        self.report.results.append(result)
        self._log_result(result)

        # wiki_get - non-existent
        _args = {"page_name": "NonExistentPage_DoesNotExist_12345"}
        _, response, _raw = await self._call_tool(
            "wiki_get",
            _args,
        )
        result = CheckResult(
            tool="wiki_get",
            test_name="non_existent",
            passed="not_found" in response
            or "does not exist" in response.lower(),
            response=response[:200],
            notes="Expected not_found error",
            call_args=_args,
            **_extract_raw_fields(_raw),
        )
        self.report.results.append(result)
        self._log_result(result)

        # milestone_get - non-existent
        _args = {"name": "NonExistent-Milestone-12345"}
        _, response, _raw = await self._call_tool(
            "milestone_get", _args
        )
        result = CheckResult(
            tool="milestone_get",
            test_name="non_existent",
            passed="not_found" in response
            or "error" in response.lower(),
            response=response[:200],
            notes="Expected not_found error",
            call_args=_args,
            **_extract_raw_fields(_raw),
        )
        self.report.results.append(result)
        self._log_result(result)

        # wiki_delete - non-existent
        _args = {"page_name": "NonExistentPage_ToDelete_12345"}
        _, response, _raw = await self._call_tool(
            "wiki_delete",
            _args,
        )
        result = CheckResult(
            tool="wiki_delete",
            test_name="non_existent",
            passed="not_found" in response
            or "does not exist" in response.lower(),
            response=response[:200],
            notes="Expected not_found error",
            call_args=_args,
            **_extract_raw_fields(_raw),
        )
        self.report.results.append(result)
        self._log_result(result)

        # ticket_create - missing required field
        _args = {"description": "No summary"}
        _, response, _raw = await self._call_tool(
            "ticket_create", _args
        )
        result = CheckResult(
            tool="ticket_create",
            test_name="missing_summary",
            passed="validation_error" in response
            or "required" in response.lower(),
            response=response[:200],
            notes="Expected validation_error",
            call_args=_args,
            **_extract_raw_fields(_raw),
        )
        self.report.results.append(result)
        self._log_result(result)

    async def cleanup(self):
        """Clean up test resources"""
        print(f"\n{self._color('=== Cleanup ===')}")

        cleanup_success = True

        # Batch-delete leftover batch tickets (if batch delete test failed)
        if self.test_batch_ticket_ids:
            success, _, _ = await self._call_tool(
                "ticket_batch_delete",
                {"ticket_ids": self.test_batch_ticket_ids},
            )
            if success:
                print(
                    f"  {self._color('OK')} Batch-deleted {len(self.test_batch_ticket_ids)} leftover batch tickets"
                )
                self.test_batch_ticket_ids = []
            else:
                print(
                    f"  {self._color('FAIL')} Could not batch-delete leftover tickets, trying individually"
                )
                for tid in self.test_batch_ticket_ids:
                    try:
                        await self._call_tool(
                            "ticket_delete", {"ticket_id": tid}
                        )
                    except Exception:
                        cleanup_success = False

        # Close test ticket if still exists (fallback if delete failed or was skipped)
        if self.test_ticket_id:
            try:
                await self._call_tool(
                    "ticket_update",
                    {
                        "ticket_id": self.test_ticket_id,
                        "comment": "[AUTO-CLEANUP] MCP test completed",
                        "status": "closed",
                        "resolution": "invalid",
                    },
                )
                print(
                    f"  {self._color('OK')} Closed test ticket #{self.test_ticket_id}"
                )
            except Exception as e:
                print(
                    f"  {self._color('FAIL')} Could not close ticket #{self.test_ticket_id}: {e}"
                )
                cleanup_success = False

        # Delete test wiki page if still exists
        if self.test_wiki_page:
            success, _, _ = await self._call_tool(
                "wiki_delete", {"page_name": self.test_wiki_page}
            )
            if success:
                print(
                    f"  {self._color('OK')} Deleted test wiki page: {self.test_wiki_page}"
                )
            else:
                print(
                    f"  {self._color('FAIL')} Could not delete wiki page: {self.test_wiki_page}"
                )
                cleanup_success = False

        # Delete test milestone if still exists
        if self.test_milestone:
            success, _, _ = await self._call_tool(
                "milestone_delete", {"name": self.test_milestone}
            )
            if success:
                print(
                    f"  {self._color('OK')} Deleted test milestone: {self.test_milestone}"
                )
            else:
                print(
                    f"  {self._color('FAIL')} Could not delete milestone: {self.test_milestone}"
                )
                cleanup_success = False

        # Delete leftover ticket attachment (if delete test failed/was skipped)
        if self.test_ticket_attachment_filename and self.test_ticket_id:
            success, _, _ = await self._call_tool(
                "ticket_attachment_delete",
                {
                    "ticket_id": self.test_ticket_id,
                    "filename": self.test_ticket_attachment_filename,
                },
            )
            if success:
                print(
                    f"  {self._color('OK')} Deleted leftover ticket attachment: {self.test_ticket_attachment_filename}"
                )
                self.test_ticket_attachment_filename = None
            else:
                print(
                    f"  {self._color('FAIL')} Could not delete ticket attachment: {self.test_ticket_attachment_filename}"
                )
                cleanup_success = False

        # Delete leftover wiki attachment (if delete test failed/was skipped)
        if self.test_wiki_attachment_filename and self.test_wiki_page:
            success, _, _ = await self._call_tool(
                "wiki_attachment_delete",
                {
                    "page_name": self.test_wiki_page,
                    "filename": self.test_wiki_attachment_filename,
                },
            )
            if success:
                print(
                    f"  {self._color('OK')} Deleted leftover wiki attachment: {self.test_wiki_attachment_filename}"
                )
                self.test_wiki_attachment_filename = None
            else:
                print(
                    f"  {self._color('FAIL')} Could not delete wiki attachment: {self.test_wiki_attachment_filename}"
                )
                cleanup_success = False

        # Delete leftover ticket component (if delete test failed/was skipped)
        if self.test_component:
            success, _, _ = await self._call_tool(
                "ticket_component_delete", {"name": self.test_component}
            )
            if success:
                print(
                    f"  {self._color('OK')} Deleted leftover component: {self.test_component}"
                )
                self.test_component = None
            else:
                print(
                    f"  {self._color('FAIL')} Could not delete component: {self.test_component}"
                )
                cleanup_success = False

        # Delete leftover enum value (if delete test failed/was skipped)
        if self.test_enum:
            enum_type, enum_name = self.test_enum
            success, _, _ = await self._call_tool(
                "ticket_enum_delete",
                {"enum_type": enum_type, "name": enum_name},
            )
            if success:
                print(
                    f"  {self._color('OK')} Deleted leftover {enum_type} value: {enum_name}"
                )
                self.test_enum = None
            else:
                print(
                    f"  {self._color('FAIL')} Could not delete {enum_type} value: {enum_name}"
                )
                cleanup_success = False

        return cleanup_success

    def _generate_tool_catalog(self) -> list[str]:
        """Generate Tool Catalog section showing what LLM sees at tool listing time."""
        tools_to_show = self.available_tools
        if self.tools_filter:
            tools_to_show = [
                t
                for t in self.available_tools
                if t.name in self.tools_filter
            ]

        lines = [
            "## Tool Catalog (LLM Tool Presentation)",
            "",
            f"**Total tools registered:** {len(self.available_tools)}",
        ]
        if self.tools_filter:
            lines.append(
                f"**Tools shown (filtered):** {len(tools_to_show)}"
            )
        lines.extend(
            [
                "",
                "This section shows the exact tool definitions presented to the LLM/agent",
                "via `list_tools()`. Each entry includes name, description, and full inputSchema.",
                "",
            ]
        )
        for tool in tools_to_show:
            lines.append(f"### `{tool.name}`")
            lines.append("")
            lines.append(f"**Description:** {tool.description}")
            lines.append("")
            lines.append("**inputSchema:**")
            lines.append("```json")
            lines.append(json.dumps(tool.inputSchema, indent=2))
            lines.append("```")
            lines.append("")
        return lines

    def generate_report(self, output_path: str):
        """Generate comprehensive test report"""
        self.report.date = datetime.now().isoformat()

        # Group results by tool
        tools_tested = set()
        results_by_category = {
            "Connectivity": [],
            "System Tools": [],
            "Ticket Tools": [],
            "Batch Ticket Tools": [],
            "Ticket Admin Tools": [],
            "Ticket Attachment Tools": [],
            "Wiki Tools": [],
            "Wiki File Tools": [],
            "Wiki Attachment Tools": [],
            "Milestone Tools": [],
            "Error Handling": [],
        }

        for result in self.report.results:
            tools_tested.add(result.tool)
            if result.tool == "ping":
                results_by_category["Connectivity"].append(result)
            elif result.tool == "get_server_time":
                results_by_category["System Tools"].append(result)
            elif result.tool.startswith("ticket_batch_"):
                if "empty_list" in result.test_name:
                    results_by_category["Error Handling"].append(result)
                else:
                    results_by_category["Batch Ticket Tools"].append(
                        result
                    )
            elif result.tool.startswith(
                ("ticket_component_", "ticket_enum_")
            ):
                results_by_category["Ticket Admin Tools"].append(result)
            elif result.tool.startswith("ticket_attachment_"):
                results_by_category["Ticket Attachment Tools"].append(
                    result
                )
            elif result.tool.startswith("ticket_"):
                if (
                    "non_existent" in result.test_name
                    or "missing_" in result.test_name
                ):
                    results_by_category["Error Handling"].append(result)
                else:
                    results_by_category["Ticket Tools"].append(result)
            elif result.tool.startswith("wiki_file_"):
                if "non_existent" in result.test_name:
                    results_by_category["Error Handling"].append(result)
                else:
                    results_by_category["Wiki File Tools"].append(
                        result
                    )
            elif result.tool.startswith("wiki_attachment_"):
                results_by_category["Wiki Attachment Tools"].append(
                    result
                )
            elif result.tool.startswith("wiki_"):
                if "non_existent" in result.test_name:
                    results_by_category["Error Handling"].append(result)
                else:
                    results_by_category["Wiki Tools"].append(result)
            elif result.tool.startswith("milestone_"):
                if "non_existent" in result.test_name:
                    results_by_category["Error Handling"].append(result)
                else:
                    results_by_category["Milestone Tools"].append(
                        result
                    )
        report_lines = [
            "# Comprehensive MCP Tool Test Report",
            "",
            f"**Date:** {self.report.date}",
            f"**Server:** {self.report.server_url}",
            f"**Test Script Version:** {VERSION}",
            f"**Package Version:** {PACKAGE_VERSION}",
            "",
            "## Executive Summary",
            "",
            f"- **Tools Tested:** {len(tools_tested)}/{len(self.available_tools)}",
            f"- **Total Scenarios:** {self.report.total}",
            f"- **Passed:** {self.report.passed}",
            f"- **Failed:** {self.report.failed}",
            f"- **Pass Rate:** {(self.report.passed / self.report.total * 100):.1f}%"
            if self.report.total > 0
            else "N/A",
            "",
        ]

        # Insert Tool Catalog section
        report_lines.extend(self._generate_tool_catalog())

        for category, results in results_by_category.items():
            if not results:
                continue

            report_lines.extend(
                [
                    f"## {category}",
                    "",
                ]
            )

            # Group by tool
            current_tool = None
            for result in results:
                if result.tool != current_tool:
                    current_tool = result.tool
                    report_lines.extend(
                        [
                            f"### {result.tool}",
                            "",
                        ]
                    )

                status = "PASS" if result.passed else "FAIL"
                report_lines.append(f"**{result.test_name}:** {status}")
                if result.notes:
                    report_lines.append(f"- Notes: {result.notes}")
                if not result.passed and result.error:
                    report_lines.append(
                        f"- Error: {result.error[:100]}"
                    )

                # Show call arguments
                if result.call_args:
                    report_lines.append(
                        f"- **Call args:** `{json.dumps(result.call_args, default=str)}`"
                    )
                elif result.tool != "ping":
                    report_lines.append(
                        "- **Call args:** `{}`  (no arguments)"
                    )

                # Show structured content if present
                if result.structured_content is not None:
                    sc_json = json.dumps(
                        result.structured_content, indent=2, default=str
                    )
                    # Truncate very long structured content to keep report readable
                    if len(sc_json) > 2000:
                        sc_json = sc_json[:2000] + "\n  ... (truncated)"
                    report_lines.append("- **structuredContent:**")
                    report_lines.append("  ```json")
                    for sc_line in sc_json.split("\n"):
                        report_lines.append(f"  {sc_line}")
                    report_lines.append("  ```")

                # Show isError flag if set
                if result.is_error is not None:
                    report_lines.append(
                        f"- **isError:** `{result.is_error}`"
                    )

                # Show raw text content (first 500 chars) for reference
                if result.raw_text_content:
                    combined_text = "\n---\n".join(
                        result.raw_text_content
                    )
                    if len(combined_text) > 500:
                        combined_text = (
                            combined_text[:500] + "... (truncated)"
                        )
                    report_lines.append(
                        f"- **Text content preview:** {combined_text}"
                    )

                report_lines.append("")

        # Issues found section
        failed_results = [
            r for r in self.report.results if not r.passed
        ]
        if failed_results:
            report_lines.extend(
                [
                    "## Issues Found",
                    "",
                ]
            )
            for i, result in enumerate(failed_results, 1):
                report_lines.append(
                    f"{i}. **{result.tool}.{result.test_name}**: {result.error or result.response[:100]}"
                )
            report_lines.append("")

        # Write report
        with open(output_path, "w") as f:
            f.write("\n".join(report_lines))

        print(f"\n{self._color('Report saved to:')} {output_path}")

    async def run_all_tests(self) -> bool:
        """Run all test phases"""
        try:
            # Fetch tool catalog from the MCP server
            await self.fetch_tool_catalog()

            # Phase 1: Connectivity
            if self._should_test_tool("ping"):
                await self.test_ping()
            if self._should_test_tool("get_server_time"):
                await self.test_system_tools()

            # Phase 2: Read operations
            ticket_read_tools = {
                "ticket_search",
                "ticket_get",
                "ticket_changelog",
                "ticket_actions",
                "ticket_fields",
            }
            if (
                not self.tools_filter
                or self.tools_filter & ticket_read_tools
            ):
                await self.test_ticket_read_operations()

            wiki_read_tools = {
                "wiki_get",
                "wiki_search",
                "wiki_recent_changes",
            }
            if (
                not self.tools_filter
                or self.tools_filter & wiki_read_tools
            ):
                await self.test_wiki_read_operations()

            milestone_read_tools = {"milestone_list", "milestone_get"}
            if (
                not self.tools_filter
                or self.tools_filter & milestone_read_tools
            ):
                await self.test_milestone_read_operations()

            # Phase 3: Write operations
            ticket_write_tools = {"ticket_create", "ticket_update"}
            if (
                not self.tools_filter
                or self.tools_filter & ticket_write_tools
            ):
                await self.test_ticket_write_operations()

            wiki_write_tools = {"wiki_create", "wiki_update"}
            if (
                not self.tools_filter
                or self.tools_filter & wiki_write_tools
            ):
                await self.test_wiki_write_operations()

            wiki_file_tools = {
                "wiki_file_detect_format",
                "wiki_file_push",
                "wiki_file_pull",
            }
            if (
                not self.tools_filter
                or self.tools_filter & wiki_file_tools
            ):
                await self.test_wiki_file_operations()

            milestone_write_tools = {
                "milestone_create",
                "milestone_update",
            }
            if (
                not self.tools_filter
                or self.tools_filter & milestone_write_tools
            ):
                await self.test_milestone_write_operations()

            batch_tools = {
                "ticket_batch_create",
                "ticket_batch_update",
                "ticket_batch_delete",
            }
            if not self.tools_filter or self.tools_filter & batch_tools:
                await self.test_ticket_batch_operations()

            ticket_admin_tools = {
                "ticket_component_create",
                "ticket_component_list",
                "ticket_component_delete",
                "ticket_enum_create",
                "ticket_enum_list",
                "ticket_enum_delete",
            }
            if (
                not self.tools_filter
                or self.tools_filter & ticket_admin_tools
            ):
                await self.test_ticket_admin_operations()

            ticket_attachment_tools = {
                "ticket_attachment_put",
                "ticket_attachment_get",
                "ticket_attachment_list",
                "ticket_attachment_delete",
            }
            if (
                not self.tools_filter
                or self.tools_filter & ticket_attachment_tools
            ):
                await self.test_ticket_attachment_operations()

            wiki_attachment_tools = {
                "wiki_attachment_put",
                "wiki_attachment_get",
                "wiki_attachment_list",
                "wiki_attachment_delete",
            }
            if (
                not self.tools_filter
                or self.tools_filter & wiki_attachment_tools
            ):
                await self.test_wiki_attachment_operations()

            # Phase 4: Delete operations
            delete_tools = {
                "wiki_delete",
                "milestone_delete",
                "ticket_delete",
            }
            if (
                not self.tools_filter
                or self.tools_filter & delete_tools
            ):
                await self.test_delete_operations()

            # Phase 5: Error handling (tests multiple tools)
            if not self.tools_filter:
                await self.test_error_handling()

            # Cleanup
            cleanup_ok = await self.cleanup()

            return self.report.failed == 0 and cleanup_ok

        except Exception as e:
            self.logger.error(
                f"Test execution failed: {e}", exc_info=True
            )
            return False


def setup_logging(
    log_file: str | None, verbose: bool = False
) -> logging.Logger:
    """Set up logging"""
    logger = logging.getLogger("MCPTester")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if log_file:
        fh = logging.FileHandler(log_file)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s"
            )
        )
        logger.addHandler(fh)

    if verbose:
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(
            logging.Formatter("%(levelname)s - %(message)s")
        )
        logger.addHandler(ch)

    return logger


async def async_main(args):
    """Async main function - launches trac-mcp-server and connects via MCP protocol."""
    # Build debug log path
    log_dir = Path(".")
    if args.timestamp:
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"test_trac_debug_{timestamp_str}.log"
    else:
        log_filename = "test_trac_debug.log"
    log_path = log_dir / log_filename

    # Delete previous log (only when not using timestamp)
    if not args.timestamp and log_path.exists():
        log_path.unlink()

    logger = setup_logging(str(log_path), verbose=args.verbose)

    # Print header
    print(f"\n{'=' * 70}")
    print(f"{'Comprehensive MCP Tool Live Testing':^70}")
    print(f"{'v' + VERSION:^70}")
    print(f"{'trac-mcp-server ' + PACKAGE_VERSION:^70}")
    print(f"{'=' * 70}\n")
    print(f"Debug log: {log_path}")

    # Build server command with CLI arguments
    server_cmd = "trac-mcp-server"
    server_args = []
    if args.url:
        server_args.extend(["--url", args.url])
    if args.username:
        server_args.extend(["--username", args.username])
    if args.password:
        server_args.extend(["--password", args.password])
    if args.insecure:
        server_args.append("--insecure")
    if args.permissions_file:
        server_args.extend(
            ["--permissions-file", args.permissions_file]
        )

    server_params = StdioServerParameters(
        command=server_cmd,
        args=server_args,
    )

    try:
        async with stdio_client(server_params) as (
            read_stream,
            write_stream,
        ):
            async with ClientSession(
                read_stream, write_stream
            ) as session:
                init_result = await session.initialize()
                logger.info(
                    "MCP session initialized: server=%s version=%s",
                    init_result.serverInfo.name,
                    init_result.serverInfo.version,
                )
                print(
                    f"Connected to MCP server: {init_result.serverInfo.name} v{init_result.serverInfo.version}"
                )

                tester = ComprehensiveMCPTester(
                    session=session,
                    logger=logger,
                    verbose=args.verbose,
                    tools_filter=args.tools,
                )

                success = await tester.run_all_tests()

                # Generate report
                report_path = (
                    args.output
                    or f"./comprehensive-mcp-tool-test-{datetime.now().strftime('%Y-%m-%d')}.md"
                )
                tester.generate_report(report_path)

                # Print summary
                print(f"\n{'=' * 70}")
                print(f"{'SUMMARY':^70}")
                print(f"{'=' * 70}")
                print(
                    f"Total: {tester.report.total} | Passed: {tester.report.passed} | Failed: {tester.report.failed}"
                )
                if not success:
                    print(
                        "\nSome tests failed. Check the report for details."
                    )

                return 0 if success else 1

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"\n* Fatal error: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Comprehensive MCP Tool Live Testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # Run all tests (launches trac-mcp-server subprocess)
  %(prog)s --verbose                          # Run with verbose output
  %(prog)s --url http://trac.example.com      # Override Trac URL (passed to server)
  %(prog)s --tools ping ticket_get wiki_get   # Test only specific tools
  %(prog)s --permissions-file ro.permissions  # Test with restricted permissions
  %(prog)s --timestamp                        # Keep debug log with timestamp
  %(prog)s --output ./my-report.md            # Custom report location
        """,
    )

    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {VERSION}"
    )
    parser.add_argument(
        "--url", help="Override Trac URL (passed to trac-mcp-server)"
    )
    parser.add_argument(
        "--username",
        help="Override username (passed to trac-mcp-server)",
    )
    parser.add_argument(
        "--password",
        help="Override password (passed to trac-mcp-server)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip SSL verification (passed to trac-mcp-server)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose output"
    )
    parser.add_argument("--output", "-o", help="Output report path")
    parser.add_argument(
        "--tools",
        nargs="+",
        metavar="TOOL",
        default=None,
        help="Only test specific tools (space-separated names, e.g., --tools ticket_get wiki_get ping). "
        "Also filters Tool Catalog to show only these tools.",
    )
    parser.add_argument(
        "--permissions-file",
        default=None,
        help="Path to permissions file to pass to trac-mcp-server (restricts available tools). "
        "Format: one Trac permission per line (e.g., TICKET_VIEW), # for comments.",
    )
    parser.add_argument(
        "--timestamp",
        action="store_true",
        help="Include timestamp in debug log filename (prevents overwrite on next run)",
    )

    args = parser.parse_args()
    sys.exit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
