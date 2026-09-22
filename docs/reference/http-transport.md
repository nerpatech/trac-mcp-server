# HTTP Transport

By default `trac-mcp-server` speaks MCP over stdio, for one client per subprocess (Claude Desktop, Claude Code, etc.). `--transport http` instead serves MCP over **streamable HTTP** (the current MCP spec transport -- `POST`/`GET`/`DELETE` on a single endpoint), so one long-lived process can serve multiple clients and sessions: a daemon, a container, or a deployment behind a reverse proxy.

The legacy SSE transport (`/sse` + `/messages/`) is not implemented -- it is deprecated in the MCP spec.

```bash
TRAC_MCP_AUTH_TOKEN=$(openssl rand -hex 32) \
  trac-mcp-server --transport http --host 127.0.0.1 --port 8080
```

## Endpoints

| Method + Path | Auth required? | Description |
|----------------|-----------------|--------------|
| `POST/GET/DELETE <path>` (default `/mcp`) | Yes, if `auth_token` is configured | The MCP JSON-RPC endpoint (streamable HTTP: `POST` for requests, `GET` for the SSE stream, `DELETE` to end a session) |
| `GET /healthz` | No, always open | Liveness/readiness probe. Returns `{"status": "ok"}`. |

Sessions are **stateful** (the SDK default): the server returns an `Mcp-Session-Id` response header on `initialize`, and clients send it back on subsequent requests. Each session gets its own MCP protocol state; multiple concurrent sessions are supported, including calls that pass different `instance` arguments (see [Configuration: Multiple Instances](configuration.md#multiple-instances)).

The endpoint must be reached at exactly the configured path (e.g. `http://127.0.0.1:8080/mcp`, no trailing slash) -- it is not a path *prefix*, so there is no redirect to `/mcp/`.

## Configuration

Same precedence as the rest of the server's config: CLI flag > env var (`TRAC_MCP_*`) > YAML `server:` section > default.

| Setting | CLI flag | Env var | YAML (`server:`) | Default |
|---------|----------|---------|-------------------|---------|
| Transport | `--transport {stdio,http}` | `TRAC_MCP_TRANSPORT` | `transport` | `stdio` |
| Bind host | `--host` | `TRAC_MCP_HOST` | `host` | `127.0.0.1` |
| Bind port | `--port` | `TRAC_MCP_PORT` | `port` | `8080` |
| Mount path | `--path` | `TRAC_MCP_PATH` | `path` | `/mcp` |
| Bearer token | *(none -- see below)* | `TRAC_MCP_AUTH_TOKEN` | `auth_token` | unset |
| Allow unauthenticated non-loopback bind | `--allow-unauthenticated` | -- | `allow_unauthenticated` | `false` |
| Extra allowed `Host` headers | -- | -- | `allowed_hosts` | `[]` |
| Extra allowed `Origin` headers | -- | -- | `allowed_origins` | `[]` |

```yaml
# .trac_mcp/config.yaml
server:
  transport: http
  host: 127.0.0.1
  port: 8080
  path: /mcp
  auth_token: ${TRAC_MCP_AUTH_TOKEN}
  allow_unauthenticated: false
  allowed_hosts: []
  allowed_origins: []
```

**There is deliberately no `--auth-token` CLI flag.** Command-line arguments are visible to every user on the host via the process list; the token must come from `TRAC_MCP_AUTH_TOKEN` or the YAML `server:` section instead.

## Authentication

When `auth_token` is set, every request to the MCP endpoint (not `/healthz`) must carry:

```
Authorization: Bearer <token>
```

A missing or incorrect token gets `401 Unauthorized` with a `WWW-Authenticate: Bearer` header. The comparison uses `secrets.compare_digest` (constant-time), checked against *every* configured token -- the static one and every identity's (see below) -- rather than stopping at the first match, so response timing never reveals which token, if any, was presented. When no token and no identities are configured, the endpoint is open to anyone who can reach it -- see Bind Safety below for when that's disallowed.

This is a single static shared secret, not per-user OAuth. The MCP SDK's OAuth machinery (`mcp.server.auth.*`) is a separate, larger feature and is out of scope here. For per-caller credentials without full OAuth, see Multiple Identities below.

## Multiple Identities (`TRAC_IDENTITIES`)

By default every request that presents a valid token -- the static `TRAC_MCP_AUTH_TOKEN` -- reaches Trac as the single identity `TRAC_USERNAME`/`TRAC_PASSWORD` configured for the whole process. Ticket #102 adds an alternative: a `TRAC_IDENTITIES` file mapping several *distinct* bearer tokens to their own Trac username/password, so one shared daemon can serve several callers under their own Trac identities instead of one process per identity.

```bash
TRAC_IDENTITIES=/path/to/identities.yml \
  trac-mcp-server --transport http --host 127.0.0.1 --port 8080
```

```yaml
# identities.yml -- YAML or JSON, ${VAR} interpolated from .env just like
# TRAC_INSTANCES.
identities:
  alice:
    token: ${ALICE_MCP_TOKEN}
    username: ${ALICE_TRAC_USER}
    password: ${ALICE_TRAC_PASSWORD}
  bob:
    token: ${BOB_MCP_TOKEN}
    username: ${BOB_TRAC_USER}
    password: ${BOB_TRAC_PASSWORD}
```

A request whose `Authorization: Bearer <token>` matches one of these resolves as that identity for the rest of that call: the default instance (and any declared/ad-hoc instance that doesn't set its own username/password) is reached using the identity's credentials instead of the process's own `TRAC_USERNAME`/`TRAC_PASSWORD`. The legacy static `TRAC_MCP_AUTH_TOKEN`, if still configured alongside identities, keeps resolving to no identity at all -- a caller using it gets the same behaviour as before this ticket.

**Precedence: a declared instance's own explicit credentials always win over the caller's identity.** If `instances:` (or `TRAC_INSTANCES`) gives an instance its own `username`/`password`, every token holder reaches that instance through those explicit credentials, never their own -- otherwise one identity's password could reach a host a *different* declared instance deliberately points credentials at. Only *inherited* credentials (the default instance itself, a declared instance that leaves username/password unset, or ad-hoc same-host addressing) are replaced by the caller's identity.

Validation, at startup:

- every identity needs a non-empty token, username, and password;
- every token must be unique, including against `TRAC_MCP_AUTH_TOKEN`;
- `TRAC_IDENTITIES` on the `stdio` transport is an error -- there is no per-request signal (a bearer header) for stdio to read an identity from;
- `TRAC_IDENTITIES` together with `allow_unauthenticated` and no `TRAC_MCP_AUTH_TOKEN` is an error -- declaring identities and then opting the endpoint out of authentication is contradictory, since no caller could ever present a token to be resolved as one of them.

Identities alone (no static token) still gate every request, so they satisfy the same bind-safety rule `TRAC_MCP_AUTH_TOKEN` does: a non-loopback bind is allowed once `TRAC_IDENTITIES` is configured, without also requiring a static token.

**Loaded once at startup; changing identities needs a restart.** Unlike `TRAC_INSTANCES` (which reloads on an mtime change), there is no live-reload here.

**A deployment using only `TRAC_MCP_AUTH_TOKEN` -- no `TRAC_IDENTITIES` at all -- behaves exactly as it did before this ticket.** Nothing about the single-identity path changes.

## Bind Safety

The server refuses to start `--transport http` bound to a **non-loopback** host (anything other than `127.0.0.1`, `localhost`, or `::1`) unless *either*:

- `TRAC_MCP_AUTH_TOKEN` (or `server.auth_token`) is set, **or**
- `--allow-unauthenticated` (or `server.allow_unauthenticated: true`) is explicitly passed.

```
ERROR: Server configuration error: Refusing to bind non-loopback host '0.0.0.0' for
the http transport without authentication. Set TRAC_MCP_AUTH_TOKEN (or the
server.auth_token config value), or pass --allow-unauthenticated to explicitly opt out.
```

This exists because the server holds the operator's Trac credentials -- an open, unauthenticated `0.0.0.0` bind would let anyone on the network read and write Trac through those credentials. `--allow-unauthenticated` exists for trusted-network / development use; prefer setting a token instead.

## DNS-Rebinding Protection

The MCP endpoint validates the `Host` header (and, if present, `Origin`) against an allow-list, rejecting mismatches with `421`. The default allow-list covers loopback on any port (`127.0.0.1:*`, `localhost:*`) plus the exact configured `host:port`. If you front the server with a reverse proxy that changes the `Host` header the server sees (a public hostname, a different port), add it to `allowed_hosts` (and, for browser clients, `allowed_origins`) in the YAML `server:` section.

`/healthz` is not subject to this check, so container health probes work regardless of the `Host` header they send.

## Reverse Proxies and TLS

`trac-mcp-server` does not terminate TLS itself. For anything beyond loopback/trusted-network use, put a reverse proxy (nginx, Caddy, an ingress controller) in front, terminate TLS there, and keep `trac-mcp-server` bound to `127.0.0.1`. Make sure the proxy forwards the `Authorization` header unmodified, and add the proxy's public hostname to `allowed_hosts` if it differs from what the server would otherwise expect.

## Browser Clients (Out of Scope)

Browser-based MCP clients need CORS support -- an `Access-Control-Allow-Origin` response header, with `Mcp-Session-Id` explicitly exposed via `Access-Control-Expose-Headers` so the browser's JS can read it. This is **not implemented**; the http transport as shipped targets non-browser clients (CLI tools, server-to-server integrations, `claude mcp add --transport http`). Add CORS middleware yourself if you need browser access, being careful with the allowed-origin list for the same reason `allowed_hosts` matters above.

## Logging

Unlike stdio (which must keep stdout clean for JSON-RPC and logs to a file only), the http transport logs to stderr (plus an optional file via `--log-file`), like the CLI. `uvicorn` and `uvicorn.access` are silenced to `WARNING` unless `LOG_LEVEL=DEBUG`.

## Verifying

```bash
# Health check (unauthenticated)
curl -s http://127.0.0.1:8080/healthz

# Without a token configured, calls succeed directly:
curl -s -XPOST http://127.0.0.1:8080/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'

# With a token configured:
curl -s -XPOST http://127.0.0.1:8080/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $TRAC_MCP_AUTH_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

Or register it as a real MCP server:

```bash
claude mcp add --transport http trac http://127.0.0.1:8080/mcp \
  --header "Authorization: Bearer $TRAC_MCP_AUTH_TOKEN"
```

---

[Back to Reference Overview](overview.md)
