# Architecture

**Analysis Date:** 2026-02-14

## Pattern Overview

**Overall:** Modular layered architecture with MCP protocol adapter pattern

**Key Characteristics:**
- Standalone MCP (Model Context Protocol) server that bridges AI agents to a Trac project management system via XML-RPC
- Layered design: MCP protocol layer -> tool handlers -> core client -> Trac XML-RPC
- Synchronous XML-RPC calls wrapped in async handlers via `asyncio.to_thread()` for non-blocking MCP operation
- Domain-organized tool modules (tickets, wiki, milestones, sync) with consistent handler patterns
- Format conversion subsystem (Markdown <-> TracWiki) integrated transparently at the tool handler layer
- Bidirectional document sync engine using Unison-style archive-based reconciliation

## Layers

**Protocol Layer (MCP Server):**
- Purpose: Implement MCP protocol endpoints (list_tools, call_tool, list_resources, read_resource)
- Location: `src/trac_mcp_server/mcp/server.py`
- Contains: Server initialization, tool/resource registration, request routing, CLI argument parsing
- Depends on: Tool handlers, resource handlers, lifespan manager, core client
- Used by: MCP clients (Claude Desktop, Claude Code) via stdio JSON-RPC transport
- Pattern: Single `Server` instance with decorator-registered handlers. Tool dispatch uses name-prefix routing (`ticket_*`, `wiki_*`, `milestone_*`, `doc_sync*`).

**Lifespan Layer:**
- Purpose: Server startup/shutdown lifecycle -- config loading, connection validation, fail-fast on errors
- Location: `src/trac_mcp_server/mcp/lifespan.py`
- Contains: Async context manager that creates and validates `TracClient` on startup
- Depends on: Config module, core client
- Used by: `server.py` main() function

**Tool Handler Layer:**
- Purpose: Implement MCP tool logic -- argument validation, TracClient calls, response formatting
- Location: `src/trac_mcp_server/mcp/tools/`
- Contains: Domain-specific tool modules with consistent patterns
- Depends on: Core client (via `run_sync`), converters, error builder
- Used by: Protocol layer (server.py `handle_call_tool`)
- Key files:
  - `src/trac_mcp_server/mcp/tools/__init__.py` -- barrel exports, combines tool lists
  - `src/trac_mcp_server/mcp/tools/ticket_read.py` -- search, get, changelog, fields, actions (read-only)
  - `src/trac_mcp_server/mcp/tools/ticket_write.py` -- create, update, delete (write)
  - `src/trac_mcp_server/mcp/tools/wiki_read.py` -- get, search, recent_changes (read-only)
  - `src/trac_mcp_server/mcp/tools/wiki_write.py` -- create, update, delete (write)
  - `src/trac_mcp_server/mcp/tools/wiki_file.py` -- file push/pull, format detection
  - `src/trac_mcp_server/mcp/tools/milestone.py` -- list, get, create, update, delete
  - `src/trac_mcp_server/mcp/tools/sync.py` -- doc_sync, doc_sync_status
  - `src/trac_mcp_server/mcp/tools/system.py` -- get_server_time
  - `src/trac_mcp_server/mcp/tools/errors.py` -- structured error response builder

**Resource Layer:**
- Purpose: Expose Trac wiki pages as MCP resources via URI templates
- Location: `src/trac_mcp_server/mcp/resources/`
- Contains: Wiki resource handlers (list, read by URI)
- Depends on: Core client, converters
- Used by: Protocol layer (server.py `handle_read_resource`)
- Key files:
  - `src/trac_mcp_server/mcp/resources/wiki.py` -- `trac://wiki/{page_name}` and `trac://wiki/_index`

**Core Layer:**
- Purpose: Trac XML-RPC communication -- all Trac API calls go through this layer
- Location: `src/trac_mcp_server/core/`
- Contains: TracClient (HTTP session, XML-RPC serialization/deserialization, all Trac operations), async utilities
- Depends on: Config, validators
- Used by: Tool handlers, resource handlers, sync engine
- Key files:
  - `src/trac_mcp_server/core/client.py` -- `TracClient` class with all XML-RPC methods (646 lines)
  - `src/trac_mcp_server/core/async_utils.py` -- `run_sync()`, `run_sync_limited()`, `gather_limited()`, semaphore management

