# Format Conversion

## Overview

**The inline ticket and wiki tools do not convert.** They store and return
TracWiki bytes exactly as given, so a read-edit-write round trip is
byte-exact. Ticket #69 removed the Markdown path from those tools --
along with the `format` parameter on the write tools and the `raw`
parameter on the read tools -- because the default aimed the *destructive*
failure at hand-authored TracWiki: indentation inside a `{{{ }}}`
processor block was silently stripped, with an empty warning list and a
plausible-looking render.

Passing `format="markdown"` or `raw=false` to those tools is now a
`validation_error` rather than a silently ignored argument. Passing
`format="tracwiki"` or `raw=true` is accepted as a no-op.

The converters described in the rest of this document are still shipped
and still maintained. They are reached from three places:

| Consumer | What it converts |
|----------|------------------|
| `trac-convert` (standalone binary) | Markdown -> TracWiki offline; TracWiki -> Markdown through Trac's rendered HTML (needs a Trac connection) |
| `convert_preview` (MCP tool) | Markdown -> TracWiki, without writing |
| `wiki_file_push` / `wiki_file_pull` | Markdown <-> TracWiki, driven by the file's extension |

The file tools keep converting deliberately: they have a *filename* to go
on, which the inline tools do not.

## Conversion Direction by Operation

| Operation | Input Format | Output Format | Conversion |
|-----------|--------------|---------------|------------|
| `wiki_create` / `wiki_update` | TracWiki | TracWiki | **None** -- stored byte-for-byte |
| `wiki_get` / `wiki_search` | TracWiki | TracWiki | **None** -- returned byte-for-byte |
| `ticket_create` / `ticket_update` | TracWiki | TracWiki | **None** -- stored byte-for-byte |
| `ticket_get` / `ticket_changelog` | TracWiki | TracWiki | **None** -- returned byte-for-byte |
| `milestone_*` | TracWiki | TracWiki | **None** -- byte-for-byte both ways |
| `wiki_file_push` | Markdown or TracWiki | TracWiki | Converts when the source is Markdown |
| `wiki_file_pull` | TracWiki | Markdown or TracWiki | For Markdown, Trac's rendered HTML for the page, translated: a one-way export (see below) |
| `convert_preview` | Markdown | TracWiki | Converts, without writing anything |

## Holding Markdown?

Convert it yourself before writing, with the `trac-convert` binary:

```bash
trac-convert --to tracwiki notes.md > notes.tracwiki
```

Or push the file directly and let `wiki_file_push` convert from the
extension:

```json
{
  "name": "wiki_file_push",
  "arguments": {
    "file_path": "/abs/path/notes.md",
    "page_name": "Notes"
  }
}
```

## TracWiki to Markdown: Trac's own HTML

Since Trac /trac_mcp_server #210, TracWiki -> Markdown is not a text
conversion. Trac renders the page (or the text) to HTML and the
translator (`trac_html_to_markdown`) turns that HTML into Markdown, so
macros, nested lists, tables and links come out as Trac itself reads them.

- **`wiki_file_pull`** with `format="markdown"` reads the page's version
  first, then the source and the HTML *at that version*, so an edit landing
  between the reads cannot pair one version's HTML with another's source.
- **`trac-convert`** asks Trac to render the input (`wiki.wikiToHtml`) for
  files, stdin and the clipboard; for `--from-wiki PAGE` it uses the page's
  own HTML (`wiki.getPageHTML`), so relative links resolve. It needs a
  Trac connection: with none configured it exits `4`. Markdown -> TracWiki
  stays offline.
- Trac's HTML does not say which language a code block was, so each block is
  paired with the `{{{#!lang` opener of the TracWiki source and fenced with
  that language.
- The result is a one-way export for reading. It is not meant to be pushed
  back: a Markdown file pulled this way is Trac's rendering, not the source,
  and its front matter makes the guarded push paths refuse it (see below). Pull
  `format="tracwiki"` for source that round-trips.
- Warnings the translator raises (a dropped table of contents, a table
  passed through as HTML, a list style Markdown cannot express) are listed
  in the text block of the `wiki_file_pull` result and in
  `structuredContent.warnings`, and as `warning:` lines on stderr from
  `trac-convert`.

### Front matter on an export

