# Changelog

All notable changes to trac-mcp-server will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Changed
- **Breaking: the inline ticket and wiki tools no longer convert, in either direction (Trac #69).** The `format` parameter is gone from `ticket_create`, `ticket_update`, `ticket_batch_create`, `ticket_batch_update`, `wiki_create` and `wiki_update`; the `raw` parameter is gone from `ticket_get`, `ticket_changelog`, `wiki_get`, `wiki_search` and `milestone_get`; and `?format=` is gone from the `trac://wiki/` resource. All of them now store and return TracWiki bytes exactly as given, so a read-edit-write round trip is byte-exact
  - **Why removal rather than a flipped default.** #62 added `format` defaulting to `markdown`, which aimed the *destructive* failure at hand-authored TracWiki — indentation inside a `{{{ }}}` processor block was silently stripped, invisible in the render and in the (empty) warning list, visible only in the stored bytes — and the *safe* one at Markdown, which merely renders as literal `#` and `**`. Moving the default would have left an undeclared caller still getting *something* silently; removing the alternative leaves nothing to omit
  - **A stale caller is told, once, at the call site.** `format="markdown"` or `raw=false` returns a `validation_error` naming `trac-convert`, rather than being ignored. `format="tracwiki"` and `raw=true` are accepted as no-ops, so a caller already declaring TracWiki needs no change
  - **The converters are demoted, not deleted.** They still back the standalone `trac-convert` binary (#16), the `convert_preview` tool, and `wiki_file_push`/`wiki_file_pull` — the file tools keep converting deliberately, because they have a *filename* to go on where the inline tools have only content
  - **#47's pin moved rather than being dropped.** Its symptom — marker-poor Markdown misclassified by the content heuristic and stored unconverted — is re-anchored onto the converter and `auto_convert` tests, where the heuristic still runs. The misclassification is still asserted, not relaxed
  - Inline writes now preserve a CR that reaches them, rather than normalising it as `wiki_file_push` does; measured and pinned rather than left unmeasured
- `ticket_get` now returns the ticket's comments by default (Trac #60): number, author, timestamp and body, so one call is the whole ticket. Previously comments were reachable only through `ticket_changelog`, with nothing in a `ticket_get` response indicating any existed — the compliant triage path cost two calls and the one-call path *looked* complete, which silently cost correctness (an operator's answer, already recorded in a comment, re-asked)
  - New `include_comments` (default `true`) — the documented opt-out for fetching `_ts` before a write; `max_comments` (default 50) caps long threads
  - Comments are filtered out of the changelog inside the tool, so field-only changes never reach the response: a ticket whose description was edited no longer drags two full copies of that description along to deliver a few bytes of comment text (the cost that ruled out proxying `ticket_changelog` wholesale)
  - Each comment carries the number Trac assigns it — the same number `ticket_render_check`'s `comment` parameter is keyed on, closing a discoverability gap where that number could only be found by guessing
  - Truncation is loud by construction: over the cap, the head and tail are kept and the middle dropped (a long thread has its scope set early and corrected late), and the response names how many exist, how many are shown, and which comment numbers were omitted — `comment_count` is always the exact total. A failed changelog fetch still returns the ticket, and says the comments are missing

### Fixed
- `unconfigured_intertrac_prefix` no longer false-warns on a configured prefix inside an inline code span (Trac #61): `_PREFIX_REALM_RE`'s trailing `\S+` did not stop at a backtick, so a token whose code span was followed immediately by non-whitespace — a table cell delimiter being the common case, since `||` never has a space before it — ran past the closing backtick and swallowed the next cell. The suppression path then compared a token longer than any rendered code span, found no containment, and reported a correct, configured prefix as unconfigured. Same class as #59's fenced-block gap, at the other delimiter; `_PREFIX_TICKET_RE` was never affected because it ends at `\b`
- `install.sh` now installs both `dist/` binaries, not just `trac-mcp-server` (Trac #54): it previously hard-coded a single binary name, so `trac-convert` on `PATH` was never refreshed by the normal build-and-install cycle — a copy placed there by hand once could silently drift 15+ days and a behavior change (the #37 fix) behind `dist/trac-convert`, with `--version` reporting the same string for both and unable to tell them apart
- `markdown_to_tracwiki` converter sweep (Trac #19, #20, #27, #29): four defects found and fixed together since they're all in the same `TracWikiRenderer`
  - **#19** — `[MACRO: Name(args)]`, the placeholder `tracwiki_to_markdown` emits for a macro it can't otherwise represent, now converts back to `[[Name(args)]]` instead of passing through as dead literal text; a `wiki_get` → edit → `wiki_update` round trip no longer permanently flattens a page's macros (this also affected `[[Page]]`-shaped links before #28's fix). `[[...]]` syntax typed directly in Markdown source now survives unchanged too. Both had to be shielded as sentinel placeholders on the *raw* Markdown source before mistune parses it — mistune splits an unresolvable `[...]` span into separate text fragments internally, so shielding from inside the leaf `text()` renderer isn't reliable
  - **#20** — An empty table cell (the usual shape for a row-label column) now renders as a lone space instead of a bare `||`; two adjacent `||` (`||||`) is TracWiki's colspan-2 marker, not two empty cells, and was shifting every following header/cell left by one column
  - **#27** — Plain-prose CamelCase-shaped words (`WiFi`, `LoRa`, ...) are now defensively `!`-prefixed, since Trac's WikiFormatting auto-links any such word into a broken missing-page link and Markdown has no equivalent auto-link concept to signal intent either way. Scoped to genuine prose only — code spans/blocks, a link's own display text, and macro/link names inside `[[...]]` are all unaffected
  - **#29** — A hard line break (`[[BR]]`) immediately after a colon-valued token (e.g. `substrate:trac`) now gets a leading space; Trac's wiki-link grammar otherwise greedily consumes `[[BR]]` into a failed `wikiname:target` TracLink parse instead of recognizing it as the line-break macro
- `tracwiki_to_markdown` no longer corrupts `[[Page]]` WikiLinks read via `wiki_get`/`wiki_search` (Trac #28, critical): plain double-bracket links were being routed through the unknown-macro placeholder mechanism, which silently discarded single links and, when two or more placeholders landed near each other, merged them into one unterminated construct with a leaked raw `\x00` byte
  - `[[Page]]` / `[[Page|Label]]` are now recognized as WikiLinks (matching Trac's own macro-name-registry-first resolution) and convert to real `[text](wiki:Page)` Markdown links; only a small allowlist of genuine Trac macro names, or any `[[Name(args)]]` carrying explicit arguments, still go through the macro path
  - Fixed two latent greedy-regex bugs this exposed: the macro-placeholder restore pass and the single-bracket `[url text]` link pass could each span past their own closing token into a neighboring link/macro when several appeared close together

### Added
- Streamable HTTP transport (Trac #26): `trac-mcp-server --transport http` serves MCP over `POST/GET/DELETE <path>` (default `/mcp`) via a Starlette app run by `uvicorn`, alongside the unchanged stdio default
  - Stateful sessions (`Mcp-Session-Id`, the MCP SDK default), so one long-lived process can serve multiple concurrent clients/sessions
  - Optional static bearer token auth (`TRAC_MCP_AUTH_TOKEN` / `server.auth_token`), checked with `secrets.compare_digest`; unauthenticated requests get `401` + `WWW-Authenticate: Bearer`
  - Bind-safety guard: refuses to bind a non-loopback host for `--transport http` unless a token is configured or `--allow-unauthenticated` is explicitly passed
  - DNS-rebinding protection via the MCP SDK's `TransportSecuritySettings` (`Host`/`Origin` allow-listing, extensible via `server.allowed_hosts`/`allowed_origins`)
  - Unauthenticated `GET /healthz` for container liveness/readiness probes
  - New `--transport`, `--host`, `--port`, `--path`, `--allow-unauthenticated` CLI flags and `TRAC_MCP_TRANSPORT`/`TRAC_MCP_HOST`/`TRAC_MCP_PORT`/`TRAC_MCP_PATH`/`TRAC_MCP_AUTH_TOKEN` env vars, plus a new `server:` YAML config section — no `--auth-token` CLI flag by design (would leak into the process list)
  - New `src/trac_mcp_server/mcp/http_app.py`: `BearerAuthMiddleware`, `build_http_app()`, `run_http()`
  - `Server("trac-mcp-server", version=__version__)` now reports the project version over HTTP too (previously only the stdio path set this, so `StreamableHTTPSessionManager`'s auto-generated init options would have reported the `mcp` SDK version)
  - New docs: [HTTP Transport Reference](docs/reference/http-transport.md); updated README, CLI, configuration, and deployment docs
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
