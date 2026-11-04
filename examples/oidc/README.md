# Testing OIDC per-user auth

This setup lets you run [OIDC per-user auth](../../docs/reference/http-transport.md#oidc-per-user-auth) end to end, without an identity provider of your own. It is for testing, not production: the passwords are public, Keycloak runs in development mode with a self-signed certificate, and nothing is encrypted between the server and Trac.

| Service | Address | Purpose |
|---|---|---|
| Keycloak | `http://localhost:8180` | Realm `trac-demo`, public client `trac-mcp`, users `alice` and `bob` (password = username). Tokens carry the audience `trac-mcp-api` |
| Apache with `mod_auth_openidc` | `http://localhost:8081/trac-api` | Validates the bearer token and maps `preferred_username` to `REMOTE_USER`. This is the endpoint for `TRAC_MCP_OIDC_RPC_URL` |
| Apache with Basic auth | `http://localhost:8081/trac` | The ordinary entry point, with the shared account `service-account` / `service-secret` |
| Trac 1.6 with TracXMLRPC | behind Apache, SQLite | Every logged-in user may do everything |

`trac/trac-api.conf` holds the Apache configuration. Set `KEYCLOAK_PORT` and `TRAC_PORT` if the default ports are taken, and pass the same addresses to the commands below.

## Run it

Start the stack from this directory. The first build takes a few minutes.

```bash
docker compose up -d --build --wait
```

Install the server and start it from the repository root:

```bash
pip install -e .
TRAC_URL=http://localhost:8081/trac \
TRAC_MCP_OIDC_RPC_URL=http://localhost:8081/trac-api/login/xmlrpc \
  trac-mcp-server --transport http --host 127.0.0.1 --port 8080
```

The URL is plain `http` but on a loopback host, so the server accepts it without `TRAC_MCP_OIDC_ALLOW_INSECURE_HTTP`.

Then check it, from another shell:

```bash
python3 examples/oidc/verify.py
```

`verify.py` checks that a request without a token gets `401`, that a ticket `alice` creates has her as reporter, that a comment `bob` adds has him as author, and that `list_instances` reports the one endpoint.

## See who each request runs as

Apache logs the user of each request first:

```bash
docker compose logs --no-log-prefix trac | grep '"POST'
```

```text
alice "POST /trac-api/login/xmlrpc HTTP/1.1" 200
bob "POST /trac-api/login/xmlrpc HTTP/1.1" 200
```

## With a shared account alongside

`TRAC_MCP_OIDC_RPC_URL` may be set together with `TRAC_USERNAME` and `TRAC_PASSWORD`, which keeps the startup self-test running. Tool calls must still not use the shared account. Restart the server with both and run `verify.py` again:

```bash
TRAC_URL=http://localhost:8081/trac \
TRAC_USERNAME=service-account TRAC_PASSWORD=service-secret \
TRAC_MCP_OIDC_RPC_URL=http://localhost:8081/trac-api/login/xmlrpc \
  trac-mcp-server --transport http --host 127.0.0.1 --port 8080
```

`service-account` then appears in the Apache log only for the self-test at startup, as one request per start to `/trac/login/rpc`.

## Get a token by hand

```bash
TOKEN=$(curl -s http://localhost:8180/realms/trac-demo/protocol/openid-connect/token \
  -d grant_type=password -d client_id=trac-mcp -d username=alice -d password=alice \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')
```

## Clean up

```bash
docker compose down -v
```