**Converter Layer:**
- Purpose: Bidirectional format conversion between Markdown and TracWiki markup
- Location: `src/trac_mcp_server/converters/`
- Contains: Two directional converters plus shared utilities and auto-detection
- Depends on: `mistune` (for Markdown parsing)
- Used by: Tool handlers (transparent conversion on read/write)
- Key files:
  - `src/trac_mcp_server/converters/markdown_to_tracwiki.py` -- Markdown AST -> TracWiki via mistune renderer
  - `src/trac_mcp_server/converters/tracwiki_to_markdown.py` -- TracWiki -> Markdown via regex-based parser
  - `src/trac_mcp_server/converters/common.py` -- `ConversionResult` dataclass, `detect_format_heuristic()`, `auto_convert()`, language mappings

**Sync Engine:**
- Purpose: Bidirectional document synchronization between local files and Trac wiki pages
- Location: `src/trac_mcp_server/sync/`
- Contains: Full sync pipeline (state, mapping, reconciliation, merge, conflict resolution, reporting)
- Depends on: Core client, converters, config schema, `merge3` library
- Used by: Sync tool handler (`mcp/tools/sync.py`)
- Key files:
  - `src/trac_mcp_server/sync/engine.py` -- `SyncEngine` orchestrator (834 lines, largest file)
  - `src/trac_mcp_server/sync/state.py` -- JSON state persistence with atomic writes
  - `src/trac_mcp_server/sync/mapper.py` -- `PathMapper` config-driven local path <-> wiki page mapping
  - `src/trac_mcp_server/sync/merger.py` -- Three-way merge via `merge3` library
  - `src/trac_mcp_server/sync/resolver.py` -- Conflict resolution strategies (interactive, unattended, local-wins, remote-wins)
  - `src/trac_mcp_server/sync/models.py` -- Pydantic models (`SyncAction`, `SyncEntry`, `ConflictInfo`, `SyncResult`, `SyncReport`)
  - `src/trac_mcp_server/sync/reporter.py` -- Human-readable and JSON report formatting

**Detection Layer:**
- Purpose: Detect Trac server capabilities (version, processors, markdown support)
- Location: `src/trac_mcp_server/detection/`
- Contains: Capability detection via XML-RPC, web scraping, and probe testing
- Depends on: Core client, `lxml`, `requests`
- Used by: Converter auto_convert (to decide target format)
- Key files:
  - `src/trac_mcp_server/detection/capabilities.py` -- `CapabilityDetector` with caching
  - `src/trac_mcp_server/detection/web_scraper.py` -- `/about` page scraper
  - `src/trac_mcp_server/detection/processor_utils.py` -- Wiki processor availability probing

**Configuration Layer:**
- Purpose: Configuration loading and validation
- Location: Root of `src/trac_mcp_server/`
- Contains: Two config systems -- simple env-based and hierarchical YAML-based
- Key files:
  - `src/trac_mcp_server/config.py` -- Simple `Config` dataclass, `load_config()` from env vars + CLI args (primary)
  - `src/trac_mcp_server/config_loader.py` -- Hierarchical YAML config with `!include`, env var interpolation (used by sync)
  - `src/trac_mcp_server/config_schema.py` -- Pydantic `UnifiedConfig` with sync profile models (used by sync)

**Utility Layer:**
- Purpose: Shared utilities for validation, file handling, logging, version checking
- Location: Root of `src/trac_mcp_server/`
- Key files:
  - `src/trac_mcp_server/validators.py` -- Page name and content validation functions
  - `src/trac_mcp_server/file_handler.py` -- Path validation, encoding-aware read/write, format detection
  - `src/trac_mcp_server/logger.py` -- Logging setup (MCP file-only vs CLI stderr), JSON formatter
  - `src/trac_mcp_server/version.py` -- Version consistency checking (runtime vs pyproject.toml)

