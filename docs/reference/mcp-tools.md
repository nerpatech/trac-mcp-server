# MCP Tools Reference

This page documents every tool the server registers, one section each. The README's [Available Tools](../../README.md#available-tools-54) section lists them by area. `tests/test_readme_tool_count.py` checks that each registered tool has a `## <name>` section here and that no section names a tool that is not registered.

## Instance Routing

Every tool below also accepts an optional `instance` argument (added
automatically to each tool's schema; omitted from the per-tool parameter
tables below for brevity) that routes the call to another Trac project
instead of the default one -- see
[Multiple Instances](configuration.md#multiple-instances). Call
`list_instances` to discover what's reachable.

The exception is HTTP with OIDC per-user auth (`TRAC_MCP_OIDC_RPC_URL`; see
[HTTP Transport: OIDC Per-User Auth](http-transport.md#oidc-per-user-auth)):
there every call goes only to the configured endpoint and the `instance`
argument is not offered, so none of the tools below carries it, and the
instance echo described next is not added.

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
  "text": "Server time: 2026-02-05T20:51:27+00:00"
}
```

**Structured JSON Output:**
```json
{
  "server_time": "2026-02-05T20:51:27+00:00",
  "unix_timestamp": 1770324687,
  "timezone": "UTC"
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

**Description:** Search tickets with filtering by status, owner, and keywords. Returns ticket IDs with summaries, status, owner, milestone and their web URLs.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | string | No | `"status!=closed"` | Trac query string (e.g., `status=new`, `owner=alice`, `status!=closed&keywords~=urgent`) |
| `max_results` | integer | No | `10` | Maximum results to return (min: 1, max: 100) |

**Success Response:**
```json
{
  "type": "text",
  "text": "Found 25 tickets (showing 10):\n- #42: Fix login timeout issue (status: new, owner: alice, milestone: v2.0) - https://trac.example.com/ticket/42\n- #38: Add user export feature (status: accepted, owner: bob, milestone: ) - https://trac.example.com/ticket/38\n...\n\nUse max_results to see more."
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
| `type` | string | No | - | Alias for `ticket_type` (accepted since `ticket_update`'s own field is named `type`) |
| `priority` | string | No | - | Priority level (e.g., `blocker`, `critical`, `major`, `minor`, `trivial`) |
| `severity` | string | No | - | Severity level (e.g., `critical`, `major`, `minor`) |
| `component` | string | No | - | Component name |
| `milestone` | string | No | - | Target milestone |
| `owner` | string | No | - | Assignee username |
| `cc` | string | No | - | CC email addresses |
| `keywords` | string | No | - | Keywords/tags |
| `target_cap` | integer | No | `50` | Maximum cross-instance targets the write-time link check probes (default: 50). Anything beyond the cap is reported as target_check_capped, which BLOCKS the write -- an unchecked target must not read as a clean one. Raise this for a legitimately link-dense document; there is deliberately no way to switch the check off. (minimum: 1, maximum: 500) |

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
| `summary` | string | No | - | New summary (ticket title) |
| `description` | string | No | - | New description, replacing the ticket body. TracWiki, stored verbatim |
| `type` | string | No | - | New ticket type (e.g., `defect`, `enhancement`, `task`) |
| `status` | string | No | - | New status. Refused without `action`: Trac's workflow drops a direct status write, so transition with `action` instead (e.g., `action='accept'`) |
| `action` | string | No | - | Trac workflow action to perform (e.g., `accept`, `resolve`, `reopen`, `reassign`); the canonical way to move a ticket through its workflow. Action-specific input fields are passed as `action_<action>_<action>_<field>` keys (e.g., `action_resolve_resolve_resolution='fixed'`) |
| `priority` | string | No | - | New priority |
| `severity` | string | No | - | New severity |
| `component` | string | No | - | New component |
| `milestone` | string | No | - | New milestone |
| `owner` | string | No | - | New owner |
| `resolution` | string | No | - | Resolution (e.g., `fixed`, `invalid`, `wontfix`); requires `action` (e.g., `action='resolve'`), remapped onto the action's resolution field |
| `cc` | string | No | - | CC email addresses |
| `keywords` | string | No | - | Keywords/tags |
| `target_cap` | integer | No | `50` | Maximum cross-instance targets the write-time link check probes (default: 50). Anything beyond the cap is reported as target_check_capped, which BLOCKS the write -- an unchecked target must not read as a clean one. Raise this for a legitimately link-dense document; there is deliberately no way to switch the check off. (minimum: 1, maximum: 500) |
| `reply_to` | integer | No | - | Comment number to reply to. Prepends Trac's standard `Replying to [comment:N author]:` quote block (quoting that comment's own text) before the new comment. Requires `comment` (minimum: 1) |
| `base_ts` | string | No | - | Change token from a prior `ticket_get` call's `_ts` field, or the new token a previous `ticket_update` on this ticket returned (a numeric string; pass it through as-is). When provided, the update is rejected with a `version_conflict` error naming what changed if the ticket was modified since that token was read. Strongly recommended: without it, this write silently overwrites any concurrent change |

A `status` or `resolution` given without an `action` is refused before anything is written: Trac's workflow silently drops a direct status change, and a bare resolution would leave an open ticket carrying a resolution. Close a ticket with `action="resolve"` and `resolution="fixed"`; use `action="reopen"` or `"accept"` for other transitions (`ticket_actions` lists what a ticket allows).

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

- **validation_error:** `status` or `resolution` without `action` (the text names the field or fields and shows the `action` form to use)

**Example Call:**
```json
{
  "name": "ticket_update",
  "arguments": {
    "ticket_id": 42,
    "comment": "Fixed in commit abc123. The issue was a missing timeout configuration.",
    "action": "resolve",
    "action_resolve_resolve_resolution": "fixed"
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
| `target_cap` | integer | No | `50` | Maximum cross-instance targets the write-time link check probes (default: 50). Anything beyond the cap is reported as target_check_capped, which BLOCKS the write -- an unchecked target must not read as a clean one. Raise this for a legitimately link-dense document; there is deliberately no way to switch the check off. (minimum: 1, maximum: 500) |

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
- Descriptions are TracWiki and are stored byte-for-byte

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

**Description:** Update multiple tickets in a single batch operation. Best-effort: all items attempted, per-item results reported. Comments are TracWiki and are stored byte-for-byte. A status change needs `action` per item, like `ticket_update` (e.g. `action='resolve'` with `resolution='fixed'`, or `action='reopen'`): an item with a status or resolution but no action is refused, and nothing is written for it.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `updates` | array | **Yes** | - | List of update objects with ticket_id and fields to change |
| `target_cap` | integer | No | `50` | Maximum cross-instance targets the write-time link check probes (default: 50). Anything beyond the cap is reported as target_check_capped, which BLOCKS the write -- an unchecked target must not read as a clean one. Raise this for a legitimately link-dense document; there is deliberately no way to switch the check off. (minimum: 1, maximum: 500) |

Each update object:

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number to update (minimum: 1) |
| `comment` | string | No | - | Comment body. TracWiki, stored verbatim |
| `status` | string | No | - | New status. Refused without `action`: Trac's workflow drops a direct status write, so transition with `action` instead (e.g., `action='accept'`) |
| `action` | string | No | - | Trac workflow action to perform (e.g., `accept`, `resolve`, `reopen`, `reassign`), as in `ticket_update`. Action-specific input fields are passed as `action_<action>_<action>_<field>` keys on the same item (e.g., `action_resolve_resolve_resolution='fixed'`) |
| `resolution` | string | No | - | Resolution; requires `action` (e.g., `action='resolve'`), remapped onto the action's resolution field |
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
  "warnings": [],
  "gate_notes": []
}
```

`gate_notes` (list, always present) holds the write-gate OFF line when the gate is off on this daemon, and is empty otherwise; see [Write Gate Status](#write-gate-status). `warnings` (list, always present) holds `{"id", "warning"}` for each item that succeeded with an action-resolution remap warning.

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
- Comments are TracWiki and are stored byte-for-byte
- An item with a `status` or `resolution` but no `action` is refused: it is listed under `failed` with the same text `ticket_update` returns, nothing is written for it, and the other items still run
- A bare `resolution` alongside an `action` is remapped onto the action's own resolution field (the lookup `ticket_update` does); when no such field exists the item still succeeds and its warning is listed under `warnings` in the structured output and under the item in the text

**Example Call:**
```json
{
  "name": "ticket_batch_update",
  "arguments": {
    "updates": [
      {
        "ticket_id": 42,
        "comment": "Fixed in commit abc123.",
        "action": "resolve",
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

## ticket_attachment_put

**Description:** Upload an attachment to a Trac ticket, as content_base64 (or, only where this server's file_access is 'local', a file_path on the SERVER's filesystem). To attach a file from your own machine without putting its bytes in the transcript, run the trac-mcp CLI there: `trac-mcp attach-put --ticket N FILE`.

**Permission:** TICKET_APPEND.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number to attach to (minimum: 1) |
| `content_base64` | string | One of | - | The attachment bytes, standard base64, in place of file_path. Requires filename. |
| `file_path` | string | One of | - | Absolute path on the SERVER's filesystem. Refused unless the server's file_access is 'local' (it is 'off' over http). |
| `filename` | string | No | - | Attachment filename stored on the ticket. Defaults to the basename of file_path; required with content_base64. |
| `description` | string | No | `""` | Attachment description |
| `replace` | boolean | No | `false` | If true, overwrite an existing attachment with the same filename. Default false. |

Pass exactly one of `content_base64` and `file_path`. The path form is refused unless the server's `file_access` is `local` (the stdio default; `off` over http, see [File Access](http-transport.md#file-access-file_access)). An inline upload is capped by the server's `max_inline_bytes` (default 5 MiB). To attach a file from your own machine without putting its bytes in the transcript, run `trac-mcp attach-put --ticket N FILE` there.

**Success Response:**
```json
{
  "type": "text",
  "text": "Uploaded attachment 'trace.log' to ticket #42 (36 bytes, replace=False)"
}
```
```json
{
  "ticket_id": 42,
  "requested_filename": "trace.log",
  "attached_filename": "trace.log",
  "renamed_on_collision": false,
  "file_path": null,
  "source": "inline content",
  "bytes_uploaded": 36,
  "replace": false,
  "description": "Server trace from the failing login"
}
```

`file_path` is the path the caller passed (`null` for an inline upload) and `source` is that path or `"inline content"`. When `replace` is false and the name is taken, Trac stores the file under another name: `attached_filename` then differs from `requested_filename` and `renamed_on_collision` is `true`.

**Error Responses:**

- **validation_error:** Missing `ticket_id`, neither or both of `content_base64` and `file_path`, invalid base64, no `filename` with `content_base64`, or an inline payload over `max_inline_bytes`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): content_base64 is required\n\nAction: Provide content_base64 (or file_path, only where the server's file_access is 'local')."
  }
  ```

- **permission_denied:** `file_path` while `file_access` is `off`
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): file_path is refused: file_access is 'off' on this server, so a path would name a file on the server's machine, not yours.\n\nAction: Use the trac-mcp CLI on your own machine (trac-mcp attach-put --ticket N FILE), or pass the bytes inline instead of a path."
  }
  ```

- **not_found:** Ticket does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Ticket 999 does not exist.\n\nAction: Use ticket_search to verify ticket exists."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_attachment_put",
  "arguments": {
    "ticket_id": 42,
    "filename": "trace.log",
    "content_base64": "VHJhY2ViYWNrIChtb3N0IHJlY2VudCBjYWxsIGxhc3QpLi4u",
    "description": "Server trace from the failing login"
  }
}
```

---

## ticket_attachment_get

**Description:** Download a ticket attachment. Where the server's file_access is 'off' (the http default) the bytes come back base64 in structuredContent.content_base64 and an output_path is refused; where it is 'local', output_path (a path on the server's own filesystem) is required. To save one on your own machine, run the trac-mcp CLI there: `trac-mcp attach-get --ticket N NAME FILE`.

**Permission:** TICKET_VIEW.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number the attachment belongs to (minimum: 1) |
| `filename` | string | **Yes** | - | Attachment filename to download |
| `output_path` | string | With `local` | - | Absolute path on the SERVER's filesystem; the parent directory must already exist. Refused unless the server's file_access is 'local', and required there. Where file_access is 'off', omit it to get the bytes inline. |

**Success Response:**

With `file_access` `off` (the http default) the bytes come back inline:
```json
{
  "type": "text",
  "text": "Fetched attachment 'trace.log' from ticket #42 (36 bytes); the bytes are base64 in structuredContent.content_base64"
}
```
```json
{
  "ticket_id": 42,
  "filename": "trace.log",
  "content_base64": "VHJhY2ViYWNrIChtb3N0IHJlY2VudCBjYWxsIGxhc3QpLi4u",
  "bytes": 36
}
```

With `file_access` `local` the bytes are written to `output_path` and stay out of the result:
```json
{
  "type": "text",
  "text": "Downloaded attachment 'trace.log' from ticket #42 to /tmp/trace.log (36 bytes)"
}
```
```json
{
  "ticket_id": 42,
  "filename": "trace.log",
  "output_path": "/tmp/trace.log",
  "bytes_written": 36
}
```

An inline result over the server's `max_inline_bytes` is refused with a `validation_error`.

**Error Responses:**

- **validation_error:** Missing `ticket_id` or `filename`, or no `output_path` where `file_access` is `local`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): filename is required\n\nAction: Provide filename parameter."
  }
  ```

- **permission_denied:** `output_path` while `file_access` is `off`
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): output_path is refused: file_access is 'off' on this server, so a path would name a file on the server's machine, not yours.\n\nAction: Use the trac-mcp CLI on your own machine (trac-mcp attach-get --ticket N NAME FILE), or pass the bytes inline instead of a path."
  }
  ```

- **not_found:** Ticket or attachment does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): [error details]\n\nAction: Use ticket_search to verify ticket exists."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_attachment_get",
  "arguments": {
    "ticket_id": 42,
    "filename": "trace.log"
  }
}
```

---

## ticket_attachment_list

**Description:** List attachments on a Trac ticket.

**Permission:** TICKET_VIEW.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number to list attachments for (minimum: 1) |

**Success Response:**
```json
{
  "type": "text",
  "text": "Ticket #42 has 2 attachment(s):\n- trace.log (36 bytes) by alice\n- screenshot.png (48211 bytes) by bob"
}
```
```json
{
  "ticket_id": 42,
  "count": 2,
  "attachments": [
    {
      "filename": "trace.log",
      "description": "Server trace from the failing login",
      "size": 36,
      "time": "20260116T08:12:00",
      "author": "alice"
    },
    {
      "filename": "screenshot.png",
      "description": "",
      "size": 48211,
      "time": "20260117T09:30:00",
      "author": "bob"
    }
  ]
}
```

A ticket with none answers `Ticket #42 has no attachments.` with `count` `0` and an empty `attachments` list. `time` is Trac's compact `YYYYMMDDTHH:MM:SS` string.

**Error Responses:**

- **validation_error:** Missing `ticket_id`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): ticket_id is required\n\nAction: Provide ticket_id parameter."
  }
  ```

**Known failure:** a missing ticket (or the wrong `instance`) is not an error. Neither the handler nor TracXMLRPC's `ticket.listAttachments` checks that the ticket exists, so the call answers `Ticket #999 has no attachments.` with `count` `0`, exactly as for an existing ticket with none. Check the ticket with `ticket_get` first when that matters. Tracked as Trac /trac_mcp_server #253.

**Example Call:**
```json
{
  "name": "ticket_attachment_list",
  "arguments": {
    "ticket_id": 42
  }
}
```

---

## ticket_attachment_delete

**Description:** Delete a ticket attachment permanently. Requires TICKET_ADMIN permission.

**Permission:** TICKET_ADMIN.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number the attachment belongs to (minimum: 1) |
| `filename` | string | **Yes** | - | Attachment filename to delete |

**Success Response:**
```json
{
  "type": "text",
  "text": "Deleted attachment 'trace.log' from ticket #42."
}
```
```json
{
  "ticket_id": 42,
  "filename": "trace.log"
}
```

**Error Responses:**

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): [error details]\n\nAction: Deleting ticket attachments requires TICKET_ADMIN. Contact Trac administrator."
  }
  ```

- **validation_error:** Missing `ticket_id` or `filename`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): filename is required\n\nAction: Provide filename parameter."
  }
  ```

