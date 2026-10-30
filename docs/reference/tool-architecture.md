# Tool Architecture

This document describes the internal code structure of the MCP tool system: how tools are defined, registered, dispatched, and filtered by permissions.

## Overview

The tool system has three layers:

```
CLI (--permissions-file)
        │
        ▼
┌─────────────────────────────────┐
│  ToolRegistry                   │  Filters specs by permissions,
│  (registry.py)                  │  dispatches call_tool() to handlers
└────────────┬────────────────────┘
             │ list of ToolSpec
             │
┌────────────┴────────────────────┐
│  Tool Modules                   │  Each exports *_SPECS list
│  (ticket_read.py, wiki_write.py │  with Tool definition + permissions
│   milestone.py, etc.)           │  + async handler
└────────────┬────────────────────┘
             │ async handler(client, args)
             │
┌────────────┴────────────────────┐
│  TracClient                     │  XML-RPC calls to Trac
│  (core/client.py)               │
└─────────────────────────────────┘
```

## Key Types

### ToolSpec

`src/trac_mcp_server/mcp/tools/registry.py`

Immutable dataclass that bundles three things:

```python
@dataclass(frozen=True, slots=True)
class ToolSpec:
    tool: types.Tool            # MCP tool definition (name, description, inputSchema)
    permissions: frozenset[str] # Required Trac permissions (empty = always available)
    handler: Callable[[TracClient, dict], Awaitable[CallToolResult]]
```

- `tool` -- the standard MCP SDK `Tool` object with name, description, and JSON Schema
- `permissions` -- set of Trac permission strings (e.g., `{"TICKET_VIEW"}`). Empty frozenset means the tool requires no permission and is always available (used for `ping`, `get_server_time`, `wiki_file_detect_format`)
- `handler` -- async function with standardized signature `(client, args) -> CallToolResult`

### ToolRegistry

`src/trac_mcp_server/mcp/tools/registry.py`

Constructed once at startup with all specs and two independent, combinable filters:

```python
registry = ToolRegistry(all_specs, allowed_permissions=None)  # all tools
registry = ToolRegistry(all_specs, allowed_permissions=frozenset({"TICKET_VIEW"}))  # filtered
registry = ToolRegistry(all_specs, read_only=True)  # only readOnlyHint=True tools
```

Filtering logic -- a spec is included only if it passes BOTH:
- **Permissions**: `allowed_permissions=None` includes all specs (default); otherwise a spec passes if its permissions are empty OR a subset of `allowed_permissions` (`--permissions-file`, see below)
- **Read-only**: `read_only=False` (default) includes all specs; `read_only=True` includes a spec only if its own `Tool.annotations.readOnlyHint` is `True` (`--read-only` / `TRAC_MCP_READ_ONLY`) -- a coarser, permission-agnostic alternative that needs no Trac-side permission enumeration, since it reuses the same `readOnlyHint` every spec already declares (enforced by a regression test, see below)

A tool excluded by either filter isn't just missing from `list_tools()` -- `call_tool()` raises the same "unknown tool" `ValueError` if called by name anyway, so there's no separate code path a filtered-out tool could slip through.

Provides two methods used by the MCP protocol handlers:
- `list_tools()` -- returns `list[types.Tool]` for the MCP `list_tools` response
- `call_tool(name, arguments, client)` -- dispatches to the matching handler with centralized error handling (XML-RPC faults, validation errors, unexpected exceptions)

## File Layout

```
src/trac_mcp_server/mcp/
├── server.py              # MCP protocol handlers, CLI, PING_SPEC
├── lifespan.py            # Server startup/shutdown, config loading
├── tools/
│   ├── __init__.py        # Aggregates ALL_SPECS from all modules
│   ├── registry.py        # ToolSpec, ToolRegistry, load_permissions_file
│   ├── errors.py          # Error response builders, XML-RPC fault translation
│   ├── constants.py       # Shared constants (batch size limits, etc.)
│   ├── system.py          # SYSTEM_SPECS   (1 tool:  get_server_time)
│   ├── ticket_read.py     # TICKET_READ_SPECS  (5 tools: search, get, changelog, fields, actions)
│   ├── ticket_write.py    # TICKET_WRITE_SPECS (3 tools: create, update, delete)
│   ├── ticket_batch.py    # TICKET_BATCH_SPECS (3 tools: batch_create, batch_delete, batch_update)
│   ├── wiki_read.py       # WIKI_READ_SPECS  (3 tools: get, search, recent_changes)
│   ├── wiki_write.py      # WIKI_WRITE_SPECS (3 tools: create, update, delete)
│   ├── wiki_file.py       # WIKI_FILE_SPECS  (3 tools: push, pull, detect_format)
│   └── milestone.py       # MILESTONE_SPECS  (5 tools: list, get, create, update, delete)
└── resources/
    └── wiki.py            # MCP resource handlers (wiki page URIs)
```

