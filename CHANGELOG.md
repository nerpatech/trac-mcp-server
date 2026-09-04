# Changelog

All notable changes to trac-mcp-server will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **`ci-live.sh`, the half of the suite `ci.sh` cannot run (Trac #81).** `ci.sh` passes no `--run-live`, so every `@pytest.mark.live` test is skipped — and the live suite is the only place this project exercises the real Trac substrate. Two defects landed green through `ci.sh` in one session: a live test still asserting the false positive #79 had just removed, and `stats.targets_checked` counting skipped targets as checked. Neither was found by the change that caused it; both were found later, by accident
  - **`--run-live` was deliberately not added to `ci.sh`.** That script stays hermetic — no credentials, no daemon, no network — so an ordinary local run can only fail for reasons belonging to the change under test. Making it depend on live Trac would be this ticket's own failure mode, inverted
  - **Absent credentials exit non-zero without running pytest at all**, so there is no green summary line to misread. Same shape as #80's `target_check_skipped`, one layer out: *not run* must not be able to print like *passed*. Credentials are resolved the way the tests resolve them — `.env` then the environment — so a developer with a working `.env` is not told they have none

### Changed
- **Breaking (advisory surface): `target_check_skipped` is replaced by three codes (Trac #83).** It merged outcomes that want opposite treatment from #64's blocking gate, so no severity was right for it — which is why #64 comment 2 asked for a ruling and comment 3 recorded it unanswered. `probe_targets` already distinguished the statuses; they were collapsed one function later, at reporting time
  - **`target_check_disabled`** (`info`) — the caller passed `check_targets=false`. Nobody asked for the check; that is the caller's deliberate choice, not a finding. **This is the path the ticket did not mention and the only one that fires on real content: all 188 of the corpus's findings are this one**
  - **`target_check_capped`** (`info`) — more probeable targets than the cap. The *document* is denser than the gate handles and the author can act (raise `target_cap`, split the document), so this is the candidate for #64's error column
  - **`target_check_failed`** (`info`, and stays advisory) — the probe could not reach the instance, including a ticket-realm target whose liveness control did not answer. The *checker* could not do its job; blocking here would stop every write while a remote instance is down and charge the author for an outage they did not cause. Carries the no-result-at-all case too
  - **The cap population is no longer empty, contrary to the ticket's section 3.** That was measured when the probe saw only the wiki realm; #82 widened it to the ticket realm, and the stock `TracChangeLog` help page goes from 15 unique probeable targets to **175**. Two documents now exceed the cap of 50, both that page. Worth carrying into #64: promoting `target_check_capped` to `error` would refuse writes to them, and the remedy it prints is not available to someone editing a stock help page they did not write
  - **The merged name is deleted, not aliased**, on all three paths — leaving it anywhere puts the ambiguity straight back where #64 has to rule on it. This renames a code in `convert_preview`, `ticket_render_check` and `wiki_render_check` output, and in the `target_cap` parameter description, so **a connected session needs a reconnect to see the new schema text**
  - Recall gate over the store corpus: 188 out of the old code, 188 into `target_check_disabled`, **every other code identical to the finding** and 86 documents refused, unchanged
  - The unreachable-instance row is tested against a **real** refused port and a real blackholed TEST-NET address rather than a mocked exception — but in the *offline* suite, not `ci-live.sh` as the ticket suggested: those sockets need no credentials and no Trac, and a `live` marker on a test that does not need the substrate is a test that stops running on GitHub Actions, which is precisely what #85 cost
- **The offline suite now runs without credentials, and is asserted to (Trac #81).** Without `--run-live`, `conftest.py` disables `.env` loading process-wide (`PYTHON_DOTENV_DISABLED`), and `ci.sh` additionally scrubs `TRAC_URL`/`TRAC_USERNAME`/`TRAC_PASSWORD`/`TRAC_INSECURE` from the environment for its pytest step
  - **This is the half a live script alone would not have caught.** In #85 `@pytest.mark.live` sat on a private helper instead of `TestConvertPreviewLive`, so four live tests ran *unconditionally* — they found `TRAC_URL` in `.env` on every developer machine and passed, while `master` sat red on GitHub Actions across two merges. `ci.sh` was green throughout and a `ci-live.sh` would have been green too; only an environment without credentials could see it
  - **Process-wide, because gating conftest's own `load_dotenv()` was measured and found insufficient.** `bootstrap_config()` calls `load_dotenv()` itself — correctly, since the server reads `.env` at startup — and that is the path #85's four tests take. With #85's defect seeded back in, `ci.sh` was still green under the conftest-only gate; the seeded run is what turned a plausible design into a working one, exactly what `Rules/testing/SeededDefectFirst` is for
  - Verified in both directions on the seed: `ci.sh` goes red on a misplaced `live` marker, while `pytest tests/ --run-live` — the run that originally hid it — still passes, so the gate discriminates rather than just failing

- **Breaking: the inline ticket and wiki tools no longer convert, in either direction (Trac #69).** The `format` parameter is gone from `ticket_create`, `ticket_update`, `ticket_batch_create`, `ticket_batch_update`, `wiki_create` and `wiki_update`; the `raw` parameter is gone from `ticket_get`, `ticket_changelog`, `wiki_get`, `wiki_search` and `milestone_get`; and `?format=` is gone from the `trac://wiki/` resource. All of them now store and return TracWiki bytes exactly as given, so a read-edit-write round trip is byte-exact
  - **Why removal rather than a flipped default.** #62 added `format` defaulting to `markdown`, which aimed the *destructive* failure at hand-authored TracWiki — indentation inside a `{{{ }}}` processor block was silently stripped, invisible in the render and in the (empty) warning list, visible only in the stored bytes — and the *safe* one at Markdown, which merely renders as literal `#` and `**`. Moving the default would have left an undeclared caller still getting *something* silently; removing the alternative leaves nothing to omit
  - **A stale caller is told, once, at the call site.** `format="markdown"` or `raw=false` returns a `validation_error` naming `trac-convert`, rather than being ignored. `format="tracwiki"` and `raw=true` are accepted as no-ops, so a caller already declaring TracWiki needs no change
  - **The converters are demoted, not deleted.** They still back the standalone `trac-convert` binary (#16), the `convert_preview` tool, and `wiki_file_push`/`wiki_file_pull` — the file tools keep converting deliberately, because they have a *filename* to go on where the inline tools have only content
  - **#47's pin moved rather than being dropped.** Its symptom — marker-poor Markdown misclassified by the content heuristic and stored unconverted — is re-anchored onto the converter and `auto_convert` tests, where the heuristic still runs. The misclassification is still asserted, not relaxed
  - The CRLF question left open by auto_pm:#90 is answered rather than inherited: the inline write handlers add no normalisation of their own, but a CR cannot reach the store through them regardless — `xmlrpc.client` emits a raw CR instead of escaping it as `&#13;`, and XML 1.0 line-end normalisation collapses it to LF on the parser side, before Trac sees the value. Measured end-to-end after deploy: a CRLF body stores with zero CRs. Storing bytes verbatim therefore does *not* create the CRLF-in-an-LF-store risk that was anticipated
- `ticket_get` now returns the ticket's comments by default (Trac #60): number, author, timestamp and body, so one call is the whole ticket. Previously comments were reachable only through `ticket_changelog`, with nothing in a `ticket_get` response indicating any existed — the compliant triage path cost two calls and the one-call path *looked* complete, which silently cost correctness (an operator's answer, already recorded in a comment, re-asked)
  - New `include_comments` (default `true`) — the documented opt-out for fetching `_ts` before a write; `max_comments` (default 50) caps long threads
  - Comments are filtered out of the changelog inside the tool, so field-only changes never reach the response: a ticket whose description was edited no longer drags two full copies of that description along to deliver a few bytes of comment text (the cost that ruled out proxying `ticket_changelog` wholesale)
  - Each comment carries the number Trac assigns it — the same number `ticket_render_check`'s `comment` parameter is keyed on, closing a discoverability gap where that number could only be found by guessing
  - Truncation is loud by construction: over the cap, the head and tail are kept and the middle dropped (a long thread has its scope set early and corrected late), and the response names how many exist, how many are shown, and which comment numbers were omitted — `comment_count` is always the exact total. A failed changelog fetch still returns the ticket, and says the comments are missing

### Fixed
- **A cross-instance ticket reference is now probed at all (Trac #82).** `is_probeable_wiki_href` matched `/intertrac/wiki%3A` and nothing else, so a `prefix:#N` short link was never fetched — not capped, not skipped, simply invisible, and `missing_cross_instance_target` was structurally incapable of firing for a dead one. Measured across 1020 documents on two stores: **695 cross-instance ticket references in 124 documents, none checked**, against 305 wiki-realm references in 105 documents that were
  - **Both dispatcher shapes, because the second was found by rendering rather than by reading.** `prefix:#N` becomes `/intertrac/%23N` and the realm form `prefix:ticket:N` becomes `/intertrac/ticket%3AN`; 10 of the corpus's references take the second, and a fix keyed on `%23` alone would have left every one of them invisible — the same false negative one door over, which is the #70 → #77 → #79 pattern this belongs to
  - **The ticket realm classifies by the opposite rule from the wiki realm.** A wiki page returns HTTP 200 whether or not it exists, so that check reads the body; a ticket that exists returns 200 and redirects to `/ticket/N`, while one that does not returns a bare 500. So existence is cheap and certain, and *absence* is not: a 500 is indistinguishable from the remote instance being down, misconfigured or unreachable
  - **Hence a control probe, without which this should not ship into a blocking column at all.** Before any 500 is called missing, the same instance is asked for a *wiki*-realm dispatcher target, which answers 200 whether or not that page exists. Control 200 → the instance is up and the credentials work, so the 500 is evidence; anything else → the candidate degrades to `target_check_skipped`, reported as unchecked, never as a broken link. Without it, one unwell instance would turn every cross-instance ticket link in a document into an error and, under #64, refuse the write while telling the author to fix links that are fine
  - **The control is deliberately not `prefix:#1` on the remote instance**, which the ticket suggested: a deleted ticket 1 fails the control permanently and silently disables the whole check, and a gate that always reports uncertainty looks exactly like one that always passes
  - **Classified on status, not body**, because that is the part that is about Trac rather than about one install: this host answers a missing ticket with an empty 500, `trac.edgewall.org` answers the same 500 with a 9 KB error page. One control request per distinct instance per call, and only when something on it came back 500 — a document whose cross-instance ticket links are all live costs nothing extra
  - **Measured before choosing a severity, per #79's precedent.** All 247 unique cross-instance ticket targets in the corpus resolve; zero are dead. So this ships at `error` alongside the wiki realm with no new blocking findings — and, because the live corpus is clean, the check is trusted only on the strength of seeded defects (`Rules/testing/SeededDefectFirst`), not on a green run
  - **Recall gate, both parts reported.** Same corpus, before and after: every other code identical to the finding — `bare_ticket_ref` 2525, `missing_local_target` 174, `incidental_wiki_autolink` 110, `unconfigured_intertrac_prefix` 106 — and 86 documents would be refused, unchanged. `target_check_skipped` rises 105 → 188 by construction, because 83 more documents are now known to carry cross-instance targets that a `check_targets=false` run did not verify. That code is `info` and non-blocking, so the refusal count is untouched
  - Known boundary, documented rather than assumed away: this host enforces authentication at the web layer (401, which takes the control down with it), so a permission denial cannot reach the 500 path. A Trac using fine-grained `TICKET_VIEW` restrictions might answer 500 for a ticket that exists but is invisible to the probing account, which would read as missing
  - `scripts/store_sweep.py` gained two harness fixes found while running the gate: it loads `.env` and reports missing credentials by name instead of dying on `KeyError: 'TRAC_URL'`, and its `--json` `examples` keys are now emitted in sorted order — they followed set-iteration order, so two runs of the *same* code diffed as if something had changed, in the one script whose whole purpose is a mechanical diff
- `probe_targets` no longer leaves the END of a document unchecked (Trac #80). The cross-instance target probe capped at 10 fetches and kept the **first** 10 in document order, so whatever it dropped was at the bottom of the page — which is exactly where an agent appends. A gate whose blind spot is the content just written is inverted relative to what a write-time gate is for, and Trac #64 is about to make these checks blocking. Measured live: a document with 12 distinct dead cross-instance targets reported only 10 of them; the last two came back `target_check_skipped` at `info` severity, which does not block
  - **The cap was 10 because the probe was sequential, not because probing is expensive** — 50 targets at the 5s timeout is 250s of wall clock in a `for` loop. Probes now run concurrently (8 workers), so the default cap rises to **50**, above anything measured in real content: across 998 documents on two stores the densest was 15 unique probeable targets, and that was a stock Trac help page
  - It stays a cap rather than becoming unbounded, and `target_check_skipped` still fires beyond it and still names the hrefs — a raised bound is precisely where a bound quietly becomes no bound
  - **New `target_cap` parameter** on `convert_preview`, `ticket_render_check` and `wiki_render_check`, defaulting to 50, for a deliberate audit of an unusually link-dense page
  - `client.session` is now read inside each worker rather than hoisted. `TracClient` hands out a *thread-local* session, so hoisting — harmless while the probe was sequential — would have given every worker one thread's session and quietly defeated that design
  - `stats.targets_checked` now counts targets actually fetched rather than entries in the probes dict, which included skipped ones. A capped run reporting "12 targets checked" is the same shape the `SKIPPED` status exists to prevent
  - Still open, and deliberately not fixed here: `target_check_skipped` remains `info` and therefore non-blocking, which under Trac #64's gate is the same "no errors means certified clean" shape that ticket's section 3 refuses to ship. Raising the cap shrinks the exposure; it does not close it
- `missing_local_target` no longer reports a CamelCase word Trac auto-linked out of ordinary prose as a broken link (Trac #79). Two entirely different things render as `<a class="missing wiki">` — an authored link to a page that does not exist, and any humped word in a sentence, which Trac's WikiFormatting linkifies whether or not anybody meant it as a reference — and the render carries no signal separating them. Measured across both stores, 110 of 291 findings were the second kind, and 38 correct documents would have been refused outright once Trac #64 promotes this code to a blocking error. One of them is a comment whose offending text is `"WiFi"/"LoRa"` inside a sentence quoting Trac #27: the gate would have blocked a write for describing the defect it exists to catch
  - **The incidental population keeps its signal, at `warning`.** New code `incidental_wiki_autolink`, advisory rather than blocking, carrying the `!` escape for the exact word as a fix suggestion. `missing_local_target` keeps its name, its `error` severity and the 181 authored findings
  - **The discriminating information is in the source, so the check now reads it.** An anchor is incidental only if its text equals the page name in its href (a label was typed by a person), the name is one Trac would actually auto-link, and it still occurs bare once code spans, fenced blocks, `[[...]]`, `[target label]` and realm references are blanked out
  - **Three gates, not one, because the obvious single test fails silent.** `See [[Page]]. The Page is missing.` renders exactly one anchor — from the authored, dead `[[Page]]` — yet `Page` does occur bare afterwards, so "does the text still occur bare" alone would downgrade a genuinely broken link. The shape gate is what keeps that an error
  - **Counted per target rather than decided per document.** A document carrying both `[[TracClient]]` and the prose word `TracClient` reports one error *and* one advisory; a boolean would answer "yes, it occurs bare" for the whole document and lose the dead link — Trac #70's residual through a new door
  - **The auto-link shape was measured, not reasoned about.** Twenty probes against the live daemon are pinned as `MEASURED_AUTOLINK_SHAPES`; `PyVISA` is among them because Trac #37 is this project's evidence that a hand-reasoned CamelCase rule goes wrong on an acronym tail. Where the measurement is ambiguous the gate stays narrow, since too narrow leaves today's false positive while too wide would hide a real dead link
  - **No source means no downgrade.** `build_verify_warnings` passes an empty source when `render_check` cannot pair one with the render; every anchor then stays an error rather than being downgraded for lack of evidence
  - New `scripts/store_sweep.py` replays `build_warnings` over a whole store offline, which is how the recall gate was run: 291 findings before, 181 + 110 after, every other code unchanged to the finding. The corpus it reads is cached locally and deliberately not checked in — this repository is public
- `unconfigured_intertrac_prefix` no longer false-warns on a configured prefix inside an inline code span (Trac #61): `_PREFIX_REALM_RE`'s trailing `\S+` did not stop at a backtick, so a token whose code span was followed immediately by non-whitespace — a table cell delimiter being the common case, since `||` never has a space before it — ran past the closing backtick and swallowed the next cell. The suppression path then compared a token longer than any rendered code span, found no containment, and reported a correct, configured prefix as unconfigured. Same class as #59's fenced-block gap, at the other delimiter; `_PREFIX_TICKET_RE` was never affected because it ends at `\b`
- `install.sh` now installs both `dist/` binaries, not just `trac-mcp-server` (Trac #54): it previously hard-coded a single binary name, so `trac-convert` on `PATH` was never refreshed by the normal build-and-install cycle — a copy placed there by hand once could silently drift 15+ days and a behavior change (the #37 fix) behind `dist/trac-convert`, with `--version` reporting the same string for both and unable to tell them apart
- `markdown_to_tracwiki` converter sweep (Trac #19, #20, #27, #29): four defects found and fixed together since they're all in the same `TracWikiRenderer`
  - **#19** — `[MACRO: Name(args)]`, the placeholder `tracwiki_to_markdown` emits for a macro it can't otherwise represent, now converts back to `[[Name(args)]]` instead of passing through as dead literal text; a `wiki_get` → edit → `wiki_update` round trip no longer permanently flattens a page's macros (this also affected `[[Page]]`-shaped links before #28's fix). `[[...]]` syntax typed directly in Markdown source now survives unchanged too. Both had to be shielded as sentinel placeholders on the *raw* Markdown source before mistune parses it — mistune splits an unresolvable `[...]` span into separate text fragments internally, so shielding from inside the leaf `text()` renderer isn't reliable
  - **#20** — An empty table cell (the usual shape for a row-label column) now renders as a lone space instead of a bare `||`; two adjacent `||` (`||||`) is TracWiki's colspan-2 marker, not two empty cells, and was shifting every following header/cell left by one column
  - **#27** — Plain-prose CamelCase-shaped words (`WiFi`, `LoRa`, ...) are now defensively `!`-prefixed, since Trac's WikiFormatting auto-links any such word into a broken missing-page link and Markdown has no equivalent auto-link concept to signal intent either way. Scoped to genuine prose only — code spans/blocks, a link's own display text, and macro/link names inside `[[...]]` are all unaffected
  - **#29** — A hard line break (`[[BR]]`) immediately after a colon-valued token (e.g. `substrate:trac`) now gets a leading space; Trac's wiki-link grammar otherwise greedily consumes `[[BR]]` into a failed `wikiname:target` TracLink parse instead of recognizing it as the line-break macro
- `tracwiki_to_markdown` no longer corrupts `[[Page]]` WikiLinks read via `wiki_get`/`wiki_search` (Trac #28, critical): plain double-bracket links were being routed through the unknown-macro placeholder mechanism, which silently discarded single links and, when two or more placeholders landed near each other, merged them into one unterminated construct with a leaked raw `\x00` byte
  - `[[Page]]` / `[[Page|Label]]` are now recognized as WikiLinks (matching Trac's own macro-name-registry-first resolution) and convert to real `[text](wiki:Page)` Markdown links; only a small allowlist of genuine Trac macro names, or any `[[Name(args)]]` carrying explicit arguments, still go through the macro path
  - Fixed two latent greedy-regex bugs this exposed: the macro-placeholder restore pass and the single-bracket `[url text]` link pass could each span past their own closing token into a neighboring link/macro when several appeared close together

### Added
- **A warning suite rule for damage that is invisible: `code_block_indentation_loss` (Trac #68).** Every other rule catches something a reader can *see* is wrong — literal markup, a dead link, a reference stuck in a code span. Feeding a TracWiki `{{{ }}}` block to the Markdown converter strips the leading whitespace from its body (the block is not recognised as a code construct, so ordinary paragraph handling eats it), storing syntactically invalid code with an **empty** warning list under a render that looks entirely plausible. It is visible only in the stored bytes, and it was committed twice inside #62 — a ticket whose subject was that exact corruption — caught both times only by reading the source back by hand
  - **`tracwiki_markup_in_markdown` could not have been widened to cover it.** That rule runs `blank_code_fences` first, which blanks a `{{{ ... }}}` region *including its delimiters*, so the `{{{` it scans for is erased before the scan reaches it. The shape that gets destroyed is precisely the shape it is built to ignore; #65's narrowing and this check are complements, not overlapping
  - **Detection lives in `converters/common.py`, not in the preview module**, so all three surfaces that still convert can reach it — `preview` is not packaged into the `trac-convert` binary, and #68 comment 4 measured `wiki_file_push` storing damaged bytes without ever calling `build_warnings`
  - **`wiki_file_push` refuses rather than warns.** An `error`-severity finding on a path that then stores the damaged bytes anyway would leave the corruption in the page and the recovery to whoever reads the response. `format="tracwiki"` still pushes the same file verbatim — the check gates on the declared format, not on the content. This is the one remaining write path that converts; #69 left it converting because it has a *filename* to go on
  - `convert_preview` reports it as a structured warning at severity `error` (the precedent is #59, where `intertrac_target_captured_punctuation` became an error for naming a genuinely dead link), gated off for TracWiki-declared input, where nothing converts and nothing can be lost. `trac-convert` reports it on stderr and still writes its output: that binary stores nothing, so the caller still holds the input
  - **Deliberately narrow, and measured that way.** Blocks are paired by their stripped body content rather than by position (a positional rule skips any document whose block counts differ, e.g. one holding both an indented-Markdown block and a damaged `{{{` one — a false negative on a real defect), and indentation is compared *relative* to each block's own minimum (mistune legitimately dedents a fenced block nested in a list item, which absolute comparison called loss). Two accepted residuals: a uniform dedent of a whole block, and two identical damaged blocks, both stay silent. Zero new warnings across all 72 preview fixture rows, the store corpus, and every Markdown file in the repo
- Streamable HTTP transport (Trac #26): `trac-mcp-server --transport http` serves MCP over `POST/GET/DELETE <path>` (default `/mcp`) via a Starlette app run by `uvicorn`, alongside the unchanged stdio default
  - Stateful sessions (`Mcp-Session-Id`, the MCP SDK default), so one long-lived process can serve multiple concurrent clients/sessions
  - Optional static bearer token auth (`TRAC_MCP_AUTH_TOKEN` / `server.auth_token`), checked with `secrets.compare_digest`; unauthenticated requests get `401` + `WWW-Authenticate: Bearer`
  - Bind-safety guard: refuses to bind a non-loopback host for `--transport http` unless a token is configured or `--allow-unauthenticated` is explicitly passed
  - DNS-rebinding protection via the MCP SDK's `TransportSecuritySettings` (`Host`/`Origin` allow-listing, extensible via `server.allowed_hosts`/`allowed_origins`)
  - Unauthenticated `GET /healthz` for container liveness/readiness probes
  - New `--transport`, `--host`, `--port`, `--path`, `--allow-unauthenticated` CLI flags and `TRAC_MCP_TRANSPORT`/`TRAC_MCP_HOST`/`TRAC_MCP_PORT`/`TRAC_MCP_PATH`/`TRAC_MCP_AUTH_TOKEN` env vars, plus a new `server:` YAML config section — no `--auth-token` CLI flag by design (would leak into the process list)
  - New `src/trac_mcp_server/mcp/http_app.py`: `BearerAuthMiddleware`, `build_http_app()`, `run_http()`
  - `Server("trac-mcp-server", version=__version__)` now reports the project version over HTTP too (previously only the stdio path set this, so `StreamableHTTPSessionManager`'s auto-generated init options would have reported the `mcp` SDK version)
  - New docs: [HTTP Transport Reference](docs/reference/http-transport.md); updated README, CLI, configuration, and deployment docs
- Multi-instance Trac support (Trac #18): every MCP tool and the `trac://wiki/*` resource now accept an optional `instance` argument that routes the call to another Trac project instead of the default one, with no server restart required
  - Named instances declared via a new `instances:` section in config files, or an out-of-band `TRAC_INSTANCES` file (YAML/JSON), with live reload on edit
  - Ad-hoc addressing (`instance: "/project-path"`) reaches any project on the same Trac host as the default instance using the default's credentials, restricted to the default instance's exact scheme+host so credentials can never be sent to another host
  - New `list_instances` tool (28th documented tool, 43rd registered including `ping`) surfaces configured instances plus, via a new `scrape_project_index()` web-scraping helper, other projects discoverable on the host's "Available Projects" index
  - `src/trac_mcp_server/instances.py`: `InstanceSpec`, `InstanceRegistry` (resolve/get_client/describe, with per-URL client caching and mtime-gated live reload), `UnknownInstanceError`, `load_declared_instances()`

## [2.2.0] - 2026-07-26

### Added
- New `trac-convert` CLI binary (second `[project.scripts]` entry point) — standalone TracWiki ↔ Markdown converter usable outside the MCP server
- `--from {md,tracwiki,auto}` and `--to {md,tracwiki}` format flags with `auto` detection via `converters.common.detect_format`
- stdin → stdout as default I/O mode (Unix-pipe friendly, binary-safe, no spurious trailing newlines)
- Positional `FILE` argument and `-o/--output FILE` flag for file I/O with clear errors on missing/unwritable paths
- `--from-clipboard` and `--to-clipboard` flags via `pyperclip` for cross-platform clipboard I/O (Linux/macOS/Windows/Wayland)
- `--heading-anchors {strip,preserve}` and `--unknown-macros {bracket,preserve}` flags exposing converter kwargs (keyword-only so 7 MCP tool call sites keep working unmodified; wrong-direction flags silently ignored)
- `--verbose/-v` and `--quiet/-q` mutually exclusive flags: `-v` emits three `info:` lines to stderr (source format, bytes read, bytes written); `-q` suppresses warnings
- Standardized exit codes: `EXIT_OK=0`, `EXIT_RUNTIME_ERROR=1` (I/O, clipboard, mutex), `EXIT_USAGE_ERROR=2` (argparse), `EXIT_CONVERSION_ERROR=3` (exceptions inside `convert_text()`)
- Full integration test suite for `trac-convert`: 9-cell source×destination I/O matrix (stdin/file/clipboard × stdout/file/clipboard), md↔tw roundtrip fixtures with expected-divergence assertions, cross-mode warning routing, UTF-8/CRLF/empty-input edge cases, 10 subprocess entry-point smoke tests exercising real OS pipe fd separation and console_scripts wiring (988 passing, up from 741 in v2.1.0)

### Changed
- `pyperclip>=1.8.2` added as a **required** dependency (not optional) — enables clipboard I/O out-of-the-box on all platforms
- Tool count remains 27 MCP tools (unchanged from v2.1.3); `trac-convert` is a separate binary, not an MCP tool

## [2.1.3] - 2026-07-25

### Added
- Batch ticket operations: `ticket_batch_create`, `ticket_batch_delete`, `ticket_batch_update` -- best-effort processing with per-item results and bounded parallelism via `gather_limited`
- `TRAC_MAX_BATCH_SIZE` environment variable (default: 500, range: 1-10000) for controlling maximum items per batch operation
- Config path resolution (`resolve_config_path()`) and bootstrapping (`ensure_config()`) utilities in config_loader.py
- YAML config file support via `config_loader` and `config_schema` integration in server lifespan -- discovers `.trac_mcp/config.yaml` with hierarchical loading
- Three-source configuration precedence: CLI arguments > environment variables > config file
- 5 new tests for YAML config loading path in `test_lifespan.py`
- Package version display in test script output
- Shared error translation utility (`translate_xmlrpc_error`) consolidating 5 duplicate implementations
- Shared timestamp formatting utility (`format_timestamp`) with timezone-aware UTC
- Shared constants module (`constants.py`) for tool handlers
- Network timeout (10s connect, 60s read) on XML-RPC requests
- 79 new tests covering TracClient methods, error handlers, auto_convert, and logger (781 -> 860 -> 641 after sync removal)
- `CHANGELOG.md` version history extracted from planning documents
- `severity` parameter now supported on `ticket_create` and `ticket_update` (previously accepted but dropped)
- `ticket_update` now forwards `summary`, `description` (with Markdown→TracWiki conversion), and `type` ticket attributes (previously accepted but dropped)
- `ticket_update` now forwards Trac workflow actions: `action` (e.g. `accept`, `resolve`, `reopen`) plus `action_<action>_<action>_<field>` workflow input fields (e.g. `action_resolve_resolve_resolution`). Pattern-based forwarding for `action_*` keys, since action names are workflow-config-dependent. Enables clients (e.g. auto-pm) to drive Trac workflow transitions through the MCP `ticket_update` tool instead of being silently dropped
- `core/client` now parses XML-RPC `<base64>` values, decoding them to Python `bytes` instead of dropping or mishandling them (#12)
- MCP tool wrappers for ticket attachments: `ticket_attachment_put`, `ticket_attachment_get`, `ticket_attachment_list`, `ticket_attachment_delete` — push, fetch (with byte-identical retrieval), enumerate, and remove ticket attachments via the MCP layer, with `TICKET_ADMIN`-aware permission diagnostics on delete (#10)
- MCP tool wrappers for wiki attachments: `wiki_attachment_put`, `wiki_attachment_get`, `wiki_attachment_list`, `wiki_attachment_delete` — push, fetch (with byte-identical retrieval), enumerate, and remove wiki page attachments via the MCP layer, with `WIKI_DELETE`-aware permission diagnostics on delete (#11)

### Changed
- Modernized typing across all source files to Python 3.10+ style (`X | None` instead of `Optional[X]`, built-in generics)
- Renamed `TRAC_ASSIST_CONFIG` env var to `TRAC_MCP_CONFIG` (backward compatible with deprecation warning)
- Updated copyright from OpenCode to nerpa.tech in LICENSE and pyproject.toml
- Added PyPI project URLs (Homepage, Repository, Issues, Changelog) to pyproject.toml
- Updated all documentation (configuration, deployment, CLI, troubleshooting, README) to reflect YAML config file support
- Unified `max_parallel_requests` default to 5 (was 2 in TracConfig)
- Moved `max_batch_size` from standalone function to TracConfig field with Pydantic validation
- Consolidated redundant wiki tool tests (457 -> 158 lines, 65% reduction)
- Reorganized test files into consistent `tests/test_mcp/tools/` directory structure
- Migrated 2 test files from unittest.TestCase to pytest style

### Fixed
- `ticket_get` now includes keywords, cc, reporter, and resolution fields in both text and structured JSON output (were previously omitted)
- Lazy logger formatting (`%s` instead of f-strings) in lifespan and system modules
- `ConversionResult` type mismatch in wiki resources (was passing object as string)
- `set_client()` signature to accept `TracClient | None` (removed type: ignore)
- Removed License classifier conflicting with PEP 639 (`setuptools >= 77.0` derives it from `license = "MIT"`)
- Dead code removal: duplicate `get_version()`, unused validator loop, stale imports
- Live test configuration: `.env` now loaded in conftest for `TRAC_URL` availability
- Markdown -> TracWiki converter no longer mangles sentinel-shaped markers like `[auto-pm: state NEEDS_CODE]`; non-URL-shaped links are emitted verbatim instead of being wrapped as broken wiki links (#8)

### Removed
- Sync subsystem: `doc_sync` and `doc_sync_status` MCP tools, `src/trac_mcp_server/sync/` module, and all sync-related tests (213 tests)
- `scripts/test_trac.py` live test script and associated report
- Sync config schema models (`SyncMappingRule`, `SyncProfileConfig`) and sync profile support in `UnifiedConfig`

## [2.1.0] - 2026-02-15

Post-extraction cleanup release. Hardens the standalone package after splitting from trac_assist v1.3.2.

### Changed
- Rewrote all documentation for standalone usage (README, configuration, deployment, troubleshooting, tool reference)
- Removed orphaned trac_assist references from source, comments, and docstrings
- Cleaned import paths and removed unused dependencies (cssselect, anyio from direct deps)
- Promoted pydantic and charset-normalizer to direct dependencies
- Standardized all error returns to MCP CallToolResult format (fixed isError boolean bug)
- Bumped setuptools requirement to >=77.0 for PEP 639 support

### Added
- Config validation: URL structure, max_parallel bounds (1-20), whitespace rejection
- .env.example with all supported environment variables
- MIT LICENSE file with PEP 639 SPDX metadata
- PyInstaller build.sh and install.sh scripts for standalone binary distribution
- GitHub Actions CI workflow (lint, test, live-test, build jobs)
- Local ci.sh script mirroring CI checks
- Ruff linter (E4/E7/E9/F/B/I rules) with zero-violation baseline
- Comprehensive test coverage: 741 tests (up from ~200 extracted tests)

### Fixed
- isError field in error responses now correctly set to True (was always False)
- Detection tests relying on trac_assist fixtures
- Stale monolith references in test_trac.py integration script

## [2.0.0] - 2026-02-14

Initial standalone release. Extracted from trac_assist v1.3.2 as independent MCP server package.

### Added
- 24 MCP tools (tickets, wiki, milestones, system)
- Wiki page resources via MCP resource protocol
- Markdown to TracWiki bidirectional format conversion
- stdio transport for MCP client integration
- Environment variable configuration (TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD)
- `trac-mcp-server` CLI entry point
- Independent test suite