- **not_found:** Ticket or attachment does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): [error details]\n\nAction: Use ticket_search to verify ticket exists."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_attachment_delete",
  "arguments": {
    "ticket_id": 42,
    "filename": "trace.log"
  }
}
```

---

## ticket_comment_reply

**Description:** Post a threaded reply to a ticket comment. Unlike ticket_update, this records real threading, so the reply nests under its parent in the web UI. Set quote=true to also prepend the parent's text as a quote block. TracWiki, stored byte-for-byte, and checked by the same write-time link gate as ticket_update -- on the reply's own new text, not the quoted parent.

**Permission:** TICKET_APPEND. **Needs the `tracrpc_comment` Trac plugin** (`plugins/tracrpc_comment/`, see [plugins/README.md](../../plugins/README.md)): stock XmlRpcPlugin has no comment-level methods. Without it the call answers `method_not_available` with an install hint (see the error below).

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number (minimum: 1) |
| `cnum` | integer | **Yes** | - | Comment number being replied to, as ticket_changelog reports it. (minimum: 1) |
| `comment` | string | **Yes** | - | Reply body. TracWiki, verbatim. |
| `quote` | boolean | No | `false` | Prepend the parent comment as a quote block. Default false. |
| `target_cap` | integer | No | `50` | Maximum cross-instance targets the write-time link check probes (default: 50). Anything beyond the cap is reported as target_check_capped, which BLOCKS the write -- an unchecked target must not read as a clean one. Raise this for a legitimately link-dense document; there is deliberately no way to switch the check off. (minimum: 1, maximum: 500) |

`cnum` is the comment number `ticket_changelog` (or `ticket_get`) reports, never a row count: description edits consume numbers and deletes never renumber, so gaps are normal. The write-time link gate checks the reply's own `comment`, not the quoted parent (see [Write Gate Status](#write-gate-status)).

**Success Response:**
```json
{
  "type": "text",
  "text": "Posted comment 5 on ticket #42 as a threaded reply to comment 3."
}
```

The text only; the tool returns no `structuredContent`. With the write gate off the [gate-off line](#write-gate-status) follows it.

**Error Responses:**

- **method_not_available:** The `tracrpc_comment` plugin is not installed or enabled on the Trac server
  ```json
  {
    "type": "text",
    "text": "Error (method_not_available): RPC method \"ticket.replyToComment\" not found\n\nAction: This tool needs the 'tracrpc_comment' plugin on the Trac server. Install it from the trac-mcp-server repo's plugins/ directory and enable it with: trac-admin <env> config set components 'tracrpc_comment.*' enabled"
  }
  ```

- **link_check_failed:** The reply carries a broken link
  ```json
  {
    "type": "text",
    "text": "Error (link_check_failed): Refusing to write comment: 1 blocking link error.\n\n- [<code>] <finding>\n\n[...]\n\nAction: Correct the link and retry the write, or add a 'preview-checks: allow <code>' pragma if the content is deliberate."
  }
  ```

- **validation_error:** Missing `ticket_id`, `cnum` or `comment`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): comment is required\n\nAction: Provide comment."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_comment_reply",
  "arguments": {
    "ticket_id": 42,
    "cnum": 3,
    "comment": "Confirmed on staging; the timeout is 30s, not 60s.",
    "quote": true
  }
}
```

