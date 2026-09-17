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

### It is additive — nothing is patched or replaced

`tracrpc.api.XMLRPCSystem.get_method` iterates every `IXMLRPCHandler` provider
and matches on the full dotted name, so a second component may claim the
existing `ticket` namespace as long as it yields names the incumbent does not.
Stock `TicketRPC` yields none of the four above.

The installed `tracxmlrpc` is never modified. Rollback is one `config set
... disabled` plus a reload, and cannot affect the rest of the RPC surface:

```
ssh kpoxa '/home/user/trac/.venv/bin/trac-admin /home/user/trac/projects/<env> \
    config set components "tracrpc_comment.*" disabled'
ssh kpoxa 'kill -HUP $(cat /var/run/uwsgi/uwsgi.pid)'
```

The egg can be left installed in the shared venv -- a disabled component is
inert, so nothing further needs uninstalling.

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

## Installing on the Trac host

Requires no root: uWSGI's master runs as `user`, and `master = true` makes
`HUP` a graceful worker reload.

```
# 1. install into the SHARED Trac venv (one install serves all instances)
scp -r plugins/tracrpc_comment kpoxa:/tmp/
ssh kpoxa '/home/user/trac/.venv/bin/pip install /tmp/tracrpc_comment'

# 2. enable per environment -- /trac_test FIRST, the rest once proven
ssh kpoxa '/home/user/trac/.venv/bin/trac-admin /home/user/trac/projects/trac_test \
    config set components "tracrpc_comment.*" enabled'

# 3. reload
ssh kpoxa 'kill -HUP $(cat /var/run/uwsgi/uwsgi.pid)'
```

Confirm by content, not by the install exiting 0 — `system.listMethods` from a
reconnected session must list `ticket.editComment`.

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

`tests/conftest.py` stubs exactly two names from `tracrpc` (`IXMLRPCHandler`,
a bare marker `Interface`, and `web_context`, which upstream re-exports from
`trac.web.chrome`) because TracXMLRPC's Python 3 build is not on PyPI — the
newest release there, 1.1.9, is Python 2 only. Everything the dispatcher does
above those two names is proven by live acceptance against `/trac_test`, not
here.
