# MCP Tools Reference

The Trac MCP Server provides 28 tools organized into six categories:

- **System:** 2 tools (ping, get_server_time)
- **Tickets:** 11 tools (search, get, create, update, delete, changelog, fields, actions, batch_create, batch_delete, batch_update)
- **Wiki:** 6 tools (get, search, create, update, delete, recent_changes)
- **Wiki File:** 3 tools (push, pull, detect_format)
- **Milestones:** 5 tools (list, get, create, update, delete)
- **Instances:** 1 tool (list_instances)

## Instance Routing

Every tool below also accepts an optional `instance` argument (added
automatically to each tool's schema; omitted from the per-tool parameter
tables below for brevity) that routes the call to another Trac project
instead of the default one -- see
[Multiple Instances](configuration.md#multiple-instances). Call
`list_instances` to discover what's reachable.

---

## ping

**Description:** Test Trac MCP server connectivity, validate configuration (TRAC_URL / TRAC_USERNAME / TRAC_PASSWORD), and return the Trac API version tuple

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| *(none)* | - | - | - | No parameters required |

**Success Response:**
```json
{
  "type": "text",
  "text": "Trac MCP server connected successfully. API version: [1, 1, 6]"
}
```

**Error Response:**
```json
{
  "type": "text",
  "text": "Trac connection failed: [error details]. Check TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD."
}
```

**Example Call:**
```json
{
  "name": "ping",
  "arguments": {}
}
```

---

## get_server_time

**Description:** Get current Trac server time for temporal reasoning and coordination. Returns server timestamp in both ISO 8601 and Unix timestamp formats.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| *(none)* | - | - | - | No parameters required |

**Success Response:**
```json
{
  "type": "text",
  "text": "Server time: 2026-02-05T20:51:27"
}
```

**Structured JSON Output:**
```json
{
  "server_time": "2026-02-05T20:51:27",
  "unix_timestamp": 1770324687,
  "timezone": "server"
}
```

**Error Response:**
```json
{
  "type": "text",
  "text": "Error (server_error): Failed to get server time: [error details]\n\nAction: Check Trac server connectivity and permissions."
}
```

**Implementation Notes:**
- Uses `wiki.getPageInfo("WikiStart")` to retrieve server timestamp from a page that exists by default in Trac installations
- Falls back to first available wiki page if WikiStart doesn't exist
- Parses `lastModified` field from XML-RPC DateTime or string format

**Example Call:**
```json
{
  "name": "get_server_time",
  "arguments": {}
}
```

---

## ticket_search

**Description:** Search tickets with filtering by status, owner, and keywords. Returns ticket IDs with summaries.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | string | No | `"status!=closed"` | Trac query string (e.g., `status=new`, `owner=alice`, `status!=closed&keywords~=urgent`) |
| `max_results` | integer | No | `10` | Maximum results to return (min: 1, max: 100) |

**Success Response:**
```json
{
  "type": "text",
  "text": "Found 25 tickets (showing 10):\n- #42: Fix login timeout issue (status: new, owner: alice)\n- #38: Add user export feature (status: accepted, owner: bob)\n...\n\nUse max_results to see more."
}
```

**Error Responses:**

- **server_error:** XML-RPC communication failure
  ```json
  {
    "type": "text",
    "text": "Error (server_error): Connection refused\n\nAction: Contact Trac administrator or retry later."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_search",
  "arguments": {
    "query": "status=new&component=backend",
    "max_results": 20
  }
}
```

---

## ticket_get

**Description:** Get full ticket details: summary, description, field values and — by default — every comment with its number, author and timestamp, so one call covers the whole ticket. Use ticket_changelog for full field-change history.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number to retrieve (minimum: 1) |
| `raw` | boolean | No | `false` | If true, return description **and comment bodies** in original TracWiki format without converting to Markdown |
| `include_comments` | boolean | No | `true` | Return the ticket's comments alongside its fields. Set `false` when you only need field values or the `_ts` change token before a write |
| `max_comments` | integer | No | `50` | Maximum comment bodies to return (minimum: 1). Ignored when `include_comments` is false |

**Comments.** Comments are read from the ticket's changelog and filtered there: field-only
changes never reach the response, so a ticket whose description was edited does not drag
two full copies of that description along with its comments. Each comment carries the
number Trac itself assigns — the same number `ticket_render_check`'s `comment` parameter
takes, so it can be used directly instead of guessed. Comment numbering skips field-only
changelog entries, so it is *not* a comment's ordinal position in the thread.

**Truncation is loud.** A thread longer than `max_comments` keeps its head and its tail and
drops the middle — a long thread usually has its scope set in the earliest comments and
corrected in the latest. The response then states how many comments exist, how many are
shown, and which comment numbers were omitted; `comment_count` is always the exact total,
never the number shown. If the changelog fetch fails, the ticket still comes back and the
response says plainly that the comments are missing rather than returning silently
without them.

**Success Response:**
```json
{
  "type": "text",
  "text": "Ticket #42: Fix login timeout issue\nStatus: new | Owner: alice | Reporter: bob | Type: defect\nPriority: major | Component: backend | Milestone: v2.0\nKeywords: login, timeout | Cc: carol@example.com\nCreated: 2025-01-15 10:30 | Modified: 2025-01-20 14:22\n\n## Description\nUsers are experiencing timeout errors when logging in during peak hours.\n\n**Steps to reproduce:**\n1. Open login page\n2. Enter credentials\n3. Submit form during 9-10 AM"
}
```

**Structured JSON Output:**
```json
{
  "id": 42,
  "summary": "Fix login timeout issue",
  "status": "new",
  "owner": "alice",
  "reporter": "bob",
  "type": "defect",
  "priority": "major",
  "component": "backend",
  "milestone": "v2.0",
  "keywords": "login, timeout",
  "cc": "carol@example.com",
  "resolution": "",
  "created": "2025-01-15T10:30:00",
  "modified": "2025-01-20T14:22:00",
  "description": "Users are experiencing timeout errors...",
  "comments_included": true,
  "comments": [
    {
      "number": "1",
      "author": "carol",
      "timestamp": "2025-01-16 08:12",
      "body": "Reproduced on staging."
    }
  ],
  "comment_count": 1,
  "comments_shown": 1,
  "comments_truncated": false
}
```

When a thread is truncated, `comments_truncated` is `true` and
`omitted_comment_numbers` lists the dropped comments. When the changelog fetch
fails, `comments_included` is `false` and `comments_error` carries the reason.

**Error Responses:**

- **validation_error:** Missing required parameter
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): ticket_id is required\n\nAction: Provide ticket_id parameter."
  }
  ```

- **not_found:** Ticket does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Ticket #999 not found\n\nAction: Use ticket_search to verify ticket exists."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_get",
  "arguments": {
    "ticket_id": 42,
    "raw": false
  }
}
```

