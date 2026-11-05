# Troubleshooting

## Common Errors and Solutions

### Connection Refused

**Symptom:**
```
Error (server_error): Connection refused
```

**Causes:**
- Trac server is not running
- Incorrect URL in configuration
- Firewall blocking connection

**Solutions:**
1. Verify Trac server is running: `curl -I https://trac.example.com`
2. Check URL in config (no trailing slash needed)
3. Test from same network as Trac server

---

### Authentication Failed

**Symptom:**
```
Error (server_error): 401 Unauthorized
```

**Causes:**
- Incorrect username or password
- Account disabled or locked
- HTTP Basic Auth not enabled on Trac

**Solutions:**
1. Verify credentials by logging into Trac web UI
2. Check `TRAC_USERNAME` and `TRAC_PASSWORD` are set correctly
3. Ensure Trac has XML-RPC plugin enabled with authentication

---

### SSL Certificate Error

**Symptom:**
```
Error (server_error): SSL: CERTIFICATE_VERIFY_FAILED
```

**Causes:**
- Self-signed certificate
- Expired certificate
- Certificate chain incomplete

**Solutions:**
1. For development only: Set `TRAC_INSECURE=true` in your environment or `.env` file
2. Install proper SSL certificate on Trac server
3. Add CA certificate to system trust store

---

### Permission Denied

**Symptom:**
```
Error (permission_denied): TICKET_CREATE permission required
```

**Causes:**
- User lacks required Trac permission
- Anonymous access disabled
- Resource-specific restrictions

**Solutions:**
1. Check user permissions in Trac Admin > Permissions
2. Request necessary permissions from Trac administrator
3. Required permissions by operation:
   - Tickets: `TICKET_VIEW`, `TICKET_CREATE`, `TICKET_MODIFY`
   - Wiki: `WIKI_VIEW`, `WIKI_CREATE`, `WIKI_MODIFY`, `WIKI_DELETE`
   - Milestones: `TICKET_VIEW` (read), `TICKET_ADMIN` (write)

---

### Version Conflict

**Symptom:**
```
Error (version_conflict): Page has been modified. Current version is 7, you tried to update version 5.
```

**Causes:**
- Another user modified the resource
- Stale version number from earlier fetch

**Solutions:**
1. Re-fetch the resource to get current version
2. Merge your changes with current content
3. Retry with correct version number

```json
// 1. Fetch current state
{"name": "wiki_get", "arguments": {"page_name": "Page"}}
// Response shows "Version: 7"

// 2. Retry update with correct version
{"name": "wiki_update", "arguments": {"page_name": "Page", "content": "...", "version": 7}}
```

---

### XML-RPC Plugin Not Found

**Symptom:**
```
Error (server_error): 404 Not Found: /login/rpc
```

**Causes:**
- XML-RPC plugin not installed
- Plugin not enabled in trac.ini
- Wrong URL path

**Solutions:**
1. Install plugin: `pip install TracXMLRPC`
2. Enable in `trac.ini`:
   ```ini
   [components]
   tracrpc.* = enabled
   ```
3. Restart Trac

---

## Debug Mode

Enable debug mode for detailed logging using any of these methods:

**Environment variable:**
```bash
export TRAC_DEBUG=true
```

**`.env` file:**
```bash
TRAC_DEBUG=true
```

**Config file (`.trac_mcp/config.yaml`):**
```yaml
logging:
  level: DEBUG
```

When enabled, the server writes detailed debug output to the log file.

## Log File Locations

| Mode | Default Location |
|------|------------------|
| MCP Server (stdio) | `/tmp/trac-mcp-server.log` |
| MCP Server (http) | none; logs to stderr (a file only with `--log-file` or `LOG_FILE`) |

`--log-file` wins over the `LOG_FILE` environment variable. An empty value (`--log-file ""` or `LOG_FILE=`) disables the file; stdio then logs to stderr.

**Custom MCP log location:**
```bash
trac-mcp-server --log-file /var/log/trac-mcp-server.log
```

## Testing Connectivity

Use your MCP client to call the `ping` tool:

```json
{"name": "ping", "arguments": {}}
```

A successful response returns: "Trac MCP server connected successfully. API version: ..."

---

[Back to Reference Overview](overview.md)
