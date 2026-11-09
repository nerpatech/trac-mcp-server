"""Helpers for the live (``--run-live``) rows. Trac /trac_mcp_server #173.

Three things live here so the rows and conftest share one copy:

  * ``trac_test_url()`` / ``trac_test_client()`` /
    ``trac_test_registry()``: the destructive-test instance. It is
    ``TRAC_TEST_URL`` when that is set (the instance has its own origin,
    Trac /trac_mcp_server #203), else ``/trac_test`` on the host and
    port ``TRAC_URL`` names, whatever project or sub-path ``TRAC_URL``
    itself carries.
  * the runtime write guard (``live_write_guard``): while it is
    installed, a TracClient XML-RPC WRITE whose client is not on
    ``/trac_test`` is recorded and refused before any byte is sent.
  * the stale-leftover sweep (``select_stale`` / ``sweep_stale``):
    deletes this branch's per-run objects that an interrupted run left
    on /trac_test.

Every XML-RPC request a TracClient makes passes through
``TracClient._rpc_call`` -- it holds the only ``session.post`` in
src/. ``_rpc_request`` (which every client method uses, including the
``system.multicall`` batches and ``wiki.putPageIfVersion``) and
``get_server_time`` both call it. So wrapping ``_rpc_call`` covers
every method the client has.
"""

import contextlib
import logging
import os
import xmlrpc.client
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from trac_mcp_server.config import Config
from trac_mcp_server.core.client import TracClient
from trac_mcp_server.instances import InstanceRegistry, InstanceSpec

logger = logging.getLogger(__name__)

TEST_INSTANCE = "/trac_test"

# ---------------------------------------------------------------------
# The /trac_test URL and client
# ---------------------------------------------------------------------


def trac_test_url() -> str:
    """The ``/trac_test`` instance every live row writes to.

    ``TRAC_TEST_URL`` (trailing ``/`` stripped) when it is set and
    non-empty: rookery #69 moves /trac_test to its own origin, and a
    308 across ports drops Basic credentials, so a row cannot reach it
    through ``TRAC_URL``'s host and port any more. Otherwise
    ``/trac_test`` on the scheme, host and port ``TRAC_URL`` names.

    Raises ``ValueError`` when ``TRAC_TEST_URL`` does not end with
    ``/trac_test``: the write guard (``is_trac_test_url``) would refuse
    every write to it, and a clear error here beats that.
    """
    override = os.environ.get("TRAC_TEST_URL", "").strip()
    if override:
        url = override.rstrip("/")
        if not is_trac_test_url(url):
            raise ValueError(
                f"TRAC_TEST_URL={override!r} does not end with "
                f"{TEST_INSTANCE}: the live write guard would refuse "
                "every write to it"
            )
        return url
    parsed = urlparse(os.environ["TRAC_URL"])
    return f"{parsed.scheme}://{parsed.netloc}{TEST_INSTANCE}"


def trac_test_config() -> Config:
    """A Config for ``/trac_test``, credentials from the environment."""
    return Config(
        trac_url=trac_test_url(),
        username=os.environ["TRAC_USERNAME"],
        password=os.environ["TRAC_PASSWORD"],
        insecure=os.environ.get("TRAC_INSECURE", "").lower()
        in ("1", "true", "yes"),
    )


def trac_test_client() -> TracClient:
    """A TracClient on ``/trac_test`` (see ``trac_test_url``)."""
    return TracClient(trac_test_config())


def trac_test_registry(default_config: Config) -> InstanceRegistry:
    """An InstanceRegistry whose ``/trac_test`` is ``trac_test_url()``.

    For a row that hands a registry to a server, CLI or handler stack
    and then calls with ``instance="/trac_test"``. ``/trac_test`` is
    declared, so it is looked up before the ad-hoc ``/path`` branch
    (which would join it onto ``default_config``'s host and port). The
    spec sets no credentials, so ``default_config``'s are inherited and
    a per-caller Identity replaces them, as on the ad-hoc path.
    """
    spec = InstanceSpec(
        name=TEST_INSTANCE,
        url=trac_test_url(),
        username=None,
        password=None,
        insecure=None,
    )
    return InstanceRegistry(default_config, {TEST_INSTANCE: spec})