---

## ticket_create

**Description:** Create a new ticket. The description is Markdown by default (converted to TracWiki); pass `format="tracwiki"` to store hand-authored TracWiki verbatim.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `summary` | string | **Yes** | - | Ticket title |
| `description` | string | **Yes** | - | Ticket body. Markdown by default; see `format` |
| `ticket_type` | string | No | `"defect"` | Ticket type: `defect`, `enhancement`, or `task` |
| `priority` | string | No | - | Priority level (e.g., `blocker`, `critical`, `major`, `minor`, `trivial`) |
| `severity` | string | No | - | Severity level (e.g., `critical`, `major`, `minor`) |
| `component` | string | No | - | Component name |
| `milestone` | string | No | - | Target milestone |
| `owner` | string | No | - | Assignee username |
| `cc` | string | No | - | CC email addresses |
| `keywords` | string | No | - | Keywords/tags |
| `format` | string | No | `"markdown"` | Format of the content you supply: `markdown` (converted to TracWiki) or `tracwiki` (stored byte-for-byte, converter skipped). There is no `auto` — the format is declared, never guessed from content |

**Success Response:**
```json
{
  "type": "text",
  "text": "Created ticket #43: Implement user profile page"
}
```

**Error Responses:**

- **validation_error:** Missing required fields
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): summary is required\n\nAction: Provide summary parameter."
  }
  ```

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): TICKET_CREATE permission required\n\nAction: Try adding a comment instead, or contact ticket owner."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_create",
  "arguments": {
    "summary": "Implement user profile page",
    "description": "## Overview\n\nCreate a user profile page with:\n\n- Avatar upload\n- Bio editing\n- Activity history\n\n## Acceptance Criteria\n\n1. User can upload avatar\n2. User can edit bio text\n3. Profile shows recent activity",
    "ticket_type": "enhancement",
    "priority": "major",
    "component": "frontend",
    "milestone": "v2.0"
  }
}
```

---

## ticket_update

**Description:** Update ticket attributes and/or add comments. Uses optimistic locking to prevent conflicts. The comment and the description rewrite are Markdown by default (converted to TracWiki); pass `format="tracwiki"` to store hand-authored TracWiki verbatim — one declaration governs both fields.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number to update (minimum: 1) |
| `comment` | string | No | - | Comment body (optional, max 10000 chars). Markdown by default; see `format` |
| `status` | string | No | - | New status |
| `priority` | string | No | - | New priority |
| `severity` | string | No | - | New severity |
| `component` | string | No | - | New component |
| `milestone` | string | No | - | New milestone |
| `owner` | string | No | - | New owner |
| `resolution` | string | No | - | Resolution (when closing, e.g., `fixed`, `invalid`, `wontfix`) |
| `cc` | string | No | - | CC email addresses |
| `keywords` | string | No | - | Keywords/tags |
| `format` | string | No | `"markdown"` | Format of the content you supply: `markdown` (converted to TracWiki) or `tracwiki` (stored byte-for-byte, converter skipped). There is no `auto` — the format is declared, never guessed from content |

**Success Response:**
```json
{
  "type": "text",
  "text": "Updated ticket #42 (added comment, updated 2 field(s))"
}
```

**Error Responses:**

