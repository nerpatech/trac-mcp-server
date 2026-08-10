# Configuration

trac-mcp-server supports three configuration sources, applied in the following precedence order (highest first):

1. **CLI flags** (`--url`, `--username`, `--password`, `--insecure`)
2. **Environment variables** (`TRAC_URL`, `TRAC_USERNAME`, etc.)
3. **Config file** (`.trac_mcp/config.yaml` or `.trac_mcp/config.yml`)
4. **Built-in defaults**

Higher-precedence sources override lower ones. You can mix sources freely -- for example, store most settings in a config file and override the password via an environment variable.

## Configuration File

trac-mcp-server discovers YAML configuration files using convention-based search. The first file found (in this order) is used:

| Priority | Location | Description |
|----------|----------|-------------|
| 1 | `$TRAC_MCP_CONFIG` | Explicit path via environment variable |
| 2 | `.trac_mcp/config.yml` (in CWD) | Project-level config |
| 3 | `.trac_mcp/config.yaml` (in CWD) | Project-level config (alternate extension) |
| 4 | `~/.config/trac_mcp/config.yml` | XDG global config |
| 5 | `~/.trac_mcp/config.yaml` | Legacy global config |

### Example config.yaml

```yaml
trac:
  url: https://trac.example.com
  username: admin
  password: secret
  insecure: false
  debug: false
  max_parallel_requests: 5
  max_batch_size: 500

logging:
  level: INFO
  file: /tmp/trac-mcp-server.log
```

All fields are optional. Omitted fields use built-in defaults or can be supplied via environment variables.

### Environment Variable Interpolation

YAML values support `${VAR}` and `${VAR:-default}` syntax for referencing environment variables:

```yaml
trac:
  url: ${TRAC_URL}
  username: ${TRAC_USERNAME}
  password: ${TRAC_PASSWORD:-default_password}
```

This is useful for keeping secrets out of config files while still using YAML for other settings.

### !include Support

Config files support `!include` directives for splitting configuration across multiple files:

```yaml
trac: !include trac-connection.yml
logging: !include logging.yml
```

Include paths are resolved relative to the file containing the `!include` directive. Circular includes are detected and raise an error.

### Bootstrapping

On first use, you can create a starter config file template by creating the directory and file manually:

```bash
mkdir -p .trac_mcp
cat > .trac_mcp/config.yml << 'EOF'
# trac-mcp-server configuration
trac:
  url: https://trac.example.com
  username: admin
  password: secret

logging:
  level: INFO
EOF
```

The `ensure_config()` function in `config_loader.py` can also create a starter template programmatically.

## Environment Variables

Environment variables override config file values. They are always checked, even when a config file is present (via `${VAR}` interpolation in YAML values).

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TRAC_URL` | Yes | -- | Trac instance URL (must start with `http://` or `https://`) |
| `TRAC_USERNAME` | Yes | -- | Trac username for XML-RPC authentication |
| `TRAC_PASSWORD` | Yes | -- | Trac password for XML-RPC authentication |
| `TRAC_INSECURE` | No | `false` | Skip SSL certificate verification (development only) |
| `TRAC_DEBUG` | No | `false` | Enable debug logging |
| `TRAC_MAX_PARALLEL_REQUESTS` | No | `5` | Maximum parallel XML-RPC requests to Trac |
| `TRAC_MAX_BATCH_SIZE` | No | `500` | Maximum items per batch ticket operation (1-10000) |

Boolean variables (`TRAC_INSECURE`, `TRAC_DEBUG`) accept `true`, `1`, `yes`, or `on` (case-insensitive). Any other value is treated as `false`.

**Note:** Environment variables always take precedence over config file values, whether or not a config file is present. The `${VAR}` interpolation syntax in YAML is a convenience for referencing env vars within config files, but is not required -- env vars are checked directly during configuration loading and override any config file value for the same setting.

## .env File Support

trac-mcp-server uses [python-dotenv](https://pypi.org/project/python-dotenv/) to load a `.env` file from the working directory. Values from `.env` are treated as environment variables and sit between real shell env vars and config file values in the precedence order. You can use `.env` alongside a YAML config file -- for example, store non-secret settings in `config.yaml` and credentials in `.env`.

```bash
# .env
TRAC_URL=https://trac.example.com
TRAC_USERNAME=your-username
TRAC_PASSWORD=your-password
TRAC_INSECURE=false
TRAC_DEBUG=false
TRAC_MAX_PARALLEL_REQUESTS=5
TRAC_MAX_BATCH_SIZE=500
```

Environment variables set in the shell take precedence over values in `.env`.

## MCP Client Configuration

When an MCP client (Claude Desktop, Claude Code, etc.) launches `trac-mcp-server`, it passes environment variables in the client configuration. Config files are an alternative to passing environment variables in the MCP client config -- if you have a `.trac_mcp/config.yml` in the working directory, the server will use it automatically.

**Claude Desktop** (`claude_desktop_config.json`):

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

**Claude Code:**

```bash
claude mcp add trac -e TRAC_URL=https://trac.example.com \
  -e TRAC_USERNAME=your-username \
  -e TRAC_PASSWORD=your-password \
  -- trac-mcp-server
```

## Multiple Instances

If your Trac host serves more than one project, you can declare additional
named instances in your config file's `instances:` section. Every MCP tool
accepts an optional `instance` argument that routes the call to one of these
instead of the default -- see the [MCP Tools Reference](mcp-tools.md) for the
argument, and the `list_instances` tool for discovering what's reachable.

```yaml
trac:
  url: http://trac.example.com:8000/main-project
  username: agent_rpc
  password: secret

instances:
  bcs:
    url: /bcs                 # relative -- resolved against the default host
  other-host:
    url: https://other.example.com/trac
    username: other-user      # unset fields (username/password/insecure)
    password: other-pass      # inherit from the default `trac:` section
```

Instances can also be declared out-of-band via a `TRAC_INSTANCES` file (YAML
or JSON), pointed to by the `TRAC_INSTANCES` environment variable -- either
`{"instances": {...}}` or a bare `{name: {...}}` mapping. `TRAC_INSTANCES`
wins over the config file's `instances:` section on a name collision, and
edits to either source are picked up on the next call without restarting the
server.

### Ad-hoc addressing and the same-host restriction

Beyond named instances, any tool call can pass `instance: "/project-path"` to
reach *any* project on the same Trac host as the default instance, with no
config needed -- the server joins the path onto the default host and reuses
the default credentials. An absolute URL is also accepted for `instance`, but
**only if its scheme and host exactly match the default instance's**.

This restriction is deliberate: if arbitrary hostnames were accepted, an
agent (or a prompt-injected value flowing into a tool call) could redirect
the operator's configured Trac credentials to an attacker-controlled server.
Requests for `instance` values on a different host are rejected with an
`unknown_instance` error before any request leaves the process.

## Validation

Configuration is validated at server startup. The server will refuse to start with a clear error message if validation fails.

**Validation rules:**

- `TRAC_URL` must start with `http://` or `https://`
- `TRAC_USERNAME` cannot be empty
- `TRAC_PASSWORD` cannot be empty
- When `TRAC_INSECURE=true`, a warning is logged (use only for development)

The `trac-mcp-server` command also supports CLI flags that override environment variables. See the [CLI Reference](cli.md) for details.

---

[Back to Reference Overview](overview.md)
