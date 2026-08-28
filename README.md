<p align="center">
  <img src="img/trac_mcp_server_banner_dark_1280.png" alt="trac-mcp-server banner" />
</p>

# trac-mcp-server

Standalone MCP server that gives AI agents full access to Trac project management -- tickets, wiki, milestones, and search -- via the Model Context Protocol.

## Quick Start

Requires Python 3.10 or later.

```bash
pip install .
```

Set your Trac connection:

```bash
export TRAC_URL="https://trac.example.com"
export TRAC_USERNAME="your-username"
export TRAC_PASSWORD="your-password"
```

Run the server:

```bash
trac-mcp-server
```

> **Using auto-pm?** When trac-mcp-server is driven through auto-pm, auto-pm reads its connection settings from `.auto_pm/config.yml` in your project root (rather than the environment variables above). Run `auto-pm setup` in your project directory to generate the file; the resulting `.auto_pm/config.yml` carries `trac.url`, `trac.username`, a `${TRAC_PASSWORD}` reference, and component mappings.

## Configuration

Configuration via environment variables, `.env` file, or YAML config file (`.trac_mcp/config.yaml`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TRAC_URL` | Yes | -- | Trac instance URL |
| `TRAC_USERNAME` | Yes, unless `TRAC_MCP_OIDC_RPC_URL` is set | -- | Trac username |
| `TRAC_PASSWORD` | Yes, unless `TRAC_MCP_OIDC_RPC_URL` is set | -- | Trac password |
| `TRAC_INSECURE` | No | `false` | Skip SSL verification (development only) |
| `TRAC_DEBUG` | No | `false` | Enable debug logging |
| `TRAC_MAX_PARALLEL_REQUESTS` | No | `5` | Max parallel XML-RPC requests |
| `TRAC_MAX_BATCH_SIZE` | No | `500` | Max items per batch operation (1-10000) |
| `TRAC_RPC_TIMEOUT` | No | `60` | Read timeout in seconds for XML-RPC requests (5-300) |
| `TRAC_MCP_TRANSPORT` | No | `stdio` | MCP transport: `stdio` or `http` |
| `TRAC_MCP_HOST` | No | `127.0.0.1` | Bind host for the `http` transport |
| `TRAC_MCP_PORT` | No | `8080` | Bind port for the `http` transport |
| `TRAC_MCP_AUTH_TOKEN` | No | -- | Bearer token required by the `http` transport |
| `TRAC_MCP_OIDC_RPC_URL` | No | -- | OIDC-protected XML-RPC URL for per-user auth (shared/multi-tenant `http` deployments) -- see [HTTP Transport: OIDC Per-User Auth](docs/reference/http-transport.md#oidc-per-user-auth) |
| `TRAC_MCP_READ_ONLY` | No | `false` | Expose only read-only tools, regardless of transport -- see [Tool Architecture: Read-Only Filtering](docs/reference/tool-architecture.md#read-only-filtering---read-only) |

For YAML config file format and advanced options, see [Configuration Reference](docs/reference/configuration.md). For the `http` transport specifically, see [HTTP Transport](docs/reference/http-transport.md).

## MCP Client Integration

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "trac": {
      "command": "trac-mcp-server",
      "env": {
        "TRAC_URL": "https://trac.example.com",
        "TRAC_USERNAME": "your-username",
        "TRAC_PASSWORD": "your-password"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add trac -e TRAC_URL=https://trac.example.com \
  -e TRAC_USERNAME=your-username \
  -e TRAC_PASSWORD=your-password \
  -- trac-mcp-server
```

### Other MCP Clients

Any MCP client that supports stdio transport can launch `trac-mcp-server` as a subprocess. Pass Trac credentials via environment variables.

For a shared or remote deployment, `trac-mcp-server` can also serve MCP over streamable HTTP instead of stdio:

```bash
TRAC_MCP_AUTH_TOKEN=your-token trac-mcp-server --transport http --port 8080
```

See [HTTP Transport](docs/reference/http-transport.md) for auth, bind-safety rules, and reverse-proxy guidance.

## Format Conversion CLI

The package also installs `trac-convert`, a standalone CLI for converting between TracWiki and Markdown without requiring a Trac connection. Useful for one-off conversions, editor pipelines, and clipboard workflows.

```bash
# Convert Markdown to TracWiki (stdin → stdout)
trac-convert --from md --to tracwiki < README.md > README.tw

# Convert a file (auto-detects source format)
trac-convert --to md notes.tw -o notes.md

# Clipboard round-trip
trac-convert --from-clipboard --to tracwiki --to-clipboard
```

Supports stdin/stdout pipes, file I/O, and system clipboard on Linux, macOS, and Windows (via `pyperclip`). See [CLI Reference](docs/reference/cli.md#trac-convert) for the full flag list.

### Trac Wiki I/O

`trac-convert` can also read from and write to a live Trac instance via `--from-wiki PAGE` and `--to-wiki PAGE`, reusing the same credentials and config precedence as `trac-mcp-server`: `.trac_mcp/config.yml`, `TRAC_*` env vars, or `--trac-*` CLI flags — CLI wins.

```bash
# Verify Trac connectivity (prints resolved config, pings server)
trac-convert --check-trac

# Fetch a Trac wiki page, convert to Markdown, save locally
trac-convert --from-wiki DesignDoc --to md -o design.md

# Edit design.md in your favourite editor, then push it back
trac-convert --from-file design.md --to-wiki DesignDoc --wiki-comment "Refined via CLI"

# One-liner Trac → clipboard for quick reference lookup
trac-convert --from-wiki ReleaseNotes --to md --to-clipboard
```

Auth setup: use an existing `.trac_mcp/config.yml` (the same file `trac-mcp-server` uses) or supply ad-hoc flags — `--trac-url`, `--trac-username`, `--trac-password-file ~/.trac_pass` (prefer `--trac-password-file` to keep secrets out of shell history). See [Configuration](docs/reference/configuration.md) for the full precedence rules.

Trac errors (auth, network, page-not-found, permission-denied) exit with code `4` and write a human-readable message to stderr. See [CLI Reference](docs/reference/cli.md#trac-wiki-io) for the full flag list.

## Available Tools (43)

Every tool below also accepts an optional `instance` argument to route the call to another Trac project on the same host (or a named instance from config) instead of the default -- see [Multiple Instances](docs/reference/configuration.md#multiple-instances).

### Tickets (11)
| Tool | Description |
|------|-------------|
| `ticket_search` | Search tickets with Trac query language |
| `ticket_get` | Get ticket details by ID |
| `ticket_create` | Create new tickets |
| `ticket_update` | Update existing tickets |
| `ticket_delete` | Delete tickets |
| `ticket_changelog` | Get ticket change history |
| `ticket_fields` | List available ticket fields |
| `ticket_actions` | Get available ticket actions |
| `ticket_batch_create` | Create multiple tickets in one batch |
| `ticket_batch_delete` | Delete multiple tickets in one batch |
| `ticket_batch_update` | Update multiple tickets in one batch |

### Ticket Attachments (4)
| Tool | Description |
|------|-------------|
| `ticket_attachment_put` | Upload a local file as an attachment to a ticket (bytes sent via XML-RPC, not inlined) |
| `ticket_attachment_get` | Download a ticket attachment to a local file (bytes written to output_path, not inlined) |
| `ticket_attachment_list` | List attachments on a ticket |
| `ticket_attachment_delete` | Delete a ticket attachment (requires TICKET_ADMIN) |

### Ticket Admin (6)
| Tool | Description |
|------|-------------|
| `ticket_component_create` | Create a new ticket component (requires TICKET_ADMIN) |
| `ticket_component_list` | List all ticket components |
| `ticket_component_delete` | Delete a ticket component (requires TICKET_ADMIN) |
| `ticket_enum_create` | Create a new enum value (priority, resolution, severity, type, version) — requires TICKET_ADMIN |
| `ticket_enum_list` | List enum values for a given enum type |
| `ticket_enum_delete` | Delete an enum value (requires TICKET_ADMIN) |

### Wiki (7)
| Tool | Description |
|------|-------------|
| `wiki_get` | Get wiki page content (with Markdown conversion) |
| `wiki_search` | Search wiki pages |
| `wiki_create` | Create new wiki pages |
| `wiki_update` | Update existing wiki pages |
| `wiki_delete` | Delete wiki pages |
| `wiki_recent_changes` | List recent wiki changes |
| `wiki_get_history` | Get version history for a wiki page |

### Wiki Files (3)
| Tool | Description |
|------|-------------|
| `wiki_file_push` | Push local file to wiki (auto format conversion) |
| `wiki_file_pull` | Pull wiki page to local file |
| `wiki_file_detect_format` | Detect content format (Markdown/TracWiki) |

### Wiki Attachments (4)
| Tool | Description |
|------|-------------|
| `wiki_attachment_put` | Upload a local file as an attachment to a wiki page (bytes sent via XML-RPC, not inlined) |
| `wiki_attachment_get` | Download a wiki attachment to a local file (bytes written to output_path, not inlined) |
| `wiki_attachment_list` | List attachments on a wiki page |
| `wiki_attachment_delete` | Delete a wiki attachment (requires WIKI_DELETE) |

### Milestones (5)
| Tool | Description |
|------|-------------|
| `milestone_list` | List all milestones |
| `milestone_get` | Get milestone details |
| `milestone_create` | Create new milestones |
| `milestone_update` | Update existing milestones |
| `milestone_delete` | Delete milestones |

### System (2)
| Tool | Description |
|------|-------------|
| `ping` | Test connectivity and return API version |
| `get_server_time` | Get Trac server time |

### Instances (1)
| Tool | Description |
|------|-------------|
| `list_instances` | List configured and (via discovery) host-visible Trac instances |


## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

### Project Structure

```
src/trac_mcp_server/
  config.py       # Environment variable configuration
  core/           # Trac XML-RPC client, async utilities
  mcp/            # MCP server, tools, resources
  converters/     # Markdown <-> TracWiki conversion
  detection/      # Content format detection
```

## Documentation

See [docs/reference/overview.md](docs/reference/overview.md) for detailed tool reference, configuration, and troubleshooting.