- **not_found:** Ticket does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Ticket #999 not found\n\nAction: Use ticket_search to verify ticket exists."
  }
  ```

- **version_conflict:** Concurrent modification detected
  ```json
  {
    "type": "text",
    "text": "Error (version_conflict): Ticket has been modified since you loaded it\n\nAction: Fetch current version with ticket_get(ticket_id=N), then retry update."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_update",
  "arguments": {
    "ticket_id": 42,
    "comment": "Fixed in commit abc123. The issue was a missing timeout configuration.",
    "status": "closed",
    "resolution": "fixed"
  }
}
```

---

## ticket_delete

**Description:** Delete a ticket permanently. Warning: This cannot be undone. Requires TICKET_ADMIN permission and `tracopt.ticket.deleter` enabled in trac.ini.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number to delete (minimum: 1) |

**Success Response:**
```json
{
  "type": "text",
  "text": "Deleted ticket #42."
}
```

**Error Responses:**

- **not_found:** Ticket does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Ticket #999 not found\n\nAction: Use ticket_search to verify ticket exists."
  }
  ```

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): [error details]\n\nAction: This tool requires TICKET_ADMIN permission and 'tracopt.ticket.deleter' enabled in trac.ini. Contact Trac administrator."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_delete",
  "arguments": {
    "ticket_id": 42
  }
}
```

---

## ticket_changelog

**Description:** Get ticket change history. Use this to investigate who changed what and when.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number to get history for (minimum: 1) |
| `raw` | boolean | No | `false` | If true, return comment content in original TracWiki format without converting to Markdown |

**Success Response:**
```json
{
  "type": "text",
  "text": "Changelog for ticket #42:\n- 2025-01-15 10:30 by alice: status set to 'new'\n- 2025-01-16 09:15 by bob: owner set to 'alice'\n- 2025-01-20 14:22 by alice: comment: Started investigating this issue.\n    Found the root cause in the session handler."
}
```

**Error Responses:**

- **not_found:** Ticket does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Ticket #999 not found\n\nAction: Use ticket_search to verify ticket exists."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_changelog",
  "arguments": {
    "ticket_id": 42
  }
}
```

---

## ticket_fields

**Description:** Get all ticket field definitions (standard + custom fields). Returns field metadata including name, type, label, options (for select fields), and custom flag.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| *(none)* | - | - | - | No parameters required |

**Success Response:**
```json
{
  "type": "text",
  "text": "Ticket Fields (15 total):\n\nStandard Fields:\n- summary (text): Summary\n- description (textarea): Description\n- status (select): Status [new, accepted, assigned, closed]\n- priority (select): Priority [blocker, critical, major, minor, trivial]\n- component (select): Component [backend, frontend, docs]\n- type (select): Type [defect, enhancement, task]\n- owner (text): Owner\n\nCustom Fields:\n- estimated_hours (text): Estimated Hours"
}
```

**Example Call:**
```json
{
  "name": "ticket_fields",
  "arguments": {}
}
```

---

## ticket_actions

**Description:** Get valid workflow actions for a ticket's current state. Returns available state transitions (e.g., accept, resolve, reassign). Essential for agents to know which actions are possible before updating ticket status.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number to retrieve actions for (minimum: 1) |

**Success Response:**
```json
{
  "type": "text",
  "text": "Available actions for ticket #42:\n\n- leave: leave\n- accept: accept\n- resolve: resolve [requires: action_resolve_resolve_resolution]"
}
```

**Structured JSON Output:**
```json
{
  "actions": [
    {
      "name": "leave",
      "label": "leave"
    },
    {
      "name": "accept",
      "label": "accept"
    },
    {
      "name": "resolve",
      "label": "resolve",
      "input_fields": ["action_resolve_resolve_resolution"]
    }
  ]
}
```

**Error Responses:**

- **validation_error:** Missing required parameter
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): ticket_id is required\n\nAction: Check parameter values and retry."
  }
  ```

- **method_not_available:** Trac instance does not support workflow introspection
  ```json
  {
    "type": "text",
    "text": "Error (method_not_available): ticket.getActions() not available on this Trac instance\n\nAction: This Trac instance may not support workflow introspection via XML-RPC. Check Trac version and enabled components."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_actions",
  "arguments": {
    "ticket_id": 42
  }
}
```

---

## ticket_batch_create

**Description:** Create multiple tickets in a single batch operation. Best-effort: all items attempted, per-item results reported. Bounded by TRAC_MAX_PARALLEL_REQUESTS semaphore. Descriptions are Markdown by default; `format` sits at the call level and governs every item in the batch.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `tickets` | array | **Yes** | - | List of ticket objects to create |
| `format` | string | No | `"markdown"` | Format of the content you supply: `markdown` (converted to TracWiki) or `tracwiki` (stored byte-for-byte, converter skipped). There is no `auto` — the format is declared, never guessed from content |

Each ticket object:

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `summary` | string | **Yes** | - | Ticket title |
| `description` | string | **Yes** | - | Ticket body. Markdown by default; see the call-level `format` |
| `ticket_type` | string | No | `"defect"` | Ticket type |
| `priority` | string | No | - | Priority level |
| `component` | string | No | - | Component name |
| `milestone` | string | No | - | Target milestone |
| `owner` | string | No | - | Assignee username |
| `keywords` | string | No | - | Keywords/tags |
| `cc` | string | No | - | CC email addresses |

**Success Response:**
```json
{
  "type": "text",
  "text": "Batch create: 2/3 succeeded, 1 failed.\n\nCreated:\n  - #101: First ticket\n  - #102: Second ticket\n\nFailed:\n  - [index 2] Third ticket: summary is required"
}
```

**Structured JSON Output:**
```json
{
  "created": [
    {"id": 101, "summary": "First ticket"},
    {"id": 102, "summary": "Second ticket"}
  ],
  "failed": [
    {"index": 2, "summary": "Third ticket", "error": "summary is required"}
  ],
  "total": 3,
  "succeeded": 2,
  "failed_count": 1
}
```

**Error Responses:**

- **validation_error:** Empty tickets or batch size exceeded
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): tickets list is required and cannot be empty\n\nAction: Provide a non-empty tickets array."
  }
  ```

  ```json
  {
    "type": "text",
    "text": "Error (validation_error): Batch size 600 exceeds maximum 500. Split into smaller batches.\n\nAction: Reduce the number of tickets per request."
  }
  ```

**Implementation Notes:**
- Batch size limited by `TRAC_MAX_BATCH_SIZE` (default 500, max 10000)
- Parallelism bounded by `TRAC_MAX_PARALLEL_REQUESTS` semaphore
- Descriptions are converted from Markdown to TracWiki before creation

