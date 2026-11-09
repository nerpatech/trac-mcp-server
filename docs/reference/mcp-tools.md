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

**Every result names the instance it reached.** Omitting the argument falls
back to the server's default instance, which used to be silent: a page or
ticket that exists on both the intended project and the default one was
written to the wrong one with no error. Each result now carries `instance`
(the resolved path, e.g. `/bcs`) and `instance_source` (`explicit` when the
caller passed the argument, `server_default` when the fallback answered), in
`structuredContent` when the tool returns it and as a trailing
`[instance: /bcs (explicit)]` content block either way. Errors are annotated
too -- that is where it matters most, since "ticket does not exist" and
"wrong instance" read identically without it.

## Write Gate Status

Every write that stores TracWiki (`ticket_create`, `ticket_update`,
`ticket_comment_edit`, `ticket_comment_reply`, `wiki_create`, `wiki_update`,
`wiki_file_push`, `milestone_create`, `milestone_update`, and per item
`ticket_batch_create` / `ticket_batch_update`) passes through the write-time
link gate, which refuses a write carrying a broken link with
`link_check_failed`. The gate has a daemon-wide emergency off switch,
`TRAC_WRITE_GATE=0` (or `write_gate: false` in the YAML config); every routed
instance inherits it.

**A disabled gate says so.** With the switch off, each of those writes still
succeeds, and its response carries this line (the batch tools add it once
per call, not once per item):

```
NOTE: the write gate is OFF on this daemon (TRAC_WRITE_GATE=0, or write_gate: false in its config file), so this write was NOT link-checked -- not verified clean.
```

The line appears whether or not the write carried any text: it reports the
daemon's state. With the gate on it never appears.

**The note is in `structuredContent` too.** Claude Code shows a tool result's
`structuredContent` in preference to its text block whenever the handler
returns a dict, so a note carried only in the text is invisible there.
`wiki_file_push`, `ticket_batch_create` and `ticket_batch_update` therefore
carry it as `gate_notes`, a list of strings (the OFF line when the gate is
off, empty otherwise). `wiki_file_push`'s `gate_notes` also holds the
gate-on advisory lines the push appends to its text. The other write tools
return text only, which Claude Code does show.

`list_instances` and `ping` report the switch too, as `write_gate` (top-level
in `structuredContent`, a boolean, and as a line in the text). With the gate on
the line is `write_gate: true`; with it off it carries a suffix:
`write_gate: false -- OFF on this daemon: writes are NOT link-checked`. The server's startup log
carries `write_gate=on` (`INFO`) or `write_gate=off` (`WARNING`) as its own
line, written once the lifespan has loaded the Trac config; the
`file_access=..., max_inline_bytes=...` line is separate and is written
before the lifespan starts.

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
  "text": "Trac MCP server connected successfully. API version: [1, 1, 6]\nwrite_gate: true"
}
```
```json
{
  "message": "Trac MCP server connected successfully. API version: [1, 1, 6]\nwrite_gate: true",
  "api_version": [1, 1, 6],
  "write_gate": true
}
```

`message` is the full text, because Claude Code shows `structuredContent` in
preference to the text block. `write_gate` is whether the write-time link gate
runs on this daemon -- see [Write Gate Status](#write-gate-status). With the
gate off the text line reads
`write_gate: false -- OFF on this daemon: writes are NOT link-checked` (and
`message` carries it too), with `"write_gate": false` in the dict.

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

## ticket_check_list

**Description:** Check a whole list of ticket numbers, or of texts (for example keys from another system), against Trac in one call. Returns which values exist, with the requested fields, and which are missing, so a comparison needs one call instead of one `ticket_search` per value.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `values` | string | **Yes** | - | Ticket numbers or texts, separated by commas, semicolons or new lines; a JSON array of strings is accepted too. At most 1000; duplicates (ignoring case) count once |
| `match_field` | string | No | `"id"` | `id`: the values are ticket numbers (`#12` and `12` are the same). Otherwise the exact (case-sensitive) name of a text field of the instance, i.e. a field of type `text` or `textarea` (`summary`, `keywords`, `description`, a custom text field, ...): the values are searched for in that field as whole words. Any other name is a `validation_error` that lists the allowed fields |
| `query` | string | No | - | Trac query that narrows where to look, e.g. `status!=closed&component=Core`. It may only narrow by other fields: `max`, `page`, `order`, `desc`, `group`, `groupdesc`, `col`, `format`, `report`, `verbose`, `rows`, `row`, an `or` clause, a clause without `=`, and the field being matched (`id`, or `match_field`) are refused with a `validation_error` naming the key |
| `fields` | string | No | `"summary,status"` | Comma-separated ticket fields to return for each match. `id` and `url` are always present and cannot be requested as fields; they are ignored here |
| `only_missing` | boolean | No | `false` | Return only the values without a match: no `found` and no field values. In id mode no ticket is fetched; in text mode every candidate is still fetched, because the whole-word match needs its text |

