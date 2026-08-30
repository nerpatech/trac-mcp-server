# Codebase Structure

**Analysis Date:** 2026-02-14

## Directory Layout

```
trac_mcp_server/
├── src/
│   └── trac_mcp_server/           # Main package (6523 lines total)
│       ├── __init__.py             # Package root, __version__
│       ├── config.py               # Simple env-based config (135 lines)
│       ├── config_loader.py        # Hierarchical YAML config loader (214 lines)
│       ├── config_schema.py        # Pydantic unified config schema (463 lines)
│       ├── file_handler.py         # File I/O, path validation, format detection (197 lines)
│       ├── logger.py               # Logging setup (MCP/CLI modes) (99 lines)
│       ├── validators.py           # Input validation functions (98 lines)
│       ├── version.py              # Version consistency checking (55 lines)
│       ├── core/                   # Trac XML-RPC client layer
│       │   ├── __init__.py         # Exports TracClient, run_sync
│       │   ├── client.py           # TracClient - all XML-RPC operations (646 lines)
│       │   └── async_utils.py      # Async/sync bridge, semaphore (73 lines)
│       ├── converters/             # Markdown <-> TracWiki conversion
│       │   ├── __init__.py         # Barrel exports
│       │   ├── common.py           # ConversionResult, auto_convert, heuristics (245 lines)
│       │   ├── markdown_to_tracwiki.py  # MD -> TracWiki via mistune (428 lines)
│       │   └── tracwiki_to_markdown.py  # TracWiki -> MD via regex (458 lines)
│       ├── detection/              # Trac server capability detection
│       │   ├── __init__.py         # Exports CapabilityDetector
│       │   ├── capabilities.py     # Detection orchestrator with caching (357 lines)
│       │   ├── web_scraper.py      # /about page scraper (103 lines)
│       │   └── processor_utils.py  # Wiki processor probing (97 lines)
│       ├── mcp/                    # MCP protocol layer
│       │   ├── __init__.py         # Package comment
│       │   ├── __main__.py         # python -m entry point
│       │   ├── server.py           # MCP server, routing, CLI (353 lines)
│       │   ├── lifespan.py         # Server lifecycle manager (95 lines)
│       │   ├── tools/              # MCP tool implementations
│       │   │   ├── __init__.py     # Barrel exports, combined tool lists
│       │   │   ├── errors.py       # Structured error response builder
│       │   │   ├── ticket_read.py  # search, get, changelog, fields, actions
│       │   │   ├── ticket_write.py # create, update, delete
│       │   │   ├── wiki_read.py    # get, search, recent_changes
│       │   │   ├── wiki_write.py   # create, update, delete
│       │   │   ├── wiki_file.py    # file push/pull, format detection
│       │   │   ├── milestone.py    # list, get, create, update, delete
│       │   │   ├── sync.py         # doc_sync, doc_sync_status
│       │   │   └── system.py       # get_server_time
│       │   └── resources/          # MCP resource implementations
│       │       ├── __init__.py     # Barrel exports
│       │       └── wiki.py         # trac://wiki/ URI handler, page index
│       └── sync/                   # Bidirectional document sync engine
│           ├── __init__.py         # Full docstring, barrel exports
│           ├── engine.py           # SyncEngine orchestrator (834 lines)
│           ├── state.py            # JSON state persistence (169 lines)
│           ├── mapper.py           # PathMapper local<->wiki mapping (243 lines)
│           ├── merger.py           # Three-way merge via merge3 (89 lines)
│           ├── resolver.py         # Conflict resolution strategies (328 lines)
│           ├── reporter.py         # Report formatting (274 lines)
│           └── models.py           # Pydantic data contracts (179 lines)
├── tests/                          # Test suite
│   ├── __init__.py
│   ├── conftest.py                 # Shared fixtures
│   ├── test_client.py              # TracClient tests
│   ├── test_converter.py           # Format conversion tests
│   ├── test_detection.py           # Capability detection tests
│   ├── test_file_handler.py        # File handler tests
│   ├── test_config_loader.py       # YAML config loader tests
│   ├── test_config_schema.py       # Config schema tests
│   ├── test_config_schema_sync.py  # Sync profile config tests
│   ├── test_wiki_resources.py      # Wiki resource handler tests
│   ├── test_mcp_resources.py       # MCP resource integration tests
│   ├── test_wiki_file_integration.py  # Wiki file tool integration tests
│   ├── test_wiki_file_tools.py     # Wiki file tool unit tests
│   ├── test_sync_engine.py         # Sync engine tests
│   ├── test_sync_integration.py    # Sync integration tests
│   ├── test_sync_mapper.py         # PathMapper tests
│   ├── test_sync_resolver.py       # Conflict resolver tests
│   ├── test_sync_state.py          # Sync state tests
│   ├── test_sync_reporter.py       # Sync reporter tests
│   ├── test_sync_tools.py          # Sync MCP tool tests
│   └── test_mcp/                   # MCP tool handler tests
│       ├── __init__.py
│       ├── test_server_milestone_tools.py
│       ├── test_server_wiki_tools.py
│       └── tools/
│           ├── __init__.py
│           ├── test_ticket.py
│           ├── test_wiki.py
│           └── test_system.py
├── docs/                           # User-facing documentation
│   ├── deployment.md               # Deployment guide
│   ├── permissions.md              # Trac permissions reference
│   └── reference/                  # API reference docs
│       ├── overview.md
│       ├── cli.md
│       ├── configuration.md
│       ├── mcp-tools.md            # Tool reference (34KB, largest doc)
│       ├── mcp-resources.md
│       ├── error-handling.md
│       ├── format-conversion.md
│       ├── structured-json-output.md
│       └── troubleshooting.md
├── scripts/                        # Utility scripts
│   └── test_trac.py                # Manual Trac testing script (52KB)
├── .claude/                        # Claude configuration
├── pyproject.toml                  # Project metadata, dependencies, scripts
├── README.md                       # Project readme
└── .gitignore                      # Git ignore rules
```

