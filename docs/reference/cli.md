# CLI Reference

## trac-mcp-server

The `trac-mcp-server` command starts the MCP server. It communicates via stdin/stdout using JSON-RPC 2.0 over the Model Context Protocol. It is designed to be launched by MCP clients (Claude Desktop, Claude Code, etc.), not used interactively.

### Usage

```bash
trac-mcp-server
trac-mcp-server --version
trac-mcp-server --transport http --port 8080   # streamable HTTP instead of stdio
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--url URL` | -- | Override Trac URL (takes precedence over `TRAC_URL` env var) |
| `--username USER` | -- | Override Trac username (takes precedence over `TRAC_USERNAME` env var) |
| `--password PASS` | -- | Override Trac password (takes precedence over `TRAC_PASSWORD` env var) |
| `--insecure` | `false` | Skip SSL certificate verification (development only) |
| `--log-file PATH` | `LOG_FILE`, else `/tmp/trac-mcp-server.log` (stdio) / none (http); `""` disables | Log file location. An empty value (`--log-file ""` or `LOG_FILE=`) means no log file: stdio then logs to stderr, http to stderr only |
| `--permissions-file PATH` | -- | Restrict available tools by Trac permissions (see [Tool Architecture](tool-architecture.md#permission-filtering)) |
| `--read-only` | `false` | Expose only tools marked read-only (`readOnlyHint`), on every transport. `ticket_attachment_get`, `wiki_attachment_get` and `wiki_file_pull` are hidden too: they read from Trac but can write a local file. Also settable via `TRAC_MCP_READ_ONLY` env var or `config.yaml` `server.read_only`. Combinable with `--permissions-file` -- a tool must pass both filters. |
| `--transport {stdio,http}` | `stdio` | MCP transport to serve (also settable via `TRAC_MCP_TRANSPORT` or `config.yaml` `server.transport`) |
| `--host HOST` | `127.0.0.1` | Bind host for `--transport http` |
| `--port PORT` | `8080` | Bind port for `--transport http` |
| `--path PATH` | `/mcp` | URL path the MCP endpoint is mounted at, for `--transport http` |
| `--allow-unauthenticated` | `false` | Allow `--transport http` to bind a non-loopback host without an auth token. Dangerous -- exposes Trac credentials to the network. Prefer `TRAC_MCP_AUTH_TOKEN`. |
| `--file-access {local,off}` | `off` for http, `local` for stdio | Whether the file tools may read and write this process's filesystem through `file_path`/`output_path` (see [HTTP Transport](http-transport.md#file-access-file_access)) |
| `--version` | -- | Show version and exit |

There is no `--auth-token` flag: it would leak the secret into the process list. Set it via `TRAC_MCP_AUTH_TOKEN` or `config.yaml`'s `server.auth_token`. See [HTTP Transport](http-transport.md) for the full auth and bind-safety rules.

### Configuration

Configuration can come from YAML config files, environment variables, or CLI flags. CLI flags take highest precedence. See [Configuration](configuration.md) for details.

### How It Works

By default the server runs over stdio transport: it reads JSON-RPC requests from stdin and writes responses to stdout. All log output goes to a file (never stdout), so the stdio channel stays clean for MCP protocol messages.

Typical lifecycle:

1. MCP client launches `trac-mcp-server` as a subprocess
2. Server validates Trac connection on startup
3. Server handles MCP tool calls (tickets, wiki, milestones, etc.) until the client disconnects

With `--transport http`, the server instead serves MCP over streamable HTTP (`POST/GET/DELETE` on the configured path) via `uvicorn`, so one long-lived process can serve multiple clients/sessions. See [HTTP Transport](http-transport.md).

### Installation

```bash
pip install .          # installs trac-mcp-server command
pipx install .         # alternative: isolated environment
```

The `trac-mcp-server` command is registered as an entry point in `pyproject.toml`.

---

## trac-convert

The `trac-convert` command is a standalone binary that converts between TracWiki and Markdown formats. It is designed for interactive shell use and Unix pipe composition. Markdown -> TracWiki works without a Trac connection. TracWiki -> Markdown does not: Trac renders the text and `trac-convert` translates the HTML it returns (Trac /trac_mcp_server #210), so it connects to a live Trac instance, as do `--from-wiki`, `--to-wiki`, and `--check-trac`, using the same credentials and config precedence as `trac-mcp-server`. With no Trac configured, TracWiki -> Markdown exits `4` and writes nothing to stdout.

### Usage

```bash
trac-convert --from md --to tracwiki < input.md > output.tw     # stdin → stdout
trac-convert --to tracwiki input.md -o output.tw                 # file → file
trac-convert --from-clipboard --to md                            # clipboard → stdout
trac-convert --to-clipboard input.md                             # file → clipboard
trac-convert --check-trac                                        # verify Trac connectivity
trac-convert --from-wiki MyPage --to md -o my-page.md           # Trac → local file
trac-convert --from-wiki MyPage --to md -o my-page.md --assets images  # ... and its attachments
trac-convert --from-file my-page.md --to-wiki MyPage            # local file → Trac
trac-convert --version
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--from {md,tracwiki,auto}` | `auto` | Source format; `auto` sniffs the input via heuristics |
| `--to {md,tracwiki}` | *(required unless --check-trac or --to-wiki)* | Destination format |
| `FILE` (positional) | stdin | Input file path; omit or use `-` for stdin |
| `--from-file PATH` | -- | Read input from a local file (explicit named alternative to the positional `FILE`). Mutually exclusive with `FILE`, `--from-clipboard`, and `--from-wiki`. |
| `-o, --output FILE` | stdout | Output file path; omit for stdout |
| `--from-clipboard` | -- | Read input from system clipboard instead of stdin/file. On Linux, requires one of `wl-clipboard`, `xclip`, or `xsel` to be installed (tried in that order, based on `$WAYLAND_DISPLAY` / `$DISPLAY`); falls back to `pyperclip`. On headless terminals without a clipboard tool, use `--from-file PATH` or pipe input on stdin instead. |
| `--to-clipboard` | -- | Write output to system clipboard instead of stdout/file. Same Linux tool requirement as `--from-clipboard`. |
| `--heading-anchors {on,off}` | `on` | md→tracwiki only: emit explicit `#slug` anchors on TracWiki headings (silently ignored in tw→md direction) |
| `--link-map FROM=TO` | -- | TracWiki -> Markdown only (`--from-wiki` and file / stdin / clipboard input): rewrite link targets that start with `FROM` to start with `TO`, longest `FROM` first, split at the first `=`. Repeatable. **Replaces** the configured map (`export.link_rewrites`, `TRAC_MCP_LINK_REWRITES`; see [Configuration](configuration.md#link-rewrites-export)) rather than adding to it. Image sources are never rewritten, an in-page anchor is written `#frag` and never mapped, and the link text is kept. In any other direction it prints one `warning: --link-map has no effect ...` line (suppressed by `-q`). A value with no `=`, an empty `FROM`, or a repeated `FROM` is a usage error (exit `2`). |
| `--assets DIR` | -- | `--from-wiki PAGE --to md` only: download the Trac attachments the page references into `DIR` (created if needed) and point their links at the local files; see [Exporting attachments](#exporting-attachments-assets). Any other use (`--to tracwiki`, `--to-wiki`, no `--from-wiki`, with `--check-trac`) is a usage error (exit `2`), as is a `DIR` that is a file. |
| `--assets-url PREFIX` | -- | With `--assets`: link each saved file as `PREFIX/<path>` (the URL it is served from; `<path>` is the file's path under `DIR`, see [Exporting attachments](#exporting-attachments-assets)) instead of by its relative path from the output file. A usage error (exit `2`) without `--assets`. |
| `-v, --verbose` | -- | Emit `info:` diagnostics to stderr (mutually exclusive with `-q`) |
| `-q, --quiet` | -- | Suppress `warning:` lines to stderr (mutually exclusive with `-v`; does NOT suppress `error:` lines) |
| `--from-wiki PAGE` | -- | Fetch input from a Trac wiki page (source format is TracWiki); to Markdown it translates the page's own rendered HTML, so relative links resolve as Trac resolves them. The page version is read first and the source and the HTML are both read at it, so a save in between cannot pair one version's source with another's HTML; if the version cannot be read, both reads are the latest and a `warning:` line says they were not pinned. Mutually exclusive with `FILE` positional and `--from-clipboard`. |
| `--force` | -- | Convert Markdown exported from Trac (front matter carrying `trac_page`) to TracWiki anyway, stripping that front matter. Without it, that conversion (`--to tracwiki` or `--to-wiki`) exits `1` with `trac-convert: refusing to convert: this Markdown was exported from Trac page 'P' (instance I, version V) and the export is one-way; edit the page as TracWiki (trac-convert --from-wiki P --to tracwiki), or pass --force` and writes nothing, to Trac or to the output. `--from-wiki PAGE --to md` starts its output (stdout, `-o` or the clipboard) with that front matter; text, file and clipboard input never gets any. See [Format Conversion](format-conversion.md#front-matter-on-an-export) |
| `--to-wiki PAGE` | -- | Write output to a Trac wiki page (target format is TracWiki). Mutually exclusive with `-o/--output` and `--to-clipboard`. Bypasses the `--to` requirement. |
| `--wiki-comment MSG` | `Updated via trac-convert` | Change comment recorded on the wiki page when using `--to-wiki`. Ignored without `--to-wiki`. |
| `--check-trac` | -- | Print resolved Trac config source per field, ping the server, and exit (no conversion performed). |
| `--trac-url URL` | -- | Override Trac URL (default: `TRAC_URL` env var or YAML config). |
| `--trac-username USER` | -- | Override Trac username (default: `TRAC_USERNAME` env var or YAML config). |
| `--trac-password PASS` | -- | Override Trac password (default: `TRAC_PASSWORD` env var or YAML config). Prefer `--trac-password-file` for secrets. |
| `--trac-password-file PATH` | -- | Read Trac password from file (single line, trimmed). Takes precedence over `--trac-password`. |
| `--version` | -- | Show version and exit |

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Runtime error (I/O failure, clipboard unavailable, mutually-exclusive flag conflict) |
| `2` | Usage error (argparse-emitted: missing required flag, bad choice) |
| `3` | Conversion error (exception raised inside the converter) |
| `4` | Trac error (auth failure, network/timeout, permission denied, page not found, XML-RPC fault, SSL error) — emitted by `--check-trac`, `--from-wiki`, `--to-wiki`, and the rendering step of TracWiki -> Markdown. TracWiki -> Markdown with no Trac configured also exits `4`: the message says to set `TRAC_URL`, `TRAC_USERNAME`, `TRAC_PASSWORD` (or `.trac_mcp/config.yaml`) or pass `--trac-url`, and nothing is written to stdout. When the configuration exists but is invalid (a bad `TRAC_URL`), the message ends with a `  config error: <reason>` line, here and on `--from-wiki`'s "no Trac credentials found" message; when `--from auto` resolved the input to TracWiki, a last line says so and to pass `--from md` if it is Markdown. With `--to-wiki`, nothing was stored. |
| `5` | `--to-wiki` stored the page, but reading back its new version failed. The message on stderr says the page WAS stored; do not retry the write. |

### Auto-detection

When `--from auto` is used (the default when omitted), the format is detected via `converters.common.detect_format`: TracWiki markers (`{{{`, `[[`, `= Heading =`) win, otherwise Markdown is assumed. On ambiguous input, prefer the explicit `--from` flag. Detection runs before Trac is contacted, so input that resolves to Markdown never needs a connection.

### Trac Wiki I/O

`--from-wiki` and `--to-wiki` read and write Trac wiki pages directly via the same `TracClient` and config precedence as `trac-mcp-server` (CLI `--trac-*` > env `TRAC_*` > `.trac_mcp/config.yml`).

```bash
# Verify connectivity: prints resolved config source per field and pings the server
trac-convert --check-trac

# Fetch a Trac wiki page, convert to Markdown, save locally
trac-convert --from-wiki MyPage --to md -o my-page.md

# Push a Markdown file back to Trac
trac-convert --from-file notes.md --to-wiki MyPage --wiki-comment "Edited via CLI"

# Edit a page as TracWiki, not Markdown (the Markdown export is one-way):
# fetch the source, edit it locally, then push it back
trac-convert --from-wiki MyPage --to tracwiki -o my-page.tw
# (edit my-page.tw)
trac-convert --from tracwiki --from-file my-page.tw --to-wiki MyPage
```

On failure, `trac-convert` exits with code `4` and writes a classified error message (page-not-found, permission-denied, timeout, SSL, connection, generic) to stderr; a failed render of TracWiki -> Markdown is reported the same way, with the action `wiki render`. See [Configuration](configuration.md) for the full auth and config precedence rules.

### Exporting attachments (`--assets`)

Trac renders an attachment as an absolute URL, so an exported page still shows its images from Trac: `[[Image(x.png)]]` exports as `[![alt](https://host/inst/raw-attachment/wiki/PAGE/x.png "title")](https://host/inst/attachment/wiki/PAGE/x.png)`, and Trac's HTML (`trac-mcp wiki-pull --format html`) has the same two URLs as `<a href>` and `<img src>`. `--assets DIR` (on `trac-convert --from-wiki ... --to md` and on `trac-mcp wiki-pull`, Markdown or HTML) downloads the attachments the exported text references into `DIR` and rewrites those links, so the page can be published without Trac. It is for a blog or static site: the images land under the site's `static/` directory and the page links to them.

- **What is downloaded.** A URL counts when it sits where a URL goes (a Markdown link or image target, an `<autolink>`, a quoted `src` or `href` attribute) and its path is `<instance>/raw-attachment/<realm>/<parent>/<file>` or `<instance>/attachment/...` with realm `wiki` or `ticket`, on **this** instance. When the instance has a path (`/proj`), the scheme and host are ignored (Trac's base URL can name another host or port than the one the command connects to), so absolute and root-relative URLs both match. An instance served from the root has no path to tell its URLs from another Trac's, so there an absolute URL matches only on the instance's own host (the hostname alone: scheme and port are ignored); a root-relative URL always matches. A URL with a `?query` or `#fragment` is left alone, and an external URL never matches. A wiki parent may contain `/`. A page name or file name that is empty, `.` or `..`, or holds a backslash, a `:` or a NUL, is not an attachment reference (it could not be a file name everywhere), and a ticket number must be ASCII digits. Each attachment is fetched once; its `attachment` and `raw-attachment` URLs become the same local link. URLs that have an attachment's path shape (`/raw-attachment/(wiki|ticket)/...` or `/attachment/(wiki|ticket)/...`) but are not under the instance path (Trac behind a proxy whose base path differs from the one the command knows) are left untouched and reported in one warning that gives their number and the instance path they were compared with; another host's URL is not counted where the host decides (an instance served from the root), since a link to another Trac is expected.
- **Never in code.** A URL inside code is text about a URL: it is neither fetched nor rewritten. In Markdown that is a fenced code block (``` or `~~~`, closed by a fence of the same character and at least the same length; one never closed runs to the end), an inline code span (a backtick run closed by a run of the same length), and the content of a raw HTML `<pre>`, `<code>`, `<script>`, `<style>` or `<textarea>` (a backslash-escaped `\<pre>`, which is how the exporter writes a literal tag in prose, opens nothing); in HTML, the content of those elements. Indented code blocks are not recognised: the exporter always fences.
- **Local paths.** One path per attachment, decided by the attachment alone and never by which others the page has or which were found: a wiki attachment is `DIR/<page path>/<file>`, the page path's segments being directories, and a ticket attachment is `DIR/ticket-<N>/<file>`. Page `blog/my-post` with `x.png` is saved as `DIR/blog/my-post/x.png`, ticket 42's `log.png` as `DIR/ticket-42/log.png`, so two pages with a `diagram.png` never meet. The directories between are created. A file already at the path with different bytes is overwritten (it is the same attachment, updated); one with identical bytes is left alone. The one exception is a clash: two attachments with one path, two paths that differ only in letter case (`Diagram.png` and `diagram.png` of one page; a case-insensitive file system cannot keep them apart), or a file where another needs a directory, cannot both be saved, so the first in the page is saved and the later one keeps its original URLs, with a warning that says which kind it was. A wiki page literally named `ticket-5` and ticket #5 share the directory `DIR/ticket-5/`, so a file of one name on both is such a clash ("wiki page 'ticket-5' and ticket #5 both map to ticket-5/x.png; keeping the first").
- **The links.** Without `--assets-url` a link is the relative path from the output file's directory (the current directory for stdout or the clipboard) to the file, with `/` separators and percent-encoding, e.g. output `content/posts/x.md`, `DIR` `static/images` and page `blog/my-post` give `../../static/images/blog/my-post/x.png`. With `--assets-url PREFIX` it is `PREFIX` (any trailing `/` dropped), then the path's segments, each percent-encoded, whatever the paths on disk: for a blog, `--assets static/images --assets-url /images` saves page `blog/my-post`'s `x.png` as `static/images/blog/my-post/x.png` and links it `/images/blog/my-post/x.png`. A bare `<URL>` autolink becomes `[<file>](<link>)`, because a relative link is not an autolink.
- **All or nothing.** Every attachment is written to a temporary sibling of its final file (`.<name>.tmp-<random>`, in the same directory) as soon as it is fetched, so payloads are not all held in memory, and the output file is written the same way last. Only then is everything renamed into place, the assets first and the output last. A fetch that fails for any reason except "not found" or "too large" (a permission error, the server or network down), or a write that fails, stops the command with every temporary file and every directory made for them removed, so `DIR` and the output are exactly as they were (exit `4` for a failed fetch and `1` for a failed write with `trac-convert`; with `trac-mcp` exit `1` when the server answered with an error or a write failed, `3` when the call itself failed). A failure in the renames themselves cannot be undone for the files already moved; the error says how many were. Before anything is fetched or written the planned paths are checked: an existing file where a directory is needed (page `blog` has an attachment `my-post`, then page `blog/my-post` is exported), a target that is an existing directory, an output path that is a directory or in an unwritable or missing directory, are errors (exit `2`), as is a `DIR` that is a file. A missing attachment is a warning (`attachment 'c.png' of wiki page 'P' was not found; its links are left as they are`): its original URLs stay, and the exit is still `0`. So is one the server refuses for its size (`trac-mcp` moves attachments inline, bound by the server's `max_inline_bytes`; the warning names the attachment and the limit): its URLs stay and the report counts it. `trac-convert` talks to Trac directly and has no such limit.
- **Report.** One line on stderr, `Saved N attachments to DIR (M unchanged, K missing)`: `N` counts every attachment now in `DIR` and `M` of them were already there with the same bytes. When the server refused any for size it reads `(M unchanged, K missing, S too large)`. With `trac-convert` it is an `info:` line and, like the warnings, `-q` suppresses it.
- **`link_rewrites` still runs first and is not touched.** `--assets` acts on whatever is still in Trac's attachment form afterwards. A [link-rewrite](configuration.md#link-rewrites-export) rule whose `FROM` covers the attachment links (the wrapping `attachment/` link) wins for those links: they are rewritten to its `TO` and never downloaded for themselves. Image sources are never rewritten by a map, so the images are still localised, which is the point.
- **Not for TracWiki.** A TracWiki export names attachments, not their URLs, so `--assets` with `--format tracwiki` or `--to tracwiki` is a usage error. The MCP tools do not take it.


### Installation

```bash
pip install .          # installs trac-mcp-server, trac-convert and trac-mcp
pipx install .         # alternative: isolated environment, all three available
```

All three entry points are registered in `pyproject.toml` under `[project.scripts]`.

---

## trac-mcp

`trac-mcp` moves files between **your** machine and a `trac-mcp-server` reached over HTTP. The server's file tools cannot see your filesystem when the server runs elsewhere, and over http they refuse paths by default ([File Access](http-transport.md#file-access-file_access)). `trac-mcp` reads and writes the local file itself and calls the same tools with their inline forms, so:

- the bytes never pass through a model's transcript -- an agent runs it as a shell command;
- the write keeps everything the server does: the caller's Trac identity, Markdown conversion, the indentation guard and the link gate.

Unlike `trac-convert --to-wiki`, which talks XML-RPC to Trac directly with local Trac credentials, `trac-mcp` needs only the MCP endpoint and a bearer token.

### Usage

```bash
trac-mcp wiki-push PAGE FILE [--format auto|markdown|tracwiki] [--comment TEXT] [--keep-frontmatter] [--force]
trac-mcp wiki-pull PAGE FILE [--format markdown|html|tracwiki] [--page-version N] [--link-map FROM=TO ...] [--assets DIR [--assets-url PREFIX]]
trac-mcp attach-put (--ticket N | --page PAGE) FILE [--filename NAME] [--description TEXT] [--replace]
trac-mcp attach-get (--ticket N | --page PAGE) NAME FILE
trac-mcp detect-format FILE        # runs locally, no server call
trac-mcp ticket-comment TICKET FILE [--reply-to N] [--base-ts TS]
trac-mcp ticket-comment-edit TICKET CNUM FILE [--base-ts TS] [--expect-file PATH]
trac-mcp ticket-describe TICKET FILE [--base-ts TS]
trac-mcp ticket-create --summary S --description-file FILE [--type T] [--component C] [--keywords K] [--owner O] [--priority P] [--milestone M]
trac-mcp ticket-pull TICKET FILE [--comment N]
```

The five `ticket-*` commands move ticket bodies. The file is read as UTF-8 and sent **verbatim** as TracWiki -- no format detection and no conversion -- and an empty or whitespace-only file is a usage error (exit 2). Standard input is read as bytes, so `-` keeps CRLF line endings exactly as a file path does. The 10000-character limit (the server's) applies to adding a comment: `ticket-comment` refuses a longer body with exit 2 before connecting. `ticket-describe` and `ticket-comment-edit` have no such limit, so a long body belongs in the description, in an edit of an existing comment, or split across comments.

- `ticket-comment` prints `Added comment N to ticket #T`; `N` is read back from the ticket's changelog, never counted. If it cannot be determined (the read-back failed, or the stored body differs from the sent one) the command exits **4**, not 1: the comment *was* stored, so do not retry the write. Exit 1 always means nothing was stored.
- `ticket-comment` and `ticket-describe` lock optimistically: they read the ticket's `_ts` just before writing and pass it as `base_ts`, so a change in between is refused with `version_conflict` (exit 1) instead of overwritten. `--base-ts TS` supplies the token yourself and skips that read.
- `ticket-comment-edit` replaces comment `CNUM` of the ticket from `FILE` (`-` for stdin; `FILE` and `--expect-file` cannot both be `-`, exit 2), so a long comment can be corrected in place without re-typing it inline. It locks like `ticket-comment` (`--base-ts TS`, else the ticket's `_ts` read just before the write; a stale token is `version_conflict`, exit 1). `--expect-file PATH` refuses unless the comment's stored body equals that file (compared with CR and CRLF folded to LF on both sides, since Trac stores LF, so a CRLF file this command pushed works as the next `--expect-file`; an empty file matches an empty comment), which covers an edit made in the web UI since you read it: on a difference it exits 1, stores nothing, and prints the byte offset of the first difference and both lengths, never the bodies (a missing comment `CNUM` is exit 1 too). After the write the command reads the comment back and prints `Stored comment N matches the text sent, byte-for-byte` (exit 0), or says it matches the text sent with line endings folded to LF (Trac stores CR and CRLF as LF; also exit 0). If the stored text differs from the file, or the read-back itself failed, the edit *was* stored: it prints the byte offset and lengths (never the bodies) and exits **4**, so do not retry blindly. Unlike `ticket-comment`, an edit has no 10000-character limit (that limit applies only to adding a comment), so a 30 KB comment is sent as is. The previous text stays readable with `ticket_comment_history`.
- `ticket-create` prints the server's `Created ticket #N: ...` line.
- `ticket-pull` writes the stored description (or comment `N`) to `FILE` (`-` for stdout) byte for byte, so `cmp` against the pushed file holds.

`wiki-push` exits **4**, not 1, when the page was stored but its new version could not be read back afterwards (the server reports `readback_failed`): do not retry the push. Exit 1 means nothing was stored.

When the server reports `possible_overwrite` (the page was updated but reads back as a version other than the one after the version the push was based on, so another write most likely landed just before it and was replaced), `wiki-push` prints a `trac-mcp: warning: possible overwrite ...` line on stderr naming the versions in between, and still exits **0**: the page was stored, and a non-zero code would invite a retry that writes it again. Inspect those versions with the `wiki_diff` tool.

`wiki-pull` writes what the tool returns, so a Markdown pull starts with YAML front matter (`title`, `source`, `trac_instance`, `trac_page`, `trac_version`, `exported`; see [Format Conversion](format-conversion.md#front-matter-on-an-export)). `wiki-push` of that text is refused by the server (a `validation_error`, exit 1, nothing stored) unless you pass `--force`, which is sent as `force: true` (only when given). Against a daemon that predates the guard `--force` fails with `the server does not know --force: it predates the exported-Markdown guard ...` (exit 1): such a daemon does not refuse the push either, so deploy it or push without `--force`. Deleting the front matter by hand defeats the guard; that is accepted.

`wiki-pull` prints each warning the server returns for the pull (what the Markdown translation passed through as HTML or dropped, or that the page version could not be read so the reads were not pinned to one version) as a `trac-mcp: warning: ...` line on stderr after writing the file, and still exits **0**.

`wiki-pull --link-map FROM=TO` (repeatable, split at the first `=`) is sent as the `link_rewrites` argument of `wiki_file_pull` and **replaces** the map for that pull. Without it, `wiki-pull` reads `export.link_rewrites` -- that key only, nothing else of the config, and not the `TRAC_MCP_LINK_REWRITES` variable -- from the config file it finds on **this machine** (`.trac_mcp/config.yml` / `.yaml` in the working directory, or the global ones; see [Configuration](configuration.md#configuration-file)) and sends that. If no config file has the key, it sends nothing and the **daemon's** configured map applies. The rewrite map is a publishing choice, so it is kept with the person exporting rather than in the daemon's config. A local config file that cannot be read, or whose `export` section is invalid, is a usage error (exit `2`, one `trac-mcp: wiki-pull: ...` line naming the problem) and nothing is pulled; `--link-map` bypasses the file, and `--format tracwiki` never reads it (and `--link-map` with it prints a `no effect` warning and sends nothing); `--format html` reads it as Markdown does. `--format html` writes Trac's own render as a complete minimal HTML document (no conversion, no CSS) with the `href` of each link rewritten: an in-page anchor (a `[[PageOutline]]` entry, `[#Heading x]`) becomes `#Heading` even with no map, and the map applies to the other links; everything else, image sources included, is Trac's render byte for byte. A daemon that predates this (#219) ignores the map for `html`, and `wiki-pull` then prints `the server did not confirm the link map was applied`. Links only: image sources are never rewritten, and an in-page anchor is written `#frag` and never mapped. See [Link rewrites](configuration.md#link-rewrites-export).

`wiki-pull --assets DIR [--assets-url PREFIX]` downloads the attachments the pulled page references into `DIR` and rewrites their links, for `--format markdown` and `--format html` (with `--format tracwiki` it is a usage error, exit `2`); the rules, the layout and the report are those of [`trac-convert --assets`](#exporting-attachments-assets). The attachments are fetched with the server's `wiki_attachment_get` and `ticket_attachment_get` tools, as `attach-get` does, on the same `--instance`, all over one MCP session; the page's own instance (the result's `instance`, else the front matter's `trac_instance`) says which URLs are this instance's. `--link-map` and `export.link_rewrites` run on the server before this, so a rule that covers the attachment links wins for them; images are never mapped and are still localised. Nothing is written if any fetch or write fails (exit `1`, as for an error from the server).

The server's write-time link gate and notes apply to all three writes, as for the equivalent MCP tools.

`FILE` may be `-` for stdin or stdout. From stdin `wiki-push` has no filename to detect a format from, so the text is stored as TracWiki unless you pass `--format markdown`. Global options go before the subcommand: `trac-mcp --instance /bcs wiki-push ...`.

### Connecting

| Option | Description |
|--------|-------------|
| `--url URL` | MCP endpoint, e.g. `http://192.0.2.10:8091/mcp` |
| `--server NAME` | `.mcp.json` entry to use (default `trac-http`) |
| `--token-env NAME` | Environment variable holding the bearer token |
| `--instance PATH` | Trac instance, e.g. `/auto_pm` (default: the server's) |

Without `--url`, `trac-mcp` uses the nearest `.mcp.json` from the working directory up: that entry's `url` and its `Authorization: Bearer ...` header, with `${VAR}` and `${VAR:-default}` expanded from the environment exactly as Claude Code expands them. Run from a project directory, it therefore connects as the same identity the project's MCP session uses, with no extra setup. Failing both, it reads `TRAC_MCP_URL` and `TRAC_MCP_AUTH_TOKEN`. A variable named with `--token-env` must be set; if the default `TRAC_MCP_AUTH_TOKEN` is unset, `trac-mcp` says that it is sending no token. An HTTP failure is reported by status, e.g. `HTTP 401 Unauthorized -- the bearer token was missing or rejected`.

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | The server answered with an error (the message is printed to stderr) |
| 2 | Usage error, or a local file problem |
| 3 | No endpoint, a missing token variable, or a transport/HTTP failure (e.g. 401) |
| 4 | The write WAS stored, but reading it back failed: a `ticket-comment` whose number could not be determined, or a `wiki-push` whose new page version could not be read. Do not retry the write |

Exit 1 always means nothing was stored.

### Installation

Installed with the package, next to `trac-mcp-server` and `trac-convert`:

```bash
pip install .          # or: pipx install .
```

---

[Back to Reference Overview](overview.md)