**Matching.** Text matching is whole-word and case-insensitive. `-` counts as a word boundary, so `ABC-12` matches inside `ABC-12-3` but not inside `ABC-123`. The Trac query only narrows the candidates; the whole-word filter on the full value decides the match. What the query searches for depends on the field: for a list field (`type` `text` with `format` `list`, such as `keywords` and `cc`) Trac splits the value on whitespace and double quotes and reads a leading `-` as NOT, so the query searches the longest word of each value (leading `-` stripped); for any other field Trac does one substring match on the whole value, so the query searches the whole value (`disk quota exceeded` as it stands), split only at a backslash, for which Trac has no escape, and at the literal `$USER`, which Trac replaces with the caller's name. In both cases `|` and `&` are escaped.

**Limitation.** On a SQLite-backed Trac the substring match folds case for ASCII letters only, so a value whose non-ASCII letters differ in case from the stored text (for example `ÄRGER` against `ärger`) is not among the candidates and can be reported missing.

**Loading failures.** Each ticket is fetched at most once per call, and a ticket that failed to load is not retried within the call. In text mode a value with no whole-word match among the loaded candidates is in `missing` only if every candidate of its batch loaded; if one of them failed to load, the failed ticket might have matched, so the value is in `unresolved` instead. In id mode existence comes from the query, so a ticket that exists but failed to load stays in `found` with only `id` and `url` (no field values) and its id is in `failed_ids`; `found_count` is the same with or without `only_missing`.

**Output keys (structured JSON):**