**Example Call:**
```json
{
  "name": "ticket_batch_create",
  "arguments": {
    "tickets": [
      {
        "summary": "Implement user avatars",
        "description": "Add avatar upload support to user profiles.",
        "ticket_type": "enhancement",
        "priority": "major"
      },
      {
        "summary": "Fix login timeout",
        "description": "Users experience timeouts during peak hours.",
        "priority": "critical",
        "component": "backend"
      }
    ]
  }
}
```

---

## ticket_batch_delete

**Description:** Delete multiple tickets in a single batch operation. Best-effort: all items attempted, per-item results reported. Requires TICKET_ADMIN permission.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_ids` | array of integers | **Yes** | - | List of ticket IDs to delete (minimum: 1 per item) |

**Success Response:**
```json
{
  "type": "text",
  "text": "Batch delete: 2/2 succeeded, 0 failed.\n\nDeleted:\n  - #101\n  - #102"
}
```

**Structured JSON Output:**
```json
{
  "deleted": [101, 102],
  "failed": [],
  "total": 2,
  "succeeded": 2,
  "failed_count": 0
}
```

**Error Responses:**

- **validation_error:** Empty ticket_ids or batch size exceeded
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): ticket_ids list is required and cannot be empty\n\nAction: Provide a non-empty ticket_ids array."
  }
  ```

  ```json
  {
    "type": "text",
    "text": "Error (validation_error): Batch size 600 exceeds maximum 500. Split into smaller batches.\n\nAction: Reduce the number of ticket IDs per request."
  }
  ```

**Implementation Notes:**
- Batch size limited by `TRAC_MAX_BATCH_SIZE` (default 500, max 10000)
- Parallelism bounded by `TRAC_MAX_PARALLEL_REQUESTS` semaphore
- Requires TICKET_ADMIN permission and `tracopt.ticket.deleter` enabled in trac.ini

**Example Call:**
```json
{
  "name": "ticket_batch_delete",
  "arguments": {
    "ticket_ids": [101, 102, 103]
  }
}
```

---

## ticket_batch_update

**Description:** Update multiple tickets in a single batch operation. Best-effort: all items attempted, per-item results reported. Comments are Markdown by default; `format` sits at the call level and governs every item in the batch. (This tool takes no per-item `description`, so `format` governs comments only here.)

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `updates` | array | **Yes** | - | List of update objects with ticket_id and fields to change |
| `format` | string | No | `"markdown"` | Format of the content you supply: `markdown` (converted to TracWiki) or `tracwiki` (stored byte-for-byte, converter skipped). There is no `auto` — the format is declared, never guessed from content |

Each update object:

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number to update (minimum: 1) |
| `comment` | string | No | - | Comment body. Markdown by default; see the call-level `format` |
| `status` | string | No | - | New status |
| `resolution` | string | No | - | Resolution (when closing) |
| `priority` | string | No | - | New priority |
| `component` | string | No | - | New component |
| `milestone` | string | No | - | New milestone |
| `owner` | string | No | - | New owner |
| `keywords` | string | No | - | Keywords/tags |
| `cc` | string | No | - | CC email addresses |

**Success Response:**
```json
{
  "type": "text",
  "text": "Batch update: 3/3 succeeded, 0 failed.\n\nUpdated:\n  - #42\n  - #43\n  - #44"
}
```

**Structured JSON Output:**
```json
{
  "updated": [42, 43, 44],
  "failed": [],
  "total": 3,
  "succeeded": 3,
  "failed_count": 0
}
```

**Error Responses:**

- **validation_error:** Empty updates or batch size exceeded
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): updates list is required and cannot be empty\n\nAction: Provide a non-empty updates array."
  }
  ```

  ```json
  {
    "type": "text",
    "text": "Error (validation_error): Batch size 600 exceeds maximum 500. Split into smaller batches.\n\nAction: Reduce the number of updates per request."
  }
  ```

**Implementation Notes:**
- Batch size limited by `TRAC_MAX_BATCH_SIZE` (default 500, max 10000)
- Parallelism bounded by `TRAC_MAX_PARALLEL_REQUESTS` semaphore
- Comments are converted from Markdown to TracWiki before submission

**Example Call:**
```json
{
  "name": "ticket_batch_update",
  "arguments": {
    "updates": [
      {
        "ticket_id": 42,
        "comment": "Fixed in commit abc123.",
        "status": "closed",
        "resolution": "fixed"
      },
      {
        "ticket_id": 43,
        "priority": "critical",
        "milestone": "v2.1"
      }
    ]
  }
}
```

---

## wiki_get

**Description:** Get wiki page content with Markdown output. Returns full content with metadata (version, author, modified date).

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **Yes** | - | Wiki page name to retrieve |
| `version` | integer | No | *(latest)* | Specific version to retrieve (minimum: 1) |
| `raw` | boolean | No | `false` | If true, return original TracWiki format without converting to Markdown |

**Success Response:**
```json
{
  "type": "text",
  "text": "# WikiStart\nVersion: 5 | Author: admin | Modified: 2025-01-20 10:00\n----\n\n# Welcome to the Project\n\nThis is the main wiki page.\n\n## Quick Links\n\n- [Development Guide](wiki:DevelopmentGuide)\n- [API Reference](wiki:API/Reference)"
}
```

**Error Responses:**

- **not_found:** Page does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Wiki page 'NoSuchPage' does not exist\n\nAction: Use wiki_search to find pages similar to 'NoSuchPage'."
  }
  ```

