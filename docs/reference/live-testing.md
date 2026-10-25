# Live Testing Guide

## Overview

Two scripts provide live testing of `trac-mcp-server` against a running Trac instance. Both communicate via **MCP stdio protocol** -- the same transport that Claude Desktop, Claude Code, and other MCP clients use -- by launching `trac-mcp-server` as a subprocess.

| Script | Purpose | Scope |
|--------|---------|-------|
| `scripts/test_trac.py` | Comprehensive tool testing with report generation | Every registered tool: calls, responses, error handling (see "Tools Tested: N/M" in the generated report -- M is fetched live from `list_tools()`, not hardcoded) |
| `scripts/agent_scenarios.py` | Permission-based agent persona testing | Tool exposure per permission set, reference comparison |

## Prerequisites

- A running Trac instance with XML-RPC plugin enabled
- `trac-mcp-server` package installed (`pip install -e .`)
- For `test_trac.py`: credentials with **full permissions** (creates/deletes resources)
- For `agent_scenarios.py`: any valid credentials (read-only by default)

Connection is configured via CLI flags passed through to the server subprocess:

```bash
--url http://trac.example.com --username admin --password secret
```

The server also reads `TRAC_URL`, `TRAC_USERNAME`, `TRAC_PASSWORD` environment variables and `.trac_mcp/config.yaml` when CLI flags are not provided.

---

## test_trac.py -- Comprehensive Tool Testing

End-to-end test harness (v8.0.0) that validates the Trac MCP server's tools against a live Trac instance. Exercises the full tool lifecycle from an LLM/agent perspective:

1. **Tool Presentation** -- what the LLM sees when tools are listed (name, description, inputSchema)
2. **Tool Call** -- the exact arguments sent to each tool
3. **Tool Return** -- the full `CallToolResult` including text content, `structuredContent`, and `isError`

The generated Markdown report serves as a **reference output** for verifying the tool surface during development and before releases.

### Quick Start

```bash
# Run all tool tests (launches trac-mcp-server subprocess)
python scripts/test_trac.py

# Override connection details
python scripts/test_trac.py --url http://trac.example.com --username admin --password secret

# Test only specific tools
python scripts/test_trac.py --tools ping ticket_get wiki_get

# Test with restricted permissions (passed through to server)
python scripts/test_trac.py --permissions-file scripts/scenarios/readonly.permissions

# Verbose output with custom report path
python scripts/test_trac.py --verbose --output ./test-report.md

# Keep debug log with timestamp (won't be overwritten next run)
python scripts/test_trac.py --timestamp
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--url URL` | Trac URL (passed to server subprocess) |
| `--username USER` | Trac username (passed to server subprocess) |
| `--password PASS` | Trac password (passed to server subprocess) |
| `--insecure` | Skip SSL certificate verification |
| `--tools TOOL [TOOL ...]` | Test only specific tools (also filters Tool Catalog) |
| `--permissions-file PATH` | Pass permissions file to server (restricts available tools) |
| `-v, --verbose` | Verbose console output (shows notes for each test) |
| `-o, --output PATH` | Output report path (default: `./comprehensive-mcp-tool-test-YYYY-MM-DD.md`) |
| `--timestamp` | Include timestamp in debug log filename (prevents overwrite) |
| `--version` | Show script version and exit |

### Debug Logging

A debug log is always created:

- **Default**: `./test_trac_debug.log` -- previous copy deleted at startup
- **With `--timestamp`**: `./test_trac_debug_20260218_143000.log` -- preserved across runs

### Test Phases

Tests run in a deliberate order that manages resource dependencies:

| Phase | Description | Tools Tested |
|-------|-------------|--------------|
| 1 | Connectivity | `ping` |
| 1b | System tools | `get_server_time` |
| 2a | Ticket reads | `ticket_search`, `ticket_get`, `ticket_changelog`, `ticket_actions`, `ticket_fields` |
| 2b | Wiki reads | `wiki_get`, `wiki_get_history`, `wiki_diff`, `wiki_search`, `wiki_recent_changes` |
| 2c | Milestone reads | `milestone_list`, `milestone_get` |
| 3a | Ticket writes | `ticket_create`, `ticket_update` |
| 3b | Wiki writes | `wiki_create`, `wiki_update` |
| 3c | Milestone writes | `milestone_create`, `milestone_update` |
| 3d | Wiki file ops | `wiki_file_detect_format`, `wiki_file_push`, `wiki_file_pull` |
| 3f | Batch tickets | `ticket_batch_create`, `ticket_batch_update`, `ticket_batch_delete` |
| 3g | Ticket admin | `ticket_component_create`, `ticket_component_list`, `ticket_component_delete`, `ticket_enum_create`, `ticket_enum_list`, `ticket_enum_delete` |
| 3h | Ticket attachments | `ticket_attachment_put`, `ticket_attachment_list`, `ticket_attachment_get`, `ticket_attachment_delete` |
| 3i | Wiki attachments | `wiki_attachment_put`, `wiki_attachment_list`, `wiki_attachment_get`, `wiki_attachment_delete` |
| 4 | Delete ops | `wiki_delete`, `milestone_delete`, `ticket_delete` |
| 5 | Error handling | Non-existent resources, missing required fields, empty lists |