---

## ticket_comment_edit

**Description:** Correct the text of an existing ticket comment. The previous text is kept as a revision (readable with ticket_comment_history), so this is a correction, not an erasure. TracWiki, stored byte-for-byte, and checked by the same write-time link gate as ticket_update. Requires base_ts (ticket_get's _ts on THIS ticket_id/instance): the underlying RPC has no precondition of its own, so without it a call that resolves to the wrong instance or ticket overwrites an unrelated comment there with no warning (ticket #104).

**Permission:** TICKET_EDIT_COMMENT. **Needs the `tracrpc_comment` Trac plugin** (`plugins/tracrpc_comment/`, see [plugins/README.md](../../plugins/README.md)); without it the call answers `method_not_available` with an install hint.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number (minimum: 1) |
| `cnum` | integer | **Yes** | - | Comment number as ticket_changelog reports it. Do not count rows -- numbering has gaps. (minimum: 1) |
| `comment` | string | **Yes** | - | Replacement body. TracWiki, verbatim. |
| `base_ts` | string | **Yes** | - | Change token from a ticket_get() call on THIS ticket_id, made on THIS instance, immediately before this edit (a numeric string -- pass it through as-is). Rejected with a version_conflict error if it does not match the ticket's current _ts -- which is also what happens when the instance/ticket_id resolved to a ticket other than the one just read. Required: unlike ticket_update, there is no server-side lock this falls back to, so omitting it would leave every call unprotected. |
| `target_cap` | integer | No | `50` | Maximum cross-instance targets the write-time link check probes (default: 50). Anything beyond the cap is reported as target_check_capped, which BLOCKS the write -- an unchecked target must not read as a clean one. Raise this for a legitimately link-dense document; there is deliberately no way to switch the check off. (minimum: 1, maximum: 500) |

`base_ts` is checked client-side against a fresh `ticket.get` before the edit, because the underlying RPC has no precondition of its own. Take it from `ticket_get`'s `_ts` on the same `ticket_id` and the same instance, immediately before the edit.

**Success Response:**
```json
{
  "type": "text",
  "text": "Edited comment 3 on ticket #42. The previous text is kept as a revision -- read it with ticket_comment_history.\nReplaced (previous text): 'Reproduced on staging.'"
}
```

The `Replaced (previous text)` line is the first 80 characters of the prior revision, read best-effort: it is left out when that read fails. With the write gate off the [gate-off line](#write-gate-status) follows. The tool returns no `structuredContent`.

**Error Responses:**

- **version_conflict:** `base_ts` does not match the ticket's current `_ts`: the ticket changed, or the `instance`/`ticket_id` pair resolved to a different ticket than intended. Nothing was edited.
  ```json
  {
    "type": "text",
    "text": "Error (version_conflict): Ticket #42's current base_ts is 1737390720000000, not the 1737300000000000 this call supplied -- comment 3 was NOT edited. Either the ticket changed since you read it, or this instance/ticket_id pair resolved to a different ticket than the one you intended.\n\nAction: Re-read with ticket_get(ticket_id=42) on the instance you meant to target, confirm it is the right ticket, and retry with the fresh base_ts from that response."
  }
  ```

- **method_not_available:** The `tracrpc_comment` plugin is not installed or enabled on the Trac server
  ```json
  {
    "type": "text",
    "text": "Error (method_not_available): RPC method \"ticket.editComment\" not found\n\nAction: This tool needs the 'tracrpc_comment' plugin on the Trac server. Install it from the trac-mcp-server repo's plugins/ directory and enable it with: trac-admin <env> config set components 'tracrpc_comment.*' enabled"
  }
  ```

- **link_check_failed:** The replacement text carries a broken link
  ```json
  {
    "type": "text",
    "text": "Error (link_check_failed): Refusing to write comment: 1 blocking link error.\n\n- [<code>] <finding>\n\n[...]\n\nAction: Correct the link and retry the write, or add a 'preview-checks: allow <code>' pragma if the content is deliberate."
  }
  ```

- **validation_error:** Missing `ticket_id`, `cnum`, `comment` or `base_ts`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): base_ts is required\n\nAction: Provide base_ts."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_comment_edit",
  "arguments": {
    "ticket_id": 42,
    "cnum": 3,
    "comment": "Reproduced on staging with the 9 AM load profile.",
    "base_ts": "1737390720000000"
  }
}
```

---

## ticket_comment_delete

**Description:** Delete a ticket comment permanently. Cannot be undone. Refuses when the comment was posted together with field changes, because Trac would also REVERT those changes -- deleting the comment that accompanied a close reopens the ticket. Pass force=true only after reading what the refusal names. Requires TICKET_ADMIN.

**Permission:** TICKET_ADMIN. **Needs the `tracrpc_comment` Trac plugin** (`plugins/tracrpc_comment/`, see [plugins/README.md](../../plugins/README.md)); without it the call answers `method_not_available` with an install hint.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number (minimum: 1) |
| `cnum` | integer | **Yes** | - | Comment number as ticket_changelog reports it. Do not count rows -- numbering has gaps. (minimum: 1) |
| `force` | boolean | No | `false` | Delete even though bundled field changes will be reverted. Default false. |

**Success Response:**
```json
{
  "type": "text",
  "text": "Deleted comment 3 on ticket #42."
}
```

With `force=true` the text ends `Bundled field changes were reverted.` The tool returns no `structuredContent`.

**Error Responses:**

- **bundled_change_refused:** The comment was posted together with field changes, which Trac would also revert. The text is the server's own refusal, which names the fields.
  ```json
  {
    "type": "text",
    "text": "Error (bundled_change_refused): [the server's refusal, naming the fields that would be reverted]\n\nAction: Re-read the ticket and confirm the field changes may be reverted, then retry with force=true. Prefer editing the comment instead if the record should be kept."
  }
  ```

- **method_not_available:** The `tracrpc_comment` plugin is not installed or enabled on the Trac server
  ```json
  {
    "type": "text",
    "text": "Error (method_not_available): RPC method \"ticket.deleteComment\" not found\n\nAction: This tool needs the 'tracrpc_comment' plugin on the Trac server. Install it from the trac-mcp-server repo's plugins/ directory and enable it with: trac-admin <env> config set components 'tracrpc_comment.*' enabled"
  }
  ```

- **validation_error:** Missing `ticket_id` or `cnum`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): cnum is required\n\nAction: Provide cnum."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_comment_delete",
  "arguments": {
    "ticket_id": 42,
    "cnum": 3
  }
}
```

---

## ticket_comment_history

**Description:** Read the edit revisions of a ticket comment, oldest first. A never-edited comment has exactly one revision, so an empty result means the comment does not exist.

**Permission:** TICKET_VIEW. **Needs the `tracrpc_comment` Trac plugin** (`plugins/tracrpc_comment/`, see [plugins/README.md](../../plugins/README.md)); without it the call answers `method_not_available` with an install hint.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | integer | **Yes** | - | Ticket number (minimum: 1) |
| `cnum` | integer | **Yes** | - | Comment number as ticket_changelog reports it. Do not count rows -- numbering has gaps. (minimum: 1) |

**Success Response:**
```json
{
  "type": "text",
  "text": "Comment 3 on ticket #42 -- 2 revision(s), oldest first:\n\n--- revision 0 by carol at 20260116T08:12:00 ---\nReproduced on staging.\n\n--- revision 1 by carol at 20260116T09:05:00 ---\nReproduced on staging with the 9 AM load profile."
}
```

The text only; the tool returns no `structuredContent`. A comment that does not exist on an existing ticket (or whose number falls in a gap) is not an error: the text reads `No comment 3 on ticket #42. Comment numbering has gaps -- check ticket_changelog.` A ticket that does not exist is an error (below).

**Error Responses:**

- **method_not_available:** The `tracrpc_comment` plugin is not installed or enabled on the Trac server
  ```json
  {
    "type": "text",
    "text": "Error (method_not_available): RPC method \"ticket.getCommentHistory\" not found\n\nAction: This tool needs the 'tracrpc_comment' plugin on the Trac server. Install it from the trac-mcp-server repo's plugins/ directory and enable it with: trac-admin <env> config set components 'tracrpc_comment.*' enabled"
  }
  ```