**Example Call:**
```json
{
  "name": "wiki_get",
  "arguments": {
    "page_name": "DevelopmentGuide",
    "version": 3
  }
}
```

---

## wiki_search

**Description:** Search wiki pages by content with relevance ranking. Returns snippets showing matched text.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | string | **Yes** | - | Search query string |
| `prefix` | string | No | - | Filter to pages starting with this prefix (namespace filter) |
| `limit` | integer | No | `10` | Maximum results per page (min: 1, max: 50) |
| `cursor` | string | No | - | Pagination cursor from previous response |
| `raw` | boolean | No | `false` | If true, return snippets in original TracWiki format without converting to Markdown |

**Success Response:**
```json
{
  "type": "text",
  "text": "Found 15 wiki pages (showing 1-10):\n\n**DevelopmentGuide**\n  ...follow the **coding standards** defined in this document...\n\n**API/Authentication**\n  ...use JWT tokens for **authentication**...\n\nUse cursor 'eyJvZmZzZXQiOjEwLCJ0b3RhbCI6MTV9' to get next page."
}
```

**Example Call:**
```json
{
  "name": "wiki_search",
  "arguments": {
    "query": "authentication",
    "prefix": "API/",
    "limit": 10
  }
}
```

---

## wiki_create

**Description:** Create a new wiki page. Content is Markdown by default (converted to TracWiki); pass `format="tracwiki"` to store hand-authored TracWiki verbatim. Fails if the page exists (use wiki_update instead).

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **Yes** | - | Wiki page name to create |
| `content` | string | **Yes** | - | Page content. Markdown by default; see `format` |
| `comment` | string | No | - | Change comment |
| `format` | string | No | `"markdown"` | Format of the content you supply: `markdown` (converted to TracWiki) or `tracwiki` (stored byte-for-byte, converter skipped). There is no `auto` — the format is declared, never guessed from content |

**Success Response:**
```json
{
  "type": "text",
  "text": "Created wiki page 'NewFeature/Design' (version 1)"
}
```

**Success Response with Warnings:**
```json
{
  "type": "text",
  "text": "Created wiki page 'NewFeature/Design' (version 1)\n\nConversion warnings:\n- HTML tags detected - these may not render correctly in TracWiki."
}
```

**Error Responses:**

- **already_exists:** Page already exists
  ```json
  {
    "type": "text",
    "text": "Error (already_exists): Page 'WikiStart' already exists\n\nAction: Use wiki_update to modify existing page, or choose a different name."
  }
  ```

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): WIKI_CREATE permission required\n\nAction: Contact Trac administrator for write access to wiki pages."
  }
  ```

**Example Call:**
```json
{
  "name": "wiki_create",
  "arguments": {
    "page_name": "NewFeature/Design",
    "content": "# Feature Design\n\n## Overview\n\nThis document describes the design for the new feature.\n\n## Requirements\n\n1. Must support multiple users\n2. Must have audit logging\n3. Must integrate with existing auth",
    "comment": "Initial design document"
  }
}
```

---

## wiki_update

**Description:** Update an existing wiki page with optimistic locking (requires version for conflict detection). Content is Markdown by default (converted to TracWiki); pass `format="tracwiki"` to store hand-authored TracWiki verbatim.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **Yes** | - | Wiki page name to update |
| `content` | string | **Yes** | - | Page content. Markdown by default; see `format` |
| `version` | integer | **Yes** | - | Current page version for optimistic locking (minimum: 1) |
| `comment` | string | No | - | Change comment |
| `format` | string | No | `"markdown"` | Format of the content you supply: `markdown` (converted to TracWiki) or `tracwiki` (stored byte-for-byte, converter skipped). There is no `auto` — the format is declared, never guessed from content |

**Success Response:**
```json
{
  "type": "text",
  "text": "Updated wiki page 'DevelopmentGuide' to version 6"
}
```

**Error Responses:**

- **version_conflict:** Page was modified by another user
  ```json
  {
    "type": "text",
    "text": "Error (version_conflict): Page has been modified. Current version is 7, you tried to update version 5.\n\nAction: Fetch current content with wiki_get(page_name='DevelopmentGuide'), then retry update with version=7."
  }
  ```

- **not_found:** Page does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Wiki page 'NoSuchPage' does not exist\n\nAction: Use wiki_search to find pages similar to 'NoSuchPage'."
  }
  ```

**Example Call:**
```json
{
  "name": "wiki_update",
  "arguments": {
    "page_name": "DevelopmentGuide",
    "content": "# Development Guide\n\n## Updated Section\n\nNew content here...",
    "version": 5,
    "comment": "Updated coding standards section"
  }
}
```

---

## wiki_delete

**Description:** Delete a wiki page. Warning: This cannot be undone. Requires WIKI_DELETE permission.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **Yes** | - | Wiki page name to delete |

**Success Response:**
```json
{
  "type": "text",
  "text": "Deleted wiki page 'OldPage'."
}
```

**Error Responses:**

- **not_found:** Page does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Wiki page 'NoSuchPage' does not exist\n\nAction: Use wiki_search to find available pages."
  }
  ```

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): WIKI_DELETE permission required\n\nAction: Contact Trac administrator for write access to wiki pages."
  }
  ```

**Example Call:**
```json
{
  "name": "wiki_delete",
  "arguments": {
    "page_name": "OldPage"
  }
}
```

---

## wiki_recent_changes

**Description:** Get recently modified wiki pages sorted by modification date (newest first). Useful for finding stale or recently updated documentation.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `since_days` | integer | No | `30` | Return pages modified within this many days (minimum: 1) |
| `limit` | integer | No | `20` | Maximum results to return (min: 1, max: 100) |