## Tool Module Structure

Every tool module follows the same pattern. Using `ticket_read.py` as an example:

### 1. Tool definitions (`*_TOOLS` list)

MCP `types.Tool` objects with name, description, and JSON Schema:

```python
TICKET_READ_TOOLS = [
    types.Tool(
        name="ticket_search",
        description="Search tickets with filtering...",
        inputSchema={
            "type": "object",
            "properties": { ... },
            "required": [],
        },
    ),
    # ... more tools
]
```

### 2. Handler functions

Each tool has an async handler with the standardized signature `(client: TracClient, args: dict) -> CallToolResult`:

```python
async def _handle_search(client: TracClient, args: dict) -> types.CallToolResult:
    query = args.get("query", "status!=closed")
    max_results = args.get("max_results", 10)
    # ... validate, call client, build response
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=response_text)],
        structuredContent=structured_data,
    )
```

### 3. ToolSpec list (`*_SPECS`)

Bundles tool definition + permissions + handler:

```python
TICKET_READ_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=TICKET_READ_TOOLS[0],   # ticket_search
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_search,
    ),
    ToolSpec(
        tool=TICKET_READ_TOOLS[1],   # ticket_get
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_get,
    ),
    # ...
]
```

### 4. Legacy dispatcher (backward compat)

Each module also has a `handle_*_tool()` dispatcher function from the pre-registry architecture. These are no longer used by `server.py` but are preserved for backward compatibility with tests and external consumers:

```python
async def handle_ticket_read_tool(name: str, arguments: dict | None, client: TracClient):
    args = arguments or {}
    match name:
        case "ticket_search": return await _handle_search(client, args)
        # ...
```

## Aggregation

`tools/__init__.py` collects specs from all modules:

```python
TICKET_SPECS = (
    TICKET_READ_SPECS + TICKET_WRITE_SPECS
    + TICKET_BATCH_SPECS + TICKET_ATTACHMENT_SPECS
)
WIKI_SPECS = (
    WIKI_READ_SPECS + WIKI_WRITE_SPECS
    + WIKI_FILE_SPECS + WIKI_ATTACHMENT_SPECS
)

ALL_SPECS: list[ToolSpec] = (
    SYSTEM_SPECS + TICKET_SPECS + WIKI_SPECS + MILESTONE_SPECS
    + TICKET_ADMIN_SPECS + INSTANCE_SPECS
)
```