- **validation_error:** Missing `ticket_id` or `cnum`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): cnum is required\n\nAction: Provide cnum."
  }
  ```

- **not_found:** The ticket does not exist (`getCommentHistory` loads the ticket first, and Trac's `ResourceNotFound` reaches the client as an XML-RPC fault with code 404, which the registry's fault translation reports as `not_found`)
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Ticket 999 does not exist.\n\nAction: Use ticket_search to verify ticket exists."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_comment_history",
  "arguments": {
    "ticket_id": 42,
    "cnum": 3
  }
}
```

---

## ticket_component_create

**Description:** Create a new ticket component. Requires TICKET_ADMIN permission. Component must not already exist.

**Permission:** TICKET_ADMIN.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `name` | string | **Yes** | - | Component name (required, must be unique). |
| `description` | string | No | `""` | Optional component description. |
| `owner` | string | No | `""` | Optional default owner username. |

**Success Response:**
```json
{
  "type": "text",
  "text": "Component 'backend' created."
}
```

The text only; the tool returns no `structuredContent`.

**Error Responses:**

- **validation_error:** Missing or empty `name`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): name is required\n\nAction: Pass a non-empty component name."
  }
  ```

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): [error details]\n\nAction: Try adding a comment instead, or contact ticket owner."
  }
  ```

A component that already exists comes back as the server's own fault text in a `server_error` (`Contact Trac administrator or retry later.`): only milestones have an `already_exists` error type.

**Example Call:**
```json
{
  "name": "ticket_component_create",
  "arguments": {
    "name": "backend",
    "description": "Server-side code",
    "owner": "alice"
  }
}
```

---

## ticket_component_list

**Description:** List all ticket components with their attributes (name, description, owner). Requires TICKET_VIEW.

**Permission:** TICKET_VIEW.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| *(none)* | - | - | - | No parameters required |

**Success Response:**
```json
{
  "type": "text",
  "text": "[{\"name\": \"backend\", \"owner\": \"alice\", \"description\": \"Server-side code\"}, {\"name\": \"frontend\", \"owner\": \"\", \"description\": \"\"}]"
}
```

The text block is the JSON array itself (`json.dumps` of one `{name, owner, description}` object per component, in the order Trac lists them); the tool returns no `structuredContent`. `owner` and `description` are empty strings when unset.

**Error Responses:**

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): [error details]\n\nAction: Try adding a comment instead, or contact ticket owner."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_component_list",
  "arguments": {}
}
```

---

## ticket_component_delete

**Description:** Delete a ticket component. Requires TICKET_ADMIN permission.

**Permission:** TICKET_ADMIN.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `name` | string | **Yes** | - | Component name to delete. |

**Success Response:**
```json
{
  "type": "text",
  "text": "Component 'backend' deleted."
}
```

The text only; the tool returns no `structuredContent`.

**Error Responses:**

- **validation_error:** Missing or empty `name`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): name is required\n\nAction: Pass a non-empty component name."
  }
  ```

- **not_found:** Component does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): [error details]\n\nAction: Use ticket_search to verify ticket exists."
  }
  ```

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): [error details]\n\nAction: Try adding a comment instead, or contact ticket owner."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_component_delete",
  "arguments": {
    "name": "backend"
  }
}
```

---

## ticket_enum_create

**Description:** Create a new value for a Trac enum field (priority, resolution, severity, type, or version). Requires TICKET_ADMIN permission.

**Permission:** TICKET_ADMIN.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `enum_type` | string | **Yes** | - | Enum field to mutate. Must be one of priority, resolution, severity, type, version. (one of `priority`, `resolution`, `severity`, `type`, `version`) |
| `name` | string | **Yes** | - | New enum value name. |

**Success Response:**
```json
{
  "type": "text",
  "text": "priority value 'urgent' created."
}
```

The text only; the tool returns no `structuredContent`.

**Error Responses:**

- **validation_error:** `enum_type` missing or not one of `priority`, `resolution`, `severity`, `type`, `version`; or `name` missing or empty
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): enum_type must be one of ['priority', 'resolution', 'severity', 'type', 'version']\n\nAction: Pass a valid enum_type."
  }
  ```

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): [error details]\n\nAction: Try adding a comment instead, or contact ticket owner."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_enum_create",
  "arguments": {
    "enum_type": "priority",
    "name": "urgent"
  }
}
```

---

## ticket_enum_list

**Description:** List all values for a Trac enum field. Returns values in Trac's configured order. Requires TICKET_VIEW permission.

**Permission:** TICKET_VIEW.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `enum_type` | string | **Yes** | - | Enum field to list. Must be one of priority, resolution, severity, type, version. (one of `priority`, `resolution`, `severity`, `type`, `version`) |

**Success Response:**
```json
{
  "type": "text",
  "text": "[\"blocker\", \"critical\", \"major\", \"minor\", \"trivial\"]"
}
```

The text block is the JSON array of value names (`json.dumps`), in Trac's configured order; the tool returns no `structuredContent`.

**Error Responses:**

- **validation_error:** `enum_type` missing or not one of `priority`, `resolution`, `severity`, `type`, `version`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): enum_type must be one of ['priority', 'resolution', 'severity', 'type', 'version']\n\nAction: Pass a valid enum_type."
  }
  ```

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): [error details]\n\nAction: Try adding a comment instead, or contact ticket owner."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_enum_list",
  "arguments": {
    "enum_type": "priority"
  }
}
```

---

## ticket_enum_delete

**Description:** Delete a value from a ticket enum field (priority, resolution, severity, type, or version). Requires TICKET_ADMIN permission.

**Permission:** TICKET_ADMIN.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `enum_type` | string | **Yes** | - | Enum field to mutate. Must be one of priority, resolution, severity, type, version. (one of `priority`, `resolution`, `severity`, `type`, `version`) |
| `name` | string | **Yes** | - | Enum value name to delete. |

**Success Response:**
```json
{
  "type": "text",
  "text": "priority value 'urgent' deleted."
}
```

The text only; the tool returns no `structuredContent`.

**Error Responses:**

- **validation_error:** `enum_type` missing or not one of `priority`, `resolution`, `severity`, `type`, `version`; or `name` missing or empty
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): name is required\n\nAction: Pass a non-empty enum value name."
  }
  ```

- **not_found:** Value does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): [error details]\n\nAction: Use ticket_search to verify ticket exists."
  }
  ```

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): [error details]\n\nAction: Try adding a comment instead, or contact ticket owner."
  }
  ```

**Example Call:**
```json
{
  "name": "ticket_enum_delete",
  "arguments": {
    "enum_type": "priority",
    "name": "urgent"
  }
}
```

---

## wiki_get

**Description:** Get wiki page content with Markdown output. Returns full content with metadata (version, author, modified date).

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **One of** | - | Wiki page name to retrieve |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |
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
| `page_name` | string | **One of** | - | Wiki page name to create |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |
| `content` | string | **Yes** | - | Page content. TracWiki, stored verbatim |
| `target_cap` | integer | No | `50` | Maximum cross-instance targets the write-time link check probes (default: 50). Anything beyond the cap is reported as target_check_capped, which BLOCKS the write -- an unchecked target must not read as a clean one. Raise this for a legitimately link-dense document; there is deliberately no way to switch the check off. (minimum: 1, maximum: 500) |
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
| `page_name` | string | **One of** | - | Wiki page name to update |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |
| `content` | string | **Yes** | - | Page content. TracWiki, stored verbatim |
| `version` | integer | **Yes** | - | The version you read; checked against the page's current version before the write (minimum: 1) |
| `target_cap` | integer | No | `50` | Maximum cross-instance targets the write-time link check probes (default: 50). Anything beyond the cap is reported as target_check_capped, which BLOCKS the write -- an unchecked target must not read as a clean one. Raise this for a legitimately link-dense document; there is deliberately no way to switch the check off. (minimum: 1, maximum: 500) |
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
| `page_name` | string | **One of** | - | Wiki page name to delete |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |

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

## wiki_get_history

**Description:** Get wiki page revision history newest-first. Returns list of revisions with version, author, lastModified, and comment (commit message). Useful for detecting prior edits or scanning attribution markers in change comments.

**Permission:** WIKI_VIEW.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **One of** | - | Wiki page name to retrieve history for |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |
| `limit` | integer | No | - | Maximum revisions to return, newest first. Omit for all revisions. (minimum: 1) |

**Success Response:**
```json
{
  "type": "text",
  "text": "# DevelopmentGuide history\n\n- v3 by alice at 2025-01-20 10:00: Add the release checklist\n- v2 by bob at 2025-01-12 16:41: (no comment)\n- v1 by alice at 2025-01-05 09:15: Initial draft"
}
```
```json
{
  "page_name": "DevelopmentGuide",
  "revisions": [
    {
      "version": 3,
      "author": "alice",
      "lastModified": "2025-01-20 10:00",
      "comment": "Add the release checklist"
    },
    {
      "version": 2,
      "author": "bob",
      "lastModified": "2025-01-12 16:41",
      "comment": ""
    },
    {
      "version": 1,
      "author": "alice",
      "lastModified": "2025-01-05 09:15",
      "comment": "Initial draft"
    }
  ]
}
```

Revisions are newest first. A version whose metadata cannot be fetched (a gap, or no permission) is skipped, so a partial history comes back rather than none. `comment` is the change comment, `""` when there is none.

**Error Responses:**

- **validation_error:** Missing `page_name`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): page_name is required\n\nAction: Provide page_name parameter."
  }
  ```