**Success Response:**
```json
{
  "type": "text",
  "text": "Wiki pages modified in last 30 days:\n\n- WikiStart (modified: 2026-02-05 20:51 by admin)\n- DevelopmentGuide (modified: 2026-02-03 14:22 by alice)\n- API/Reference (modified: 2026-01-28 09:15 by bob)"
}
```

**Structured JSON Output:**
```json
{
  "pages": [
    {
      "name": "WikiStart",
      "author": "admin",
      "lastModified": "2026-02-05 20:51",
      "version": 12
    },
    {
      "name": "DevelopmentGuide",
      "author": "alice",
      "lastModified": "2026-02-03 14:22",
      "version": 5
    }
  ],
  "since_days": 30
}
```

**Empty Response:**
```json
{
  "type": "text",
  "text": "No wiki pages modified in the last 30 days."
}
```

**Example Call:**
```json
{
  "name": "wiki_recent_changes",
  "arguments": {
    "since_days": 7,
    "limit": 10
  }
}
```

---

## wiki_file_push

**Description:** Push a local file to a Trac wiki page. Reads the file, auto-detects format (Markdown/TracWiki), converts if needed, and creates or updates the wiki page.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `file_path` | string | **Yes** | - | Absolute path to local file |
| `page_name` | string | **Yes** | - | Target wiki page name |
| `comment` | string | No | `""` | Change comment |
| `format` | string | No | `"auto"` | Source format override: `auto`, `markdown`, or `tracwiki`. Default auto-detects from extension then content |
| `strip_frontmatter` | boolean | No | `true` | Strip YAML frontmatter from .md files before pushing |

**Success Response:**
```json
{
  "type": "text",
  "text": "Created wiki page 'Docs/MyPage' (version 1)"
}
```

**Structured JSON Output:**
```json
{
  "page_name": "Docs/MyPage",
  "action": "created",
  "version": 1,
  "source_format": "markdown",
  "converted": true,
  "file_path": "/path/to/file.md",
  "warnings": []
}
```

**Error Responses:**

- **validation_error:** Missing required parameter or invalid file path
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): file_path is required\n\nAction: Provide file_path parameter."
  }
  ```

- **validation_error:** File not found
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): File not found: /path/to/missing.md\n\nAction: Check parameter values and retry."
  }
  ```

**Implementation Notes:**
- Auto-detects format from file extension (.md/.markdown = Markdown, .wiki/.tracwiki = TracWiki) with content heuristic fallback
- Strips YAML frontmatter by default (first `---` block)
- Creates new page if it doesn't exist, updates with optimistic locking if it does
- Handles Trac instances that return 0 (int) instead of Fault for non-existent pages

**Example Call:**
```json
{
  "name": "wiki_file_push",
  "arguments": {
    "file_path": "/home/user/docs/design.md",
    "page_name": "Docs/Design",
    "comment": "Push design doc to wiki",
    "format": "auto",
    "strip_frontmatter": true
  }
}
```

---

## wiki_file_pull

**Description:** Pull a Trac wiki page to a local file. Fetches page content, converts to the requested format, and writes to the specified path.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **Yes** | - | Wiki page name to pull |
| `file_path` | string | **Yes** | - | Absolute path for output file |
| `format` | string | No | `"markdown"` | Output format: `markdown` or `tracwiki` |
| `version` | integer | No | *(latest)* | Specific page version to pull (minimum: 1) |

**Success Response:**
```json
{
  "type": "text",
  "text": "Pulled wiki page 'Docs/Design' (version 3) to /home/user/docs/design.md (4521 bytes, format=markdown)"
}
```

**Structured JSON Output:**
```json
{
  "page_name": "Docs/Design",
  "file_path": "/home/user/docs/design.md",
  "format": "markdown",
  "version": 3,
  "bytes_written": 4521,
  "converted": true
}
```

**Error Responses:**

- **not_found:** Wiki page does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Wiki page 'NoSuchPage' does not exist\n\nAction: Use wiki_search to find available pages."
  }
  ```

- **validation_error:** Invalid output path
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): Output parent directory not found: /nonexistent/dir\n\nAction: Check parameter values and retry."
  }
  ```

**Implementation Notes:**
- Converts TracWiki to Markdown by default; use `format: "tracwiki"` to get raw content
- Parent directory of `file_path` must exist
- Fetches page info separately for version metadata

**Example Call:**
```json
{
  "name": "wiki_file_pull",
  "arguments": {
    "page_name": "Docs/Design",
    "file_path": "/home/user/docs/design.md",
    "format": "markdown"
  }
}
```

---

## wiki_file_detect_format

**Description:** Detect the format of a local file (Markdown or TracWiki). Uses file extension first, then content-based heuristic detection.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `file_path` | string | **Yes** | - | Absolute path to file to analyze |

**Success Response:**
```json
{
  "type": "text",
  "text": "File: /home/user/docs/design.md\nFormat: markdown\nEncoding: utf-8\nSize: 4521 bytes"
}
```

**Structured JSON Output:**
```json
{
  "file_path": "/home/user/docs/design.md",
  "format": "markdown",
  "encoding": "utf-8",
  "size_bytes": 4521
}
```

**Error Responses:**

