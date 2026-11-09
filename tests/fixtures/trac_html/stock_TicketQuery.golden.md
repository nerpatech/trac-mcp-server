# TicketQuery Wiki Macro

The TicketQuery macro lets you display ticket information anywhere that accepts [WikiFormatting](https://trac.example.org/trac_mcp_server/wiki/WikiFormatting).
The query language used by the `[[TicketQuery]]` macro is described in the [TracQuery](https://trac.example.org/trac_mcp_server/wiki/TracQuery#QueryLanguage) page.

## Usage

### `[[TicketQuery]]`

Wiki macro listing tickets that match certain criteria.

This macro accepts a comma-separated list of keyed parameters,
in the form "key=value".

If the key is the name of a field, the value must use the syntax
of a filter specifier as defined in [TracQuery#QueryLanguage](https://trac.example.org/trac_mcp_server/wiki/TracQuery#QueryLanguage).
Note that this is *not* the same as the simplified URL syntax
used for `query:` links starting with a `?` character. Commas (`,`)
can be included in field values by escaping them with a backslash (`\`).

Groups of field constraints to be OR-ed together can be separated by a
literal `or` argument.

In addition to filters, several other named parameters can be used
to control how the results are presented. All of them are optional.

The `format` parameter determines how the list of tickets is
presented:

- **list** -- the default presentation is to list the ticket ID next
  to the summary, with each ticket on a separate line.
- **compact** -- the tickets are presented as a comma-separated
  list of ticket IDs.
- **count** -- only the count of matching tickets is displayed
- **rawcount** -- only the count of matching tickets is displayed,
  not even with a link to the corresponding query (*since 1.1.1*)
- **table** -- a view similar to the custom query view (but without
  the controls)
- **progress** -- a view similar to the milestone progress bars

The `max` parameter can be used to limit the number of tickets shown
(defaults to **0**, i.e. no maximum).

The `order` parameter sets the field used for ordering tickets
(defaults to **id**).

The `desc` parameter indicates whether the order of the tickets
should be reversed (defaults to **false**).

The `group` parameter sets the field used for grouping tickets
(defaults to not being set).

The `groupdesc` parameter indicates whether the natural display
order of the groups should be reversed (defaults to **false**).

The `verbose` parameter can be set to a true value in order to
get the description for the listed tickets. For **table** format only.
*deprecated in favor of the `rows` parameter*

The `rows` parameter can be used to specify which field(s) should
be viewed as a row, e.g. `rows=description|summary`

The `col` parameter can be used to specify which fields should
be viewed as columns. For **table** format only.

For compatibility with Trac 0.10, if there's a last positional parameter
given to the macro, it will be used to specify the `format`.
Also, using "&" as a field separator still works (except for `order`)
but is deprecated.

## Examples

<table>
<tr><th> <strong>Example</strong> </th><th> <strong>Result</strong> </th><th> <strong>Macro</strong>
</th></tr><tr>
<th>Number of <a href="https://trac.example.org/trac_mcp_server/query?milestone=&amp;status=new&amp;order=priority">Triage tickets</a>: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?milestone=&amp;status=new&amp;max=0&amp;order=id">23</a></strong>
</td><td> <code>[[TicketQuery(status=new&amp;milestone=,count)]]</code>
</td></tr><tr>
<th>Number of new tickets: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?status=new&amp;max=0&amp;order=id">23</a></strong>
</td><td> <code>[[TicketQuery(status=new,count)]]</code>
</td></tr><tr>
<th>Number of reopened tickets: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?status=reopened&amp;max=0&amp;order=id">0</a></strong>
</td><td> <code>[[TicketQuery(status=reopened,count)]]</code>
</td></tr><tr>
<th>Number of assigned tickets: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?status=assigned&amp;max=0&amp;order=id">2</a></strong>
</td><td> <code>[[TicketQuery(status=assigned,count)]]</code>
</td></tr><tr>
<th>Number of invalid tickets: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?resolution=invalid&amp;status=closed&amp;max=0&amp;order=id">1</a></strong>
</td><td> <code>[[TicketQuery(status=closed,resolution=invalid,count)]]</code>
</td></tr><tr>
<th>Number of worksforme tickets: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?resolution=worksforme&amp;status=closed&amp;max=0&amp;order=id">0</a></strong>
</td><td> <code>[[TicketQuery(status=closed,resolution=worksforme,count)]]</code>
</td></tr><tr>
<th>Number of duplicate tickets: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?resolution=duplicate&amp;status=closed&amp;max=0&amp;order=id">4</a></strong>
</td><td> <code>[[TicketQuery(status=closed,resolution=duplicate,count)]]</code>
</td></tr><tr>
<th>Number of wontfix tickets: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?resolution=wontfix&amp;status=closed&amp;max=0&amp;order=id">2</a></strong>
</td><td> <code>[[TicketQuery(status=closed,resolution=wontfix,count)]]</code>
</td></tr><tr>
<th>Number of fixed tickets: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?resolution=fixed&amp;status=closed&amp;max=0&amp;order=id">182</a></strong>
</td><td> <code>[[TicketQuery(status=closed,resolution=fixed,count)]]</code>
</td></tr><tr>
<th>Number of untriaged tickets (milestone unset): 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?milestone=&amp;status=!closed&amp;max=0&amp;order=id">25</a></strong>
</td><td> <code>[[TicketQuery(status!=closed,milestone=,count)]]</code>
</td></tr><tr>
<th>Total number of tickets: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?max=0&amp;order=id">216</a></strong>
</td><td> <code>[[TicketQuery(count)]]</code>
</td></tr><tr>
<th>Number of tickets reported <strong>or</strong> owned by current user: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?reporter=%24USER&amp;or&amp;owner=%24USER&amp;max=0&amp;order=id">214</a></strong>
</td><td> <code>[[TicketQuery(reporter=$USER,or,owner=$USER,count)]]</code>
</td></tr><tr>
<th>Number of tickets created this month: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?time=thismonth..&amp;max=0&amp;order=id">97</a></strong>
</td><td> <code>[[TicketQuery(created=thismonth..,count)]]</code>
</td></tr><tr>
<th>Number of closed Firefox tickets: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?keywords=~firefox&amp;status=closed&amp;max=0&amp;order=id">0</a></strong>
</td><td> <code>[[TicketQuery(status=closed,keywords~=firefox,count)]]</code>
</td></tr><tr>
<th>Number of closed Opera tickets: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?keywords=~opera&amp;status=closed&amp;max=0&amp;order=id">0</a></strong>
</td><td> <code>[[TicketQuery(status=closed,keywords~=opera,count)]]</code>
</td></tr><tr>
<th>Number of closed tickets affecting Firefox and Opera: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?keywords=~firefox+opera&amp;status=closed&amp;max=0&amp;order=id">0</a></strong>
</td><td> <code>[[TicketQuery(status=closed,keywords~=firefox opera,count)]]</code>
</td></tr><tr>
<th>Number of closed tickets affecting Firefox or Opera: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?keywords=~firefox&amp;keywords=~opera&amp;status=closed&amp;max=0&amp;order=id">0</a></strong>
</td><td> <code>[[TicketQuery(status=closed,keywords~=firefox|opera,count)]]</code>
</td></tr><tr>
<th>Number of tickets that affect Firefox or are closed and affect Opera: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?keywords=~firefox&amp;or&amp;keywords=~opera&amp;status=closed&amp;max=0&amp;order=id">0</a></strong>
</td><td> <code>[[TicketQuery(status=closed,keywords~=opera,or,keywords~=firefox,count)]]</code>
</td></tr><tr>
<th>Number of closed Firefox tickets that don't affect Opera: 
</th><td> <strong><a href="https://trac.example.org/trac_mcp_server/query?keywords=~firefox+-+opera&amp;status=closed&amp;max=0&amp;order=id">0</a></strong>
</td><td> <code>[[TicketQuery(status=closed,keywords~=firefox -opera,count)]]</code>
</td></tr><tr>
<th>Last 3 modified tickets: 
</th><td><strong><span><a href="/trac_mcp_server/ticket/206">#206</a>, <a href="/trac_mcp_server/ticket/216">#216</a>, <a href="/trac_mcp_server/ticket/215">#215</a></span></strong>
</td><td> <code>[[TicketQuery(max=3,order=modified,desc=1,compact)]]</code>
</td></tr><tr>
<th rowspan="2,"><p>
Details of ticket <a href="https://trac.example.org/trac_mcp_server/ticket/1">#1</a>:
</p>
</th><td style="border-bottom: 0"></td><td><p>
<code>[[TicketQuery(id=1,col=id|owner|reporter,rows=summary,table)]]</code>
</p>
</td></tr><tr>
<td colspan="2," style="border-top: 0"><p>
</p>
<div>
<table>
<caption>
</caption>
<thead>
<tr>
<th>
<a href="https://trac.example.org/trac_mcp_server/query?id=1&amp;max=0&amp;order=id&amp;desc=1&amp;col=id&amp;col=owner&amp;col=reporter&amp;row=summary">Ticket</a>
</th>
<th>
<a href="https://trac.example.org/trac_mcp_server/query?id=1&amp;max=0&amp;order=owner&amp;col=id&amp;col=owner&amp;col=reporter&amp;row=summary">Owner</a>
</th>
<th>
<a href="https://trac.example.org/trac_mcp_server/query?id=1&amp;max=0&amp;order=reporter&amp;col=id&amp;col=owner&amp;col=reporter&amp;row=summary">Reporter</a>
</th>
</tr>
</thead>
<tbody>
<tr>
<td><a href="/trac_mcp_server/ticket/1">#1</a></td>
<td>
<span class="trac-author-user">agent_rpc</span>
</td>
<td>
<span class="trac-author">nerpa_admin</span>
</td>
</tr>
<tr>
<th>Summary</th>
<td colspan="2">
          README Quick Start section should mention .auto_pm/config.yml location
        </td>
</tr>
</tbody>
</table>
</div><p>
</p>
</td></tr><tr>
</tr></table>

### Format: list

```
[[TicketQuery(version=0.6|0.7&resolution=duplicate)]]
```

This is displayed as:

> No results

```
[[TicketQuery(id=123)]]
```

This is displayed as:

> [#123](/trac_mcp_server/ticket/123)
> :   wiki_conventions(instance): one read-only call returns an instance's declared wiki style, the style page and its PageTemplates

### Format: compact

```
[[TicketQuery(version=0.6|0.7&resolution=duplicate, compact)]]
```

This is displayed as:

> No results

### Format: count

```
[[TicketQuery(version=0.6|0.7&resolution=duplicate, count)]]
```

This is displayed as:

> [0](https://trac.example.org/trac_mcp_server/query?resolution=duplicate&version=0.6&version=0.7&max=0&order=id)

### Format: progress

```
[[TicketQuery(milestone=0.12.8&group=type,format=progress)]]
```

This is displayed as:



### Format: table

You can choose the columns displayed in the table format (`format=table`) using `col=<field>`. You can specify multiple fields and the order they are displayed in by placing pipes (`|`) between the columns:

```
[[TicketQuery(max=3,status=closed,order=id,desc=1,format=table,col=resolution|summary|owner|reporter)]]
```

This is displayed as:

## Results (1 - 3 of 191)

| [Ticket](https://trac.example.org/trac_mcp_server/query?status=closed&max=3&order=id&col=id&col=resolution&col=summary&col=owner&col=reporter) | [Resolution](https://trac.example.org/trac_mcp_server/query?status=closed&max=3&order=resolution&col=id&col=resolution&col=summary&col=owner&col=reporter) | [Summary](https://trac.example.org/trac_mcp_server/query?status=closed&max=3&order=summary&col=id&col=resolution&col=summary&col=owner&col=reporter) | [Owner](https://trac.example.org/trac_mcp_server/query?status=closed&max=3&order=owner&col=id&col=resolution&col=summary&col=owner&col=reporter) | [Reporter](https://trac.example.org/trac_mcp_server/query?status=closed&max=3&order=reporter&col=id&col=resolution&col=summary&col=owner&col=reporter) |
| --- | --- | --- | --- | --- |
| [#202](/trac_mcp_server/ticket/202) | fixed | [OIDC: answer 401 for an expired bearer token (GitHub PR 134)](/trac_mcp_server/ticket/202) | agent_rpc | agent_rpc |
| [#195](/trac_mcp_server/ticket/195) | duplicate | [tracwiki_to_markdown: after a column-0 list item (\`- a\` / \`\* a\`) and a blank line, an indented marker is nested by the round trip, though Trac opens a new list](/trac_mcp_server/ticket/195) | agent_rpc | agent_rpc |
| [#192](/trac_mcp_server/ticket/192) | fixed | [tracwiki_to_markdown: an item's continuation after a stashed processor block ({{{#!comment}}}, {{{#!td}}}) leaves the item on the round trip](/trac_mcp_server/ticket/192) | agent_rpc | agent_rpc |

#### Full rows

In *table* format you can specify full rows using `rows=<field>`:

```
[[TicketQuery(max=3,status=closed,order=id,desc=1,format=table,col=resolution|summary|owner|reporter,rows=description)]]
```

This is displayed as:

## Results (1 - 3 of 191)

<table>
<caption>
</caption>
<thead>
<tr>
<th>
<a href="https://trac.example.org/trac_mcp_server/query?status=closed&amp;max=3&amp;order=id&amp;col=id&amp;col=resolution&amp;col=summary&amp;col=owner&amp;col=reporter&amp;row=description">Ticket</a>
</th>
<th>
<a href="https://trac.example.org/trac_mcp_server/query?status=closed&amp;max=3&amp;order=resolution&amp;col=id&amp;col=resolution&amp;col=summary&amp;col=owner&amp;col=reporter&amp;row=description">Resolution</a>
</th>
<th>
<a href="https://trac.example.org/trac_mcp_server/query?status=closed&amp;max=3&amp;order=summary&amp;col=id&amp;col=resolution&amp;col=summary&amp;col=owner&amp;col=reporter&amp;row=description">Summary</a>
</th>
<th>
<a href="https://trac.example.org/trac_mcp_server/query?status=closed&amp;max=3&amp;order=owner&amp;col=id&amp;col=resolution&amp;col=summary&amp;col=owner&amp;col=reporter&amp;row=description">Owner</a>
</th>
<th>
<a href="https://trac.example.org/trac_mcp_server/query?status=closed&amp;max=3&amp;order=reporter&amp;col=id&amp;col=resolution&amp;col=summary&amp;col=owner&amp;col=reporter&amp;row=description">Reporter</a>
</th>
</tr>
</thead>
<tbody>
<tr>
<td><a href="/trac_mcp_server/ticket/202">#202</a></td>
<td>
          fixed
        </td>
<td>
<a href="/trac_mcp_server/ticket/202">OIDC: answer 401 for an expired bearer token (GitHub PR 134)</a>
</td>
<td>
<span class="trac-author-user">agent_rpc</span>
</td>
<td>
<span class="trac-author-user">agent_rpc</span>
</td>
</tr>
<tr>
<th>Description</th>
<td colspan="4">
<p>
Contributor PR: <a href="https://github.com/nerpatech/trac-mcp-server/pull/134">https://github.com/nerpatech/trac-mcp-server/pull/134</a> (by thekk1, a follow-up to their PR 109).
</p>
<h2 id="Problem">Problem</h2>
<p>
Under <code>TRAC_MCP_OIDC_RPC_URL</code>, an expired access token is forwarded to Trac, whose mod_auth_openidc rejects it. That 401 reaches the MCP client only as text inside a tool result with HTTP 200. A client that refreshes its token on a 401 from the MCP endpoint (LibreChat) never sees one, so every later call fails until the user reconnects by hand.
</p>
<h2 id="ThePR">The PR</h2>
<p>
<code>OidcTokenRequiredMiddleware</code> reads the <code>exp</code> claim of a JWT bearer token, without checking the signature, and answers <code>401</code> with <code>WWW-Authenticate: Bearer error="invalid_token"</code> once <code>exp</code> has passed or is less than 30 s away. Tokens that are not JWTs, or have no numeric <code>exp</code>, pass through as before. Files: <code>src/trac_mcp_server/mcp/http_app.py</code>, <code>tests/test_mcp/test_http_app.py</code>, <code>CHANGELOG.md</code>.
</p>
<h2 id="Localtriage2026-11-08">Local triage (2026-11-08)</h2>
<p>
Merged onto master <code>cc9f971</code> in a scratch worktree:
</p>
<ul><li>The only conflict is <code>CHANGELOG.md</code> (both add a line at the top of <code>### Fixed</code>); keeping both resolves it.
</li><li><code>ruff check</code> and <code>ruff format --check</code> clean; pytest 5398 passed, 105 skipped.
</li><li>Seeded: with the middleware's expiry check disabled, <code>TestOidcExpiredToken::test_expired_jwt_rejected_with_invalid_token</code> fails (200 instead of 401).
</li></ul><p>
Review findings, posted to the PR as a review comment:
</p>
<ol><li><code>_bearer_token_expired</code> catches <code>binascii.Error</code> and <code>ValueError</code> but not <code>RecursionError</code>. A deeply nested JSON payload makes <code>json.loads</code> raise it, so an unauthenticated caller gets a 500 with a traceback instead of a pass-through. Fix: add <code>RecursionError</code> to the <code>except</code>, plus a test.
</li><li>The class docstring still says "The token's value is not validated here", contradicting the new paragraph. It should say the signature is not validated.
</li></ol><p>
Probed and fine: <code>"exp": NaN</code> passes through, <code>-Infinity</code> counts as expired, and a non-UTF-8 payload, an integer over 4300 digits and a non-ASCII segment all pass through.
</p>
<h2 id="Next">Next</h2>
<p>
Waiting on the contributor to rebase on master and fix the two findings. Then re-verify and land with <code>/finish &lt;this ticket&gt; merge PR 134</code>.
</p>
</td>
</tr>
<tr>
<td><a href="/trac_mcp_server/ticket/195">#195</a></td>
<td>
          duplicate
        </td>
<td>
<a href="/trac_mcp_server/ticket/195">tracwiki_to_markdown: after a column-0 list item (`- a` / `* a`) and a blank line, an indented marker is nested by the round trip, though Trac opens a new list</a>
</td>
<td>
<span class="trac-author-user">agent_rpc</span>
</td>
<td>
<span class="trac-author-user">agent_rpc</span>
</td>
</tr>
<tr>
<th>Description</th>
<td colspan="4">
<h2 id="a1.Errordescription">1. Error description</h2>
<p>
<a href="https://trac.example.org/trac_mcp_server/ticket/183">#183</a> fences an indented marker after a blank line inside a list, because Trac opens a new top-level list there and CommonMark nests it. That works for indented lists only. <code>_items_open_above</code> walks with <code>_LIST_MARKER_RE</code>, which needs leading whitespace, so a column-0 item above the blank line is not seen and the fence is declined.
</p>
<p>
Measured on <code>/trac_test</code> on 2026-11-08 (<code>convert_preview</code>, <code>format=tracwiki</code>):
</p>
<table>
<tr><th> source </th><th> Trac renders </th><th> read leg (<a href="https://trac.example.org/trac_mcp_server/ticket/183">#183</a> branch) </th><th> round trip 
</th></tr><tr><td> <code>- a</code> / blank / <code>   * c</code> / <code>- d</code> </td><td> three <code>&lt;ul&gt;</code> </td><td> <code>- a</code> / blank / <code>   - c</code> / blank / <code>- d</code> </td><td> <code> * a</code> / <code>   * c</code> / <code> * d</code>: c nested in a, the blank line gone 
</td></tr><tr><td> <code>* a</code> / blank / <code>  * c</code> / <code>* d</code> </td><td> three <code>&lt;ul&gt;</code> </td><td> <code>* a</code> / blank / <code>  - c</code> / blank / <code>* d</code> </td><td> <code> * a</code> / <code>   * c</code> / <code> * d</code>
</td></tr></table>
<p>
Same bytes on <code>master</code> f5c4b88. Found by <a href="https://trac.example.org/trac_mcp_server/ticket/183">#183</a>'s code review (finding 6), and separately while checking <a href="https://trac.example.org/trac_mcp_server/ticket/183">#183</a>'s declines.
</p>
<h2 id="a2.Methodofreplication">2. Method of replication</h2>
<p>
<code>markdown_to_tracwiki(tracwiki_to_markdown("- a\n\n   * c\n- d\n").text)</code>, then render both with <code>convert_preview</code>.
</p>
<h2 id="a3.Workaround">3. Workaround</h2>
<p>
Edit such pages as <a href="https://trac.example.org/trac_mcp_server/wiki/TracWiki">TracWiki</a>.
</p>
<h2 id="a4.Remediationsuggestion">4. Remediation suggestion</h2>
<p>
Not designed. Let <a href="https://trac.example.org/trac_mcp_server/ticket/183">#183</a>'s upward walk see column-0 items (<code>_LIST_MARKER_COL0_RE</code>, as <a href="https://trac.example.org/trac_mcp_server/ticket/182">#182</a>'s <code>_is_column_zero_list_line</code> does), with the text column of a column-0 marker. Check it against <a href="https://trac.example.org/trac_mcp_server/ticket/190">#190</a> (continuation under a column-0 item) and <a href="https://trac.example.org/trac_mcp_server/ticket/89">#89</a> (column-0 lettered lists).
</p>
<h2 id="a5.Relatedtickets">5. Related tickets</h2>
<ul><li><a href="https://trac.example.org/trac_mcp_server/ticket/183">#183</a>: the indented-list form.
</li><li><a href="https://trac.example.org/trac_mcp_server/ticket/190">#190</a>, <a href="https://trac.example.org/trac_mcp_server/ticket/89">#89</a>, <a href="https://trac.example.org/trac_mcp_server/ticket/182">#182</a>: column-0 list handling.
</li></ul>
</td>
</tr>
<tr>
<td><a href="/trac_mcp_server/ticket/192">#192</a></td>
<td>
          fixed
        </td>
<td>
<a href="/trac_mcp_server/ticket/192">tracwiki_to_markdown: an item's continuation after a stashed processor block ({{{#!comment}}}, {{{#!td}}}) leaves the item on the round trip</a>
</td>
<td>
<span class="trac-author-user">agent_rpc</span>
</td>
<td>
<span class="trac-author-user">agent_rpc</span>
</td>
</tr>
<tr>
<th>Description</th>
<td colspan="4">
<h2 id="a1.Errordescription">1. Error description</h2>
<p>
Trac keeps a processor block inside the open list item, and the lines after it continue the item. Measured on <code>/trac_test</code> on 2026-11-08 (<code>convert_preview</code>, <code>format=tracwiki</code>): <code>- item</code> / <code>{{{#!comment</code> / <code>note</code> / <code>}}}</code> / <code>  continuation text</code> / <code>  - sub</code> renders <code>&lt;ul&gt;&lt;li&gt;item continuation text &lt;ul&gt;&lt;li&gt;sub&lt;/li&gt;&lt;/ul&gt;&lt;/li&gt;&lt;/ul&gt;</code>.
</p>
<p>
<code>_fallback_processor_blocks</code> stashes <code>{{{#!comment}}}</code>, <code>{{{#!table}}}</code>, <code>{{{#!td}}}</code> and <code>{{{#!th}}}</code> blocks as a column-0 <code>tracwiki-unconverted</code> fence, so the Markdown ends the list at the fence:
</p>
<table>
<tr><th> read leg (<code>master</code> 8061f1b) </th><th> round trip 
</th></tr><tr><td> <code>- item</code> / fence / <code>  continuation text</code> / <code>  - sub</code> </td><td> <code> * item</code> / blank / the block / blank / <code>continuation text</code> / blank / <code> * sub</code>
</td></tr></table>
<p>
<code>continuation text</code> becomes a top-level paragraph, and <code>sub</code> becomes a new top-level list, not a nested one. <a href="https://trac.example.org/trac_mcp_server/ticket/143">#143</a> fixed the same loss for a plain <code>{{{ }}}</code> block in an item. These stashed processor blocks take a separate path.
</p>
<h2 id="Howmuchcontent">How much content</h2>
<p>
Unknown. Found while checking <a href="https://trac.example.org/trac_mcp_server/ticket/182">#182</a>'s code review (finding 2), where <a href="https://trac.example.org/trac_mcp_server/ticket/182">#182</a> now leaves this shape at <code>master</code>'s bytes.
</p>
<h2 id="a2.Methodofreplication">2. Method of replication</h2>
<p>
<code>tracwiki_to_markdown("- item\n{{{#!comment\nnote\n}}}\n  continuation text\n  - sub\n")</code>, then <code>markdown_to_tracwiki</code> of the result. The same happens with an indented <code> * a</code> / <code>   {{{#!td</code> ... <code>   }}}</code> / <code>   more</code> / <code> * b</code>.
</p>
<h2 id="a3.Workaround">3. Workaround</h2>
<p>
Edit such pages as <a href="https://trac.example.org/trac_mcp_server/wiki/TracWiki">TracWiki</a>.
</p>
<h2 id="a4.Remediationsuggestion">4. Remediation suggestion</h2>
<p>
Not designed. The fence would have to sit at the item's text column, as <a href="https://trac.example.org/trac_mcp_server/ticket/144">#144</a> does for a plain block in an item, so that CommonMark keeps it, and the lines after it, inside the item. Then check that <code>_split_past_bound</code>, <a href="https://trac.example.org/trac_mcp_server/ticket/182">#182</a>'s cut (its stash lines leave the open-list flag alone) and <a href="https://trac.example.org/trac_mcp_server/ticket/143">#143</a>'s after-block continuation all agree on the shape.
</p>
<h2 id="a5.Relatedtickets">5. Related tickets</h2>
<ul><li><a href="https://trac.example.org/trac_mcp_server/ticket/182">#182</a>: its review finding 2; the shape is left at <code>master</code>'s bytes there.
</li><li><a href="https://trac.example.org/trac_mcp_server/ticket/143">#143</a>: the item continuation after a plain <code>{{{ }}}</code> block.
</li><li><a href="https://trac.example.org/trac_mcp_server/ticket/144">#144</a>: a block in a list item fenced at the item's text column.
</li></ul>
</td>
</tr>
</tbody>
</table>

---

See also: [TracQuery](https://trac.example.org/trac_mcp_server/wiki/TracQuery), [TracTickets](https://trac.example.org/trac_mcp_server/wiki/TracTickets), [TracReports](https://trac.example.org/trac_mcp_server/wiki/TracReports)