- **not_found:** Page does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Wiki page \"NoSuchPage\" does not exist\n\nAction: Use wiki_search to find pages similar to 'NoSuchPage'."
  }
  ```

**Example Call:**
```json
{
  "name": "wiki_get_history",
  "arguments": {
    "page_name": "DevelopmentGuide",
    "limit": 10
  }
}
```

---

## wiki_diff

**Description:** Unified diff between two versions of a wiki page, computed on the stored TracWiki, together with the revisions in the range (version, author, time, change comment), oldest first. The start is from_version (default: the version before to_version; the empty page when to_version is 1) or from_author: the newest version by that author at or before to_version, i.e. everything since that author's last write (identical=true when the author wrote to_version itself). Returns the resolved versions, identical, added/removed line counts and the hunk count; a diff longer than max_chars is cut at a hunk boundary and flagged truncated, with the counts still covering the whole diff.

**Permission:** WIKI_VIEW.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **One of** | - | Wiki page name to diff |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |
| `to_version` | integer | No | - | End version (default: latest) (minimum: 1) |
| `from_version` | integer | No | - | Start version, lower than to_version (default: to_version - 1). Not together with from_author. (minimum: 1) |
| `from_author` | string | No | - | Start at the newest version by this Trac username at or before to_version. An error if that author has none -- never a fall-back to version 1. Not together with from_version. |
| `context` | integer | No | `3` | Unchanged lines around each change (default: 3) (minimum: 0) |
| `max_chars` | integer | No | `50000` | Maximum length of the returned diff (default: 50000) (minimum: 1000) |

`from_version` and `from_author` are mutually exclusive. With neither, the diff starts at the version before `to_version` (skipping deleted ones), or at the empty page when `to_version` is 1. Both bodies are fetched and diffed server-side (on the stored TracWiki, with CRLF read as LF), so only the diff and the change comments reach the caller.

**Success Response:**
```json
{
  "type": "text",
  "text": "# DevelopmentGuide diff: v2 -> v3\n+2 -1 lines in 1 hunk(s)\nRevisions (oldest first):\n- v3 by alice at 2025-01-20 10:00: Add the release checklist\n----\n--- DevelopmentGuide@v2\n+++ DevelopmentGuide@v3\n@@ -1,7 +1,8 @@\n = Release =\n \n Steps:\n-1. Tag the release\n+1. Run the full test suite\n+2. Tag the release\n \n Done.\n See also\n"
}
```
```json
{
  "page_name": "DevelopmentGuide",
  "from_version": 2,
  "to_version": 3,
  "latest_version": 3,
  "identical": false,
  "added": 2,
  "removed": 1,
  "hunks": 1,
  "hunks_shown": 1,
  "truncated": false,
  "minimal": true,
  "line_endings_normalized": false,
  "diff": "--- DevelopmentGuide@v2\n+++ DevelopmentGuide@v3\n@@ -1,7 +1,8 @@\n = Release =\n \n Steps:\n-1. Tag the release\n+1. Run the full test suite\n+2. Tag the release\n \n Done.\n See also\n",
  "revisions": [
    {
      "version": 3,
      "author": "alice",
      "lastModified": "2025-01-20 10:00",
      "comment": "Add the release checklist"
    }
  ]
}
```

`from_version` is `null` when the diff starts from the empty page. `from_author` is echoed back when it was passed. `added` and `removed` count lines over the whole diff; a diff longer than `max_chars` is cut at a hunk boundary, `truncated` is `true`, `hunks_shown` is below `hunks`, and the text ends with a `[truncated: ...]` line. `identical` is `true` when the two bodies are equal (the text then says `Identical: no change in this range.`). `minimal` is `false` when more than 2000 lines changed: one block then replaces everything between the common start and end, and the counts over-state the edit. `line_endings_normalized` is `true` when either body had CRLF line endings.

**Error Responses:**

- **validation_error:** Missing `page_name`, both `from_version` and `from_author`, an empty `from_author`, or `from_version` not below `to_version`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): from_version and from_author are mutually exclusive\n\nAction: Pass one start: from_version=N or from_author='username'."
  }
  ```

- **not_found:** Page does not exist, `to_version` is above the latest, or `from_author` wrote no version at or before `to_version`
  ```json
  {
    "type": "text",
    "text": "Error (not_found): Wiki page \"DevelopmentGuide\" has no version 9 (latest is 3)\n\nAction: Omit to_version for the latest, or list versions with wiki_get_history."
  }
  ```

**Example Call:**
```json
{
  "name": "wiki_diff",
  "arguments": {
    "page_name": "DevelopmentGuide",
    "from_version": 2,
    "to_version": 3
  }
}
```

---

## wiki_conventions

