# MCP Resources Reference

The Trac MCP Server exposes wiki pages as resources via URI templates, enabling direct content access without tool calls.

`trac://wiki/{page_name}` is a resource template: a client finds it with `resources/templates/list`, not `resources/list`, which holds only the concrete `trac://wiki/_index`.

## When Resources Are Served

The wiki resources are served exactly when the `wiki_get` tool is, because they read the same pages and go through the same filters (Trac /trac_mcp_server #273):

| Setting | `wiki_get` | Wiki resources |
|---------|-----------|----------------|
| No filter | served | listed and readable |
| `--permissions-file` with `WIKI_VIEW` | served | listed and readable |
| `--permissions-file` without `WIKI_VIEW` (e.g. the `ticket_manager` profile) | dropped | withdrawn |
| `--read-only` | served (a read stays) | listed and readable |
| `wiki_get` in `disabled_tools` | disabled | withdrawn |

When withdrawn, `resources/list` and `resources/templates/list` return nothing, and a read of `trac://wiki/...` is refused as an unknown resource type, the way a filtered-out tool is refused as an unknown tool, so a hidden resource looks no different from a missing one.

## Resource URIs

| URI Pattern | Description |
|-------------|-------------|
| `trac://wiki/{page_name}` | Read specific wiki page as stored (TracWiki, byte-for-byte) |
| `trac://wiki/{page_name}?version=N` | Read specific historical version |
| `trac://wiki/{page_name}?instance=/project` | Read from another Trac instance |
| `trac://wiki/_index` | List all wiki pages in hierarchical tree structure |

## Query Parameters

| Parameter | Values | Description |
|-----------|--------|-------------|
| `version` | Integer (1+) | Retrieve specific version instead of latest. Anything that is not a whole number from 1 to 2147483647 (`abc`, `0`, `-1`, `1.5`, an empty `?version=`, a longer number) returns `Error (validation_error)`; omit the parameter for the latest |
| `instance` | Configured name, or a path/URL on the same host | Route to another Trac instance instead of the default -- see [Multiple Instances](configuration.md#multiple-instances) |

## Examples

**Read WikiStart:**
```
trac://wiki/WikiStart
```

Content comes back as stored -- TracWiki, byte-for-byte. `?format=tracwiki`
is accepted (it is what comes back anyway); any other `format` value, such
as `markdown`, was removed with the rest of the Markdown read path (ticket
#69) and is reported as an error rather than ignored, so a stale caller is
told instead of quietly handed something else. To convert the result, use the
`trac-convert` binary.

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

**Invalid version:**
```
Error (validation_error): version='abc' is not a version number -- it must be a whole number from 1 to 2147483647.

Hint: use ?version=N with N from 1 up, or drop the version query parameter to read the latest version.
```

**Unknown instance:**
```
Error (unknown_instance): Unknown instance 'nope'. Configured instances: default, bcs

Action: Call list_instances to see what is reachable.
```

---

[Back to Reference Overview](overview.md)
