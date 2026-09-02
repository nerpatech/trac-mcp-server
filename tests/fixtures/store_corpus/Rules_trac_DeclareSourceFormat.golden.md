**Scope:** `substrate:trac`   
**Type:** hard-rule   
**Status:** active   
**Related:** [[Rules/trac/SurgicalRepairs]], [[Rules/trac/RoundTripBeforePush]], [[Rules/trac/RenderVerify]], [[Rules/trac/PreferInterTracLinks]], [[Meta/PageConventions]]   
**Source:** ticket #89; supersedes [[Rules/trac/AuthorMarkdownNotTracWiki]]; upstream fix trac_mcp_server:#62

## The rule
**Every MCP write tool takes a `format` parameter. Declare it on every write.**

```
format="markdown"    (default)   converted to TracWiki before storing
format="tracwiki"                stored byte-for-byte, converter never runs
```

Six tools carry it: `wiki_create`, `wiki_update`, `ticket_create`, `ticket_update`, `ticket_batch_create` and `ticket_batch_update`. On the batch pair it sits at the **call** level and governs every item in the batch. `wiki_file_push` and `convert_preview` have had it longer. There is deliberately no `auto` value — the format is declared, never guessed from content, because guessing is what trac_mcp_server:#47 showed to be unreliable.

Authoring !TracWiki is a first-class choice, not a violation. What is forbidden is authoring !TracWiki and **not saying so** — that single combination is the one that silently corrupts.

## What the superseded rule got wrong
[[Rules/trac/AuthorMarkdownNotTracWiki]] required Markdown always, on the grounds that *"hand-written !TracWiki eats code blocks."* The advice was correct for the tooling of its time. **The reason was backwards, and the reason is what outlived the tooling.**

!TracWiki does not eat code blocks. Feeding !TracWiki to a Markdown parser does. Measured on identical content through a ticket write, varying only `format`:

 - `format="tracwiki"` — stored **byte-identical** to the input.
 - `format="markdown"` — the four-space indent inside a `{{{#!python}}}` processor block is **stripped**, producing syntactically invalid code, with an empty `warnings` list.

A Markdown fenced block survives that path; a !TracWiki processor block does not. **So the damage fell exclusively on the author who hand-wrote !TracWiki** — which is to say, on the population the old rule was steering away from, for a reason that described the wrong layer. The operator's own experience contradicted the recorded claim throughout, because the web UI stores verbatim.

Stating a tooling workaround as a property of the markup language is what let the claim survive the tooling being fixed, and propagate into sibling rules as settled fact.

## Choosing a format
 - **Editing something that already exists** — read it with `raw=true`, edit the !TracWiki source, push it back with `format="tracwiki"`. The stored bytes then change only where you changed them. This is now the ordinary way to edit an existing page, not an exception; see [[Rules/trac/SurgicalRepairs]].
 - **Authoring new prose** — either format works. Markdown still converts correctly and remains a fine default for plain prose.
 - **Content Markdown cannot express** — a processor block, `[[BR]]`, an !InterTrac realm, a macro, or a `preview-checks:` pragma. Declare `tracwiki` and hand-author it.
 - **Never** send a hand-written `{{{ }}}` processor block through the Markdown path.

## The trap that remains
**The default did not move.** Omit `format` and you get `markdown`, and hand-authored !TracWiki is corrupted exactly as it always was. The flip of the default to verbatim is tracked as trac_mcp_server:#63.

That corruption is silent at every checkpoint an agent habitually uses: the call succeeds, `warnings` comes back empty, and the render looks plausible. It is visible only in the stored bytes. **Assert on stored source, never on the render and never on the warning list.**

## The read leg converts too
`ticket_get` and `wiki_get` return **Markdown** by default — not what is stored. Content read back that way carries literal `!` escape artifacts, so re-pushing it stores them. **Read with `raw=true` whenever the read feeds a later write.** The read leg has had its own content-destroying defects: trac_mcp_server:#51 emitted NUL bytes and dropped the line it was quoting.

## What carries over unchanged
 - **Linking to a wiki page** uses the double-bracket form — `[[Rules/trac/RenderVerify]]` — never a guessed relative or hand-built URL.
 - **Cross-instance links** follow [[Rules/trac/PreferInterTracLinks]] and [[Reference/trac/InterTrac]]. The absolute-URL form this rule's predecessor prescribed was retracted upstream and is not the preferred form.
 - **Render-verify every write** per [[Rules/trac/RenderVerify]], reading resolved `href` values rather than warning counts.