## Directory Purposes

**`src/trac_mcp_server/`:**
- Purpose: Main application package -- all production code lives here
- Contains: Python modules organized by domain/responsibility
- Key files: `__init__.py` (version), `config.py` (primary config)

**`src/trac_mcp_server/core/`:**
- Purpose: Trac XML-RPC communication infrastructure
- Contains: TracClient class, async bridge utilities
- Key files: `client.py` (all Trac API methods), `async_utils.py` (threading bridge)

**`src/trac_mcp_server/mcp/`:**
- Purpose: MCP protocol implementation -- server, tools, resources
- Contains: Protocol-specific code that depends on the `mcp` SDK
- Key files: `server.py` (main server), `lifespan.py` (startup/shutdown)

**`src/trac_mcp_server/mcp/tools/`:**
- Purpose: Individual MCP tool implementations grouped by domain
- Contains: Tool definition lists + async handler functions
- Key files: One file per domain (tickets, wiki, milestones, etc.)

**`src/trac_mcp_server/mcp/resources/`:**
- Purpose: MCP resource handlers for read-only wiki access via URI templates
- Contains: Wiki page resource with `trac://wiki/` URI scheme
- Key files: `wiki.py` (page reader + index builder)

**`src/trac_mcp_server/converters/`:**
- Purpose: Bidirectional Markdown <-> TracWiki format conversion
- Contains: Two directional converters, shared types, auto-detection
- Key files: `markdown_to_tracwiki.py` (mistune-based), `tracwiki_to_markdown.py` (regex-based)

**`src/trac_mcp_server/detection/`:**
- Purpose: Runtime detection of Trac server capabilities
- Contains: Multi-strategy capability detection with caching
- Key files: `capabilities.py` (orchestrator), `web_scraper.py`, `processor_utils.py`

**`src/trac_mcp_server/sync/`:**
- Purpose: Bidirectional document sync engine (local files <-> Trac wiki)
- Contains: Full sync pipeline modules
- Key files: `engine.py` (orchestrator, 834 lines), `models.py` (data contracts)

**`tests/`:**
- Purpose: pytest test suite
- Contains: Unit tests organized to mirror source structure
- Key files: `conftest.py` (shared fixtures), domain-specific test files

**`docs/`:**
- Purpose: User-facing documentation for deployment, configuration, and tool reference
- Contains: Markdown files organized by topic
- Key files: `reference/mcp-tools.md` (comprehensive tool API reference)

**`scripts/`:**
- Purpose: Development and testing utility scripts
- Contains: Manual integration test script
- Key files: `test_trac.py` (large manual testing script)

## Key File Locations

**Entry Points:**
- `src/trac_mcp_server/mcp/server.py`: Primary entry -- CLI parsing, async main, MCP server loop
- `src/trac_mcp_server/mcp/__main__.py`: Package execution entry (`python -m trac_mcp_server.mcp`)

