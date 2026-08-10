# Changelog

All notable changes to trac-mcp-server will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Multi-instance Trac support (Trac #18): every MCP tool and the `trac://wiki/*` resource now accept an optional `instance` argument that routes the call to another Trac project instead of the default one, with no server restart required
  - Named instances declared via a new `instances:` section in config files, or an out-of-band `TRAC_INSTANCES` file (YAML/JSON), with live reload on edit
  - Ad-hoc addressing (`instance: "/project-path"`) reaches any project on the same Trac host as the default instance using the default's credentials, restricted to the default instance's exact scheme+host so credentials can never be sent to another host
  - New `list_instances` tool (28th documented tool, 43rd registered including `ping`) surfaces configured instances plus, via a new `scrape_project_index()` web-scraping helper, other projects discoverable on the host's "Available Projects" index
  - `src/trac_mcp_server/instances.py`: `InstanceSpec`, `InstanceRegistry` (resolve/get_client/describe, with per-URL client caching and mtime-gated live reload), `UnknownInstanceError`, `load_declared_instances()`

## [2.2.0] - 2026-07-26

### Added
- New `trac-convert` CLI binary (second `[project.scripts]` entry point) — standalone TracWiki ↔ Markdown converter usable outside the MCP server
- `--from {md,tracwiki,auto}` and `--to {md,tracwiki}` format flags with `auto` detection via `converters.common.detect_format`
- stdin → stdout as default I/O mode (Unix-pipe friendly, binary-safe, no spurious trailing newlines)
- Positional `FILE` argument and `-o/--output FILE` flag for file I/O with clear errors on missing/unwritable paths
- `--from-clipboard` and `--to-clipboard` flags via `pyperclip` for cross-platform clipboard I/O (Linux/macOS/Windows/Wayland)
- `--heading-anchors {strip,preserve}` and `--unknown-macros {bracket,preserve}` flags exposing converter kwargs (keyword-only so 7 MCP tool call sites keep working unmodified; wrong-direction flags silently ignored)
- `--verbose/-v` and `--quiet/-q` mutually exclusive flags: `-v` emits three `info:` lines to stderr (source format, bytes read, bytes written); `-q` suppresses warnings
- Standardized exit codes: `EXIT_OK=0`, `EXIT_RUNTIME_ERROR=1` (I/O, clipboard, mutex), `EXIT_USAGE_ERROR=2` (argparse), `EXIT_CONVERSION_ERROR=3` (exceptions inside `convert_text()`)
- Full integration test suite for `trac-convert`: 9-cell source×destination I/O matrix (stdin/file/clipboard × stdout/file/clipboard), md↔tw roundtrip fixtures with expected-divergence assertions, cross-mode warning routing, UTF-8/CRLF/empty-input edge cases, 10 subprocess entry-point smoke tests exercising real OS pipe fd separation and console_scripts wiring (988 passing, up from 741 in v2.1.0)

### Changed
- `pyperclip>=1.8.2` added as a **required** dependency (not optional) — enables clipboard I/O out-of-the-box on all platforms
- Tool count remains 27 MCP tools (unchanged from v2.1.3); `trac-convert` is a separate binary, not an MCP tool

## [2.1.3] - 2026-07-25

### Added
- Batch ticket operations: `ticket_batch_create`, `ticket_batch_delete`, `ticket_batch_update` -- best-effort processing with per-item results and bounded parallelism via `gather_limited`
- `TRAC_MAX_BATCH_SIZE` environment variable (default: 500, range: 1-10000) for controlling maximum items per batch operation
- Config path resolution (`resolve_config_path()`) and bootstrapping (`ensure_config()`) utilities in config_loader.py
- YAML config file support via `config_loader` and `config_schema` integration in server lifespan -- discovers `.trac_mcp/config.yaml` with hierarchical loading
- Three-source configuration precedence: CLI arguments > environment variables > config file
- 5 new tests for YAML config loading path in `test_lifespan.py`
- Package version display in test script output
- Shared error translation utility (`translate_xmlrpc_error`) consolidating 5 duplicate implementations
- Shared timestamp formatting utility (`format_timestamp`) with timezone-aware UTC
- Shared constants module (`constants.py`) for tool handlers
- Network timeout (10s connect, 60s read) on XML-RPC requests
- 79 new tests covering TracClient methods, error handlers, auto_convert, and logger (781 -> 860 -> 641 after sync removal)
- `CHANGELOG.md` version history extracted from planning documents
- `severity` parameter now supported on `ticket_create` and `ticket_update` (previously accepted but dropped)
- `ticket_update` now forwards `summary`, `description` (with Markdown→TracWiki conversion), and `type` ticket attributes (previously accepted but dropped)
- `ticket_update` now forwards Trac workflow actions: `action` (e.g. `accept`, `resolve`, `reopen`) plus `action_<action>_<action>_<field>` workflow input fields (e.g. `action_resolve_resolve_resolution`). Pattern-based forwarding for `action_*` keys, since action names are workflow-config-dependent. Enables clients (e.g. auto-pm) to drive Trac workflow transitions through the MCP `ticket_update` tool instead of being silently dropped
- `core/client` now parses XML-RPC `<base64>` values, decoding them to Python `bytes` instead of dropping or mishandling them (#12)
- MCP tool wrappers for ticket attachments: `ticket_attachment_put`, `ticket_attachment_get`, `ticket_attachment_list`, `ticket_attachment_delete` — push, fetch (with byte-identical retrieval), enumerate, and remove ticket attachments via the MCP layer, with `TICKET_ADMIN`-aware permission diagnostics on delete (#10)
- MCP tool wrappers for wiki attachments: `wiki_attachment_put`, `wiki_attachment_get`, `wiki_attachment_list`, `wiki_attachment_delete` — push, fetch (with byte-identical retrieval), enumerate, and remove wiki page attachments via the MCP layer, with `WIKI_DELETE`-aware permission diagnostics on delete (#11)

### Changed
- Modernized typing across all source files to Python 3.10+ style (`X | None` instead of `Optional[X]`, built-in generics)
- Renamed `TRAC_ASSIST_CONFIG` env var to `TRAC_MCP_CONFIG` (backward compatible with deprecation warning)
- Updated copyright from OpenCode to nerpa.tech in LICENSE and pyproject.toml
- Added PyPI project URLs (Homepage, Repository, Issues, Changelog) to pyproject.toml
- Updated all documentation (configuration, deployment, CLI, troubleshooting, README) to reflect YAML config file support
- Unified `max_parallel_requests` default to 5 (was 2 in TracConfig)
- Moved `max_batch_size` from standalone function to TracConfig field with Pydantic validation
- Consolidated redundant wiki tool tests (457 -> 158 lines, 65% reduction)
- Reorganized test files into consistent `tests/test_mcp/tools/` directory structure
- Migrated 2 test files from unittest.TestCase to pytest style

### Fixed
- `ticket_get` now includes keywords, cc, reporter, and resolution fields in both text and structured JSON output (were previously omitted)
- Lazy logger formatting (`%s` instead of f-strings) in lifespan and system modules
- `ConversionResult` type mismatch in wiki resources (was passing object as string)
- `set_client()` signature to accept `TracClient | None` (removed type: ignore)
- Removed License classifier conflicting with PEP 639 (`setuptools >= 77.0` derives it from `license = "MIT"`)
- Dead code removal: duplicate `get_version()`, unused validator loop, stale imports
- Live test configuration: `.env` now loaded in conftest for `TRAC_URL` availability
- Markdown -> TracWiki converter no longer mangles sentinel-shaped markers like `[auto-pm: state NEEDS_CODE]`; non-URL-shaped links are emitted verbatim instead of being wrapped as broken wiki links (#8)

### Removed
- Sync subsystem: `doc_sync` and `doc_sync_status` MCP tools, `src/trac_mcp_server/sync/` module, and all sync-related tests (213 tests)
- `scripts/test_trac.py` live test script and associated report
- Sync config schema models (`SyncMappingRule`, `SyncProfileConfig`) and sync profile support in `UnifiedConfig`

## [2.1.0] - 2026-02-15

Post-extraction cleanup release. Hardens the standalone package after splitting from trac_assist v1.3.2.

### Changed
- Rewrote all documentation for standalone usage (README, configuration, deployment, troubleshooting, tool reference)
- Removed orphaned trac_assist references from source, comments, and docstrings
- Cleaned import paths and removed unused dependencies (cssselect, anyio from direct deps)
- Promoted pydantic and charset-normalizer to direct dependencies
- Standardized all error returns to MCP CallToolResult format (fixed isError boolean bug)
- Bumped setuptools requirement to >=77.0 for PEP 639 support

### Added
- Config validation: URL structure, max_parallel bounds (1-20), whitespace rejection
- .env.example with all supported environment variables
- MIT LICENSE file with PEP 639 SPDX metadata
- PyInstaller build.sh and install.sh scripts for standalone binary distribution
- GitHub Actions CI workflow (lint, test, live-test, build jobs)
- Local ci.sh script mirroring CI checks
- Ruff linter (E4/E7/E9/F/B/I rules) with zero-violation baseline
- Comprehensive test coverage: 741 tests (up from ~200 extracted tests)

### Fixed
- isError field in error responses now correctly set to True (was always False)
- Detection tests relying on trac_assist fixtures
- Stale monolith references in test_trac.py integration script

## [2.0.0] - 2026-02-14

Initial standalone release. Extracted from trac_assist v1.3.2 as independent MCP server package.

### Added
- 24 MCP tools (tickets, wiki, milestones, system)
- Wiki page resources via MCP resource protocol
- Markdown to TracWiki bidirectional format conversion
- stdio transport for MCP client integration
- Environment variable configuration (TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD)
- `trac-mcp-server` CLI entry point
- Independent test suite
