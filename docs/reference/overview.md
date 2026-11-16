# trac-mcp-server Reference

Standalone MCP (Model Context Protocol) server for Trac project management integration. This server enables AI agents and MCP clients to interact with Trac instances via standardized tools for tickets, wiki pages, milestones, and search.

For quick start and installation, see the [README](../../README.md).

---

## Reference Sections

| Section | File | Description |
|---------|------|-------------|
| MCP Tools | [mcp-tools.md](mcp-tools.md) | Per-tool reference: parameters, results and examples |
| MCP Resources | [mcp-resources.md](mcp-resources.md) | Wiki page resources via URI templates |
| CLI Reference | [cli.md](cli.md) | Command-line interface documentation |
| Configuration | [configuration.md](configuration.md) | Config sources, file format, environment variables |
| HTTP Transport | [http-transport.md](http-transport.md) | Streamable HTTP transport: endpoints, auth, bind safety, reverse proxies |
| Error Handling | [error-handling.md](error-handling.md) | Error types, response format, corrective actions |
| Format Conversion | [format-conversion.md](format-conversion.md) | Markdown to/from TracWiki conversion rules and mappings |
| Troubleshooting | [troubleshooting.md](troubleshooting.md) | Common errors, solutions, debug mode |
| Structured JSON Output | [structured-json-output.md](structured-json-output.md) | Dual output format for programmatic consumption |
| Tool Architecture | [tool-architecture.md](tool-architecture.md) | Internal tool code structure, ToolSpec/ToolRegistry, permission filtering |
| Live Testing | [live-testing.md](live-testing.md) | E2E test harness and agent scenario tests: tool testing, permission scenarios, reference output |

## Additional Documentation

| Document | Location | Description |
|----------|----------|-------------|
| Deployment Guide | [deployment.md](../deployment.md) | Installation, MCP client configuration, credential management |
| Permissions Guide | [permissions.md](../permissions.md) | Required Trac permissions, security considerations |
| auto-pm | [auto-pm](https://github.com/nerpatech/auto-pm) | Automated project management client that uses trac-mcp-server to manage Trac tickets and milestones |

---

*Reference documentation for trac-mcp-server*
