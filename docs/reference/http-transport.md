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
| `POST/GET/DELETE <path>` (default `/mcp`) | Yes, if `auth_token` is configured; or requires an `Authorization: Bearer <token>` per caller if `oidc_rpc_url` is configured instead (mutually exclusive, see [OIDC Per-User Auth](#oidc-per-user-auth)) | The MCP JSON-RPC endpoint (streamable HTTP: `POST` for requests, `GET` for the SSE stream, `DELETE` to end a session) |
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
| OIDC per-user RPC URL | -- | `TRAC_MCP_OIDC_RPC_URL` | `oidc_rpc_url` | unset |
| Allow unauthenticated non-loopback bind | `--allow-unauthenticated` | -- | `allow_unauthenticated` | `false` |
| Extra allowed `Host` headers | -- | `TRAC_MCP_ALLOWED_HOSTS` (comma-separated) | `allowed_hosts` | `[]` |
| Extra allowed `Origin` headers | -- | `TRAC_MCP_ALLOWED_ORIGINS` (comma-separated) | `allowed_origins` | `[]` |

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

A missing or incorrect token gets `401 Unauthorized` with a `WWW-Authenticate: Bearer` header. The comparison uses `secrets.compare_digest` (constant-time). When no token is configured, the endpoint is open to anyone who can reach it -- see Bind Safety below for when that's disallowed.

This is a single static shared secret, not per-user OAuth. The MCP SDK's OAuth machinery (`mcp.server.auth.*`) is a separate, larger feature and is out of scope here.

## OIDC Per-User Auth

For a shared deployment where a single `trac-mcp-server` process serves many different people through one gateway (e.g. LibreChat, where each person already has their own Keycloak/OIDC login), the static bearer token above is the wrong tool -- it authenticates *the gateway*, not the individual user, so every Trac action would be attributed to (and limited by the permissions of) one shared identity.

`TRAC_MCP_OIDC_RPC_URL` switches the server into per-user mode instead: every request must carry the caller's own OIDC access token in the standard `Authorization: Bearer <token>` header, and that token is forwarded verbatim to the URL configured there. `trac-mcp-server` does not decode, verify, or cache the token's contents -- it trusts whatever sits in front of that URL (typically Apache + `mod_auth_openidc`, validating the token against your identity provider's JWKS and mapping it to a Trac username) to accept or reject it. There is no fallback: a request without a bearer token gets `401 Unauthorized` before it ever reaches tool dispatch, and `TRAC_USERNAME`/`TRAC_PASSWORD` are not required in this mode -- there is deliberately no shared service-account identity for a call to silently fall back to.

The standard header is used deliberately, not a custom one: an MCP client that performs its own OAuth flow per server (see the LibreChat example below) has no way to attach anything but a normal `Authorization` header -- it's acting as a generic OAuth client, not driving a bespoke per-server header scheme.

```bash
TRAC_MCP_OIDC_RPC_URL=https://trac.example.com/trac-api/login/xmlrpc \
  trac-mcp-server --transport http --host 127.0.0.1 --port 8080
```

```yaml
# .trac_mcp/config.yaml
server:
  transport: http
  oidc_rpc_url: ${TRAC_MCP_OIDC_RPC_URL}
```

**Trac/Apache side.** This needs a *second* WSGI entry point onto the same Trac environment, distinct from the one used for the Basic/LDAP path, protected by `mod_auth_openidc` instead:

```apache
OIDCOAuthVerifyJwksUri   https://your-idp.example.com/realms/YOUR_REALM/protocol/openid-connect/certs
OIDCOAuthRemoteUserClaim preferred_username

WSGIScriptAlias /trac-api /path/to/trac.wsgi
<Location "/trac-api">
    AuthType oauth20
    AuthName "trac-api"
    Require claim aud:trac-mcp-api
    # ... plus whatever authorizes the mapped REMOTE_USER (LDAP group, etc.)
</Location>
```

`TRAC_MCP_OIDC_RPC_URL` must point at that Location's XML-RPC endpoint (here, `.../trac-api/login/xmlrpc`) -- not necessarily the same path suffix as the default Basic/LDAP endpoint (`.../login/rpc`), since it's a separate Apache `Location` block that can be mounted and routed however your Trac/XML-RPC plugin setup requires.

**Mutually exclusive with the static bearer token.** `TRAC_MCP_AUTH_TOKEN` and `TRAC_MCP_OIDC_RPC_URL` cannot both be set -- `validate_server_config()` rejects that combination at startup. Both would be read off the same `Authorization` header for different purposes: a static shared secret can never equal an arbitrary per-user token, so every real caller would fail the comparison. In this mode, the "who may reach this endpoint" concern is instead handled by not exposing it beyond the trusted network (e.g. no published port in `docker-compose.yml`, matching the example below) plus the fact that Trac's own Apache layer rejects any token it doesn't recognize -- an unauthenticated caller that does reach the container can't do anything with it.

**Multiple instances (`instance` argument) are not supported in this mode.** OIDC per-user auth serves only its single configured Trac endpoint; a tool call passing `instance` (other than `"default"`) is rejected with a clear error rather than silently ignored.

**Caching.** A `TracClient` is built once per distinct token and reused for subsequent calls (bounded to the 256 most recently used tokens, evicted oldest-first) -- so a user's session doesn't pay the cost of a new HTTP connection pool on every tool call, without holding tokens past their natural turnover.

```bash
# Missing Authorization header -> 401, before JSON-RPC dispatch:
curl -s -o /dev/null -w '%{http_code}\n' -XPOST http://127.0.0.1:8080/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'

# With it, the caller's own Keycloak/OIDC access token is forwarded to Trac:
curl -s -XPOST http://127.0.0.1:8080/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $USER_OIDC_ACCESS_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

### LibreChat Integration

LibreChat supports a per-MCP-server OAuth flow matching the pattern above: it performs its own Authorization Code + PKCE exchange against your identity provider (using a dedicated IdP client registered for this MCP server) and attaches the resulting per-user access token to every request as `Authorization: Bearer <token>` -- no header configuration needed on the LibreChat side, since it's a generic OAuth client, not something forwarding a custom header. In `librechat.yaml`:

```yaml
mcpServers:
  trac:
    type: streamable-http
    url: http://trac-mcp:8080/mcp
    requiresOAuth: true
    oauth:
      authorization_url: https://your-idp.example.com/realms/YOUR_REALM/protocol/openid-connect/auth
      token_url: https://your-idp.example.com/realms/YOUR_REALM/protocol/openid-connect/token
      client_id: trac-mcp
      client_secret: '...'
      redirect_uri: https://your-librechat-host/api/mcp/trac/oauth/callback
      scope: 'openid profile email offline_access'
      token_endpoint_auth_methods_supported: ['client_secret_post']
      code_challenge_methods_supported: ['S256']
```

This needs a dedicated Keycloak client (`trac-mcp` above) distinct from LibreChat's own login client, with the redirect URI registered, PKCE (`S256`) enabled, and its issued tokens carrying whatever `aud` claim your Apache `<Location "/trac-api">` block requires (see the `Require claim aud:...` line above) -- typically via an audience mapper on that client.

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

The MCP endpoint validates the `Host` header (and, if present, `Origin`) against an allow-list, rejecting mismatches with `421`. The default allow-list covers loopback on any port (`127.0.0.1:*`, `localhost:*`) plus the exact configured `host:port`. If a client reaches this server by a name other than what it's bound to -- a reverse proxy that changes the `Host` header, or a docker-compose service name like `trac-mcp:8080` -- add it via `TRAC_MCP_ALLOWED_HOSTS` (and, for browser clients, `TRAC_MCP_ALLOWED_ORIGINS`), or the YAML `server:` section's `allowed_hosts`/`allowed_origins`.

```bash
TRAC_MCP_ALLOWED_HOSTS=trac-mcp:8080
```

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
