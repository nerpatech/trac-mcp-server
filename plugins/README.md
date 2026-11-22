# Trac server-side plugins

Code in this directory **does not run in the MCP server**. It is installed on
the Trac host and runs inside Trac's own process. The MCP server is a client of
it.

That split is the thing to keep straight: the MCP server and Trac are separate
services, usually on separate hosts, and deploying the MCP server never
installs or updates anything in this directory.

## `tracrpc_comment`

Adds XML-RPC methods that XmlRpcPlugin does not expose:

| method | what it adds |
|---|---|
| `ticket.editComment` | correct a comment; prior text kept as a revision |
| `ticket.deleteComment` | remove a comment (guarded — see below) |
| `ticket.getCommentHistory` | read a comment's edit revisions |
| `ticket.replyToComment` | post a **threaded** reply |
| `ticket.getChangeAt` | the comment of the change set stored at one microsecond `_ts` (#132) |
| `timeline.getEvents` / `timeline.getFilters` | the Trac timeline |
| `wiki.putPageIfVersion` | a `wiki.putPage` that refuses a stale version (#129) |

Three components, each enabled by the `tracrpc_comment.*` pattern below:
`TicketCommentRPC` (`tracrpc_comment.ticket_comment`), `TimelineRPC`
(`tracrpc_comment.timeline`) and `WikiConditionalPutRPC`
(`tracrpc_comment.wiki_put`). To enable only the wiki method, name it
instead: `config set components tracrpc_comment.wiki_put.* enabled`.

### It is additive — nothing is patched or replaced

`tracrpc.api.XMLRPCSystem.get_method` iterates every `IXMLRPCHandler` provider
and matches on the full dotted name, so a second component may claim the
existing `ticket` namespace as long as it yields names the incumbent does not.
Stock `TicketRPC` yields none of the five above, and stock `WikiRPC` does not
yield `putPageIfVersion`.

The installed `tracxmlrpc` is never modified. Rollback is one `config set
... disabled` in that instance's `trac.ini` plus a reload, and cannot affect
the rest of the RPC surface:

```
<trac-venv>/bin/trac-admin <env> config set components "tracrpc_comment.*" disabled
# then reload the server Trac runs in
```

A disabled component is inert, so nothing further needs uninstalling, and
disabling it in one instance leaves every other instance as it was. Do not
`pip uninstall` it to switch it off for one instance: the venv is shared, so
that removes it from every instance served from it at once.

### Two behaviours worth knowing before you use it

**`deleteComment` refuses by default when the comment came with field
changes.** `Ticket.delete_change` does not delete a comment — it deletes the
whole change-set recorded at that timestamp and *reverts* every field change
in it. Deleting the comment that accompanied a close would silently reopen the
ticket. Pass `force=True` to accept that; the refusal names the fields at risk.

**`replyToComment` is not `ticket.update` with quoted text.** Trac records
threading in the comment row itself (`save_changes(..., replyto=N)` stores the
child's number as `"<parent>.<child>"`), and that marker is what makes the web
UI nest the reply. `ticket.update` passes no `replyto`, so every comment
created over RPC before this plugin was flat. `quote=True` additionally
prepends Trac's own `Replying to [comment:N author]:` block.

### `wiki.putPageIfVersion(pagename, content, attributes, expected_version)`

Stock `wiki.putPage` ignores a `version` attribute, so the MCP server used to
check the version with a separate `wiki.getPageInfo` first, and a write
landing between the two was silently replaced (#120, #129). This method takes
the version the caller read and stores the page only if it is still current.

- **Atomic.** The page is loaded, its version compared and `WikiPage.save`
  called inside one `env.db_transaction`. On top of that, the `wiki` table's
  primary key `(name, version)` makes a write that commits between the
  comparison and the `INSERT` fail the `INSERT`, so the call refuses instead
  of overwriting on every database backend. Returns `True`. Only that race
  is reported as a conflict: after an `IntegrityError` the page is re-read,
  and if its version is still `expected_version` nobody else wrote, so the
  original error (a NOT NULL, or a constraint another plugin added)
  propagates instead of a conflict the client could never clear by retrying.
- **The conflict fault** is code **409** with a message starting
  `[wiki-version-conflict current_version=N]` (`none` when the page does not
  exist). TracXMLRPC wraps it as
  `'<message>' while executing 'wiki.putPageIfVersion()'`. Nothing is stored.
- **`expected_version` 0** means "create the page only if it is still
  absent" (a missing page has version 0 in Trac). Any other number on a
  missing page is a conflict with `current_version=none`.
- **Same save as `putPage`**: `WikiPage.save` itself, so name validation,
  "Page not modified", the page-name cache and the `IWikiChangeListener`
  notifications are Trac's own. Permissions: `WIKI_VIEW` on the page (to
  learn its version), then `WIKI_MODIFY`, `WIKI_CREATE` for a new page or
  `WIKI_ADMIN` for a read-only one, each against the page's resource.
- **Differences from `putPage`**: the author is always the authenticated
  caller (an `author` attribute is ignored), and `readonly` changes only when
  the attribute is passed (stock `putPage` clears it for an admin who omits
  it).

The MCP server uses it for every versioned wiki write and falls back to its
client-side precheck when the method is missing, so installing it is optional
but closes the window.

### `ticket.getChangeAt(id, ts)`

`ticket.update` returns the new change token `_ts` (microseconds). Finding the
comment that write just posted used to mean reading the whole
`ticket.changeLog` and matching to the whole second (#124, #131, #132). This
method returns the one change set stored at that exact microsecond.

- **Arguments.** `id` is the ticket number. `ts` is a **string** of ASCII
  digits, the `_ts` value as `ticket.update` returns it -- never an int,
  because XML-RPC ints are 32-bit. Anything else (empty, signed, decimal,
  non-string, or too large to be a time) raises a `TracError`.
- **Returns** `[cnum, author, comment]`: `cnum` exactly as Trac stores it (a
  string; dotted like `"2.5"` for a threaded reply made with
  `replyToComment`), `author` the change's author, and `comment` the stored
  text, unmodified. An empty comment can arrive as `None` over the wire.
- **Returns `[]`** when nothing was changed at that microsecond, or the change
  there has no `comment` field. An empty list is therefore "no such comment",
  not an error.
- **Permission.** `TICKET_VIEW` on the ticket, checked in the method like its
  siblings (the dispatcher entry declares `None`, see the comment in
  `xmlrpc_methods`). A missing ticket raises whatever `Ticket(env, id)` does.

The MCP server asks it first on every `ticket_update` that carries a comment,
and falls back to the old changelog scan when the method is missing (remembered
for five minutes) or the answer is unusable, so installing it is optional but
saves reading the changelog.

## Installing on a Trac host

### Install by commit into Trac's venv

The plugin is pip-installable straight from a commit of this repository.
`plugins/tracrpc_comment/pyproject.toml` carries only a `[build-system]`
table (`setuptools>=40.8.0`, the first release with `setuptools.build_meta`);
`setup.py` stays the metadata source, so the version is still the one in
`setup.py`. The `setuptools<81` that Trac 1.6 needs (it imports
`pkg_resources`, which setuptools 81 removed) is a property of the *Trac
venv's* own setuptools at run time, not of pip's isolated build environment,
so it is not pinned here: keep that venv on `setuptools<81`.

```
# 1. install into the venv Trac runs from. <sha> is the commit to deploy --
#    a sha, not a branch, so what runs is what was reviewed.
<trac-venv>/bin/pip install \
    "git+https://github.com/nerpatech/trac-mcp-server@<sha>#subdirectory=plugins/tracrpc_comment"

# 2. enable it in ONE instance -- a test instance first, the rest once proven
<trac-venv>/bin/trac-admin <env> config set components "tracrpc_comment.*" enabled

# 3. reload the server Trac runs in (a graceful worker reload is enough)
```

The host needs `git` and a route to the remote the URL names. Any clone of
this repository works in place of the GitHub URL, a local mirror included.

**A host with no route to PyPI** cannot fetch the isolated build
environment's `setuptools`, so install with `--no-build-isolation`: pip then
builds with the Trac venv's own setuptools, which must stay `<81` for
Trac 1.6's `pkg_resources` anyway.

**Moving to a newer commit.** Every commit keeps version 1.2.0 until the
plugin's code is next released, so a plain `pip install` of a newer sha sees
`TracRpcComment 1.2.0` already installed and does nothing. Reinstall it
explicitly. `--no-deps` keeps the reinstall to the plugin alone (it declares
no dependencies today, so this is a guard, not a change):

```
<trac-venv>/bin/pip install --force-reinstall --no-deps \
    "git+https://github.com/nerpatech/trac-mcp-server@<newsha>#subdirectory=plugins/tracrpc_comment"
```

For the same reason, the version does not tell you which commit is running.

Confirm by content, not by pip exiting 0: `system.listMethods` from a
reconnected session must list `ticket.editComment`, `wiki.putPageIfVersion`
and `ticket.getChangeAt`. `importlib.metadata.entry_points(group="trac.plugins")`
in that venv lists the three `tracrpc_comment.*` entries.

**Enabling is per instance, the version is per process.** A plugin installed
in the venv is enabled only where an instance's `trac.ini` says so; unlike an
egg in an instance's `plugins/` directory, it gets no default. But instances
served by the same worker processes share one Python working set, so they
all run the same version of it. Do not also keep a `TracRpcComment-*.egg`
in an instance's `plugins/`: a host moving from per-instance eggs to the venv
install deletes every such egg in the same change, then reloads. Trac's
loader skips an egg the venv install already provides, but only an egg of a
*different* version says so (`VersionConflict` in that instance's
`trac.log`); one of the same version, 1.2.0 for every commit so far, is
skipped silently and waits for the next version bump. Look for the file, not
for the log line.

### The maintainers' Trac host

On the host behind this project's own Trac, the plugin is not installed by
hand. It ships inside the Trac stack release, pinned by a commit of this
repository in the deployment repository's lock file (rookery,
`services/trac-uwsgi/requirements.lock`). The procedure is in rookery
`services/trac-uwsgi/README.md`, and the migration that set it up is rookery
ticket 69. In short:

 * **Moving it** means a commit here, pushed to `origin` (the host builds from
   its copy of `origin`, which has every commit before GitHub does), then a
   new pin in rookery, staged with `stage.sh trac <rookery commit>` (the
   stage serves `/trac_test`) and deployed with
   `deploy.sh trac <rookery commit>`. `deploy.sh trac --rollback` rolls
   back the whole stack release (Trac, every plugin and uWSGI), not the
   plugin alone; to switch the plugin off, disable it per instance.
 * **Never `pip install` into a release venv.** A release is root-owned and
   recorded by the deploy; changing it in place is unrecorded, and lost or
   inconsistent on the next deploy or rollback.
 * **Enabling stays per instance**, in `[components]`, with the release's own
   `trac-admin`, run as the instance's owner, never as root.
 * **The rookery release id**, not the version in `setup.py`, tells which pin
   is running.

## Tests

The suite runs against **real Trac** (real model, real SQLite, real permission
policies), because `delete_change`'s revert semantics are a property of Trac
that a mock could only reproduce if its author already knew about them.

Trac is not a dependency of the MCP server, so this tree carries its own venv
rather than contaminating the hermetic one `ci.sh` depends on:

```
cd plugins/tracrpc_comment
python3 -m venv .venv
.venv/bin/pip install -c constraints.txt 'setuptools<81' Trac pytest
PYTHONPATH=. .venv/bin/python -m pytest -c pytest.ini
```

`constraints.txt` (this directory) pins every package that install pulls in,
so the run is the same one CI makes: the `plugin` job in
`.github/workflows/ci.yml` installs with the same `-c` command and runs the
same pytest line with `plugins/tracrpc_comment` as its working directory.
The file's header says how to regenerate it. `ci.sh` and CI's lint job also
run `ruff check` and `ruff format --check` over `plugins/`.

`setuptools<81` is required because Trac 1.6 imports `pkg_resources`, which
setuptools 81 removed.

`tests/test_wiki_put.py`'s interleave tests use a real `Environment` on a
temporary SQLite **file** rather than `EnvironmentStub`: the stub's in-memory
database is one shared connection, so a second writer would join the call's
own transaction instead of committing on its own as another request would.
The second writer runs on its own thread, hooked in right after the version
is read; there are no sleeps.

`tests/conftest.py` stubs exactly two names from `tracrpc` (`IXMLRPCHandler`,
a bare marker `Interface`, and `web_context`, which upstream re-exports from
`trac.web.chrome`) because TracXMLRPC's Python 3 build is not on PyPI — the
newest release there, 1.1.9, is Python 2 only. Everything the dispatcher does
above those two names is proven by live acceptance against `/trac_test`, not
here.
