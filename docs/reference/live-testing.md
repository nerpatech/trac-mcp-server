# Live Testing Guide

## Overview

Live testing of `trac-mcp-server` against a running Trac instance has three parts:

| Part | Purpose | Scope |
|------|---------|-------|
| `scripts/agent_scenarios.py` | Permission-based agent persona testing | Tool exposure per permission set, reference comparison |
| `scripts/daemon_smoke.py` | Smoke checks of a deployed HTTP daemon | Run through `ci-live.sh --daemon` |
| `pytest --run-live` | The `@pytest.mark.live` rows of the test suite | Rows that write use `/trac_test`, with per-run names and cleanup in `finally` |

`agent_scenarios.py` communicates via **MCP stdio protocol** -- the same transport that Claude Desktop, Claude Code, and other MCP clients use -- by launching `trac-mcp-server` as a subprocess.

## Prerequisites

- A running Trac instance with XML-RPC plugin enabled
- `trac-mcp-server` package installed (`pip install -e .`)
- For `agent_scenarios.py`: any valid credentials (read-only by default)

`agent_scenarios.py` takes `--url`, `--username` and `--insecure` and passes them to the server subprocess as CLI flags:

```bash
--url http://trac.example.com --username admin
```

It has no `--password` option (removed in v2.0.0), so a password never appears on a command line: export `TRAC_PASSWORD`.

Exactly these connection variables exported in the calling shell are passed through to the server subprocess: `TRAC_URL`, `TRAC_USERNAME`, `TRAC_PASSWORD`, `TRAC_INSECURE` and `TRAC_RPC_TIMEOUT`. No other variable is passed, in particular no `TRAC_MCP_*` server-mode setting (`TRAC_MCP_TRANSPORT`, `TRAC_MCP_READ_ONLY`, `TRAC_MCP_DISABLED_TOOLS`, ...): with one of those the subprocess would serve HTTP instead of stdio or offer fewer tools than a scenario expects, and `--update-refs` would write that list into the reference files.

An exported variable beats the same setting in `.env`: the server's `load_dotenv()` does not override variables that are already set, so a stale exported `TRAC_URL` wins over the `.env` file. Unset it (or export the right value) before a run. For anything not given by a flag, a variable or `.env`, the server also reads `.trac_mcp/config.yaml`.

---

## agent_scenarios.py -- Permission Scenario Testing

Agent scenario test runner (v2.0.0) that verifies permission-based tool filtering across different agent personas. For each scenario, it launches `trac-mcp-server` with a specific permissions file, calls `list_tools()`, and compares the result against a stored reference.

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

# Override connection details (the password comes from TRAC_PASSWORD)
export TRAC_PASSWORD=...
python scripts/agent_scenarios.py --url http://trac.example.com --username admin
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--url URL` | Trac URL (passed to server subprocess) |
| `--username USER` | Trac username (passed to server subprocess) |
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
| `readonly` | TICKET_VIEW, WIKI_VIEW, MILESTONE_VIEW | 29 | Read-only agent, no modifications |
| `ticket_manager` | 10 perms (all ticket + all milestone) | 35 | Ticket/milestone management, no wiki |
| `wiki_editor` | WIKI_VIEW, WIKI_CREATE, WIKI_MODIFY | 18 | Wiki editing, no tickets or milestones |
| `full_access` | All 14 permissions | 52 | Unrestricted access to all tools |

System tools (`ping`, `get_server_time`, `list_instances`, `wiki_file_detect_format`) require no permissions and are always available. Tool counts above are pinned to the reference `.expected_tools.txt` files under `scripts/scenarios/` -- regenerate them with `--update-refs` (see below) after adding, removing, or re-permissioning a tool; `agent_scenarios.py` fails loudly on drift, so a stale table here is just documentation lag, not a live risk.

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

`agent_scenarios.py` uses this exit code convention:

| Code | Meaning |
|------|---------|
| 0 | All tests/scenarios passed |
| 1 | One or more tests/scenarios failed, or a fatal error occurred |

## Example Workflow

```bash
# 1. Run scenario tests first (fast, no side effects)
python scripts/agent_scenarios.py --live --verbose

# 2. Run the live pytest rows (rows that write use /trac_test)
pytest --run-live
```

---

[Back to Reference Overview](overview.md)