**Configuration:**
- `pyproject.toml`: Project metadata, dependencies, console script registration, pytest config
- `src/trac_mcp_server/config.py`: Runtime config loading from env vars / CLI args
- `src/trac_mcp_server/config_loader.py`: Hierarchical YAML config loading (used by sync)
- `src/trac_mcp_server/config_schema.py`: Pydantic config models (used by sync)

**Core Logic:**
- `src/trac_mcp_server/core/client.py`: All Trac XML-RPC API operations
- `src/trac_mcp_server/mcp/tools/`: All MCP tool implementations
- `src/trac_mcp_server/sync/engine.py`: Sync engine orchestrator
- `src/trac_mcp_server/converters/`: Format conversion logic

**Testing:**
- `tests/conftest.py`: Shared test fixtures
- `tests/test_*.py`: Individual test modules
- `tests/test_mcp/`: MCP-specific tool handler tests

## Naming Conventions

**Files:**
- `snake_case.py` for all Python modules: `ticket_read.py`, `async_utils.py`, `config_loader.py`
- Test files: `test_` prefix matching source module name: `test_converter.py`, `test_sync_engine.py`
- `__init__.py` for package initialization and barrel exports

**Directories:**
- `snake_case` for all Python packages: `trac_mcp_server`, `mcp`, `core`, `converters`, `detection`, `sync`
- `tools/` and `resources/` under `mcp/` for MCP-specific domain grouping

**Tool Modules:**
- Named by domain: `ticket_read.py`, `ticket_write.py`, `wiki_read.py`, `wiki_write.py`
- Read/write split pattern: separate modules for read-only vs mutating operations
- Each exports `*_TOOLS` list and `handle_*_tool()` dispatcher function

**Constants:**
- Tool lists: `TICKET_READ_TOOLS`, `WIKI_WRITE_TOOLS`, `MILESTONE_TOOLS`, `SYNC_TOOLS`
- Combined lists in `__init__.py`: `TICKET_TOOLS = TICKET_READ_TOOLS + TICKET_WRITE_TOOLS`

## Where to Add New Code

**New MCP Tool (e.g., attachment operations):**
- Create handler: `src/trac_mcp_server/mcp/tools/attachment.py`
- Follow pattern: export `ATTACHMENT_TOOLS` list + `handle_attachment_tool()` function
- Add TracClient methods: `src/trac_mcp_server/core/client.py`
- Register in barrel: `src/trac_mcp_server/mcp/tools/__init__.py`
- Add to router: `src/trac_mcp_server/mcp/server.py:handle_call_tool()` and `handle_list_tools()`
- Add tests: `tests/test_mcp/tools/test_attachment.py`

**New MCP Resource (e.g., ticket resources):**
- Create handler: `src/trac_mcp_server/mcp/resources/ticket.py`
- Register in barrel: `src/trac_mcp_server/mcp/resources/__init__.py`
- Add URI routing: `src/trac_mcp_server/mcp/server.py:handle_read_resource()`
- Add tests: `tests/test_ticket_resources.py`

**New TracClient Operation:**
- Add method to: `src/trac_mcp_server/core/client.py`
- Follow pattern: synchronous method using `self._rpc_request(service, method, *params)`
- Call from tool handler via: `await run_sync(client.new_method, args)` or `await run_sync_limited(...)` for parallel

**New Converter Direction or Feature:**
- Modify: `src/trac_mcp_server/converters/markdown_to_tracwiki.py` or `tracwiki_to_markdown.py`
- Update language mappings: `src/trac_mcp_server/converters/common.py`
- Add tests: `tests/test_converter.py`

**New Sync Feature:**
- Modify relevant module in `src/trac_mcp_server/sync/`
- Engine orchestration: `engine.py`, State: `state.py`
- Add tests: `tests/test_sync_*.py` (matching module name)

**New Configuration Option:**
- Simple server config: `src/trac_mcp_server/config.py` (add to `Config` dataclass + `load_config()`)
- Sync profile config: `src/trac_mcp_server/config_schema.py` (add to Pydantic models)

**New Utility:**
- Shared helpers: `src/trac_mcp_server/` (root package) as new module
- Async utilities: `src/trac_mcp_server/core/async_utils.py`
- Validation: `src/trac_mcp_server/validators.py`

## Special Directories

**`.venv/`:**
- Purpose: Python virtual environment
- Generated: Yes
- Committed: No

**`.claude/`:**
- Purpose: Claude Code configuration
- Generated: By Claude Code
- Committed: Yes

**`__pycache__/`:**
- Purpose: Python bytecode cache (found in many subdirectories)
- Generated: Yes
- Committed: No

---

*Structure analysis: 2026-02-14*