## Data Flow

**MCP Tool Call Request Lifecycle:**

1. MCP client sends JSON-RPC `tools/call` request via stdio
2. `mcp.server.stdio` transport deserializes request
3. `server.py:handle_call_tool()` receives tool name + arguments
4. Router dispatches by name prefix to domain handler (e.g., `ticket_*` -> `handle_ticket_read_tool`)
5. Domain handler validates arguments, calls `TracClient` methods via `await run_sync(client.method, args)`
6. `run_sync()` offloads synchronous XML-RPC call to thread pool via `asyncio.to_thread()`
7. `TracClient._rpc_request()` serializes XML-RPC payload, sends HTTP POST, parses XML response
8. No conversion step: the inline ticket and wiki tools move TracWiki bytes unchanged in both directions (ticket #69)
9. Handler builds `list[TextContent]` or `CallToolResult` with text + structured JSON
10. Response sent back to MCP client via stdio transport

**MCP Resource Read Lifecycle:**

1. MCP client sends `resources/read` with URI (e.g., `trac://wiki/WikiStart`)
2. `server.py:handle_read_resource()` validates URI scheme (`trac://`)
3. Routes by host portion (`wiki` -> `handle_read_wiki_resource`)
4. `wiki.py` parses page name, query params (format, version)
5. Fetches content + metadata in parallel via `asyncio.gather()` + `run_sync_limited()`
6. Converts to Markdown (default) or returns raw TracWiki
7. Returns formatted string with metadata header

**Sync Engine Flow:**

1. `doc_sync` tool handler loads hierarchical config, creates `SyncEngine`
2. Engine loads persisted state from JSON file
3. `PathMapper` discovers local/remote pairs from config mappings
4. Engine gathers current content hashes on both sides
5. `reconciler.reconcile()` determines action for each pair (push/pull/conflict/skip/create/delete)
6. Actions filtered by configured direction (push-only, pull-only, bidirectional)
7. Conflicts resolved via configured strategy (local-wins, remote-wins, markers, interactive merge)
8. Actions executed: push converts MD->TracWiki, pull converts TracWiki->MD
9. State updated per-entry with atomic writes
10. `SyncReport` built and formatted for tool response

**State Management:**
- Global `_trac_client` module variable in `server.py`, set by `main()` after lifespan yields
- No persistent application state beyond sync state files (`.trac_assist/sync_*.json`)
- TracClient uses thread-local `requests.Session` for thread safety with `asyncio.to_thread()`
- Concurrency bounded by `asyncio.Semaphore` (configurable `TRAC_MAX_PARALLEL_REQUESTS`, default 5)

## Key Abstractions

**TracClient:**
- Purpose: Single point of contact for all Trac XML-RPC operations
- Location: `src/trac_mcp_server/core/client.py`
- Pattern: Fat client with methods for every Trac operation (tickets, wiki, milestones)
- Thread safety: Thread-local sessions, stateless XML-RPC calls

**Tool Handler Pattern:**
- Purpose: Consistent structure for all MCP tool implementations
- Examples: `src/trac_mcp_server/mcp/tools/ticket_read.py`, `src/trac_mcp_server/mcp/tools/wiki_write.py`
- Pattern: Each module exports `TOOLS` list (tool definitions) and `handle_*_tool()` dispatcher function. Internal `_handle_*()` functions implement individual tools. All catch XML-RPC faults and translate to structured error responses via `build_error_response()`.

**ConversionResult:**
- Purpose: Uniform return type for all format conversions
- Location: `src/trac_mcp_server/converters/common.py`
- Pattern: Dataclass with `text`, `source_format`, `target_format`, `converted` flag, `warnings` list

**SyncEngine + Models:**
- Purpose: Orchestrate bidirectional document sync with full conflict resolution
- Location: `src/trac_mcp_server/sync/`
- Pattern: Pipeline pattern -- state -> discover -> reconcile -> resolve -> execute -> report. Pydantic models for data contracts. Pure function reconciler. Protocol-based conflict resolvers.

**Structured Error Responses:**
- Purpose: Machine-actionable error messages for AI agents with corrective actions
- Location: `src/trac_mcp_server/mcp/tools/errors.py`
- Pattern: `build_error_response(error_type, message, corrective_action)` returns `list[TextContent]` with formatted error + recovery hint

## Entry Points

**Primary CLI Entry Point:**
- Location: `src/trac_mcp_server/mcp/server.py:run()`
- Registered as: `trac-mcp-server` console script in `pyproject.toml`
- Triggers: `trac-mcp-server` CLI command or `python -m trac_mcp_server.mcp`
- Responsibilities: Parse CLI args, call `asyncio.run(main())` with config overrides

**Package Entry Point:**
- Location: `src/trac_mcp_server/mcp/__main__.py`
- Triggers: `python -m trac_mcp_server.mcp`
- Responsibilities: Import and call `run()` from `server.py`

**Async Main:**
- Location: `src/trac_mcp_server/mcp/server.py:main()`
- Triggers: Called by `run()` via `asyncio.run()`
- Responsibilities: Setup logging, version check, lifespan context (config + connection validation), stdio transport start, server run loop

## Error Handling

**Strategy:** Layered error handling with domain-specific translation at each level

**Patterns:**
- **XML-RPC Fault Translation:** Each tool module has `_translate_xmlrpc_error()` that maps `xmlrpc.client.Fault` to structured error responses with categories: `not_found`, `permission_denied`, `version_conflict`, `server_error`, `already_exists`
- **Validation Errors:** `ValueError` caught and returned as `validation_error` type with corrective action hint
- **Catch-All:** Generic `Exception` caught at tool handler level, returned as `server_error` with "retry later" guidance
- **Fail-Fast Startup:** Lifespan manager raises `RuntimeError` if config is invalid or Trac connection fails, with user-facing messages to stderr
- **Per-File Sync Errors:** Sync engine handles errors per-path -- a single file failure does not abort the entire sync run
- **Optimistic Locking:** Wiki update and ticket update use version-based optimistic locking. Version conflicts return structured errors with instructions to re-fetch current version.

## Cross-Cutting Concerns

**Logging:**
- Module: `src/trac_mcp_server/logger.py`
- MCP mode: File-only logging (never stdout, which is reserved for JSON-RPC). Default: `/tmp/trac-mcp-server.log`
- CLI mode: stderr logging with optional file handler
- JSON formatter available for structured debug output
- Third-party library logs silenced unless DEBUG level

**Validation:**
- Input validation: `src/trac_mcp_server/validators.py` for page names and content
- Path validation: `src/trac_mcp_server/file_handler.py` for file system paths
- Config validation: `src/trac_mcp_server/config.py:validate_config()` for URL format, non-empty credentials
- Schema validation: Pydantic models in `config_schema.py` and `sync/models.py`

**Authentication:**
- HTTP Basic Auth via `requests.Session` with username/password from config
- Credentials sourced from: CLI args > environment variables > .env file
- Thread-local sessions in TracClient for thread safety

**Concurrency:**
- Async/sync bridge via `asyncio.to_thread()` in `core/async_utils.py`
- Bounded concurrency via `asyncio.Semaphore` (configurable, default 5)
- `run_sync()` for single calls, `run_sync_limited()` + `gather_limited()` for parallel bounded calls
- Thread-local `requests.Session` instances in TracClient

**Format Conversion:**
- **Not at the inline tool boundaries.** `ticket_*`, `wiki_get`/`wiki_search`, `wiki_create`/`wiki_update` and `milestone_*` store and return TracWiki bytes unchanged; there is no `format` or `raw` parameter to select otherwise (ticket #69)
- The converters remain, reached from three places: the standalone `trac-convert` binary, the `convert_preview` tool, and `wiki_file_push`/`wiki_file_pull`
- The file tools still convert because they have a *filename* to go on; source-format auto-detection by content heuristic lives there and in `auto_convert`, not in an inline write path

---

*Architecture analysis: 2026-02-14*
