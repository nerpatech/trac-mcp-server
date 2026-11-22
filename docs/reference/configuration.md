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
  rpc_timeout: 60
  write_gate: true       # false = emergency off switch; see "Write gate"

logging:
  level: INFO            # unset = WARNING for stdio, INFO for http
  file: /var/log/trac-mcp-server.log   # unset = the default; see "Logging"

server:
  transport: stdio       # or "http"
  host: 127.0.0.1
  port: 8080
  path: /mcp
  auth_token: ${TRAC_MCP_AUTH_TOKEN}
  allow_unauthenticated: false
  read_only: false
  disabled_tools: []
  allowed_hosts: []
  allowed_origins: []

export:
  link_rewrites:
    - from: https://trac.example.com/wiki/
      to: https://blog.example/
```

All fields are optional. Omitted fields use built-in defaults or can be supplied via environment variables. `server:` settings govern the MCP server *process itself* (transport, bind address, auth), not the Trac connection -- see [HTTP Transport](http-transport.md) for the full field reference and bind-safety rules.

### Link rewrites (`export:`)

The Markdown export (`wiki_file_pull` with `format=markdown`, `trac-mcp wiki-pull`, `trac-convert` on TracWiki) writes each link as Trac rendered it: a link to another wiki page points at Trac. `export.link_rewrites` rewrites those targets, for a page published somewhere else -- say, the sibling posts of a blog. The HTML export (`format=html`) uses the same map and the same rules for the `href` of each link and changes nothing else of Trac's render (an `<img src>`, text, `<pre>` content and every other byte stay as Trac wrote them).

```yaml
export:
  link_rewrites:
    - from: https://trac.example.com/wiki/   # URL prefix to replace
      to: https://blog.example/              # what replaces it
