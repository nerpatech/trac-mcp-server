# CLI Reference

## trac-mcp-server

The `trac-mcp-server` command starts the MCP server. It communicates via stdin/stdout using JSON-RPC 2.0 over the Model Context Protocol. It is designed to be launched by MCP clients (Claude Desktop, Claude Code, etc.), not used interactively.

### Usage

```bash
trac-mcp-server
trac-mcp-server --version
trac-mcp-server --transport http --port 8080   # streamable HTTP instead of stdio
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--url URL` | -- | Override Trac URL (takes precedence over `TRAC_URL` env var) |
| `--username USER` | -- | Override Trac username (takes precedence over `TRAC_USERNAME` env var) |
| `--password PASS` | -- | Override Trac password (takes precedence over `TRAC_PASSWORD` env var) |
| `--insecure` | `false` | Skip SSL certificate verification (development only) |
| `--log-file PATH` | `/tmp/trac-mcp-server.log` | Log file location |
| `--permissions-file PATH` | -- | Restrict available tools by Trac permissions (see [Tool Architecture](tool-architecture.md#permission-filtering)) |
| `--read-only` | `false` | Expose only read-only tools (view/search/list/get), regardless of transport. Also settable via `TRAC_MCP_READ_ONLY` env var or `config.yaml` `server.read_only`. Combinable with `--permissions-file` -- a tool must pass both filters. |
| `--transport {stdio,http}` | `stdio` | MCP transport to serve (also settable via `TRAC_MCP_TRANSPORT` or `config.yaml` `server.transport`) |
| `--host HOST` | `127.0.0.1` | Bind host for `--transport http` |
| `--port PORT` | `8080` | Bind port for `--transport http` |
| `--path PATH` | `/mcp` | URL path the MCP endpoint is mounted at, for `--transport http` |
| `--allow-unauthenticated` | `false` | Allow `--transport http` to bind a non-loopback host without an auth token. Dangerous -- exposes Trac credentials to the network. Prefer `TRAC_MCP_AUTH_TOKEN`. |
| `--version` | -- | Show version and exit |

There is no `--auth-token` flag: it would leak the secret into the process list. Set it via `TRAC_MCP_AUTH_TOKEN` or `config.yaml`'s `server.auth_token`. See [HTTP Transport](http-transport.md) for the full auth and bind-safety rules.

### Configuration

Configuration can come from YAML config files, environment variables, or CLI flags. CLI flags take highest precedence. See [Configuration](configuration.md) for details.

### How It Works

By default the server runs over stdio transport: it reads JSON-RPC requests from stdin and writes responses to stdout. All log output goes to a file (never stdout), so the stdio channel stays clean for MCP protocol messages.

Typical lifecycle:

1. MCP client launches `trac-mcp-server` as a subprocess
2. Server validates Trac connection on startup
3. Server handles MCP tool calls (tickets, wiki, milestones, etc.) until the client disconnects

With `--transport http`, the server instead serves MCP over streamable HTTP (`POST/GET/DELETE` on the configured path) via `uvicorn`, so one long-lived process can serve multiple clients/sessions. See [HTTP Transport](http-transport.md).

### Installation

```bash
pip install .          # installs trac-mcp-server command
pipx install .         # alternative: isolated environment
```

The `trac-mcp-server` command is registered as an entry point in `pyproject.toml`.

---

## trac-convert

The `trac-convert` command is a standalone binary that converts between TracWiki and Markdown formats. It is designed for interactive shell use and Unix pipe composition. Most operations work without a Trac connection; `--from-wiki`, `--to-wiki`, and `--check-trac` connect to a live Trac instance using the same credentials and config precedence as `trac-mcp-server`.

### Usage

```bash
trac-convert --from md --to tracwiki < input.md > output.tw     # stdin → stdout
trac-convert --to tracwiki input.md -o output.tw                 # file → file
trac-convert --from-clipboard --to md                            # clipboard → stdout
trac-convert --to-clipboard input.md                             # file → clipboard
trac-convert --check-trac                                        # verify Trac connectivity
trac-convert --from-wiki MyPage --to md -o my-page.md           # Trac → local file
trac-convert --from-file my-page.md --to-wiki MyPage            # local file → Trac
trac-convert --version
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--from {md,tracwiki,auto}` | `auto` | Source format; `auto` sniffs the input via heuristics |
| `--to {md,tracwiki}` | *(required unless --check-trac or --to-wiki)* | Destination format |
| `FILE` (positional) | stdin | Input file path; omit or use `-` for stdin |
| `--from-file PATH` | -- | Read input from a local file (explicit named alternative to the positional `FILE`). Mutually exclusive with `FILE`, `--from-clipboard`, and `--from-wiki`. |
| `-o, --output FILE` | stdout | Output file path; omit for stdout |
| `--from-clipboard` | -- | Read input from system clipboard instead of stdin/file. On Linux, requires one of `wl-clipboard`, `xclip`, or `xsel` to be installed (tried in that order, based on `$WAYLAND_DISPLAY` / `$DISPLAY`); falls back to `pyperclip`. On headless terminals without a clipboard tool, use `--from-file PATH` or pipe input on stdin instead. |
| `--to-clipboard` | -- | Write output to system clipboard instead of stdout/file. Same Linux tool requirement as `--from-clipboard`. |
| `--heading-anchors {on,off}` | `on` | md→tracwiki only: emit explicit `#slug` anchors on TracWiki headings (silently ignored in tw→md direction) |
| `--unknown-macros {bracket,preserve,drop}` | `bracket` | tw→md only: how to render unknown TracWiki macros — `bracket` = `[MACRO: Name]`, `preserve` = leave `[[Name]]` literal, `drop` = omit (silently ignored in md→tw direction) |
| `-v, --verbose` | -- | Emit `info:` diagnostics to stderr (mutually exclusive with `-q`) |
| `-q, --quiet` | -- | Suppress `warning:` lines to stderr (mutually exclusive with `-v`; does NOT suppress `error:` lines) |
| `--from-wiki PAGE` | -- | Fetch input from a Trac wiki page (source format is TracWiki). Mutually exclusive with `FILE` positional and `--from-clipboard`. |
| `--to-wiki PAGE` | -- | Write output to a Trac wiki page (target format is TracWiki). Mutually exclusive with `-o/--output` and `--to-clipboard`. Bypasses the `--to` requirement. |
| `--wiki-comment MSG` | `Updated via trac-convert` | Change comment recorded on the wiki page when using `--to-wiki`. Ignored without `--to-wiki`. |
| `--check-trac` | -- | Print resolved Trac config source per field, ping the server, and exit (no conversion performed). |
| `--trac-url URL` | -- | Override Trac URL (default: `TRAC_URL` env var or YAML config). |
| `--trac-username USER` | -- | Override Trac username (default: `TRAC_USERNAME` env var or YAML config). |
| `--trac-password PASS` | -- | Override Trac password (default: `TRAC_PASSWORD` env var or YAML config). Prefer `--trac-password-file` for secrets. |
| `--trac-password-file PATH` | -- | Read Trac password from file (single line, trimmed). Takes precedence over `--trac-password`. |
| `--version` | -- | Show version and exit |

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Runtime error (I/O failure, clipboard unavailable, mutually-exclusive flag conflict) |
| `2` | Usage error (argparse-emitted: missing required flag, bad choice) |
| `3` | Conversion error (exception raised inside the converter) |
| `4` | Trac error (auth failure, network/timeout, permission denied, page not found, XML-RPC fault, SSL error) — emitted by `--check-trac`, `--from-wiki`, and `--to-wiki`. |

### Auto-detection

When `--from auto` is used (the default when omitted), the format is detected via `converters.common.detect_format`: TracWiki markers (`{{{`, `[[`, `= Heading =`) win, otherwise Markdown is assumed. On ambiguous input, prefer the explicit `--from` flag.

### Trac Wiki I/O

`--from-wiki` and `--to-wiki` read and write Trac wiki pages directly via the same `TracClient` and config precedence as `trac-mcp-server` (CLI `--trac-*` > env `TRAC_*` > `.trac_mcp/config.yml`).

```bash
# Verify connectivity: prints resolved config source per field and pings the server
trac-convert --check-trac

# Fetch a Trac wiki page, convert to Markdown, save locally
trac-convert --from-wiki MyPage --to md -o my-page.md

# Push a Markdown file back to Trac
trac-convert --from-file notes.md --to-wiki MyPage --wiki-comment "Edited via CLI"

# Round-trip: fetch, edit locally, then push back
trac-convert --from-wiki MyPage --to md -o my-page.md
# (edit my-page.md)
trac-convert --from-file my-page.md --to-wiki MyPage
```

On failure, `trac-convert` exits with code `4` and writes a classified error message (page-not-found, permission-denied, timeout, SSL, connection, generic) to stderr. See [Configuration](configuration.md) for the full auth and config precedence rules.

### Installation

```bash
pip install .          # installs BOTH trac-mcp-server and trac-convert
pipx install .         # alternative: isolated environment, both binaries available
```

Both entry points are registered in `pyproject.toml` under `[project.scripts]`.

---

[Back to Reference Overview](overview.md)
