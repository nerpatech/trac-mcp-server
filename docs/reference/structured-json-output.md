# Structured JSON Output

## Overview

Several tools return **dual output**: human-readable text alongside machine-parseable structured JSON. This enables AI agents to process results programmatically while maintaining backward-compatible text output.

## How It Works

MCP tool responses can include both `content` (text) and `structuredContent` (JSON):

```json
{
  "content": [
    {
      "type": "text",
      "text": "Found 3 tickets (showing 3):\n- #42: Fix login issue (status: new, owner: alice)"
    }
  ],
  "structuredContent": {
    "tickets": [
      {"id": 42, "summary": "Fix login issue", "status": "new", "owner": "alice"}
    ],
    "total": 3,
    "showing": 3
  }
}
```

## Backward Compatibility

- **Text output unchanged**: All tools continue to return `content` with `type: "text"` as before
- **Structured JSON is additive**: `structuredContent` is an additional field, not a replacement
- **No breaking changes**: Clients that only read `content[].text` are unaffected

## Tools with Structured JSON Output

| Tool | Structured JSON Fields |
|------|----------------------|
| `get_server_time` | `server_time`, `unix_timestamp`, `timezone` |
| `ticket_search` | `tickets[]`, `total`, `showing` |
| `ticket_get` | `id`, `summary`, `description`, `status`, `owner`, `reporter`, `type`, `priority`, `component`, `milestone`, `keywords`, `cc`, `resolution`, `created`, `modified`, `_ts`, `comments_included`, and (unless `include_comments=false`) `comments[]` (each: `number`, `author`, `timestamp`, `body`), `comment_count`, `comments_shown`, `comments_truncated`, optional `omitted_comment_numbers`, optional `comments_error` |
| `ticket_fields` | `fields[]` (each: `name`, `type`, `label`, `custom`, optional `options`) |
| `ticket_actions` | `actions[]` (each: `name`, `label`, optional `hints`, `input_fields`) |
| `ticket_batch_create` | `created[]`, `failed[]`, `total`, `succeeded`, `failed_count` |
| `ticket_batch_delete` | `deleted[]`, `failed[]`, `total`, `succeeded`, `failed_count` |
| `ticket_batch_update` | `updated[]`, `failed[]`, `total`, `succeeded`, `failed_count` |
| `wiki_get` | `name`, `content`, `version`, `author`, `lastModified` |
| `wiki_recent_changes` | `pages[]` (each: `name`, `author`, `lastModified`, `version`), `since_days` |
| `wiki_file_push` | `page_name`, `action`, `version`, `source_format`, `converted`, `file_path`, `warnings` |
| `wiki_file_pull` | `page_name`, `file_path`, `format`, `version`, `bytes_written`, `converted` |
| `wiki_file_detect_format` | `file_path`, `format`, `encoding`, `size_bytes` |

## Schema Details

Structured JSON schemas follow these conventions:

- **Lists use plural keys**: `tickets`, `actions`, `fields`, `pages`
- **Metadata alongside data**: `total`, `showing`, `since_days` appear at the same level
- **Consistent field names**: `lastModified`, `author`, `version` used consistently across tools
- **No nested depth**: Schemas are flat or one level deep for easy parsing

---

[Back to Reference Overview](overview.md)