```

- A link target (`href`) that starts with `from` has that prefix replaced by `to`. When several rules match, the **longest** `from` wins, whatever the order of the list.
- The link text is never changed: `[https://trac.example.com/wiki/A](https://blog.example/A)` keeps the original URL as its label.
- **Image sources** (`src`) are never rewritten (images and attachments are a separate ticket, #215), and an in-page anchor is written `#frag`, never mapped. Trac renders `[#Heading x]` as the document's own URL plus the fragment (`<trac_url>/wiki/<page>#Heading` for a page, `<trac_url>#Heading` for text rendered with no page), so a link whose URL before the `#` is the document's own (two absolute URLs are compared by path only, after percent-decoding and ignoring one trailing `/`, because Trac builds its URLs from its own base URL, which can name another host than the daemon's `TRAC_URL`) and that has a fragment is written `#Heading` and left alone, as is a bare `#Heading`, with or without rules (so a published HTML export's table of contents stays in the page instead of jumping into Trac). A link to the same page with no fragment, or to another page with one, is an ordinary link and is rewritten.
- `from` must be a non-empty string, `to` a string (it may be empty), and two rules may not share a `from`. A bad entry fails config loading, naming it (`export.link_rewrites[1]`).
- `TRAC_MCP_LINK_REWRITES` replaces the list whole: whitespace-separated `FROM=TO` pairs, each split at the first `=`. Set to an empty value, it clears the list. It cannot express a prefix that contains whitespace; use the YAML list for that. A pair with no `=` is refused at startup.
- Precedence is the same as for other settings: environment variable (or `.env`) over YAML over none.

Where each tool reads the map:

| Consumer | Map used |
|----------|----------|
| `wiki_file_pull` (daemon), `format=markdown` or `html` | its `link_rewrites` argument if given (`[]` = none), else the **daemon's** configured map; `format=tracwiki` uses none |
| `trac-convert` (TracWiki -> Markdown) | `--link-map` if given, else the configured map of the machine it runs on |
| `trac-mcp wiki-pull` | `--link-map` if given, else the `export.link_rewrites` key of the local `.trac_mcp/config.yaml` for `--format markdown` or `html` (that key only, no `TRAC_MCP_LINK_REWRITES`; `.env` is loaded first, so `${VAR}` in a rule resolves as it does for the daemon), else nothing is sent and the daemon's map applies |

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
  level: INFO   # optional; leave it out for the mode default
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
| `TRAC_DEBUG` | No | `false` | Enable debug logging (YAML `trac.debug`): the level is DEBUG whatever `LOG_LEVEL` or `logging.level` say. See [Logging](#logging) |
| `LOG_LEVEL` | No | `WARNING` (stdio) / `INFO` (http) | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` (any case; a blank value counts as unset). Overrides YAML `logging.level`. An unknown name stops startup naming the variable. See [Logging](#logging) |
| `TRAC_MAX_PARALLEL_REQUESTS` | No | `5` | Maximum parallel XML-RPC requests to Trac |
| `TRAC_MAX_BATCH_SIZE` | No | `500` | Maximum items per batch ticket operation (1-10000) |
| `TRAC_RPC_TIMEOUT` | No | `60` | Read timeout in seconds for XML-RPC requests (5-300) |
| `TRAC_ALLOWED_INSTANCES` | No | -- | Comma-separated instances (`/path` on the `TRAC_URL` host, or absolute URL) the default identity may reach; unset = unrestricted. `TRAC_URL` must be on the list (ticket #110) |
| `TRAC_WRITE_GATE` | No | `true` | Write gate (YAML `trac.write_gate`): refuses a write whose text carries a broken link. Off only for `0`, `false`, `no` or `off` (case-insensitive); any other value, an empty one included, leaves it on. See [Write gate](#write-gate) |
| `TRAC_MCP_LINK_REWRITES` | No | -- | Whitespace-separated `FROM=TO` link-rewrite rules of the Markdown export, split at the first `=`; replaces the YAML `export.link_rewrites` list whole, an empty value clears it. See [Link rewrites](#link-rewrites-export) |
| `LOG_FILE` | No | per-user state file (stdio) / none (http) | Log file path, used when `--log-file` is not given; overrides YAML `logging.file`. Set it to an empty string for no log file (stdio then logs to stderr). See [Logging](#logging) |

Boolean variables (`TRAC_INSECURE`, `TRAC_DEBUG`) accept `true`, `1`, `yes`, or `on` (case-insensitive). Any other value is treated as `false`.

### Server (Transport) Environment Variables

These govern the MCP server process itself -- transport, bind address, and auth -- not the Trac connection. Same precedence rules apply: CLI flag > env var > YAML `server:` section > default.

| Variable | Default | Description |
|----------|---------|-------------|
| `TRAC_MCP_TRANSPORT` | `stdio` | MCP transport: `stdio` or `http` |
| `TRAC_MCP_HOST` | `127.0.0.1` | Bind host for the `http` transport |
| `TRAC_MCP_PORT` | `8080` | Bind port for the `http` transport (1-65535) |
| `TRAC_MCP_PATH` | `/mcp` | URL path the MCP endpoint is mounted at |
| `TRAC_MCP_AUTH_TOKEN` | -- | Bearer token required by the `http` transport. Env/YAML only -- there is no CLI flag, to keep it out of the process list. |
| `TRAC_MCP_READ_ONLY` | `false` | Expose only tools marked read-only (`--read-only`, YAML `read_only`); see [Tool Architecture](tool-architecture.md#read-only-filtering---read-only). `true`/`false`, `1`/`0`, `yes`/`no`, `on`/`off`; any other value is refused at startup, an empty value counts as unset. |
| `TRAC_MCP_DISABLED_TOOLS` | -- | Comma-separated tool names to leave out (YAML `disabled_tools`); see [Tool Architecture](tool-architecture.md#disabling-individual-tools). Replaces the YAML list; an empty value counts as unset. |
| `TRAC_MCP_ALLOWED_HOSTS` | -- | Comma-separated `Host` headers accepted besides loopback and the bind address (`host[:port]` or `host:*`). Replaces the YAML `allowed_hosts` list; empty counts as unset. |
| `TRAC_MCP_ALLOWED_ORIGINS` | -- | Comma-separated `Origin` headers accepted, with scheme (`http://trac-mcp:8080`). Replaces the YAML `allowed_origins` list; empty counts as unset. |
| `TRAC_MCP_FILE_ACCESS` | `off` for `http`, `local` for `stdio` | Whether file tools may read and write paths on the server (`--file-access`, YAML `file_access`): `local` or `off`. An empty value counts as unset (YAML, else the transport default); any other value is refused at startup. |
| `TRAC_MCP_MAX_INLINE_BYTES` | `5242880` (5 MiB) | Largest inline file-tool result in bytes (YAML `max_inline_bytes`); must be positive, zero or negative is refused at startup, an empty value counts as unset. |

An empty or blank `TRAC_MCP_PORT` counts as unset: the YAML `port`, else `8080`, applies. The same holds for `TRAC_MCP_MAX_INLINE_BYTES`.

See [HTTP Transport](http-transport.md) for the full picture, including the bind-safety rule that refuses an unauthenticated non-loopback bind.

**Note:** Environment variables always take precedence over config file values, whether or not a config file is present. The `${VAR}` interpolation syntax in YAML is a convenience for referencing env vars within config files, but is not required -- env vars are checked directly during configuration loading and override any config file value for the same setting.

## Logging

Three settings, each resolved from the same layers (Trac /trac_mcp_server #277). A `.env` file counts as the environment; the config file's keys count only when the file sets them.

**Level**, highest first:

1. `TRAC_DEBUG` true (or YAML `trac.debug: true`): DEBUG.
2. The `LOG_LEVEL` environment variable (a blank value counts as unset).
3. YAML `logging.level`.
4. The mode default: WARNING for stdio, INFO for http.

`LOG_LEVEL` and `logging.level` accept DEBUG, INFO, WARNING, ERROR and CRITICAL in any case. Any other name stops startup with a message that names the source (`LOG_LEVEL` or `logging.level`). Leaving `level` out of the file is not the same as `level: INFO`: it keeps stdio at WARNING.

**File**, highest first:

1. `--log-file`.
2. The `LOG_FILE` environment variable (set but empty means no file).
3. YAML `logging.file`.
4. The default: for stdio, `$XDG_STATE_HOME/trac-mcp-server/server.log`, or `~/.local/state/trac-mcp-server/server.log` when `XDG_STATE_HOME` is unset or blank; for http, no file.

An empty or blank value at any step means no file: stdio then logs to stderr, http logs to stderr only.

The stdio default file is created for you: its directory with mode 0700, the file with 0600, and it rotates at 5 MiB, keeping three old files (`server.log.1` to `.3`). If it cannot be created (a read-only home, say), the server prints one `WARNING: cannot open default log file ...; logging to stderr` line and carries on logging to stderr. A file you name yourself (any of the first three steps) is a plain append file, created with mode 0600 if it does not exist, never rotated and with no directory created for it; if it cannot be opened, startup stops with `ERROR: Cannot open log file`.

## Write gate

Every write that stores TracWiki (`ticket_create`, `ticket_update`, `ticket_comment_edit`, `ticket_comment_reply`, `wiki_create`, `wiki_update`, `wiki_file_push`, `milestone_create`, `milestone_update`, and per item `ticket_batch_create` / `ticket_batch_update`) passes through a write-time link check. A write carrying a link that does not resolve is refused with `link_check_failed`.

- **Off switch**: `TRAC_WRITE_GATE=0` (or `write_gate: false` under `trac:`). It parses fail-closed: only `0`, `false`, `no` or `off` (case-insensitive) turn the gate off, so a typo or an empty value leaves it on.
- **Routed instances**: every instance the daemon routes to inherits the switch.
- **Disabled gate says so**: each write response then carries a `NOTE:` line saying the write was not link-checked.

See [MCP Tools: Write Gate Status](mcp-tools.md#write-gate-status) for the response format and the `gate_notes` field.

## .env File Support

trac-mcp-server uses [python-dotenv](https://pypi.org/project/python-dotenv/) to load a `.env` file, searched from the working directory upward: the nearest `.env` wins, and the search stops after the first directory that holds a `.git` or is your home directory, so a `.env` above either is never read. A `.env` that you do not own, or that group or others can write, is skipped with a warning (POSIX). (An editable install therefore no longer reads the checkout's `.env` when started from another directory, and an installed copy reads the `.env` of its working directory.) Values from `.env` are treated as environment variables and sit between real shell env vars and config file values in the precedence order. You can use `.env` alongside a YAML config file -- for example, store non-secret settings in `config.yaml` and credentials in `.env`.

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
Every result reports which instance answered, in `instance` and
`instance_source`, so a call that silently fell back to the default is
visible from the response alone.

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
    username: other-user      # username and password are a pair: set both,
    password: other-pass      # or neither (then the default's pair is used)
```

`username` and `password` are set as a pair: both set, or neither. With
neither, the instance uses the default `trac:` pair (or, over HTTP, the
caller's identity per ticket #102). A username without a password, or a
password without a username, is refused when the config loads, so one
account's half is never combined with the default account's other half and
sent to that instance's host. An unset `${VAR}` interpolates to an empty
string and is the usual cause. `insecure` still inherits from the default
when unset.

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