Write phases (3a-3i) create temporary resources cleaned up in phase 4 (or immediately, for attachment/admin resources that don't depend on phase-4 deletes). The `--tools` flag skips phases that don't contain any of the selected tools. `ticket_attachment_*` (phase 3h) requires the test ticket from phase 3a; `wiki_attachment_*` (phase 3i) requires the test wiki page from phase 3b -- both phases no-op with a note if run standalone via `--tools` without their dependency.

Not yet covered: `list_instances` (multi-instance support, Trac #18) once merged.

### Report Structure

The generated Markdown report contains:

**Executive Summary** -- Pass/fail counts, tools tested, and pass rate.

**Tool Catalog (LLM Tool Presentation)** -- All registered tools with their exact `name`, `description`, and full `inputSchema` JSON -- the same data an LLM receives from `list_tools()`. When `--tools` is used, only selected tools appear.

**Per-Test Results** -- Each entry shows:

- **PASS/FAIL** status and notes
- **Call args** -- exact JSON arguments sent to the tool
- **structuredContent** -- the structured JSON from `CallToolResult` (if present)
- **isError** -- the error flag from `CallToolResult` (if set)
- **Text content preview** -- the text response the LLM would read (first 500 chars)

Results are grouped by category: Connectivity, System Tools, Ticket Tools, Batch Ticket Tools, Ticket Admin Tools, Ticket Attachment Tools, Wiki Tools, Wiki File Tools, Wiki Attachment Tools, Milestone Tools, Error Handling.

**Issues Found** -- Lists all failed tests with error details.

### What Gets Created and Cleaned Up

| Resource | Naming Pattern | Created In | Cleaned Up In |
|----------|---------------|------------|---------------|
| Ticket | `[MCP TEST <timestamp>] ...` | Phase 3a | Phase 4 |
| Wiki page | `MCPTest_<timestamp>` | Phase 3b | Phase 4 |
| Wiki page (file test) | `MCPFileTest_<timestamp>` | Phase 3d | Phase 3d |
| Milestone | `MCP-Test-<timestamp>` | Phase 3c | Phase 4 |
| Batch tickets | `[MCP BATCH <timestamp>] ...` | Phase 3f | Phase 3f |
| Ticket component | `MCPComponent_<timestamp>` | Phase 3g | Phase 3g |
| Enum value (`severity`) | `MCPSeverity<timestamp>` | Phase 3g | Phase 3g |
| Ticket attachment | `mcp_test_<timestamp>.txt` (on phase 3a's ticket) | Phase 3h | Phase 3h |
| Wiki attachment | `mcp_test_<timestamp>.txt` (on phase 3b's page) | Phase 3i | Phase 3i |

Any of the above left over from a failed mid-run (component, enum value, ticket/wiki attachment) are also swept up in the final `cleanup()` pass, same as tickets/pages/milestones.

### Batch Test Configuration

The `BATCH_TEST_SIZE` constant (default: 10) controls how many tickets are created in batch operation tests. Keep it small for routine testing; increase for load/stress testing.

---

## agent_scenarios.py -- Permission Scenario Testing

Agent scenario test runner (v1.0.0) that verifies permission-based tool filtering across different agent personas. For each scenario, it launches `trac-mcp-server` with a specific permissions file, calls `list_tools()`, and compares the result against a stored reference.

### Quick Start

```bash
# Run all 4 scenarios (readonly, ticket_manager, wiki_editor, full_access)
python scripts/agent_scenarios.py

# Run a specific scenario
python scripts/agent_scenarios.py --scenarios readonly

# Also run safe read-only tool calls to verify connectivity
python scripts/agent_scenarios.py --live --verbose

# Update reference files from live server output
python scripts/agent_scenarios.py --update-refs

# Override connection details
python scripts/agent_scenarios.py --url http://trac.example.com --username admin --password secret
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--url URL` | Trac URL (passed to server subprocess) |
| `--username USER` | Trac username (passed to server subprocess) |
| `--password PASS` | Trac password (passed to server subprocess) |
| `--insecure` | Skip SSL certificate verification |
| `--scenarios NAME [NAME ...]` | Run only these scenarios (default: all discovered) |
| `--update-refs` | Overwrite `.expected_tools.txt` files with live output |
| `--live` | Also run safe read-only tool calls (not just `list_tools` comparison) |
| `-v, --verbose` | Verbose console output (per-tool call results) |
| `--timestamp` | Include timestamp in debug log filename |
| `--version` | Show script version and exit |

### Debug Logging

A debug log is always created:

- **Default**: `./agent_scenarios_debug.log` -- previous copy deleted at startup
- **With `--timestamp`**: `./agent_scenarios_debug_20260218_143000.log` -- preserved across runs

### Scenario Definitions

Scenarios live in `scripts/scenarios/`. Each scenario is a pair of files:

- `{name}.permissions` -- Trac permissions for the agent persona (one per line, `#` comments)
- `{name}.expected_tools.txt` -- Expected tool names (one per line, sorted alphabetically)

Adding a new scenario requires no code changes -- just add the file pair.

#### Built-in Scenarios

| Scenario | Permissions | Expected Tools | Description |
|----------|-------------|:--------------:|-------------|
| `readonly` | TICKET_VIEW, WIKI_VIEW, MILESTONE_VIEW | 21 | Read-only agent, no modifications |
| `ticket_manager` | 10 perms (all ticket + all milestone) | 29 | Ticket/milestone management, no wiki |
| `wiki_editor` | WIKI_VIEW, WIKI_CREATE, WIKI_MODIFY | 14 | Wiki editing, no tickets or milestones |
| `full_access` | All 14 permissions | 42 | Unrestricted access to all tools |

System tools (`ping`, `get_server_time`, `wiki_file_detect_format`) require no permissions and are always available. Tool counts above are pinned to the reference `.expected_tools.txt` files under `scripts/scenarios/` -- regenerate them with `--update-refs` (see below) after adding, removing, or re-permissioning a tool; `agent_scenarios.py` fails loudly on drift, so a stale table here is just documentation lag, not a live risk.

#### Example Permission File

```
# Read-only agent persona
# Can view tickets, wiki pages, and milestones but cannot modify anything.
TICKET_VIEW
WIKI_VIEW
MILESTONE_VIEW
```

#### Example Expected Tools File

```
get_server_time
milestone_get
milestone_list
ping
ticket_actions
ticket_changelog
ticket_fields
ticket_get
ticket_search
wiki_file_detect_format
wiki_file_pull
wiki_get
wiki_recent_changes
wiki_search
```

### How Comparison Works

1. Launches `trac-mcp-server --permissions-file {name}.permissions` as subprocess
2. Connects via MCP stdio protocol (`stdio_client` + `ClientSession`)
3. Calls `session.list_tools()` to get actual tools
4. Sorts tool names alphabetically
5. Loads expected tools from `{name}.expected_tools.txt`
6. Set comparison: **PASS** if equal, **FAIL** with diff (extra/missing tools)

### Live Mode (`--live`)

When `--live` is enabled, after the tool list comparison, the runner also executes safe read-only tool calls against the Trac instance. Only tools in the `SAFE_CALLS` dict are called:

| Tool | Arguments | Notes |
|------|-----------|-------|
| `ping` | `{}` | Server connectivity check |
| `get_server_time` | `{}` | Returns server timestamp |
| `ticket_search` | `{}` | Default search (returns up to 10 tickets) |
| `ticket_fields` | `{}` | Lists available ticket fields |
| `wiki_search` | `{"query": "wiki"}` | Searches wiki for "wiki" |
| `wiki_recent_changes` | `{"since_days": 7}` | Wiki changes in last 7 days |
| `milestone_list` | `{}` | Lists all milestones |
| `wiki_file_detect_format` | `{"file_path": "/dev/null"}` | Format detection probe |

Tools not in this table are skipped in live mode (they require write operations or specific entity IDs).

### Reference Update Mode (`--update-refs`)

When `--update-refs` is used, the runner overwrites the `.expected_tools.txt` files with the actual `list_tools()` output from the live server. Use this to:

- Bootstrap reference files after adding new tools
- Update references after permission mapping changes
- Re-baseline after ToolSpec modifications

### Creating Custom Scenarios

1. Create `scripts/scenarios/{name}.permissions` with required Trac permissions
2. Run `python scripts/agent_scenarios.py --scenarios {name} --update-refs` to generate the expected tools file
3. Review `scripts/scenarios/{name}.expected_tools.txt` to verify correctness
4. Run `python scripts/agent_scenarios.py --scenarios {name}` to confirm pass

---

## Exit Codes

Both scripts use the same exit code convention:

| Code | Meaning |
|------|---------|
| 0 | All tests/scenarios passed |
| 1 | One or more tests/scenarios failed, or a fatal error occurred |

## Example Workflow

```bash
# 1. Run scenario tests first (fast, no side effects)
python scripts/agent_scenarios.py --live --verbose

# 2. If all scenarios pass, run comprehensive tool tests
python scripts/test_trac.py -v -o ./test-report.md

# 3. For a subset check during development
python scripts/test_trac.py --tools ping ticket_search ticket_get --verbose

# 4. Test a specific permission profile
python scripts/test_trac.py --permissions-file scripts/scenarios/readonly.permissions --verbose

# 5. Compare reports across versions
diff prev-report.md test-report.md
```

---

[Back to Reference Overview](overview.md)