# ---------------------------------------------------------------------
# The runtime write guard
# ---------------------------------------------------------------------

# A method name (the part after the realm: ``wiki.putPage`` ->
# ``putPage``, ``ticket.milestone.delete`` -> ``delete``) is a write
# when it starts with one of these. Derived from the names TracClient
# posts: putPage, putPageIfVersion, putAttachment, putAttachmentEx,
# deletePage, deleteAttachment, deleteComment, delete (ticket,
# milestone, component, enums), create (ticket, milestone, component,
# enums), update (ticket, milestone), editComment, replyToComment.
WRITE_PREFIXES = (
    "put",
    "delete",
    "create",
    "update",
    "edit",
    "reply",
)


class LiveWriteRefused(Exception):
    """A live write was aimed at a client not on /trac_test."""


def is_write_method(qualified: str) -> bool:
    """Whether ``realm.method`` names a write."""
    name = qualified.rsplit(".", 1)[-1]
    return name.startswith(WRITE_PREFIXES)


def write_methods_in(service: str, method: str, params) -> list[str]:
    """The write methods one request carries: itself, or the writes
    among a ``system.multicall``'s calls."""
    qualified = f"{service}.{method}"
    if qualified == "system.multicall":
        calls = params[0] if params else []
        found = []
        for call in calls if isinstance(calls, list | tuple) else []:
            name = (
                call.get("methodName")
                if isinstance(call, dict)
                else None
            )
            if not isinstance(name, str):
                # Cannot tell what it is: not provably a read.
                found.append(f"<unreadable multicall entry {call!r}>")
            elif is_write_method(name):
                found.append(name)
        return found
    return [qualified] if is_write_method(qualified) else []


def is_trac_test_url(url: str) -> bool:
    """Whether the URL's path ends with ``/trac_test``."""
    path = urlparse(url).path.rstrip("/")
    return path.endswith(TEST_INSTANCE)


def write_violation(
    client: TracClient, service: str, method: str, params
) -> str | None:
    """Why this request must not be sent, or ``None`` when it may be.

    Reads are always allowed. A write needs the client's instance URL
    (``config.trac_url``) to end with ``/trac_test``; and when an RPC
    endpoint override is set (OIDC), that endpoint too, since it is
    where the request really goes.
    """
    writes = write_methods_in(service, method, params)
    if not writes:
        return None
    problems = []
    if not is_trac_test_url(client.config.trac_url):
        problems.append(f"instance {client.config.trac_url!r}")
    override = client.config.rpc_url_override
    if override and TEST_INSTANCE not in urlparse(override).path:
        problems.append(f"RPC endpoint {override!r}")
    if not problems:
        return None
    return (
        f"write {', '.join(writes)} to {' and '.join(problems)}: "
        f"a live row may only write to a client whose URL ends with "
        f"{TEST_INSTANCE}"
    )


@contextlib.contextmanager
def live_write_guard() -> Iterator[list[str]]:
    """Refuse a TracClient write to a non-/trac_test instance.

    Wraps ``TracClient._rpc_call`` for the duration. A violation is
    appended to the yielded list (so a test body that swallows the
    exception cannot hide it) and raised as ``LiveWriteRefused``
    BEFORE the request is built or sent, so nothing is written.
    """
    violations: list[str] = []
    original = TracClient._rpc_call

    def guarded(self, service, method, *params):
        reason = write_violation(self, service, method, params)
        if reason is not None:
            violations.append(reason)
            raise LiveWriteRefused(reason)
        return original(self, service, method, *params)

    TracClient._rpc_call = guarded  # type: ignore[method-assign]
    try:
        yield violations
    finally:
        TracClient._rpc_call = original  # type: ignore[method-assign]


# ---------------------------------------------------------------------
# The stale-leftover sweep
# ---------------------------------------------------------------------

