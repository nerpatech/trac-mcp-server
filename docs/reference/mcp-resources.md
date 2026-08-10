# MCP Resources Reference

The Trac MCP Server exposes wiki pages as resources via URI templates, enabling direct content access without tool calls.

## Resource URIs

| URI Pattern | Description |
|-------------|-------------|
| `trac://wiki/{page_name}` | Read specific wiki page (Markdown by default) |
| `trac://wiki/{page_name}?format=tracwiki` | Read page in raw TracWiki format |
| `trac://wiki/{page_name}?version=N` | Read specific historical version |
| `trac://wiki/{page_name}?instance=/project` | Read from another Trac instance |
| `trac://wiki/_index` | List all wiki pages in hierarchical tree structure |

## Query Parameters

| Parameter | Values | Description |
|-----------|--------|-------------|
| `format` | `markdown` (default), `tracwiki` | Output format for page content |
| `version` | Integer (1+) | Retrieve specific version instead of latest |
| `instance` | Configured name, or a path/URL on the same host | Route to another Trac instance instead of the default -- see [Multiple Instances](configuration.md#multiple-instances) |

## Examples

**Read WikiStart in Markdown:**
```
trac://wiki/WikiStart
```

**Read raw TracWiki:**
```
trac://wiki/WikiStart?format=tracwiki
```

**Read specific version:**
```
trac://wiki/WikiStart?version=5
```

**Read nested page:**
```
trac://wiki/API/Reference
```

**List all pages:**
```
trac://wiki/_index
```

**Read from another instance:**
```
trac://wiki/WikiStart?instance=/other-project
```

## Response Format

**Page Content:**
```
# WikiStart

**Author:** admin
**Version:** 5
**Last Modified:** 2025-01-20 10:00

---

[Page content in Markdown...]
```

**Page Index:**
```
# Wiki Pages

API
|-- Authentication
|-- Reference
`-- Examples
Dev
|-- Setup
`-- Testing
WikiStart
```

## Error Responses

**Page not found:**
```
Error (not_found): Page 'NoSuchPage' not found.

Similar pages: WikiStart, WikiSandbox
```

**Version not found:**
```
Error (invalid_version): Version 99 not found for page 'WikiStart'.

Hint: Use trac://wiki/WikiStart to see the latest version.
```

**Unknown instance:**
```
Error (unknown_instance): Unknown instance 'nope'. Configured instances: default, bcs

Action: Call list_instances to see what is reachable.
```

---

[Back to Reference Overview](overview.md)