- **validation_error:** File not found or invalid path
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): File not found: /path/to/missing.md\n\nAction: Check parameter values and retry."
  }
  ```

**Implementation Notes:**
- Extension-first detection: `.md`/`.markdown` = Markdown, `.wiki`/`.tracwiki` = TracWiki
- Falls back to content-based heuristic for ambiguous extensions (.txt, etc.)
- Uses charset-normalizer for encoding detection
- Read-only operation (no modifications)

**Example Call:**
```json
{
  "name": "wiki_file_detect_format",
  "arguments": {
    "file_path": "/home/user/docs/design.md"
  }
}
```

---

## milestone_list

**Description:** List all milestone names. Returns array of milestone names. Requires TICKET_VIEW permission.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| *(none)* | - | - | - | No parameters required |

**Success Response:**
```json
{
  "type": "text",
  "text": "v1.0\nv1.1\nv2.0\nFuture"
}
```

**Example Call:**
```json
{
  "name": "milestone_list",
  "arguments": {}
}
```

---

## milestone_get

**Description:** Get milestone details by name. Returns name, due date, completion date, and description. Requires TICKET_VIEW permission.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `name` | string | **Yes** | - | Milestone name |
| `raw` | boolean | No | `false` | If true, return description in original TracWiki format without converting to Markdown |

**Success Response:**
```json
{
  "type": "text",
  "text": "Milestone: v2.0\nDue: 2025-06-30T23:59:59\nCompleted: (Not set)\n\n## Description\nMajor release with new features:\n\n- User profiles\n- Dashboard redesign\n- API v2"
}
```

**Error Responses:**

- **not_found:** Milestone does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Milestone 'v3.0' not found\n\nAction: Use milestone_list to verify milestone exists."
  }
  ```

**Example Call:**
```json
{
  "name": "milestone_get",
  "arguments": {
    "name": "v2.0"
  }
}
```

---

## milestone_create

**Description:** Create a new milestone. Requires TICKET_ADMIN permission.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `name` | string | **Yes** | - | Milestone name |
| `attributes` | object | No | `{}` | Milestone attributes |
| `attributes.due` | string | No | - | Due date in ISO 8601 format (e.g., `2026-12-31T23:59:59` or `2026-12-31`) |
| `attributes.completed` | string/integer | No | - | Completion date in ISO 8601 format, or `0` for not completed |
| `attributes.description` | string | No | - | Milestone description |

**Success Response:**
```json
{
  "type": "text",
  "text": "Created milestone: v3.0"
}
```

**Error Responses:**

- **already_exists:** Milestone already exists
  ```json
  {
    "type": "text",
    "text": "Error (already_exists): Milestone 'v2.0' already exists\n\nAction: Use milestone_update to modify existing milestone, or choose different name."
  }
  ```

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): TICKET_ADMIN permission required (requires TICKET_ADMIN for create/update/delete)\n\nAction: Contact Trac administrator for TICKET_ADMIN permission."
  }
  ```

**Example Call:**
```json
{
  "name": "milestone_create",
  "arguments": {
    "name": "v3.0",
    "attributes": {
      "due": "2026-12-31T23:59:59",
      "description": "Major release with breaking changes"
    }
  }
}
```

---

## milestone_update

**Description:** Update an existing milestone. Requires TICKET_ADMIN permission.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `name` | string | **Yes** | - | Milestone name |
| `attributes` | object | **Yes** | - | Milestone attributes to update |
| `attributes.due` | string | No | - | Due date in ISO 8601 format |
| `attributes.completed` | string/integer | No | - | Completion date in ISO 8601 format, or `0` for not completed |
| `attributes.description` | string | No | - | Milestone description |

**Success Response:**
```json
{
  "type": "text",
  "text": "Updated milestone 'v2.0' (updated 2 field(s): due, description)"
}
```

**Error Responses:**

- **not_found:** Milestone does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Milestone 'v3.0' not found\n\nAction: Use milestone_list to verify milestone exists."
  }
  ```

**Example Call:**
```json
{
  "name": "milestone_update",
  "arguments": {
    "name": "v2.0",
    "attributes": {
      "due": "2025-09-30T23:59:59",
      "completed": "2025-08-15T12:00:00"
    }
  }
}
```

---

## milestone_delete

**Description:** Delete a milestone by name. Requires TICKET_ADMIN permission. Warning: This cannot be undone.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `name` | string | **Yes** | - | Milestone name |

**Success Response:**
```json
{
  "type": "text",
  "text": "Deleted milestone: v1.0-beta"
}
```

**Error Responses:**

