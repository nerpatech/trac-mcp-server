# Deployment Guide

This guide covers installing and configuring trac-mcp-server for use with MCP clients.

## Quick Start

**Get up and running in 5 minutes:**

### Prerequisites

- Python 3.10 or higher
- Access to a Trac instance with the XML-RPC plugin enabled
- Trac credentials (username and password)

### Steps

1. **Clone and install:**
   ```bash
   git clone <your-repo-url>
   cd trac-mcp-server
   pip install .
   ```

2. **Set your Trac connection:**
   ```bash
   export TRAC_URL=https://trac.example.com
   export TRAC_USERNAME=your-username
   export TRAC_PASSWORD=your-password
   ```

   Or create a `.env` file in the working directory:
   ```bash
   TRAC_URL=https://trac.example.com
   TRAC_USERNAME=your-username
   TRAC_PASSWORD=your-password
   ```

3. **Verify the installation:**
   ```bash
   trac-mcp-server --version
   ```

4. **Configure your MCP client** (see [MCP Client Configuration](#mcp-client-configuration) below).

---

## Installation Methods

### From Source (Recommended)

```bash
git clone <your-repo-url>
cd trac-mcp-server
pip install .
```

This installs the `trac-mcp-server` command and all dependencies.

### Development Installation

For development and testing, install in editable mode with dev dependencies:

```bash
pip install -e ".[dev]"
```

### pipx (Isolated Environment)

```bash
pipx install .
```

This installs `trac-mcp-server` in an isolated environment, avoiding dependency conflicts with other Python packages.

---

## Configuration

trac-mcp-server supports three configuration sources: CLI flags, environment variables, and YAML config files. See [Configuration Reference](reference/configuration.md) for full details.

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

Transport/bind settings (`TRAC_MCP_TRANSPORT`, `TRAC_MCP_HOST`, `TRAC_MCP_PORT`, `TRAC_MCP_AUTH_TOKEN`, `TRAC_MCP_OIDC_RPC_URL`, ...) are covered separately -- see [HTTP Deployment](#http-deployment) below and [HTTP Transport Reference](reference/http-transport.md#oidc-per-user-auth) (the latter for shared/multi-tenant per-user auth, e.g. behind LibreChat).

### Config File

You can also configure trac-mcp-server with a YAML config file at `.trac_mcp/config.yml` in the working directory:

```yaml
trac:
  url: https://trac.example.com
  username: admin
  password: secret
```

See [Configuration Reference](reference/configuration.md) for the full config file format and discovery order.

### .env File

trac-mcp-server loads a `.env` file from the working directory via python-dotenv. Values from `.env` sit between real shell env vars and config file values in precedence. You can use `.env` alongside a YAML config file.

```bash
# .env
TRAC_URL=https://trac.example.com
TRAC_USERNAME=your-username
TRAC_PASSWORD=your-password
```

**Security:** Never commit `.env` files to version control. The `.env` file is listed in `.gitignore`.

### Credential Security

- Store production credentials separately from development credentials
- Rotate credentials regularly
- Limit Trac account permissions to only what is needed

---

## MCP Client Configuration

MCP clients launch `trac-mcp-server` as a subprocess and communicate via stdio. Configure your client with the `trac-mcp-server` command and Trac credentials.

### Claude Desktop

**Configuration file location:**

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

**Configuration:**

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

Replace the credential values with your actual Trac instance details.

**After saving:**
1. Restart Claude Desktop completely (quit and relaunch)
2. Verify "trac" server shows as connected in settings
3. Test by asking: "Use the trac tool to ping the server"

**Note:** Claude Desktop does not load `.env` files. All credentials must be in the config JSON.

### Claude Code

Add the server with the Claude Code CLI:

```bash
claude mcp add trac -e TRAC_URL=https://trac.example.com \
  -e TRAC_USERNAME=your-username \
  -e TRAC_PASSWORD=your-password \
  -- trac-mcp-server
```

Or create `.mcp.json` in your workspace root:

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

If `.mcp.json` contains credentials, add it to `.gitignore`.

### Cline (VS Code Extension)

Add to your VS Code `settings.json`:

```json
{
  "cline.mcpServers": {
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

Reload VS Code window after saving.

### OpenCode

Create `opencode.json` in your project root:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "trac": {
      "type": "local",
      "command": ["trac-mcp-server"],
      "enabled": true,
      "environment": {
        "TRAC_URL": "https://trac.example.com",
        "TRAC_USERNAME": "your-username",
        "TRAC_PASSWORD": "your-password"
      }
    }
  }
}
```

OpenCode uses `"mcp"` (not `"mcpServers"`), `"environment"` (not `"env"`), and requires `"type": "local"` and `"enabled": true`.

### Generic MCP Client

Any MCP client that supports stdio transport can launch `trac-mcp-server`:

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

By default the server reads JSON-RPC requests from stdin and writes responses to stdout -- no network ports or sockets are needed. For a shared or remote deployment instead, see [HTTP Deployment](#http-deployment) below.

---

## HTTP Deployment

For a long-lived process shared by multiple clients or sessions (a daemon, a container, or a remote deployment behind a reverse proxy), run `trac-mcp-server` with `--transport http` instead of the stdio default. Full auth, bind-safety, and reverse-proxy guidance lives in [HTTP Transport Reference](reference/http-transport.md); this section covers the deployment-shaped basics.

### Quick Start

```bash
TRAC_MCP_AUTH_TOKEN=$(openssl rand -hex 32) \
  trac-mcp-server --transport http --host 127.0.0.1 --port 8080
```

By default the server binds `127.0.0.1` only. Binding a non-loopback host (e.g. `0.0.0.0`, for a container or remote box) requires `TRAC_MCP_AUTH_TOKEN` (or `server.auth_token` in `config.yaml`) to be set -- the server refuses to start otherwise, unless you explicitly pass `--allow-unauthenticated` (not recommended; it exposes the operator's Trac credentials to the network).

### Health Check

`GET /healthz` is unauthenticated and returns `{"status": "ok"}` -- use it for container liveness/readiness probes and load-balancer health checks.

```bash
curl -s http://127.0.0.1:8080/healthz
```

### Client Configuration

MCP clients that support the streamable HTTP transport connect with the endpoint URL and, if configured, an `Authorization: Bearer <token>` header:

```bash
claude mcp add --transport http trac http://127.0.0.1:8080/mcp \
  --header "Authorization: Bearer $TRAC_MCP_AUTH_TOKEN"
```

### Containers and Reverse Proxies

Running behind a reverse proxy (nginx, Caddy, an ingress controller) for TLS termination is the recommended way to expose the server beyond loopback -- terminate TLS at the proxy and keep `trac-mcp-server` itself bound to `127.0.0.1`. See [HTTP Transport Reference](reference/http-transport.md) for `allowed_hosts`/`allowed_origins` configuration when the proxy changes the `Host` header the server sees.

---

## Testing Your Setup

### Verify Installation

```bash
trac-mcp-server --version
```

### Test via MCP Client

After configuring your MCP client:

1. Restart the client (Claude Desktop, VS Code, etc.)
2. Check connection status in the client's MCP server panel
3. Test with a simple request: "Use the trac ping tool"
4. Expected response: "Trac MCP server connected successfully. API version: ..."

### Verify Permissions

Test that your Trac account has the necessary permissions by using the MCP tools:

- **Read access:** Use `ticket_search` or `wiki_get` to confirm read permissions
- **Write access:** Use `ticket_create` to create a test ticket (requires `TICKET_CREATE` permission)
- **Wiki access:** Use `wiki_get` to fetch a wiki page (requires `WIKI_VIEW` permission)

See [docs/permissions.md](permissions.md) for a detailed permissions reference.

---

## Troubleshooting

For common issues and solutions, see [Troubleshooting Reference](reference/troubleshooting.md).

**Quick diagnostic steps:**

1. Verify Python version: `python --version` (must be 3.10+)
2. Verify installation: `trac-mcp-server --version`
3. Enable debug logging: set `TRAC_DEBUG=true` in your environment or `.env` file
4. Check log file: `/tmp/trac-mcp-server.log` (default location)
5. Check MCP client logs for connection or startup errors