| Key | Description |
|-----|-------------|
| `values_checked` | Number of distinct values received |
| `found` | Omitted with `only_missing`. In id mode, an object keyed by the value as given; in text mode, keyed by the value, each holding a list of matching tickets. Each hit holds `id`, the requested fields (unset fields are `""`) and `url` |
| `missing` | Values with no match |
| `found_count`, `missing_count` | The size of `found` and of `missing` (`unresolved` is not counted) |
| `not_a_ticket_number` | Id mode only, omitted when empty: values that are not a ticket number (one optional leading `#`, then 1 to 18 ASCII digits) |
| `unresolved` | Text mode only, omitted when empty: values with no whole-word match among the loaded candidates while a candidate of their batch failed to load. Not in `missing`; retry them |
| `unsearchable` | Text mode only, omitted when empty: values with no searchable word (for example `\`, `-` or `""`); they are not queried and are not in `missing` |
| `failed_ids` | Always present: ids of tickets that matched but could not be loaded. In id mode such a ticket is in `found` with only `id` and `url`; in text mode it is not matched and the values it might have matched are in `unresolved` or already found. Retry to load them. Always empty in id mode with `only_missing`, which loads nothing |

**Success Response:**
```json
{
  "values_checked": 3,
  "found": {
    "1": {"id": 1, "summary": "First", "status": "new", "url": "https://trac.example.com/ticket/1"}
  },
  "missing": ["2"],
  "found_count": 1,
  "missing_count": 1,
  "not_a_ticket_number": ["abc"],
  "failed_ids": []
}
```

**Error Responses:**

- **validation_error:** Empty `values`, more than 1000 values, a `match_field` that is not a field name or not a text field of the instance, or a `query` with a refused key
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): query may not contain 'max'\n\nAction: The query may only narrow by other fields, ..."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_check_list",
  "arguments": {
    "values": "ABC-12, ABC-13",
    "match_field": "summary",
    "query": "status!=closed",
    "fields": "summary,status"
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

**Description:** Create a new ticket. The description is TracWiki and is stored byte-for-byte -- nothing is converted, so hand-authored markup survives exactly as written.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `summary` | string | **Yes** | - | Ticket title |
| `description` | string | **Yes** | - | Ticket body. TracWiki, stored verbatim |
| `ticket_type` | string | No | `"defect"` | Ticket type: `defect`, `enhancement`, or `task` |
| `priority` | string | No | - | Priority level (e.g., `blocker`, `critical`, `major`, `minor`, `trivial`) |
| `severity` | string | No | - | Severity level (e.g., `critical`, `major`, `minor`) |
| `component` | string | No | - | Component name |
| `milestone` | string | No | - | Target milestone |
| `owner` | string | No | - | Assignee username |
| `cc` | string | No | - | CC email addresses |
| `keywords` | string | No | - | Keywords/tags |

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

**Description:** Update ticket attributes and/or add comments. Uses optimistic locking to prevent conflicts. The comment and the description rewrite are TracWiki and are stored byte-for-byte.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number to update (minimum: 1) |
| `comment` | string | No | - | Comment body (optional, max 10000 chars). TracWiki, stored verbatim |
| `status` | string | No | - | New status |
| `priority` | string | No | - | New priority |
| `severity` | string | No | - | New severity |
| `component` | string | No | - | New component |
| `milestone` | string | No | - | New milestone |
| `owner` | string | No | - | New owner |
| `resolution` | string | No | - | Resolution (when closing, e.g., `fixed`, `invalid`, `wontfix`) |
| `cc` | string | No | - | CC email addresses |
| `keywords` | string | No | - | Keywords/tags |

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

**Description:** Create multiple tickets in a single batch operation. Best-effort: all items attempted, per-item results reported. Bounded by TRAC_MAX_PARALLEL_REQUESTS semaphore. Descriptions are TracWiki and are stored byte-for-byte.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `tickets` | array | **Yes** | - | List of ticket objects to create |

Each ticket object:

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `summary` | string | **Yes** | - | Ticket title |
| `description` | string | **Yes** | - | Ticket body. TracWiki, stored verbatim |
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
  "failed_count": 1,
  "gate_notes": []
}
```

`gate_notes` (list, always present) holds the write-gate OFF line when the gate is off on this daemon, and is empty otherwise; see [Write Gate Status](#write-gate-status).

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

**Description:** Update multiple tickets in a single batch operation. Best-effort: all items attempted, per-item results reported. Comments are TracWiki and are stored byte-for-byte.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `updates` | array | **Yes** | - | List of update objects with ticket_id and fields to change |

Each update object:

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number to update (minimum: 1) |
| `comment` | string | No | - | Comment body. TracWiki, stored verbatim |
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
  "failed_count": 0,
  "gate_notes": []
}
```

`gate_notes` (list, always present) holds the write-gate OFF line when the gate is off on this daemon, and is empty otherwise; see [Write Gate Status](#write-gate-status).

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

**Description:** Ranked keyword search (BM25) over wiki page names and text. The query is split into words (compound and CamelCase names also into their parts); pages with more of the rarer words rank higher. It is not a phrase match. `prefix` filters before ranking. Snippets are as stored: TracWiki, byte-for-byte.

Every page with a positive score is returned, best first (ties by page name), so `limit` (up to 50) and `cursor` page through the whole ranked list. Each hit shows its score to two decimals. The server keeps a cache of page text per instance and identity: the first search reads every page, later ones re-read only pages that are new or edited.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | string | **Yes** | - | Search words; matched word by word and ranked, not a phrase |
| `prefix` | string | No | - | Only pages whose name starts with this prefix (namespace filter); applied before ranking |
| `limit` | integer | No | `10` | Maximum results per page (min: 1, max: 50) |
| `cursor` | string | No | - | Pagination cursor from previous response |

**Success Response:**
```json
{
  "type": "text",
  "text": "Found 15 wiki pages (showing 1-10):\n\n**DevelopmentGuide** (score 3.41)\n  ...follow the **coding standards** defined in this document...\n\n**API/Authentication** (score 2.87)\n  ...use JWT tokens for **authentication**...\n\nUse cursor 'eyJvZmZzZXQiOjEwLCJ0b3RhbCI6MTV9' to get next page."
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

**Description:** Create a new wiki page. Content is TracWiki and is stored byte-for-byte -- nothing is converted, so hand-authored markup survives exactly as written. Fails if the page exists (use wiki_update instead).

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **Yes** | - | Wiki page name to create |
| `content` | string | **Yes** | - | Page content. TracWiki, stored verbatim |
| `comment` | string | No | - | Change comment |

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
  The same error answers a race: the page was absent at the existence check, and another writer created it with byte-identical content before this call's write. The message says so, names the version the page is at (or "unknown"), and says Trac stored nothing; the action says nothing needs doing, since the page already holds this content (Trac /trac_mcp_server #174).

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

**Description:** Update an existing wiki page. Requires the version you read: it is checked against the page's current version immediately before the write, and a stale one is refused with version_conflict. Content is TracWiki and is stored byte-for-byte -- nothing is converted, so hand-authored markup survives exactly as written.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **Yes** | - | Wiki page name to update |
| `content` | string | **Yes** | - | Page content. TracWiki, stored verbatim |
| `version` | integer | **Yes** | - | The version you read; checked against the page's current version before the write (minimum: 1) |
| `comment` | string | No | - | Change comment |

**Success Response:**
```json
{
  "type": "text",
  "text": "Updated wiki page 'DevelopmentGuide' to version 6"
}
```

**How the version is checked** (Trac /trac_mcp_server #120, #129): where the Trac instance has the [`tracrpc_comment`](../../plugins/README.md) plugin's `wiki.putPageIfVersion`, the write goes through it, and the server compares the version and saves in one database transaction, so a write by someone else can never land in between: it is refused with the same `version_conflict` as a stale version. Where the plugin is missing, the client reads the page's version immediately before a stock `wiki.putPage` and compares it itself, which leaves a window of one round trip between the two. The tool's responses are the same either way, and a `version` below 1 (versions start at 1) never reaches the plugin: the client refuses it as `version_conflict` itself, with or without the plugin. The client remembers a missing plugin for five minutes before asking again.

**Possible overwrite:** without the plugin, a write that lands between the version check and `wiki.putPage` is not refused. When the page reads back afterwards as anything other than `version + 1`, the result is still a success, with a warning line after the first one naming the versions written in between and how to inspect them (Trac /trac_mcp_server #130). With the plugin, only a write that lands just after this one, before the read-back, can raise it:
```json
{
  "type": "text",
  "text": "Updated wiki page 'DevelopmentGuide' to version 7\nWarning: possible overwrite on 'DevelopmentGuide'. This update was based on version 5, so it should have stored version 6, but the page reads back as version 7. Version 6 was most likely written by someone else between the version check and this write, which replaced it without merging. The page was stored; inspect what version 6 changed with wiki_diff(page_name='DevelopmentGuide', from_version=5, to_version=6) and re-apply anything this write dropped."
}
```

**Unchanged:** content byte-identical to the stored page is not an error. Trac answers "Page not modified" and stores nothing, and the result says so, with no `isError`:
```json
{
  "type": "text",
  "text": "Wiki page 'DevelopmentGuide' unchanged: content identical to version 5. Trac stored nothing."
}
```
The version named is the page's current version, re-read after the no-op, so when someone else stored the same content meanwhile (the caller passed 5, the page is now 6) it says 6. If that re-read fails the version the caller passed is named, and when neither is known the text says "version unknown". Only Trac's own "page not modified" fault counts as this outcome (Trac /trac_mcp_server #174).

**Error Responses:**

- **version_conflict:** Page was modified after you read it (including, with the plugin, while this call was running), or no longer exists. Nothing was stored
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

**Description:** Push a document to a Trac wiki page. Takes the text inline as `content`, or -- only where the server's `file_access` is `local` -- as a `file_path` on the **server's** filesystem; auto-detects format (Markdown/TracWiki), converts if needed, and creates or updates the wiki page. To push a file from your own machine, use the [`trac-mcp` CLI](cli.md#trac-mcp) there: `trac-mcp wiki-push PAGE FILE`.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **Yes** | - | Target wiki page name |
| `content` | string | One of | - | The document text, in place of `file_path` |
| `filename` | string | No | - | With `content`: the source file's name (e.g. `notes.md`), used only for extension-based format detection. With neither `filename` nor `format`, inline content is stored as TracWiki, verbatim |
| `file_path` | string | One of | - | Absolute path on the **server's** filesystem; refused unless `file_access` is `local` |
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
  "file_path": null,
  "source": "inline content",
  "warnings": [],
  "readback_failed": false,
  "possible_overwrite": false,
  "gate_notes": []
}
```

**`action`** (string, always present): `"created"` (the page did not exist), `"updated"` (it did and the push stored a new version), or `"unchanged"` (the content was byte-identical to the stored page, so nothing was stored; see below).

**`gate_notes`** (list of strings, always present): the lines the write gate appended to the text -- the write-gate OFF line when the gate is off on this daemon, or the gate-on advisory ("Also reported") lines -- empty when there are none. Carried in `structuredContent` because Claude Code shows it in preference to the text block; see [Write Gate Status](#write-gate-status).

**`readback_failed`** (boolean, always present): `true` when the page was stored but its new version could not be read back; see below.

**`possible_overwrite`** (boolean, always present): `true` when an update stored a version other than the one after the version it was based on -- the push read version 3, so an uncontended write stores 4, but the page reads back as 5. Another write most likely landed between the version check and `putPage` and was replaced by this push without merging (or landed just after it). `intervening_versions` (present only when `true`) lists the versions between the base version and the stored one (`[4]` here), and the text carries a warning that names them and suggests `wiki_diff(page_name=..., from_version=3, to_version=4)` to inspect what they changed. The result is still a success: the page was stored. A create never sets it. Detection only, at no extra RPC (Trac /trac_mcp_server #130). Where the instance has the `tracrpc_comment` plugin's `wiki.putPageIfVersion`, an update goes through it and the window is closed: a write landing between the check and the save is refused as `version_conflict` instead (#129), so the flag can then only mean a write that landed just after this one. See [wiki_update](#wiki_update) for how the version is checked.

**Unchanged:** content byte-identical to the stored page is not an error. Trac answers "Page not modified" and stores nothing, and the result says so, with no `isError`: the text is `Wiki page 'Docs/MyPage' unchanged (version 4): content identical to the stored page. Trac stored nothing.`, `action` is `"unchanged"`, and every other key is as on a stored push (conversion warnings and `gate_notes` kept; `readback_failed` and `possible_overwrite` are `false`). `version` is the page's current version, re-read after the no-op, so a page someone else stored identically in the meantime reports the newer version, including when the push saw no page and would have created it; if that re-read fails it falls back to the version read before the write, and is `null` (text: "version unknown") when neither is known. Only Trac's own "page not modified" fault counts as this outcome (Trac /trac_mcp_server #174).

**Stored, read-back failed:** if `wiki.putPage` succeeded but reading back the page's new version then failed, the result is **not** an error -- the page was stored, and an error result means nothing was. `readback_failed` is `true`, `version` is `null`, `readback_error` carries the read-back's error, and the text says the page WAS stored and not to push again. The push posts `putPage` exactly once: a "not found" fault from the read-back (the page was deleted just after the write) no longer triggers a second write as a create (Trac /trac_mcp_server #140).

**Error Responses:**

- **validation_error:** Missing required parameter or invalid file path
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): content is required\n\nAction: Provide content (or file_path, only where the server's file_access is 'local')."
  }
  ```

- **permission_denied:** a `file_path` while the server's `file_access` is `off` (the default over http, ticket #111)
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): file_path is refused: file_access is 'off' on this server, so a path would name a file on the server's machine, not yours.\n\nAction: Use the trac-mcp CLI on your own machine (trac-mcp wiki-push PAGE FILE), or pass the bytes inline instead of a path."
  }
  ```

**Implementation Notes:**
- Exactly one of `content` and `file_path`; both is a `validation_error`
- Inline `content` over the server's `max_inline_bytes` is refused
- Auto-detects format from file extension (.md/.markdown = Markdown, .wiki/.tracwiki = TracWiki) with content heuristic fallback
- Strips YAML frontmatter by default (first `---` block)
- Creates new page if it doesn't exist, updates with optimistic locking if it does (through `wiki.putPageIfVersion` where the plugin is installed, as for `wiki_update`). Only the existence probe's "not found" means "create"; the write itself is never retried
- Handles Trac instances that return 0 (int) instead of Fault for non-existent pages

**Example Call:**
```json
{
  "name": "wiki_file_push",
  "arguments": {
    "page_name": "Docs/Design",
    "content": "# Design\n\nThe text of the document...",
    "filename": "design.md",
    "comment": "Push design doc to wiki",
    "format": "auto",
    "strip_frontmatter": true
  }
}
```

---

## wiki_file_pull

**Description:** Pull a Trac wiki page, converted to the requested format. Markdown is Trac's own rendered HTML for the page translated to Markdown: a one-way export, not meant to be pushed back; `format="tracwiki"` returns the stored source verbatim. Translator warnings come back in `structuredContent.warnings` (always a list). `link_rewrites` (`{from, to}` URL-prefix rules, longest prefix first) rewrites the links -- never the images or in-page `#anchors` -- of a Markdown pull: given, it replaces the server's configured map (`[]` = no rewriting); omitted, the configured map applies. `structuredContent.link_rewrites_source` says which was used (`call`, `config` or `none`). Where the server's `file_access` is `off` (the http default) the text comes back in `structuredContent.content` and a `file_path` is refused; where it is `local`, `file_path` -- on the **server's** filesystem -- is required, as before. To save a page on your own machine, use `trac-mcp wiki-pull PAGE FILE` there.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **Yes** | - | Wiki page name to pull |
| `file_path` | string | With `local` | - | Absolute output path on the **server's** filesystem: required where `file_access` is `local`, refused where it is `off` |
| `format` | string | No | `"markdown"` | Output format: `markdown` or `tracwiki` |
| `version` | integer | No | *(latest)* | Specific page version to pull (minimum: 1) |
| `link_rewrites` | array of `{from, to}` | No | *(the server's configured map)* | Link-rewrite rules for `format=markdown`: a link target starting with `from` (non-empty string) has that prefix replaced by `to` (string), the longest `from` first. Replaces the configured map for this call; `[]` disables rewriting. Image sources and in-page `#anchors` are never rewritten. A duplicate `from`, an empty `from` or a non-string value is a `validation_error`, returned before Trac is read. Ignored (and not validated) for `format=tracwiki` |

**Success Response:**
```json
{
  "type": "text",
  "text": "Pulled wiki page 'Docs/Design' (version 3, 4521 bytes, format=markdown); the text is in structuredContent.content"
}
```

**Structured JSON Output:**
```json
{
  "page_name": "Docs/Design",
  "format": "markdown",
  "version": 3,
  "converted": true,
  "warnings": [],
  "link_rewrites_source": "none",
  "content": "# Design\n...",
  "bytes": 4521
}
```

`link_rewrites_source` is `call` when the call passed `link_rewrites` (even `[]`), `config` when the server's configured map (`export.link_rewrites` / `TRAC_MCP_LINK_REWRITES`, see [Configuration](configuration.md#link-rewrites-export)) was applied, and `none` when no map was in force -- always `none` for `format=tracwiki`. `warnings` lists what the translator could not map exactly; it is `[]` for `format=tracwiki` and when nothing was lossy. The same warnings follow the summary line in the text block under `Conversion warnings:`. With a `file_path` (where `file_access` is `local`), `content`/`bytes` are replaced by `file_path` and `bytes_written`.

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
- Link rewriting touches `href`s only (including those inside raw HTML the translator passes through); image `src`s are never changed, a link into the page itself with a fragment (`<trac_url>/wiki/<page>#Heading`, how Trac renders `[#Heading x]`) is written `#Heading` and never rewritten, and the link text is kept, so an autolink whose target is rewritten becomes `[original URL](new URL)`
- Markdown by default: Trac's rendered HTML for the page, translated (`wiki.getPageHTML`/`getPageHTMLVersion`), with code-block languages taken from the TracWiki source; use `format: "tracwiki"` to get raw content (no HTML is read)
- For Markdown, the page's version is read first (`getPageInfo`) and the source and the HTML are fetched at that version, so a concurrent edit cannot mix versions
- An inline result over the server's `max_inline_bytes` (default 5 MiB) is refused with a `validation_error`
- Parent directory of `file_path` must exist, and is checked before Trac is called
- Fetches page info separately for version metadata (for Markdown, before the source and HTML)

**Example Call:**
```json
{
  "name": "wiki_file_pull",
  "arguments": {
    "page_name": "Docs/Design",
    "format": "markdown"
  }
}
```

---

## wiki_file_detect_format

**Description:** Detect whether text is Markdown or TracWiki. Uses the file extension first, then content-based heuristic detection. Takes `content` (plus an optional `filename`), or a `file_path` on the **server's** filesystem where `file_access` is `local`. For a file on your own machine, `trac-mcp detect-format FILE` runs the same detection locally.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `content` | string | One of | - | Text to analyze |
| `filename` | string | No | - | With `content`: the source file's name, for extension-based detection |
| `file_path` | string | One of | - | Absolute path on the **server's** filesystem; refused unless `file_access` is `local` |

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
  "file_path": null,
  "source": "inline content",
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
    "content": "# Design\n\nSome text.",
    "filename": "design.md"
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
  "text": "Default instance: http://trac.example.com:8000/main-project\nwrite_gate: true\n\nConfigured:\n  default: http://trac.example.com:8000/main-project (default)\n  bcs: http://trac.example.com:8000/bcs\n\nDiscovered on host:\n  /main-project: Main Project [configured]\n  /bcs: BCS [configured]\n  /other: Other Project"
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
  "write_gate": true,
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

`write_gate` is whether the write-time link gate runs on this daemon (see [Write Gate Status](#write-gate-status)). It is a per-daemon setting every routed instance inherits, so it is reported once, at the top level, and also to a caller whose allowlist leaves out the default instance. When it is `false`, the text line reads `write_gate: false -- OFF on this daemon: writes are NOT link-checked`.

The `credentials` field is always `"explicit"` or `"inherited"` -- passwords are never included in the response. The `discovered` key is omitted when `discover` is `false` or the host's project index could not be scraped.

**Example Call:**
```json
{
  "name": "list_instances",
  "arguments": {}
}
```

---

## convert_preview

**Description:** Dry-run a Markdown (or TracWiki) candidate through the real converter and Trac's own renderer, with no write. Returns the TracWiki that would be stored, the HTML Trac would render for it, and structured warnings for the defects `ticket_render_check` would otherwise catch only after the fact (escaped link targets, cross-instance references stuck in a code span, dead local links, a list item that continues after its nested list, and so on). Use it in place of the create-scratch/push/read-back/diff/delete round trip before replacing a whole page or description. Read-only.

Trac's `wiki.wikiToHtml` has no page or ticket context, so a bare `attachment:FILE` link renders as dead and a bare `comment:N` cannot be checked. Name the page or ticket the draft is for with `page_name` or `ticket_id` and the preview looks those up the way the write gate does (Trac /trac_mcp_server #126).

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `content` | string | **Yes** | - | Candidate content to preview |
| `format` | string | No | `markdown` | Format of `content`: `markdown` or `tracwiki`. `tracwiki` skips conversion and renders the input as-is |
| `check_targets` | boolean | No | `true` | Live-probe cross-instance wiki targets found in the render (capped, short timeout) to catch a target that renders identically whether it exists or not |
| `target_cap` | integer | No | `50` | Maximum cross-instance targets to probe (minimum: 1, maximum: 500). The cap keeps the FIRST N in document order; anything beyond it is reported as `target_check_capped` rather than checked |
| `include_html` | boolean | No | `true` | Include the rendered HTML in the response (capped at 20KB, with `html_truncated` set if cut). Warnings are always computed from the full render |
| `page_name` | string | No | - | The wiki page the draft will be written to. A bare `attachment:` link to a file that page holds is no longer reported as `missing_local_target`. Mutually exclusive with `ticket_id` |
| `ticket_id` | integer | No | - | The ticket the draft will be written to (description or comment; minimum: 1). Clears a bare `attachment:` link to a file that ticket holds, and also runs `dangling_comment_ref` against the ticket's comments. Mutually exclusive with `page_name` |

**Success Response:** a text summary (`convert_preview: N warning(s), M cross-instance target(s) checked.`), the warnings one per line, then the TracWiki under `--- TracWiki ---`. With `page_name` or `ticket_id`, one extra line follows the summary line: `Checked against: ticket 126` or `Checked against: wiki page Foo/Bar`, with ` (2 attachment(s))` appended when the attachment lookup ran, or ` (attachment list unavailable: bare attachment: links stay errors)` when it ran and failed, and ` (comment list unavailable: comment: links not checked)` when the comment lookup ran and failed (after the attachment clause if both apply).
```json
{
  "tracwiki": "See [attachment:f.txt x] here.",
  "rendered_html": "<p>...</p>",
  "html_truncated": false,
  "warnings": [],
  "stats": {"anchors": 1, "code_spans": 0, "warnings": 0, "targets_checked": 0},
  "target": {"realm": "ticket", "id": 126, "attachments_checked": true, "comments_checked": false}
}
```

`target` is `null` when neither `page_name` nor `ticket_id` was passed, and the response is then identical to what it was before the argument existed. Otherwise `realm` is `ticket` or `wiki`, `id` is the ticket number or the page name, `attachments_checked` and `comments_checked` are tri-state: `null` when that lookup was not needed (the target realm cannot have it, or the TracWiki lacks `attachment:` / `comment:`), `true` when it ran and returned a set, and `false` when it ran and failed. For `page_name`, `comments_checked` is always `null`.

The lookups reuse the write gate's helpers and run only when the converted TracWiki contains `attachment:` (and, for `ticket_id`, `comment:`). The attachment lookup fails closed: if it fails, or the page does not exist yet, bare `attachment:` links stay errors. The comment lookup fails open: if the changelog cannot be fetched, `dangling_comment_ref` does not run, and the `Checked against:` line and `comments_checked: false` say so, so a missing finding is not mistaken for a clean result. `page_name` never runs `dangling_comment_ref`, since a page has no comments.

**Error Responses:**

- **validation_error:** `content` missing, `format` not `markdown`/`tracwiki`, `ticket_id` not an integer >= 1 (a boolean is rejected), `page_name` not a non-empty string, both `page_name` and `ticket_id` passed (checked after each argument's own shape, so `page_name: ""` with a `ticket_id` reports the empty name), or Markdown the converter refuses

**Example Call:**
```json
{
  "name": "convert_preview",
  "arguments": {
    "content": "See [attachment:f.txt x] here.",
    "format": "tracwiki",
    "ticket_id": 126
  }
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
  "text": "ticket/57: 0 error(s), 1 warning(s) across 2 section(s).\n\n[description] No warnings.\n[comment 1] [warning] unconfigured_intertrac_prefix: 'zzznotaprefix:wiki:SomePage' does not match any configured InterTrac prefix -- it will render as plain text, not a link.\n\nLinks: 1 ticket, 2 wiki, 1 external, 0 other.\nTicket refs (1): #3 defect: Expand DEFAULT_TICKET_TYPE docstring comment in mcp/tools/constants.py (closed: fixed)\nMissing links: none.\nCode blocks (1):\n  [description] 3 line(s), highlighted"
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
  "ticket_refs": [
    {"ticket": 3, "ref": "#3", "summary": "defect: Expand DEFAULT_TICKET_TYPE docstring comment in mcp/tools/constants.py (closed: fixed)", "first_section": "comment 1"}
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

The text result's `Ticket refs` line lists each local ticket the document links to once, across all sections, in the order first seen (at most 50, then `... and K more`), with Trac's own title for the link (type, summary, status), so a mistyped `#N` that happens to hit an existing ticket shows up as the wrong summary. It is not a finding and does not count toward the error or warning totals; a ticket that does not exist is left off it (that is `missing_local_target`, listed under `Missing links`). The same data is in each section's `links.ticket` entries (`text`, `title`). Before Trac /trac_mcp_server #151 this was the `bare_ticket_ref` warning, emitted once per citation; that code no longer exists.

The structured result carries the same list as the top-level `ticket_refs` key (Trac /trac_mcp_server #152), because clients that read only `structuredContent` never see the text line. Each entry is `{"ticket", "ref", "summary", "first_section"}`: `ticket` is the ticket number as an integer (`null` only when no link to it had a `/ticket/N` href), `ref` the key the text line prints (`#105`, or the whitespace-collapsed link text when the href has no `/ticket/N`, uncapped), `summary` Trac's title for the link without its leading `#N: ` or `Comment M for #N: ` (`null` only when no link to that ticket had a title), and `first_section` the section where the ticket was first seen (`description`, `comment 3`, `page`), even when a later link supplied the summary. The list is complete and its entries are not shortened: the 50-entry cap and the per-entry length cap apply to the text line only, whose overflow tail points here. It is in first-seen order, a ticket cited twice appears once (the first link's values win; later links fill in a still-null `ticket` or `summary`, and a `#N: ` titled link replaces a summary that did not come from one), and a ticket that does not exist is absent. One helper builds both, so the text line and `ticket_refs` cannot disagree. `wiki_render_check` returns `ticket_refs` too, with `first_section` `page`.

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