Every Markdown export of a Trac page starts with a YAML front matter block
(Trac /trac_mcp_server #212): `wiki_file_pull` with `format="markdown"`
(inline or to a file), `trac-mcp wiki-pull`, and `trac-convert --from-wiki
PAGE --to md` (to stdout, `-o` or the clipboard). Text, file and clipboard
input to `trac-convert` never gets one -- there is no page to name.

```yaml
---
title: "Blog Post"
source: "http://host/proj/wiki/Blog/Post"
trac_instance: "/proj"
trac_page: "Blog/Post"
trac_version: 7
exported: "2026-11-09T12:00:00Z"
---
```

| Field | Where the value comes from |
|-------|----------------------------|
| `title` | The text of the first `h1` of Trac's HTML, without Trac's anchor and icon marks, whitespace collapsed; the page name when there is no `h1` |
| `source` | The page's own URL, `<trac_url>/wiki/<page>`, with the page name percent-encoded (`Notes/Q3 #2` is `Notes/Q3%20%232`) |
| `trac_instance` | The path of the Trac URL (`/proj` for `http://host/proj`, `/` for a URL with no path) |
| `trac_page` | The page name as asked for |
| `trac_version` | The version the source and the HTML were read at; `null` when it could not be read and none was requested |
| `exported` | The UTC time of the export, `YYYY-MM-DDTHH:MM:SSZ` |

Strings are written as JSON strings, which are valid YAML double-quoted
scalars, so a title with a colon, a quote or non-ASCII text parses back
exactly (non-ASCII is written as is; the few characters YAML cannot carry
raw, such as DEL, the C1 controls and U+2028, are written as `\uXXXX`).

**It is a guard.** The export is one-way, so the guarded paths --
`wiki_file_push`, `trac-mcp wiki-push` and `trac-convert` to TracWiki --
refuse Markdown whose front matter is a YAML mapping carrying a non-empty
`trac_page`, unless forced:

- `wiki_file_push` returns a `validation_error` naming the page, instance
  and version, and stores nothing; `force=true` overrides it (a client
  that predates the guard has no `force` and does not refuse either).
  `trac-mcp wiki-push` takes `--force`.
- `trac-convert` exits `1` with one `trac-convert: refusing to convert: ...`
  line on stderr when Markdown with that front matter is converted to
  TracWiki (`--to tracwiki`, or `--to-wiki`, which writes nothing to Trac);
  `--force` strips the front matter and converts the rest. Converting to
  Markdown, or Markdown with front matter that has no `trac_page`, is
  untouched.

Edit such a page as TracWiki instead (`wiki_get`, `trac-mcp wiki-pull
PAGE FILE --format tracwiki`, `trac-convert --from-wiki PAGE --to
tracwiki`). The guard is for the accidental pull-edit-push loop, not for a
determined user: deleting the front matter by hand defeats it, and that is
accepted. Connected MCP sessions keep the old `wiki_file_push` schema (no
`force`) until they reconnect. `wiki_update` and `wiki_create` store their
content verbatim as TracWiki and do not check front matter (they never
convert, so they cannot re-shape a page; Markdown written there is stored
as literal text).

`tracwiki_to_markdown` (the old regex converter) is no longer called by
either; it is removed in #214. The description of its behaviour in the
"Markup Conversion Examples" below applies to the Markdown -> TracWiki
direction only.

## ConversionResult Structure

```python
@dataclass
class ConversionResult:
    text: str           # Converted text output
    source_format: str  # 'markdown', 'tracwiki', or 'unknown'
    target_format: str  # 'markdown' or 'tracwiki'
    converted: bool     # True if conversion performed
    warnings: list[str] # Warnings about lossy conversions
```

## Language Mappings for Code Blocks

Code block languages are mapped between formats:

| Markdown | TracWiki | Notes |
|----------|----------|-------|
| `bash`, `shell`, `zsh` | `sh` | Shell variants normalize to `sh` |
| `js` | `javascript` | Short form expanded |
| `ts` | `typescript` | Short form expanded |
| `c++` | `cpp` | Normalized name |
| `text`, `plaintext`, `plain` | `text` | Text variants normalized |

**Identity languages (unchanged):** `python`, `java`, `c`, `ruby`, `go`, `rust`, `sql`, `html`, `css`, `xml`, `json`, `yaml`, `diff`, etc.

## Conversion Warnings

The converter detects potentially lossy conversions and returns warnings:

**Markdown to TracWiki:**
- HTML tags (may not render correctly)
- TOC macros (use `[[PageOutline]]` instead)

**TracWiki to Markdown:** the translator's warnings name every lossy
mapping, each once (Trac #209). They are not a fixed list: anything it does
not cover passes through as HTML with a warning that names it, so nothing is
lost silently. Examples: a dropped table of contents, a table with
`colspan`/`rowspan` cells, a lettered list exported as a numbered list,
code blocks with no matching source block.

## Markup Conversion Examples

**Headings:**
```
Markdown:  # Heading 1
TracWiki:  = Heading 1 =

Markdown:  ## Heading 2
TracWiki:  == Heading 2 ==
```

**Bold/Italic:**
```
Markdown:  **bold** and *italic*
TracWiki:  '''bold''' and ''italic''
```

**Line breaks:**
```
Markdown:  substrate:trac␣␣
           next line

TracWiki:  substrate:trac [[BR]]
           next line
```

> **Note:** A hard line break (`text` followed by two trailing spaces, `␣␣`
> above) always converts to `[[BR]]` with a leading space when the
> preceding character isn't already whitespace. Trac's wiki-link grammar
> treats a colon-valued token (`substrate:trac`, `toolchain:x`, ...)
> immediately followed by `[[BR]]` as a candidate `wikiname:target`
> TracLink, greedily consuming `[[BR]]` into the failed link-target parse
> instead of recognizing it as the line-break macro -- the leading space
> avoids the collision (ticket #29).

**Tables:**
```
Markdown:  | | Clip lead | Ground spring |
           |---|---|---|
           | 5 V undershoot | -2.40 V | -0.56 V |

TracWiki:  || ||=Clip lead=||=Ground spring=||
           ||5 V undershoot||-2.40 V||-0.56 V||
```

> **Note:** An empty cell (a table's usual shape for a row-label column)
> renders as a lone space, not a bare `||`. Two adjacent `||` with nothing
> between them (`||||`) is TracWiki's colspan-2 marker, not two separate
> empty cells -- using it for a genuinely empty cell would merge it into
> its neighbor and shift every following column left by one (ticket #20).

**Code Blocks:**
```
Markdown:  ```python
           print("hello")
           ```

TracWiki:  {{{#!python
           print("hello")
           }}}
```

**Links:**
```
Markdown:  [Link Text](https://example.com)
TracWiki:  [https://example.com Link Text]

Markdown:  [Wiki Link](WikiPage)
TracWiki:  [wiki:WikiPage Wiki Link]

Markdown:  [Wiki Link](wiki:WikiPage)
TracWiki:  [wiki:WikiPage Wiki Link]

TracWiki:  [[WikiPage]]
Markdown:  [WikiPage](wiki:WikiPage)

TracWiki:  [[WikiPage|Wiki Link]]
Markdown:  [Wiki Link](wiki:WikiPage)
```

> **Note:** Double-bracket `[[WikiPage]]` / `[[WikiPage|Label]]` links only
> ever appear on the TracWiki-to-Markdown side; `markdown_to_tracwiki`
> always emits the single-bracket `[wiki:WikiPage ...]` form shown above, so
> a `wiki_get` -> edit -> `wiki_update` round-trip normalizes double-bracket
> links to single-bracket ones rather than preserving the original syntax.

> **Note:** Link targets that already carry a TracLink resolver prefix -- `wiki:`, `ticket:`, `milestone:`, `source:`, `attachment:`, `changeset:`, and the rest of Trac's built-in resolvers -- are emitted verbatim, not prefixed a second time. This is the form `tracwiki_to_markdown` produces, so a `wiki_get` -> edit -> `wiki_update` round-trip that leaves existing links untouched preserves them exactly. Targets without a prefix (`WikiPage`, `Planning/Phases/Phase01`) get `wiki:` added.

> **Note:** Non-URL-shaped "links" (URL portion contains a `:` that is not a known TracLink resolver, e.g. sentinel tokens like `[auto-pm: state NEEDS_CODE]` or `[label](foo:bar)`) are emitted verbatim as Markdown rather than wrapped as TracWiki wiki links. This preserves machine-readable state markers through round-trip conversion.

> **Note:** The same rule applies in the TracWiki -> Markdown direction. A `[target label]` construct is only rewritten as a Markdown link when `target` is a real link target -- a known TracLink resolver, a URL scheme, or a name with no `:` at all. Bracketed prose such as `[auto-pm: state NEEDS_EDIT]` or `[note: see below]` stays literal instead of becoming `[state NEEDS_EDIT](auto-pm:)` with an empty URL. A construct also never spans a line break, so an orphan `[text]` cannot absorb a `(...)` parenthetical from a following line.

**Images:**
```
Markdown:  ![alt](image.png)
TracWiki:  [[Image(image.png)]]
```

**Lists:**
```
Markdown:  - Item 1
           - Item 2
             - Nested

TracWiki:   * Item 1
            * Item 2
              * Nested
```

**Macros:**
```
Markdown:  [MACRO: PageOutline]
TracWiki:  [[PageOutline]]

Markdown:  [MACRO: TOC(depth=2)]
TracWiki:  [[TOC(depth=2)]]

Markdown:  [[SomePage]]
TracWiki:  [[SomePage]]
```

> **Note:** `[MACRO: Name(args)]` -- the placeholder `tracwiki_to_markdown`
> emits for a macro it couldn't otherwise represent in Markdown -- converts
> back to `[[Name(args)]]`, so a `wiki_get` -> edit -> `wiki_update` round
> trip no longer permanently flattens the macro (ticket #19). `[[...]]`
> syntax typed directly in the Markdown source passes through unchanged too.

**CamelCase words:**
```
Markdown:  WiFi credentials live in .env.
TracWiki:  !WiFi credentials live in .env.
```

> **Note:** Trac's WikiFormatting auto-links any CamelCase-shaped word
> (two or more capitalized "humps", e.g. `WiFi`, `LoRa`, `PageOutline`) into
> a page-reference attempt, which renders as a broken missing-page link if
> the page doesn't exist. Markdown has no such auto-link concept, so any
> CamelCase-shaped word reaching plain prose is defensively prefixed with
> `!` -- Trac's own escape character -- so it renders as literal text
> (ticket #27). This does not apply inside code spans/blocks, inside a
> link's own display text (a TracWiki link label is never re-parsed for
> WikiFormatting), or inside `[[...]]` / restored macro syntax.

---

[Back to Reference Overview](overview.md)