`server.py` adds the `PING_SPEC` (defined in server.py itself), augments every
spec's schema with the optional `instance` parameter (`with_instance_param()`,
see [Multiple Instances](configuration.md#multiple-instances)), and constructs
the registry:

```python
declared_names = sorted(load_declared_instances())
all_specs = with_instance_param([PING_SPEC] + ALL_SPECS, declared_names)
registry = ToolRegistry(all_specs, allowed_permissions)
```

## Request Flow

```
MCP Client
    │
    │  call_tool("ticket_search", {"query": "status=new", "instance": "/bcs"})
    ▼
server.py::handle_call_tool(name, arguments)
    │  1. Pop "instance" out of a copy of arguments
    │  2. get_instances().get_client(instance) → InstanceRegistry.resolve()
    │     (default / declared name / same-host ad-hoc path or URL;
    │     UnknownInstanceError → structured "unknown_instance" response)
    │
    │  get_registry().call_tool(name, args, client)
    ▼
registry.py::ToolRegistry.call_tool()
    │  1. Look up ToolSpec by name
    │  2. Call spec.handler(client, args)
    │  3. Catch xmlrpc.Fault → translate_xmlrpc_error()
    │  4. Catch ValueError → build_error_response()
    │  5. Catch Exception → build_error_response()
    ▼
ticket_read.py::_handle_search(client, args)
    │
    │  await run_sync(client.query_tickets, query)
    ▼
TracClient (XML-RPC) → Trac server
```

## Permission Filtering

### At startup

The `--permissions-file` CLI flag specifies a text file listing allowed Trac permissions:

```
# read-only.permissions
TICKET_VIEW
WIKI_VIEW
MILESTONE_VIEW
```

The registry includes a tool only if:
- Its `permissions` frozenset is empty (always available), OR
- Its `permissions` are a subset of the allowed permissions

### Permission mapping

| Tool | Permissions |
|------|------------|
| `ping` | *(none -- always available)* |
| `get_server_time` | *(none -- always available)* |
| `ticket_search` | `TICKET_VIEW` |
| `ticket_get` | `TICKET_VIEW` |
| `ticket_changelog` | `TICKET_VIEW` |
| `ticket_fields` | `TICKET_VIEW` |
| `ticket_actions` | `TICKET_VIEW` |
| `ticket_create` | `TICKET_CREATE` |
| `ticket_update` | `TICKET_MODIFY` |
| `ticket_delete` | `TICKET_ADMIN` |
| `ticket_batch_create` | `TICKET_CREATE`, `TICKET_BATCH_MODIFY` |
| `ticket_batch_update` | `TICKET_MODIFY`, `TICKET_BATCH_MODIFY` |
| `ticket_batch_delete` | `TICKET_ADMIN`, `TICKET_BATCH_MODIFY` |
| `wiki_get` | `WIKI_VIEW` |
| `wiki_search` | `WIKI_VIEW` |
| `wiki_recent_changes` | `WIKI_VIEW` |
| `wiki_create` | `WIKI_CREATE` |
| `wiki_update` | `WIKI_MODIFY` |
| `wiki_delete` | `WIKI_DELETE` |
| `wiki_file_push` | `WIKI_CREATE`, `WIKI_MODIFY` |
| `wiki_file_pull` | `WIKI_VIEW` |
| `wiki_file_detect_format` | *(none -- always available)* |
| `milestone_list` | `MILESTONE_VIEW` |
| `milestone_get` | `MILESTONE_VIEW` |
| `milestone_create` | `MILESTONE_CREATE` |
| `milestone_update` | `MILESTONE_MODIFY` |
| `milestone_delete` | `MILESTONE_DELETE` |

### Example: read-only agent

With permissions file containing `TICKET_VIEW`, `WIKI_VIEW`, `MILESTONE_VIEW`:

**Included (14 tools):** ping, get_server_time, ticket_search, ticket_get, ticket_changelog, ticket_fields, ticket_actions, wiki_get, wiki_search, wiki_recent_changes, wiki_file_pull, wiki_file_detect_format, milestone_list, milestone_get

**Excluded (13 tools):** All create, update, delete, batch, and wiki_file_push tools

## Read-Only Filtering (`--read-only`)

`--read-only` (or `TRAC_MCP_READ_ONLY` / `config.yaml`'s `server.read_only`) produces the same *shape* of result as the permissions-file example above, without needing to enumerate Trac permissions by hand: it includes a tool only if its own `Tool.annotations.readOnlyHint` is `True`. Every registered `ToolSpec` sets this explicitly (checked by `TestAllToolsCarryReadOnlyHint` in `tests/test_mcp/tools/test_registry.py`), so the filter needs no separate maintained list.

It's independent of, and combinable with, `--permissions-file` -- a tool must pass both filters when both are configured. Prefer `--read-only` when the goal is simply "no write access, regardless of which Trac permissions the service account happens to have"; prefer `--permissions-file` when you need finer-grained control (e.g. tickets read-only but wiki read-write) or want the exposed tool list to mirror the service account's actual Trac permissions.

## Error Handling

All tool errors are handled centrally by `ToolRegistry.call_tool()`:

| Exception | Error Type | Example |
|-----------|-----------|---------|
| `xmlrpc.client.Fault` | Domain-specific (not_found, permission_denied, etc.) | Ticket #999 not found |
| `ValueError` | `validation_error` | ticket_id is required |
| `Exception` | `server_error` | Connection refused |

Error responses follow a consistent structure:

```python
CallToolResult(
    content=[TextContent(text="Error (error_type): message\n\nAction: corrective action")],
    isError=True,
)
```

The error domain (ticket, wiki, milestone) is derived from the tool name prefix for context-appropriate corrective action messages. See [Error Handling](error-handling.md) for the full error taxonomy.

## Adding a New Tool

1. **Define the tool** in the appropriate module (e.g., `ticket_read.py`):
   - Add a `types.Tool` to the `*_TOOLS` list
   - Write the handler: `async def _handle_new_tool(client: TracClient, args: dict) -> CallToolResult`
   - Add a `ToolSpec` to the `*_SPECS` list with the correct permissions

2. **No changes needed** in `__init__.py`, `server.py`, or `registry.py` -- the aggregation picks up new specs automatically.

3. **Write tests** in the corresponding test module.

---

[Back to Reference Overview](overview.md)
