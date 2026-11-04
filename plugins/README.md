# Trac server-side plugins

Code in this directory **does not run in the MCP server**. It is installed on
the Trac host and runs inside Trac's own process. The MCP server is a client of
it.

That split is the thing to keep straight: per `Reference/environment/HostTopology`
in the `auto_pm` store, the MCP server runs on the workstation (`debian`) and
Trac runs on `kpoxa`, a different machine on a different subnet. `deploy.sh` at
the repo root deploys the *former* and has nothing to do with this directory.

## `tracrpc_comment`

Adds XML-RPC methods that XmlRpcPlugin does not expose:

| method | what it adds |
|---|---|
| `ticket.editComment` | correct a comment; prior text kept as a revision |
| `ticket.deleteComment` | remove a comment (guarded — see below) |
| `ticket.getCommentHistory` | read a comment's edit revisions |
| `ticket.replyToComment` | post a **threaded** reply |
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
Stock `TicketRPC` yields none of the four above, and stock `WikiRPC` does not
yield `putPageIfVersion`.

The installed `tracxmlrpc` is never modified. Rollback is one `config set
... disabled` plus a reload, and cannot affect the rest of the RPC surface:

```
ssh kpoxa '/home/user/trac/.venv/bin/trac-admin /home/user/trac/projects/<env> \
    config set components "tracrpc_comment.*" disabled'
ssh kpoxa 'kill -HUP $(cat /var/run/uwsgi/uwsgi.pid)'
```

The egg file can be left in place in that environment's own `plugins/`
directory -- a disabled component is inert, so nothing further needs
uninstalling. Because it was installed per environment rather than into the
shared venv (see below), disabling it on one instance cannot affect any
other.

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

## Installing on the Trac host

Requires no root: uWSGI's master runs as `user`, and `master = true` makes
`HUP` a graceful worker reload.

Installed as a **per-environment egg**, not into the shared Trac venv: each
instance gets its own copy in its own `plugins/` directory, so disabling or
removing it on one instance can never affect another (confirmed by the
`TracRpcComment-1.0.0-py3.11.egg` deployed for #98 -- see the rollback note
above).

```
# 1. build the egg -- with a Python matching the target Trac venv's own
#    version (3.11 on kpoxa), not necessarily this repo's own venv; the
#    Trac venv's own interpreter is a convenient way to guarantee that.
#    This only BUILDS a distributable egg file, it does not install
#    anything into that venv's site-packages.
scp -r plugins/tracrpc_comment kpoxa:/tmp/
ssh kpoxa 'cd /tmp/tracrpc_comment && /home/user/trac/.venv/bin/python setup.py bdist_egg'

# 2. copy the egg into ONE environment's own plugins/ directory --
#    /trac_test FIRST, the rest once proven -- then enable it there
ssh kpoxa 'cp /tmp/tracrpc_comment/dist/TracRpcComment-1.1.0-py3.11.egg \
    /home/user/trac/projects/trac_test/plugins/'
ssh kpoxa '/home/user/trac/.venv/bin/trac-admin /home/user/trac/projects/trac_test \
    config set components "tracrpc_comment.*" enabled'

# 3. reload
ssh kpoxa 'kill -HUP $(cat /var/run/uwsgi/uwsgi.pid)'
```

Confirm by content, not by the install exiting 0 — `system.listMethods` from a
reconnected session must list `ticket.editComment` (and, from 1.1.0,
`wiki.putPageIfVersion`).

**Upgrading from 1.0.0** (the release without `wiki.putPageIfVersion`):
**every instance moves at once.** Install the 1.1.0 egg into every instance
first, then send one reload; a single instance can't be upgraded on its own.
 * **Why:** all instances run in the same uWSGI worker processes. The first
   instance a worker opens adds its egg's version to the process-wide
   `pkg_resources` working set. Trac's loader then skips a different version
   of the same egg in any other instance, with
   `Skipping "TracRpcComment 1.1.0": VersionConflict: (TracRpcComment 1.0.0 ...)`
   in that instance's `trac.log`.
 * **What the components are:** they are registered process-wide, so every
   instance gets the components of whichever version loaded first. On
   2026-11-04, `/trac_test` alone carried 1.1.0: it went on listing 85 methods
   with no `wiki.putPageIfVersion`, served by `trac_mcp_server`'s 1.0.0 egg.
 * **How:** in each instance's `plugins/`, swap
   `TracRpcComment-1.0.0-py3.11.egg` for `TracRpcComment-1.1.0-py3.11.egg`,
   then reload once. The `tracrpc_comment.*` pattern already enabled
   covers the new component.

The per-environment isolation described above holds for *enabling*, which is
per-instance config. It doesn't hold for *versions*.

Step 1's exact build command is reconstructed from the deployed artifact's
name and Trac's own per-environment-egg convention, not a verbatim transcript
of what was run on kpoxa -- worth a quick check against that host before
relying on it to bring up an eleventh instance.

As of 2026-09-17, step 2 has been repeated for every instance on this host
except `/trac_test` (already covered above): `auto_pm`, `bcs`, `bfg`, `grow`,
`llm_balancer`, `nerpasite`, `scopemate`, `trac_mcp_server` -- each confirmed
by `system.listMethods` returning 85 methods (was 79), including all six new
ones. `/core` had `config set` run too and the egg is installed there, but it
cannot be confirmed the same way: `agent_rpc` holds no `XML_RPC` permission
grant on `/core` at all, so `system.listMethods` itself returns `403 XML_RPC
privileges are required` -- pre-existing on that instance and unrelated to
this plugin.

## Tests

The suite runs against **real Trac** (real model, real SQLite, real permission
policies), because `delete_change`'s revert semantics are a property of Trac
that a mock could only reproduce if its author already knew about them.

Trac is not a dependency of the MCP server, so this tree carries its own venv
rather than contaminating the hermetic one `ci.sh` depends on:

```
cd plugins/tracrpc_comment
python3 -m venv .venv
.venv/bin/pip install 'setuptools<81' 'Trac==1.6' pytest
PYTHONPATH=. .venv/bin/python -m pytest -c pytest.ini
```

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