# Per-run name prefixes this branch's rows create on /trac_test.
STALE_PAGE_PREFIXES = (
    "Scratch/Ticket105WriteGate",
    "PageTemplates/Probe",
    "Probe/WikiConventions",
    "Scratch/Ticket173",
    "Scratch/TracConvertLive",
    "Scratch/Ticket210PullLive",
    "Scratch/Ticket213LinkLive",
    "Scratch/Ticket211HtmlLive",
)
# Summary of the throwaway ticket the ticket-attachment row creates.
STALE_TICKET_SUMMARY = "Ticket173AttachmentLive"
# Fixed names of the pre-#173 rows. Two of them match a prefix above
# (the old conventions probe pages); they are not this branch's to
# delete and the sweep leaves them alone entirely.
LEGACY_NAMES = frozenset(
    {
        "PageTemplates/Probe123",
        "Probe/WikiConventions123/Style",
        "Ticket105WriteGateLive",
        "Ticket87WriteGateLive",
        "TracConvertLiveTest",
    }
)
STALE_AFTER = timedelta(hours=1)
# Milestones are NOT swept: ticket.milestone.get returns name, due,
# completed and description, and Trac keeps no modification time for
# one, so "older than 1 hour" cannot be decided without risking a
# concurrent run's live milestone (a leftover one is
# Ticket87WriteGateLive<hex8>).


def _as_utc(value) -> datetime | None:
    """A Trac timestamp (compact ISO string, DateTime, or datetime) as
    an aware UTC datetime; ``None`` when it is not one."""
    if isinstance(value, datetime):
        return (
            value
            if value.tzinfo
            else value.replace(tzinfo=timezone.utc)
        )
    if isinstance(value, xmlrpc.client.DateTime):
        value = value.value
    if not isinstance(value, str):
        return None
    for fmt in ("%Y%m%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
    return None


def select_stale(
    modified: dict[str, object],
    prefixes: tuple[str, ...],
    now: datetime,
    max_age: timedelta = STALE_AFTER,
) -> list[str]:
    """Names (sorted) among ``modified`` (name -> last-modified) that
    start with one of ``prefixes``, are not a legacy name, and were
    last modified more than ``max_age`` before ``now``. A name whose
    timestamp cannot be read is left alone."""
    out = []
    for name, stamp in modified.items():
        if name in LEGACY_NAMES or not name.startswith(prefixes):
            continue
        when = _as_utc(stamp)
        if when is not None and now - when > max_age:
            out.append(name)
    return sorted(out)


def sweep_stale(
    client: TracClient, now: datetime | None = None
) -> list[str]:
    """Delete stale per-run leftovers on ``client`` (a /trac_test
    client); returns a description of each deletion. Never raises: an
    error is logged as a warning and the sweep moves on."""
    now = now or datetime.now(timezone.utc)
    deleted: list[str] = []

    try:
        names = [
            n
            for n in client.list_wiki_pages()
            if n.startswith(STALE_PAGE_PREFIXES)
            and n not in LEGACY_NAMES
        ]
        modified = {}
        for name in names:
            try:
                info = client.get_wiki_page_info(name)
                modified[name] = (
                    info.get("lastModified")
                    if isinstance(info, dict)
                    else None
                )
            except Exception as exc:
                logger.warning("sweep: info %s: %r", name, exc)
        for name in select_stale(modified, STALE_PAGE_PREFIXES, now):
            try:
                client.delete_wiki_page(name)
                deleted.append(f"page {name}")
            except Exception as exc:
                logger.warning("sweep: delete %s: %r", name, exc)
    except Exception as exc:
        logger.warning("sweep: wiki pass failed: %r", exc)

    try:
        ids = client.search_tickets(
            f"summary={STALE_TICKET_SUMMARY}&max=0"
        )
        modified_t = {}
        for tid in ids if isinstance(ids, list) else []:
            try:
                data = client.get_ticket(tid)
                attrs = data[3]
                if attrs.get("summary") == STALE_TICKET_SUMMARY:
                    modified_t[str(tid)] = attrs.get("changetime")
            except Exception as exc:
                logger.warning("sweep: ticket %s: %r", tid, exc)
        for key in select_stale(modified_t, ("",), now):
            try:
                client.delete_ticket(int(key))
                deleted.append(f"ticket {key}")
            except Exception as exc:
                logger.warning("sweep: delete ticket %s: %r", key, exc)
    except Exception as exc:
        logger.warning("sweep: ticket pass failed: %r", exc)

    for line in deleted:
        logger.warning("sweep: deleted stale %s", line)
    return deleted