- **not_found:** Milestone does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Milestone 'v1.0-beta' not found\n\nAction: Use milestone_list to verify milestone exists."
  }
  ```

**Example Call:**
```json
{
  "name": "milestone_delete",
  "arguments": {
    "name": "v1.0-beta"
  }
}
```

---

## list_instances

**Description:** List Trac instances reachable from this server: named instances from configuration, and (when `discover=true`) other projects visible on the same Trac host's project index. Use the returned path (e.g. `/project`) as the `instance` argument on any other tool to target that project. Always available -- no Trac permission required.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `discover` | boolean | No | `true` | Scrape the Trac host's project index for other reachable projects |

**Success Response:**
```json
{
  "type": "text",
  "text": "Default instance: http://trac.example.com:8000/main-project\n\nConfigured:\n  default: http://trac.example.com:8000/main-project (default)\n  bcs: http://trac.example.com:8000/bcs\n\nDiscovered on host:\n  /main-project: Main Project [configured]\n  /bcs: BCS [configured]\n  /other: Other Project"
}
```
```json
{
  "configured": [
    {
      "name": "default",
      "url": "http://trac.example.com:8000/main-project",
      "is_default": true,
      "credentials": "explicit"
    },
    {
      "name": "bcs",
      "url": "http://trac.example.com:8000/bcs",
      "is_default": false,
      "credentials": "inherited"
    }
  ],
  "default": "http://trac.example.com:8000/main-project",
  "discovered": [
    {
      "path": "/main-project",
      "title": "Main Project",
      "description": "",
      "url": "http://trac.example.com:8000/main-project",
      "configured": true
    },
    {
      "path": "/other",
      "title": "Other Project",
      "description": "",
      "url": "http://trac.example.com:8000/other",
      "configured": false
    }
  ]
}
```

The `credentials` field is always `"explicit"` or `"inherited"` -- passwords are never included in the response. The `discovered` key is omitted when `discover` is `false` or the host's project index could not be scraped.

**Example Call:**
```json
{
  "name": "list_instances",
  "arguments": {}
}
```

---

## ticket_render_check

**Description:** Fetch a ticket's LIVE rendered page (description, and each comment by default) and return structured facts + warnings, replacing the curl+grep workaround `Rules/trac/RenderVerify` otherwise forces after every ticket write. Every outbound link grouped by realm (ticket/wiki/external) with its resolved target, which wiki links carry Trac's `missing` class (dead target), code block counts, and the same defect checks `convert_preview` runs (escaped link targets, a cross-instance reference stuck in a code span, an unconfigured InterTrac prefix, and -- render-only -- markup that survived as literal text instead of converting). Use this right after any `ticket_update`/`ticket_create` write to verify what Trac actually rendered, not just what was stored.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number to render-check (minimum: 1) |
| `include_comments` | boolean | No | `true` | Also render-check every comment on the ticket, not just the description |
| `comment` | integer | No | - | Check only this one comment number instead of every comment (still includes the description); ignored when `include_comments` is false |
| `check_targets` | boolean | No | `true` | Live-probe cross-instance wiki targets found in the render (capped, short timeout) to catch a target that renders identically whether it exists or not |
| `include_html` | boolean | No | `false` | Include each section's rendered HTML in the response (capped at 20KB per section, with `html_truncated` set if cut) -- off by default, since the structured links/warnings are the point of this tool |

**Success Response:**
```json
{
  "type": "text",
  "text": "ticket/57: 0 error(s), 1 warning(s) across 2 section(s).\n\n[description] No warnings.\n[comment 1] [warning] unconfigured_intertrac_prefix: 'zzznotaprefix:wiki:SomePage' does not match any configured InterTrac prefix -- it will render as plain text, not a link.\n\nLinks: 1 ticket, 2 wiki, 1 external."
}
```
```json
{
  "target": "ticket/57",
  "sections": [
    {
      "kind": "description",
      "ref": null,
      "source_paired": true,
      "links": {
        "ticket": [],
        "wiki": [{"href": "http://.../wiki/Index", "resolved_url": null, "text": "Index", "classes": ["wiki"], "title": null, "missing": false}],
        "external": [{"href": "http://.../intertrac/wiki%3AReference/trac/InterTrac", "resolved_url": "http://.../wiki/Reference/trac/InterTrac", "text": "...", "classes": ["ext-link"], "title": "wiki:Reference/trac/InterTrac in Automated Project Manager", "missing": false}],
        "other": []
      },
      "code_blocks": [{"highlighted": true, "lines": 3}],
      "warnings": [],
      "html": null,
      "html_truncated": false
    }
  ],
  "stats": {
    "sections": 2,
    "anchors": 4,
    "warnings": 1,
    "errors": 0,
    "targets_checked": 1,
    "targets_skipped": 0
  }
}
```

Each anchor's `resolved_url` is populated only for a probed InterTrac wiki-dispatcher href (from `check_targets`'s live probe) -- it's the real page the dispatcher redirects to, not the `/intertrac/wiki%3A...` href itself. `missing: true` marks a link Trac itself rendered with the `missing` class (a dead local wiki target). Realm grouping comes from Trac's own rendered `class` attribute, never from parsing the href.

**Error Responses:**

- **not_found:** Ticket does not exist, or the requested comment number wasn't found
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Ticket #999 not found.\n\nAction: Use ticket_search to verify ticket exists."
  }
  ```
- **validation_error:** `ticket_id` missing

**Example Call:**
```json
{
  "name": "ticket_render_check",
  "arguments": {
    "ticket_id": 57,
    "check_targets": true
  }
}
```

---

## wiki_render_check

**Description:** Fetch a wiki page's LIVE rendered HTML and return structured facts + warnings, replacing the curl+grep workaround `Rules/trac/RenderVerify` otherwise forces after every wiki write. Same link inventory, code block counts, and defect checks as `ticket_render_check`. Use this right after any `wiki_update`/`wiki_create` write.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **Yes** | - | Wiki page name to render-check |
| `version` | integer | No | - | Specific version to check (defaults to latest) |
| `check_targets` | boolean | No | `true` | Live-probe cross-instance wiki targets found in the render |
| `include_html` | boolean | No | `false` | Include the rendered HTML in the response (capped at 20KB, with `html_truncated` set if cut) |

**Success Response:** same shape as `ticket_render_check`, with a single `"kind": "page"` section and `"target": "wiki/<page_name>"`.

**Error Responses:**

- **not_found:** Page does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Wiki page \"DoesNotExist\" does not exist\n\nAction: Use wiki_search to find available pages."
  }
  ```
- **validation_error:** `page_name` missing

**Example Call:**
```json
{
  "name": "wiki_render_check",
  "arguments": {
    "page_name": "Rules/trac/RenderVerify"
  }
}
```

---

[Back to Reference Overview](overview.md)