**Description:** Get an instance's wiki conventions in one read-only call: the style its WikiStart declares (a line `'''Wiki style:''' [<prefix>:wiki:<StylePage> <name>]`, or `undeclared`/`malformed`), that style page's body as stored (read from the instance the link names, within the caller's allowed instances; reported unavailable with a reason rather than failing), and every PageTemplates/* page with its body. Bodies are TracWiki, byte-for-byte.

**Permission:** WIKI_VIEW.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| *(none)* | - | - | - | No parameters required |

**Success Response:**
```json
{
  "type": "text",
  "text": "{\n  \"style\": \"agent\",\n  \"declaration_lines\": [...],\n  \"style_page\": {...},\n  \"templates\": [...]\n}"
}
```
```json
{
  "style": "agent",
  "declaration_lines": [
    "'''Wiki style:''' [auto_pm:wiki:Rules/documentation/WikiStyleAgent agent]"
  ],
  "style_page": {
    "instance": "/auto_pm",
    "name": "Rules/documentation/WikiStyleAgent",
    "version": 7,
    "body": "= Wiki style: agent =\n..."
  },
  "templates": [
    {
      "name": "PageTemplates/Rule",
      "version": 2,
      "body": "= Rule: ... =\n..."
    }
  ]
}
```

The text block is the same object as indented JSON (shortened above). The tool reads `WikiStart` and takes the style from its `'''Wiki style:''' [<prefix>:wiki:<StylePage> <name>]` line (outside any `{{{ }}}` block): `style` is that name, or `undeclared` (no such line; `style_page` is `null`) or `malformed` (a line that does not parse, or lines that disagree; `style_page` is `null`). `declaration_lines` are the marker lines found, in document order. `style_page` is read from the instance the link names (this one when the prefix is omitted or is its own), within the caller's allowed instances, and `instance` is that instance's path. `templates` lists every `PageTemplates/*` page of this instance, sorted by name. Bodies are TracWiki, byte-for-byte.

A style page or template that cannot be read is reported in place, not as an error:
```json
{
  "instance": "/other",
  "name": "Rules/documentation/WikiStyleAgent",
  "available": false,
  "reason": "instance_not_allowed",
  "message": "[error details]"
}
```

A template's record has `name`, `available`, `reason` and `message` (no `instance`). `reason` is `not_found`, `permission_denied` or `error` for a failed page read, and, for the style page only, `unknown_instance` or `instance_not_allowed`.

**Error Responses:**

- **permission_denied:** The call could not read `WikiStart` or list the pages (a missing `WikiStart` is not an error: it reads as `undeclared`)
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): [error details]\n\nAction: Contact Trac administrator for write access to wiki pages."
  }
  ```

**Example Call:**
```json
{
  "name": "wiki_conventions",
  "arguments": {}
}
```

---

## wiki_file_push

**Description:** Push a document to a Trac wiki page. Takes the text inline as `content`, or -- only where the server's `file_access` is `local` -- as a `file_path` on the **server's** filesystem; auto-detects format (Markdown/TracWiki), converts if needed, and creates or updates the wiki page. To push a file from your own machine, use the [`trac-mcp` CLI](cli.md#trac-mcp) there: `trac-mcp wiki-push PAGE FILE`.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **One of** | - | Target wiki page name; or pass its alias `page` |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |
| `content` | string | One of | - | The document text, in place of `file_path`; pass this or `file_path` |
| `filename` | string | No | - | With `content`: the source file's name (e.g. `notes.md`), used only for extension-based format detection. With neither `filename` nor `format`, inline content is stored as TracWiki, verbatim |
| `file_path` | string | One of | - | Absolute path on the **server's** filesystem; refused unless `file_access` is `local`; pass this or `content` |
| `encoding` | string | No | detected | The codec of the server-side `file_path`, a Python text codec name such as `cp1251` or `utf-8`. The file is decoded strictly with it, with no fallback to detection, and no "not UTF-8" warning is added (you said what it is). Invalid with `content`, which is already text. An unknown or non-text codec (`base64`, `rot13`) or bytes that do not decode are a `validation_error` naming the codec and byte offset, never the file's content, and nothing is stored |
| `comment` | string | No | `""` | Change comment |
| `format` | string | No | `"auto"` | Source format override: `auto`, `markdown`, or `tracwiki`. Default auto-detects from extension then content |
| `flavor` | string | No | `"commonmark"` | One of `commonmark`, `chatgpt`, `gemini`. Markdown flavour of the source: `chatgpt` converts ChatGPT's `\( \)` and `\[ \]` math (code spans and `{{{#!latex}}}` blocks), favicon citation residue and `utm_*` URL parameters; `gemini` rebuilds a copied Gemini page's code blocks (from the 'Use code with caution' marker), its bullet characters and `[N] (url)` citations; a flavour other than the default `commonmark` makes the source Markdown, so it conflicts with `format` `tracwiki`. With `format` `auto` a non-default flavour converts as Markdown even for inline content with no `filename`. See [Flavour profiles](format-conversion.md#flavour-profiles) |
| `target_cap` | integer | No | `50` | Maximum cross-instance targets the write-time link check probes (default: 50). Anything beyond the cap is reported as target_check_capped, which BLOCKS the write -- an unchecked target must not read as a clean one. Raise this for a legitimately link-dense document; there is deliberately no way to switch the check off. (minimum: 1, maximum: 500) |
| `strip_frontmatter` | boolean | No | `true` | Strip YAML frontmatter from .md files before pushing |
| `force` | boolean | No | `false` | Push Markdown whose front matter says it was exported from Trac (carries `trac_page`). Without it such a push is refused: the Markdown export is one-way |

**Encoding and BOM (Trac /trac_mcp_server #245).** A `file_path` is decoded as strict UTF-8 first, then by charset detection; pass `encoding` (for example `cp1251`) when you know the file's codec, and it is used instead, strictly. When a file is decoded, one leading U+FEFF (a byte-order mark) is removed from the text however it was decoded (not a second one after a codec that consumes the BOM itself, such as `utf-8-sig` or `utf-16`), so a file saved with a UTF-8 BOM no longer stores an invisible character at the head of the page, and the reported encoding of such a file stays `utf-8`. A U+FEFF anywhere else in the text is left alone. Inline `content` is stored as sent, BOM included: inline writes are byte-for-byte, so an older CLI or another client that decoded a BOM'd file itself still stores it. `trac-mcp wiki-push --encoding CODEC` decodes the local file (or stdin) the same way on your machine and sends the text inline; it does not send `encoding`. **Connected MCP sessions keep the old `wiki_file_push` schema (no `encoding`) until they reconnect**; a daemon that predates this does not know `encoding`.

**Refuses exported Markdown (Trac /trac_mcp_server #212).** A Markdown pull (`wiki_file_pull`, `trac-mcp wiki-pull`, `trac-convert --from-wiki ... --to md`) starts with YAML front matter naming its page. `wiki_file_push` reads the text first and, if its front matter is a YAML mapping with a non-empty string `trac_page`, returns a `validation_error` and stores nothing (no Trac call is made): `Refusing to push: this Markdown was exported from Trac page 'P' (instance /proj, version 7); the Markdown export is one-way and pushing it back would re-shape the page.` The action says to edit the page as TracWiki (`wiki_get`, or `trac-mcp wiki-pull P FILE --format tracwiki`) or to pass `force=true`. The check looks at the text only: it does not depend on `format` or `strip_frontmatter`, and it applies to `file_path` input as well. With `force=true` the push proceeds as it always did (the front matter is stripped by `strip_frontmatter` like any other). Front matter without `trac_page` (a Hugo-style `title` / `date`, say) is not refused. Deleting the front matter by hand defeats the guard; that is accepted -- it is there for the accidental pull-edit-push loop, not against a determined user. **Connected MCP sessions keep the old `wiki_file_push` schema (no `force`) until they reconnect**; a daemon that predates the guard neither refuses nor knows `force`.

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

**Description:** Pull a Trac wiki page, converted to the requested format. Markdown is Trac's own rendered HTML for the page translated to Markdown: a one-way export, not meant to be pushed back, that starts with YAML front matter (`title`, `source`, `trac_instance`, `trac_page`, `trac_version`, `exported`; `wiki_file_push` refuses that text unless `force=true`); `format="html"` is Trac's render as a complete minimal HTML document (no CSS) in which the `href` of each link is rewritten (an in-page anchor becomes `#frag`, and `link_rewrites` applies) and everything else is Trac's render unchanged, so links `link_rewrites` does not reach stay absolute; `format="tracwiki"` returns the stored source verbatim. Translator warnings come back in `structuredContent.warnings` (always a list). `link_rewrites` (`{from, to}` URL-prefix rules, longest prefix first) rewrites the links of a Markdown or html pull (never the images; an in-page anchor is written `#frag` and never mapped): given, it replaces the server's configured map (`[]` = no rewriting); omitted, the configured map applies. `structuredContent.link_rewrites_source` says which was used (`call`, `config` or `none`). Where the server's `file_access` is `off` (the http default) the text comes back in `structuredContent.content` and a `file_path` is refused; where it is `local`, `file_path` -- on the **server's** filesystem -- is required, as before. To save a page on your own machine, use `trac-mcp wiki-pull PAGE FILE` there.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **One of** | - | Wiki page name to pull |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |
| `file_path` | string | With `local` | - | Absolute output path on the **server's** filesystem: required where `file_access` is `local`, refused where it is `off` |
| `format` | string | No | `"markdown"` | Output format: `markdown` (Trac's render translated to Markdown), `html` (Trac's render as a complete minimal HTML document: `<!doctype html>`, `<meta charset="utf-8">`, `<title>` the escaped page name, the fragment as `<body>`; no CSS; only the `href` values of links are rewritten, as for `link_rewrites`, and every other byte is Trac's own) or `tracwiki` (the stored source). The `html` read is pinned to the page's current version the same way the `markdown` one is, and reads no source |
| `version` | integer | No | *(latest)* | Specific page version to pull (minimum: 1) |
| `link_rewrites` | array of `{from, to}` | No | *(the server's configured map)* | Link-rewrite rules for `format=markdown` and `format=html`: a link target starting with `from` (non-empty string) has that prefix replaced by `to` (string), the longest `from` first. Replaces the configured map for this call; `[]` disables rewriting. Image sources are never rewritten, and an in-page anchor is written `#frag`, never mapped. The input schema refuses an empty `from`, a non-string value, a missing key and any extra key in a rule (before the handler runs, as an input validation error); the handler's own `validation_error`, returned before Trac is read, covers a duplicate `from`. For `format=tracwiki` the handler ignores the argument (it is not validated), though the schema still applies |

Each link-rewrite object:

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `from` | string | **Yes** | - | URL prefix to replace (non-empty) |
| `to` | string | **Yes** | - | Replacement prefix |

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

`link_rewrites_source` is `call` when the call passed `link_rewrites` (even `[]`), `config` when the server's configured map (`export.link_rewrites` / `TRAC_MCP_LINK_REWRITES`, see [Configuration](configuration.md#link-rewrites-export)) was applied, and `none` when no map was in force -- always `none` for `format=tracwiki`. `warnings` lists what the translator could not map exactly; it is `[]` for `format=tracwiki`, for `format=html` unless its version could not be read, and when nothing was lossy. The same warnings follow the summary line in the text block under `Conversion warnings:`. With a `file_path` (where `file_access` is `local`), `content`/`bytes` are replaced by `file_path` and `bytes_written`.

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
- A Markdown pull (inline or to `file_path`) starts with a front matter block, then one blank line, then the translated page. `title` is the text of the page's first heading (Trac's anchor and icon decorations left out; the page name when there is none), `source` the page's own URL, `trac_instance` the path of the instance's URL (`/proj`; `/` for a URL with no path), `trac_page` the page name, `trac_version` the pinned version (`null` when it could not be read and none was asked for) and `exported` the UTC time of the pull. Strings are double-quoted YAML scalars. `format=html` and `format=tracwiki` output has none. `bytes` and `bytes_written` count the front matter. See [Format Conversion](format-conversion.md#front-matter-on-an-export)
- Link rewriting touches `href`s only (including those inside raw HTML the translator passes through, and, for `format=html`, every `href` attribute of Trac's render, located with an HTML tokenizer so `data-href`, text inside another attribute's value, `<pre>` content, comments, script/style content and the content of `title`, `textarea` and the other text elements are never touched; an unchanged link keeps its bytes, a rewritten one is re-escaped inside its own quote); image `src`s are never changed, a link into the page itself with a fragment (`<trac_url>/wiki/<page>#Heading`, how Trac renders `[#Heading x]`) is written `#Heading` and never mapped by `link_rewrites`, and the link text is kept, so an autolink whose target is rewritten becomes `[original URL](new URL)`
- Markdown by default: Trac's rendered HTML for the page, translated (`wiki.getPageHTML`/`getPageHTMLVersion`), with code-block languages taken from the TracWiki source; use `format: "tracwiki"` to get raw content (no HTML is read)
- For Markdown, the page's version is read first (`getPageInfo`) and the source and the HTML are fetched at that version, so a concurrent edit cannot mix versions
- `format: "html"` reads the version first too and fetches only the HTML at it (`getPageHTML`/`getPageHTMLVersion`; no source is read); `converted` is `true` (it is not the stored source). If the version cannot be read the HTML is the latest and `warnings` says it was not pinned
- An inline result over the server's `max_inline_bytes` (default 5 MiB) is refused with a `validation_error`
- Parent directory of `file_path` must exist, and is checked before Trac is called
- Fetches page info separately for version metadata. For `markdown` and `html` it is read first and the HTML (and, for `markdown`, the source) is fetched at that version; `html` reads no source. For `tracwiki` it is read after the source. The reported `version` is the info's when usable, else the requested `version`, else `1` (an unpinned read of an instance whose info answer is unusable, which also adds a `warnings` entry for `markdown` and `html`)

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
  "text": "File: /home/USER/docs/design.md\nFormat: markdown\nEncoding: utf-8\nSize: 4521 bytes"
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

## wiki_attachment_put

**Description:** Upload an attachment to a Trac wiki page, as content_base64 (or, only where this server's file_access is 'local', a file_path on the SERVER's filesystem). To attach a file from your own machine without putting its bytes in the transcript, run the trac-mcp CLI there: `trac-mcp attach-put --page PAGE FILE`. Internally calls wiki.putAttachmentEx with a 'page_name/filename' path. Target wiki page must exist (use `wiki_create` or `wiki_file_push` first).

**Permission:** WIKI_MODIFY.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **One of** | - | Wiki page name to attach to. The page must already exist; or pass its alias `page` |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |
| `filename` | string | No | - | Attachment filename stored on the page. Defaults to the basename of file_path; required with content_base64. |
| `content_base64` | string | One of | - | The attachment bytes, standard base64, in place of file_path. Requires filename. Pass this or `file_path`. |
| `file_path` | string | One of | - | Absolute path on the SERVER's filesystem. Refused unless the server's file_access is 'local' (it is 'off' over http). Pass this or `content_base64`. |
| `description` | string | No | `""` | Attachment description |
| `replace` | boolean | No | `false` | If true, overwrite an existing attachment with the same filename. If false and a collision occurs, Trac stores the file under a renamed path; see attached_filename in the response. Default false. |

Pass exactly one of `content_base64` and `file_path`. The path form is refused unless the server's `file_access` is `local` (the stdio default; `off` over http, see [File Access](http-transport.md#file-access-file_access)). An inline upload is capped by the server's `max_inline_bytes` (default 5 MiB). To attach a file from your own machine without putting its bytes in the transcript, run `trac-mcp attach-put --page PAGE FILE` there.

**Success Response:**
```json
{
  "type": "text",
  "text": "Uploaded attachment 'diagram.png' to wiki page 'DevelopmentGuide' (70 bytes, replace=False)"
}
```
```json
{
  "page_name": "DevelopmentGuide",
  "requested_filename": "diagram.png",
  "attached_filename": "diagram.png",
  "renamed_on_collision": false,
  "page_path": "DevelopmentGuide/diagram.png",
  "file_path": null,
  "source": "inline content",
  "bytes_uploaded": 70,
  "replace": false,
  "description": "Architecture overview"
}
```

`page_path` is the `PageName/filename` path Trac stores it under. `file_path` is the path the caller passed (`null` for an inline upload) and `source` is that path or `"inline content"`. When `replace` is false and the name is taken, Trac stores the file under another name: `attached_filename` then differs from `requested_filename` and `renamed_on_collision` is `true`.

**Error Responses:**

- **validation_error:** Missing `page_name`, neither or both of `content_base64` and `file_path`, invalid base64, no `filename` with `content_base64`, or an inline payload over `max_inline_bytes`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): filename is required with content_base64\n\nAction: Provide the attachment filename to store."
  }
  ```

- **permission_denied:** `file_path` while `file_access` is `off`
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): file_path is refused: file_access is 'off' on this server, so a path would name a file on the server's machine, not yours.\n\nAction: Use the trac-mcp CLI on your own machine (trac-mcp attach-put --page PAGE FILE), or pass the bytes inline instead of a path."
  }
  ```

- **not_found:** The wiki page does not exist (create it first with `wiki_create` or `wiki_file_push`)
  ```json
  {
    "type": "text",
    "text": "Error (not_found): [error details]\n\nAction: Use wiki_search to find pages similar to 'NoSuchPage'."
  }
  ```

**Example Call:**
```json
{
  "name": "wiki_attachment_put",
  "arguments": {
    "page_name": "DevelopmentGuide",
    "filename": "diagram.png",
    "content_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAHbyWPQAAAABJRU5ErkJggg==",
    "description": "Architecture overview"
  }
}
```

---

## wiki_attachment_get

**Description:** Download a wiki attachment. Where the server's file_access is 'off' (the http default) the bytes come back base64 in structuredContent.content_base64 and an output_path is refused; where it is 'local', output_path (a path on the server's own filesystem) is required. To save one on your own machine, run `trac-mcp attach-get --page PAGE NAME FILE` there. Internally calls wiki.getAttachment with a 'page_name/filename' path.

**Permission:** WIKI_VIEW.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **One of** | - | Wiki page name the attachment belongs to |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |
| `filename` | string | **Yes** | - | Attachment filename to download |
| `output_path` | string | With `local` | - | Absolute path on the SERVER's filesystem; the parent directory must already exist. Refused unless the server's file_access is 'local', and required there. Where file_access is 'off', omit it to get the bytes inline. |

**Success Response:**

With `file_access` `off` (the http default) the bytes come back inline:
```json
{
  "type": "text",
  "text": "Fetched attachment 'diagram.png' from wiki page 'DevelopmentGuide' (70 bytes); the bytes are base64 in structuredContent.content_base64"
}
```
```json
{
  "page_name": "DevelopmentGuide",
  "filename": "diagram.png",
  "page_path": "DevelopmentGuide/diagram.png",
  "content_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAHbyWPQAAAABJRU5ErkJggg==",
  "bytes": 70
}
```

With `file_access` `local` the bytes are written to `output_path` and stay out of the result:
```json
{
  "type": "text",
  "text": "Downloaded attachment 'diagram.png' from wiki page 'DevelopmentGuide' to /tmp/diagram.png (70 bytes)"
}
```
```json
{
  "page_name": "DevelopmentGuide",
  "filename": "diagram.png",
  "page_path": "DevelopmentGuide/diagram.png",
  "output_path": "/tmp/diagram.png",
  "bytes_written": 70
}
```

An inline result over the server's `max_inline_bytes` is refused with a `validation_error`.

**Error Responses:**

- **validation_error:** Missing `page_name` or `filename`, or no `output_path` where `file_access` is `local`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): filename is required\n\nAction: Provide filename parameter."
  }
  ```

- **permission_denied:** `output_path` while `file_access` is `off`
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): output_path is refused: file_access is 'off' on this server, so a path would name a file on the server's machine, not yours.\n\nAction: Use the trac-mcp CLI on your own machine (trac-mcp attach-get --page PAGE NAME FILE), or pass the bytes inline instead of a path."
  }
  ```

- **not_found:** Page or attachment does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): [error details]\n\nAction: Use wiki_search to find pages similar to 'NoSuchPage'."
  }
  ```

**Example Call:**
```json
{
  "name": "wiki_attachment_get",
  "arguments": {
    "page_name": "DevelopmentGuide",
    "filename": "diagram.png"
  }
}
```

---

## wiki_attachment_list

**Description:** List attachments on a Trac wiki page. Returns a flat list of 'page_name/filename' path strings (NOT tuples — this differs from ticket_attachment_list).

**Permission:** WIKI_VIEW.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **One of** | - | Wiki page name to list attachments for |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |

**Success Response:**
```json
{
  "type": "text",
  "text": "Wiki page 'DevelopmentGuide' has 2 attachment(s):\n- DevelopmentGuide/diagram.png\n- DevelopmentGuide/release-notes.pdf"
}
```
```json
{
  "page_name": "DevelopmentGuide",
  "count": 2,
  "attachments": [
    "DevelopmentGuide/diagram.png",
    "DevelopmentGuide/release-notes.pdf"
  ]
}
```

`attachments` is a flat list of `PageName/filename` strings, not the per-attachment records `ticket_attachment_list` returns (no size, author or description). A page with none answers `Wiki page 'DevelopmentGuide' has no attachments.` with `count` `0`.

**Error Responses:**

- **validation_error:** Missing `page_name`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): page_name is required\n\nAction: Provide page_name parameter."
  }
  ```

**Known failure:** a missing page (or the wrong `instance`) is not an error. Neither the handler nor TracXMLRPC's `wiki.listAttachments` checks that the page exists, so the call answers `Wiki page 'NoSuchPage' has no attachments.` with `count` `0`, exactly as for an existing page with none. Check the page with `wiki_get` first when that matters. Tracked as Trac /trac_mcp_server #253.

**Example Call:**
```json
{
  "name": "wiki_attachment_list",
  "arguments": {
    "page_name": "DevelopmentGuide"
  }
}
```

---

## wiki_attachment_delete

**Description:** Delete a wiki attachment permanently. Requires WIKI_DELETE permission. Internally calls wiki.deleteAttachment with a 'page_name/filename' path.

**Permission:** WIKI_DELETE.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `page_name` | string | **One of** | - | Wiki page name the attachment belongs to |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |
| `filename` | string | **Yes** | - | Attachment filename to delete |

**Success Response:**
```json
{
  "type": "text",
  "text": "Deleted attachment 'diagram.png' from wiki page 'DevelopmentGuide'."
}
```
```json
{
  "page_name": "DevelopmentGuide",
  "filename": "diagram.png",
  "page_path": "DevelopmentGuide/diagram.png"
}
```

**Error Responses:**

- **permission_denied:** Insufficient permissions
  ```json
  {
    "type": "text",
    "text": "Error (permission_denied): [error details]\n\nAction: Deleting wiki attachments requires WIKI_DELETE. Contact Trac administrator."
  }
  ```

- **validation_error:** Missing `page_name` or `filename`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): filename is required\n\nAction: Provide filename parameter."
  }
  ```

- **not_found:** Page or attachment does not exist
  ```json
  {
    "type": "text",
    "text": "Error (not_found): [error details]\n\nAction: Use wiki_search to find pages similar to 'NoSuchPage'."
  }
  ```

**Example Call:**
```json
{
  "name": "wiki_attachment_delete",
  "arguments": {
    "page_name": "DevelopmentGuide",
    "filename": "diagram.png"
  }
}
```

---

## milestone_list

**Description:** List milestone names, optionally only those starting with a prefix. With `details` or `status`, also the due and completion date of each listed milestone, read in `system.multicall` batches of 50 (one `ticket.milestone.get` each, one at a time if the server has no `system.multicall`) instead of one `milestone_get` call per milestone. Requires MILESTONE_VIEW permission.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `prefix` | string | No | - | Only milestones whose name starts with this text (case-sensitive) |
| `details` | boolean | No | `false` | Also return the due and completion date of each listed milestone |
| `status` | string | No | `all` | `open` (no completion date), `completed` (with one) or `all`; anything but `all` implies `details` |

**Success Response (names only):**
```json
{
  "type": "text",
  "text": "v1.0\nv1.1\nv2.0\nFuture"
}
```

Structured content: `{"milestones": ["v1.0", "v1.1", "v2.0", "Future"]}`.

**Success Response (`prefix: "v"`, `status: "open"`):**
```json
{
  "type": "text",
  "text": "2 milestone(s) (open) starting with 'v':\nv1.1 | due: 20250630T22:00:00 | completed: (Not set)\nv2.0 | due: (Not set) | completed: (Not set)"
}
```

Structured content: `{"milestones": ["v1.1", "v2.0"], "details": [{"name": "v1.1", "due": "20250630T22:00:00", "completed": null}, {"name": "v2.0", "due": null, "completed": null}], "failed": []}`. A milestone that could not be read is listed in `failed`, named in a warning and absent from `details`. With `status` `all` it stays in `milestones` and in the count, and its text line reads `<name> | (failed to load)`; with `open` or `completed` it cannot be classified, so it is left out of `milestones`, the count and the lines, and the warning says it is not counted. A prefix that matches nothing, with `details` or `status`, answers `0 milestone(s) (completed) starting with 'X': none` and `{"milestones": [], "details": [], "failed": []}`; without them it answers `No milestones found starting with 'X'.` (or `No milestones found.`) and `{"milestones": []}`.

**Example Calls:**
```json
{
  "name": "milestone_list",
  "arguments": {}
}
```

```json
{
  "name": "milestone_list",
  "arguments": {
    "prefix": "v",
    "status": "open"
  }
}
```

---

## milestone_get

**Description:** Get milestone details by name. Returns name, due date, completion date, and description. Requires MILESTONE_VIEW permission.

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
| `target_cap` | integer | No | `50` | Maximum cross-instance targets the write-time link check probes (default: 50). Anything beyond the cap is reported as target_check_capped, which BLOCKS the write -- an unchecked target must not read as a clean one. Raise this for a legitimately link-dense document; there is deliberately no way to switch the check off. (minimum: 1, maximum: 500) |
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
| `target_cap` | integer | No | `50` | Maximum cross-instance targets the write-time link check probes (default: 50). Anything beyond the cap is reported as target_check_capped, which BLOCKS the write -- an unchecked target must not read as a clean one. Raise this for a legitimately link-dense document; there is deliberately no way to switch the check off. (minimum: 1, maximum: 500) |
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

## timeline_search

**Description:** Search the Trac timeline -- tickets, wiki edits, changesets, milestones and more, ordered together and author-attributed. Each event's title/description/url are rendered server-side as plain text, not TracWiki or Markdown.

**Permission:** TIMELINE_VIEW. **Needs the `tracrpc_comment` Trac plugin** (`plugins/tracrpc_comment/`, see [plugins/README.md](../../plugins/README.md)): stock XmlRpcPlugin has no timeline methods. Without it the call answers `method_not_available` with an install hint (see the error below).

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `start` | integer | **Yes** | - | Start of the time range, as a unix timestamp (seconds since epoch), inclusive (required). |
| `stop` | integer | **Yes** | - | End of the time range, as a unix timestamp (seconds since epoch), inclusive (required). |
| `filters` | array of string | No | - | Event kinds to include (e.g. ticket, wiki, changeset, milestone -- available kinds depend on what's enabled on this instance). Omit for every kind; pass an empty list to select none. |
| `max_results` | integer | No | `100` | Maximum events to return, newest first (default: 100, max: 1000). (minimum: 1, maximum: 1000) |

`start` and `stop` are unix timestamps in seconds. `max_results` is clamped to the range 1 to 1000. With `filters` omitted the tool first asks the server which event kinds exist (`timeline.getFilters`) and requests all of them; an empty list selects none.

**Success Response:**
```json
{
  "type": "text",
  "text": "2 timeline event(s), newest first:\n\n- [ticket] 20260116T09:05:00 alice: Ticket #42 (defect): Fix login timeout issue closed (http://trac.example.com/project/ticket/42#comment:4)\n- [wiki] 20260115T17:20:00 bob: DevelopmentGuide edited (http://trac.example.com/project/wiki/DevelopmentGuide?version=3)"
}
```
```json
{
  "events": [
    {
      "kind": "ticket",
      "date": "20260116T09:05:00",
      "author": "alice",
      "title": "Ticket #42 (defect): Fix login timeout issue closed",
      "description": "Fixed in the session middleware.",
      "url": "http://trac.example.com/project/ticket/42#comment:4"
    },
    {
      "kind": "wiki",
      "date": "20260115T17:20:00",
      "author": "bob",
      "title": "DevelopmentGuide edited",
      "description": "Add the release checklist",
      "url": "http://trac.example.com/project/wiki/DevelopmentGuide?version=3"
    }
  ],
  "total": 2,
  "showing": 2
}
```

`title`, `description` and `url` are rendered server-side as plain text, not TracWiki or Markdown, and passed through unconverted. `url` is absolute (the plugin renders with `absurls=True`), so it carries the Trac instance's base URL. `date` is Trac's compact `YYYYMMDDTHH:MM:SS` string. `total` and `showing` are both the number of events returned. An empty range answers `No timeline events in the given range.` with `{"events": [], "total": 0, "showing": 0}`.

**Error Responses:**

- **method_not_available:** The `tracrpc_comment` plugin is not installed or enabled on the Trac server
  ```json
  {
    "type": "text",
    "text": "Error (method_not_available): RPC method \"timeline.getFilters\" not found\n\nAction: This tool needs the 'tracrpc_comment' plugin on the Trac server. Install it from the trac-mcp-server repo's plugins/ directory and enable it with: trac-admin <env> config set components 'tracrpc_comment.*' enabled"
  }
  ```
  With `filters` passed the missing method is `timeline.getEvents`.

- **validation_error:** Missing `start` or `stop`
  ```json
  {
    "type": "text",
    "text": "Error (validation_error): start, stop is required\n\nAction: Provide start, stop."
  }
  ```

**Example Call:**
```json
{
  "name": "timeline_search",
  "arguments": {
    "start": 1768521600,
    "stop": 1768608000,
    "filters": ["ticket", "wiki"],
    "max_results": 50
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
| `flavor` | string | No | `"commonmark"` | One of `commonmark`, `chatgpt`, `gemini`. Markdown flavour of the source: `chatgpt` converts ChatGPT's `\( \)` and `\[ \]` math (code spans and `{{{#!latex}}}` blocks), favicon citation residue and `utm_*` URL parameters; `gemini` rebuilds a copied Gemini page's code blocks (from the 'Use code with caution' marker), its bullet characters and `[N] (url)` citations; a flavour other than the default `commonmark` makes the source Markdown, so it conflicts with `format` `tracwiki`. The Markdown-source checks (`list_item_split`, `tracwiki_markup_in_markdown`, ...) read the flavour-normalised text, and the flavour's own notes come back as `conversion_warning`s. See [Flavour profiles](format-conversion.md#flavour-profiles) |
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
| `target_cap` | integer | No | `50` | Maximum cross-instance targets to probe (minimum: 1, maximum: 500). The cap keeps the FIRST N in document order; anything beyond it is reported as `target_check_capped` rather than checked |
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
| `page_name` | string | **One of** | - | Wiki page name to render-check |
| `page` | string | **One of** | - | Alias for `page_name`; pass one of the two |
| `version` | integer | No | - | Specific version to check (defaults to latest) |
| `check_targets` | boolean | No | `true` | Live-probe cross-instance wiki targets found in the render |
| `target_cap` | integer | No | `50` | Maximum cross-instance targets to probe (minimum: 1, maximum: 500). The cap keeps the FIRST N in document order; anything beyond it is reported as `target_check_capped` rather than checked |
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
