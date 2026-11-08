import base64
import calendar
import re
import socket
import threading
import time
import weakref
import xmlrpc.client
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, NamedTuple, TypeVar, cast
from xml.etree import ElementTree

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection

from ..config import Config
from ..validators import validate_content, validate_page_name
from .scope import ScopedSession, scope_for
from .wiki_search import BM25, Section, search_pages, split_sections

# Seconds a pooled connection may sit idle before the kernel sends a TCP
# keepalive probe. Deliberately far below the idle timeout of any plausible
# NAT or stateful firewall between client and Trac: when such a device drops
# its flow state it does so silently, with no RST to either end, so the socket
# still looks established here and the next request is written into a black
# hole and stalls until the read timeout. See tickets #21/#22 -- the failing
# requests never reached the server at all (no access-log entry, not even a
# 499), and only ever happened on this long-lived process after an idle gap.
TCP_KEEPALIVE_IDLE_SECONDS = 60
TCP_KEEPALIVE_INTERVAL_SECONDS = 15
TCP_KEEPALIVE_PROBE_COUNT = 4

# The longest ticket comment ``update_ticket`` accepts. The trac-mcp CLI
# checks the same limit before it connects (Trac #114).
MAX_COMMENT_CHARS = 10000


class CommentNumber(NamedTuple):
    """A parsed changeLog comment row's ``oldvalue``.

    ``cnum`` is the comment's own number; ``reply_to`` is the comment it
    is threaded under, or None for a flat comment.
    """

    cnum: int
    reply_to: int | None


def _keepalive_socket_options() -> list[tuple[int, int, int | bytes]]:
    """Socket options that keep pooled connections visible to middleboxes.

    Extends urllib3's defaults (TCP_NODELAY) rather than replacing them.
    The per-idle/interval/count options are platform-specific -- Linux spells
    the idle timer ``TCP_KEEPIDLE`` and macOS ``TCP_KEEPALIVE`` -- so each is
    applied only where the constant exists. ``SO_KEEPALIVE`` alone is portable
    but useless on its own: its default idle timer is 7200s on Linux, long
    past when a NAT mapping has expired.
    """
    options = list(HTTPConnection.default_socket_options)
    options.append((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1))

    idle_option = getattr(socket, "TCP_KEEPIDLE", None) or getattr(
        socket, "TCP_KEEPALIVE", None
    )
    if idle_option is not None:
        options.append(
            (
                socket.IPPROTO_TCP,
                idle_option,
                TCP_KEEPALIVE_IDLE_SECONDS,
            )
        )
    if hasattr(socket, "TCP_KEEPINTVL"):
        options.append(
            (
                socket.IPPROTO_TCP,
                socket.TCP_KEEPINTVL,
                TCP_KEEPALIVE_INTERVAL_SECONDS,
            )
        )
    if hasattr(socket, "TCP_KEEPCNT"):
        options.append(
            (
                socket.IPPROTO_TCP,
                socket.TCP_KEEPCNT,
                TCP_KEEPALIVE_PROBE_COUNT,
            )
        )
    return options


class KeepAliveHTTPAdapter(HTTPAdapter):
    """Adapter whose pooled connections carry TCP keepalive options.

    ``socket_options`` is a urllib3 pool argument rather than an
    ``HTTPAdapter`` one, so it has to be injected where requests builds the
    pool manager. Both entry points are covered: ``init_poolmanager`` for
    direct connections and ``proxy_manager_for`` for proxied ones.
    """

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("socket_options", _keepalive_socket_options())
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("socket_options", _keepalive_socket_options())
        return super().proxy_manager_for(*args, **kwargs)


class TicketCreateTimeout(Exception):
    """A ticket.create timed out, and we determined what happened to it.

    ``ticket_id`` is the id of the ticket if it was created despite the
    timeout, or None if no matching ticket could be found (so the create
    most likely did not land).

    Raised instead of retrying automatically: ticket.create is not
    idempotent, so an automatic retry can duplicate a ticket, and
    automatically returning a summary-matched ticket can silently skip a
    create the caller expected. Reporting what happened lets the caller
    decide, which is the check they would otherwise hand-roll.
    """

    def __init__(self, message: str, ticket_id: int | None = None):
        super().__init__(message)
        self.ticket_id = ticket_id


class TicketUpdateConflict(Exception):
    """A ticket_update's ``base_ts`` was stale: the ticket changed after the
    caller's read and before this write (Trac's mid-air collision fault).

    Only raised when the caller supplied ``base_ts`` -- that is the only
    case in which Trac's own lock is actually consulted; see
    ``TracClient.update_ticket`` (ticket #50).

    ``changes`` lists the changelog entries that landed after ``base_ts``
    (each ``{timestamp, author, field, oldvalue, newvalue}``), so the
    caller can see what it would otherwise silently overwrite -- comments
    especially, since those carry no field-level diff of their own.
    """

    def __init__(
        self,
        message: str,
        ticket_id: int,
        base_ts: Any,
        changes: list[dict[str, Any]],
    ):
        super().__init__(message)
        self.ticket_id = ticket_id
        self.base_ts = base_ts
        self.changes = changes


class WikiPageConflict(ValueError):
    """A wiki_update's ``version`` was stale: the page's current version is
    not the one the caller read (ticket #120).

    Raised by ``TracClient.put_wiki_page`` BEFORE ``wiki.putPage`` is
    posted, because Trac ignores the ``version`` attribute of putPage --
    no fault ever comes back for a stale one, so the comparison has to be
    made here (see ``put_wiki_page``).

    ``current_version`` is ``None`` when the page does not exist at all.
    Subclasses ``ValueError`` so callers that already catch the client's
    plain version-conflict ``ValueError`` keep working.
    """

    def __init__(
        self,
        message: str,
        page_name: str,
        expected_version: int,
        current_version: int | None,
    ):
        super().__init__(message)
        self.page_name = page_name
        self.expected_version = expected_version
        self.current_version = current_version


class WikiPageNotModified(ValueError):
    """A wiki write's content was byte-identical to the stored page, so
    Trac stored nothing (ticket #174).

    Raised by ``TracClient.put_wiki_page`` from both write paths when the
    server answers "Page not modified" (Trac's own TracError text, matched
    exactly: a false match is reported to callers as success). That is a
    no-op, not a failure: callers report it as "unchanged". ``version`` is
    the version the caller passed (``None`` for an unversioned write).
    ``current_version`` is the page's version re-read after the no-op,
    best effort (``None`` when the read failed or answered nothing
    usable): it is what the page is at now, which differs from
    ``version`` when someone else stored the same content meanwhile.

    Subclasses ``ValueError``, with the message the client has always
    used, so callers that catch the plain ``ValueError`` (the CLI
    ``convert`` code) keep working.
    """

    def __init__(
        self,
        message: str,
        page_name: str,
        version: int | None,
        current_version: int | None = None,
    ):
        super().__init__(message)
        self.page_name = page_name
        self.version = version
        self.current_version = current_version


def _describe_error(err: BaseException) -> str:
    """A one-line description of ``err``: a Fault's faultString, else its
    message, else its type name."""
    if isinstance(err, xmlrpc.client.Fault):
        return err.faultString
    return str(err) or type(err).__name__


# Trac /trac_mcp_server #129: the tracrpc_comment plugin's conditional put
# (plugins/tracrpc_comment/tracrpc_comment/wiki_put.py). It makes the
# version check and the save one database transaction on the server, which
# closes the window between put_wiki_page's client-side precheck and putPage.
WIKI_CONDITIONAL_PUT_METHOD = "putPageIfVersion"

# The plugin's conflict fault: code 409, and a message (wrapped by
# TracXMLRPC as "'<message>' while executing 'wiki.putPageIfVersion()'")
# that starts with this marker. Both must match, so no other fault -- not
# even another 409 -- can be read as a conflict.
_WIKI_CONFLICT_FAULT_CODE = 409
_WIKI_CONFLICT_MARKER = re.compile(
    r"^'?\[wiki-version-conflict current_version=(\d+|none)\]"
)

# Trac's own TracError text for a byte-identical write, compared lowercased
# (ticket #174). Not the looser "not modified": a plugin's unrelated fault
# containing that would be reported as an "unchanged" success.
_WIKI_NOT_MODIFIED_MARKER = "page not modified"

# TracXMLRPC's MethodNotFound fault code (JSON-RPC's "method not found").
_METHOD_NOT_FOUND_FAULT_CODE = -32601

# How long a client remembers that the plugin method is missing before it
# tries again. Long enough that a burst of writes to a plugin-less instance
# pays the extra round trip once, short enough that deploying the plugin is
# picked up by a running daemon without a restart.
WIKI_CONDITIONAL_PUT_ABSENT_TTL_SECONDS = 300.0

# The same, for the plugin's ``ticket.getChangeAt`` (Trac
# /trac_mcp_server #132).
TICKET_CHANGE_AT_ABSENT_TTL_SECONDS = 300.0

# The same, for the stock ``system.multicall``: one memory shared by the
# wiki search, which fills its page cache with it (Trac /trac_mcp_server
# #154), and ``get_tickets``, which batches ``ticket.get`` with it (#158).
MULTICALL_ABSENT_TTL_SECONDS = 300.0

# Pages read per ``system.multicall`` (two calls each: getPageInfo and
# getPage).
WIKI_FETCH_BATCH_PAGES = 50

# Tickets read per ``system.multicall`` (one ``ticket.get`` each) in
# ``get_tickets`` (Trac /trac_mcp_server #158).
TICKET_FETCH_BATCH = 50

# Per-prefix BM25 indexes the wiki search keeps (least recently used
# first out): each holds every section in its scope, and a caller can
# make up prefixes without end.
WIKI_SCOPE_INDEX_MAX = 16

# "Since the beginning" for ``wiki.getRecentChanges``.
_WIKI_EPOCH = "19700101T00:00:00"

# TracXMLRPC wraps every fault from a plain TracError as
# "'<message>' while executing '<method>()'".
_TRACRPC_FAULT_WRAPPER = re.compile(
    r"^'(?P<message>.*)' while executing '[\w.]+\(\)'$", re.DOTALL
)


def _is_missing_method_fault(
    err: xmlrpc.client.Fault, method: str
) -> bool:
    """True when ``err`` says the server has no RPC method ``method``.

    TracXMLRPC raises ``MethodNotFound('RPC method "<name>" not found')``
    with fault code -32601. The text fallback matches the same heuristic as
    the ticket_comment tools' ``_translate_missing_method``, narrowed to the
    method name so that a "page not found" fault can never pass for it.
    "No such method" (some servers' wording) counts on the same terms: the
    text must name ``method``.
    """
    if err.faultCode == _METHOD_NOT_FOUND_FAULT_CODE:
        return True
    text = err.faultString.lower()
    if method.lower() not in text:
        return False
    return ("not found" in text and "method" in text) or (
        "no such method" in text
    )


@dataclass
class _OptionalMethod:
    """An RPC method only some servers have (a plugin's), and the client's
    memory of one that was missing.

    ``note_fault`` records a missing-method fault; ``available`` is False
    for ``ttl`` seconds after that, so a burst of calls to a server
    without the plugin pays the extra round trip once, while a plugin
    deployed later is picked up without a restart.
    """

    name: str
    ttl: float
    absent_until: float = 0.0

    def available(self) -> bool:
        """Whether to call it: always, unless remembered absent."""
        return time.monotonic() >= self.absent_until

    def note_fault(self, err: xmlrpc.client.Fault) -> bool:
        """True, and remember the absence, when ``err`` says the server
        has no such method; False for any other fault."""
        if not _is_missing_method_fault(err, self.name):
            return False
        self.absent_until = time.monotonic() + self.ttl
        return True


def _unwrap_tracrpc_fault(
    err: xmlrpc.client.Fault,
) -> xmlrpc.client.Fault:
    """``err`` with TracXMLRPC's "while executing" wrapper removed.

    The wrapper names the method, and "wiki.putPageIfVersion()" contains
    "version" -- which ``wiki_update``'s fault handler reads as a version
    conflict. Unwrapped, a fault from the plugin path reads the same as the
    same fault from ``wiki.putPage`` would.
    """
    match = _TRACRPC_FAULT_WRAPPER.match(err.faultString)
    if match is None:
        return err
    return xmlrpc.client.Fault(err.faultCode, match.group("message"))


@dataclass
class _CachedWikiPage:
    """A wiki page as the search cache holds it: the version and
    ``lastModified`` it was read at (as ``getPageInfo`` returned them),
    and its sections tokenized once (``Section.body`` is the text)."""

    version: int | None
    modified: str | None
    sections: list[Section]


def _wiki_version(value: Any) -> int | None:
    """A page version as an int, or ``None`` when it is not one."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _wiki_time_key(value: Any) -> str | None:
    """A ``lastModified`` as the ``YYYYMMDDTHH:MM:SS`` string XML-RPC
    carries (these sort as times), or ``None`` for anything else."""
    if isinstance(value, xmlrpc.client.DateTime):
        value = value.value
    return value if isinstance(value, str) and value else None


K = TypeVar("K")
V = TypeVar("V")

# What a ``_batched_multicall`` decoder returns to drop its item: None
# is a value there, as it is on the single-read path.
_SKIP = object()


def _multicall_value(entry: Any) -> tuple[bool, Any]:
    """``(True, value)`` for one ``system.multicall`` result entry that
    is a value (a one-element list), ``(False, None)`` for a fault (a
    ``{'faultCode', 'faultString'}`` dict) or anything else."""
    if isinstance(entry, list) and len(entry) == 1:
        return True, entry[0]
    return False, None


def _wiki_page_record(
    info: Any, text: Any
) -> tuple[int | None, str | None, str] | None:
    """``(version, lastModified, text)`` from a page's ``getPageInfo``
    and ``getPage`` results, or ``None`` if they are not a page."""
    if not isinstance(info, dict):
        return None
    if text is None:  # an empty page comes back as an empty <string/>
        text = ""
    if not isinstance(text, str):
        return None
    return (
        _wiki_version(info.get("version")),
        _wiki_time_key(info.get("lastModified")),
        text,
    )


def _decode_wiki_page(name: str, values: list[Any]) -> Any:
    """``_batched_multicall`` decoder for a page's ``getPageInfo`` and
    ``getPage`` values: its record, or ``_SKIP`` if they are not a
    page."""
    record = _wiki_page_record(*values)
    return _SKIP if record is None else record


class WikiPageStoredReadbackFailed(Exception):
    """``wiki.putPage`` returned ``True`` -- the page WAS stored -- but the
    read-back that fetches its new version failed (Trac /trac_mcp_server
    #140).

    Raised by ``TracClient.put_wiki_page`` instead of the read-back's own
    error, so a caller can never mistake "stored, read-back failed" for
    "nothing stored" and write the page again. Deliberately neither an
    ``xmlrpc.client.Fault`` nor a ``ValueError``: a caller's existing
    ``except Fault`` (e.g. a "not found -> create it" fallback) or
    ``except ValueError`` (write rejected) must not match it. A caller
    that does not know about it still sees an error, never a success with
    a made-up version.

    ``cause`` is the read-back's exception (also chained as
    ``__cause__``), or ``None`` when the read-back returned something
    other than a page-info dict (some Trac instances return 0 for a
    missing page). ``url`` is the page's URL.
    """

    def __init__(
        self,
        message: str,
        page_name: str,
        url: str,
        cause: BaseException | None,
    ):
        super().__init__(message)
        self.page_name = page_name
        self.url = url
        self.cause = cause


class TracClient:
    def __init__(self, config: Config):
        self.config = config
        self._thread_local = threading.local()
        self._sessions: weakref.WeakSet[requests.Session] = (
            weakref.WeakSet()
        )
        self._sessions_lock = threading.Lock()
        self.rpc_url = self._get_rpc_url()
        # Plugin methods this client skips for a while after the server
        # said it has none: put_wiki_page's conditional put (#129) and
        # get_ticket_change_at's lookup (#132).
        self._wiki_conditional_put = _OptionalMethod(
            f"wiki.{WIKI_CONDITIONAL_PUT_METHOD}",
            WIKI_CONDITIONAL_PUT_ABSENT_TTL_SECONDS,
        )
        self._ticket_change_at = _OptionalMethod(
            "ticket.getChangeAt", TICKET_CHANGE_AT_ABSENT_TTL_SECONDS
        )
        # Whether the server has ``system.multicall``: a fact about the
        # server, not the wiki, so wiki search (#154) and ``get_tickets``
        # (#158) share this one memory of it.
        self._multicall = _OptionalMethod(
            "system.multicall", MULTICALL_ABSENT_TTL_SECONDS
        )
        # The wiki search's page cache (#154). It is per client, and a
        # client is per caller: the instance pool keeps one per instance
        # and identity, and under OIDC mcp/oidc.py keeps one per bearer
        # token (up to 256). So a cache only ever holds pages its own
        # caller can read, and a new token fills a cache of its own with
        # one full read -- never worse than the old per-query full read.
        # ``run_sync`` runs calls in threads, hence the lock around every
        # refresh and index build.
        self._wiki_cache_lock = threading.Lock()
        self._wiki_cache: dict[str, _CachedWikiPage] = {}
        self._wiki_scope_index: OrderedDict[str, BM25] = OrderedDict()

    @property
    def session(self) -> requests.Session:
        """Backward-compatible accessor for the session (returns current thread's session)."""
        return self._get_session()

    def _get_rpc_url(self) -> str:
        # OIDC per-user auth targets a distinct, operator-supplied endpoint
        # (see Config.rpc_url_override) rather than the usual .../login/rpc
        # derived from trac_url -- its URL structure is whatever the
        # OIDC-protected Apache Location is mounted at, not necessarily the
        # same path as the default Basic/LDAP endpoint.
        if self.config.rpc_url_override:
            return self.config.rpc_url_override
        return f"{self.config.trac_url.rstrip('/')}/login/rpc"

    def _get_session(self) -> requests.Session:
        """Get or create a thread-local requests.Session."""
        if not hasattr(self._thread_local, "session"):
            session = self._create_session()
            with self._sessions_lock:
                self._sessions.add(session)
            self._thread_local.session = session
        return self._thread_local.session

    def close(self) -> None:
        """Drop the pooled connections of every thread's session.

        A session stays usable after close(): the next request opens a new
        connection. So a call still running on this client, in another
        thread, is not broken by it.
        """
        with self._sessions_lock:
            sessions = list(self._sessions)
        for session in sessions:
            session.close()

    def _create_session(self) -> requests.Session:
        # Ticket #118: the session itself decides where a request may go
        # and with what credentials -- the configured password only to
        # this client's own origin, and, under an allowlist (#110),
        # nothing outside it. See core/scope.py. Under OIDC per-user auth
        # the credential is meant for the RPC endpoint, which
        # rpc_url_override may place on another origin than trac_url.
        session = ScopedSession(
            self.rpc_url
            if self.config.bearer_token
            else self.config.trac_url,
            scope_for(self.config),
        )
        if self.config.bearer_token:
            # OIDC per-user auth: forward the caller's own access token
            # verbatim. Trac's Apache layer (mod_auth_openidc) validates it
            # and maps it to a Trac username -- this client does not decode
            # or verify it, matching how session.auth below is likewise a
            # blind credential pass-through.
            session.headers["Authorization"] = (
                f"Bearer {self.config.bearer_token}"
            )
        else:
            session.auth = (self.config.username, self.config.password)
        session.verify = not self.config.insecure

        # Keep pooled connections alive at the TCP level. Without this a
        # long-lived server that goes idle between calls silently accumulates
        # dead sockets whenever a NAT or firewall sits in the path, and each
        # one costs a caller a full read timeout before it is discarded.
        adapter = KeepAliveHTTPAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _rpc_call(
        self, service: str, method: str, *params
    ) -> requests.Response:
        """POST an XML-RPC request and return the raw HTTP response.

        Split out of _rpc_request() so callers that need the transport
        response itself (e.g. get_server_time()'s HTTP Date header) don't
        have to duplicate the request-building/sending logic.
        """
        payload = xmlrpc.client.dumps(
            params, methodname=f"{service}.{method}"
        )

        headers = {"Content-Type": "text/xml"}
        session = self._get_session()
        response = session.post(
            self.rpc_url,
            data=payload,
            headers=headers,
            timeout=(10, self.config.rpc_timeout),
        )
        response.raise_for_status()
        return response

    def _rpc_request(self, service: str, method: str, *params):
        """
        Make an XML-RPC request to the Trac server.
        """
        response = self._rpc_call(service, method, *params)

        # Parse the response
        tree = ElementTree.fromstring(response.content)
        fault = tree.find(".//fault")
        if fault is not None:
            fault_code_element = fault.find(
                './/member[name="faultCode"]/value/int'
            )
            fault_string_element = fault.find(
                './/member[name="faultString"]/value/string'
            )
            fault_code = (
                int(fault_code_element.text)
                if fault_code_element is not None
                and fault_code_element.text is not None
                else 0
            )
            fault_string = (
                fault_string_element.text
                if fault_string_element is not None
                and fault_string_element.text is not None
                else "Unknown error"
            )
            raise xmlrpc.client.Fault(fault_code, fault_string)

        # Extract the value from the response
        value_element = tree.find(".//param/value")
        return self._parse_xmlrpc_value(value_element)

    def _parse_xmlrpc_value(self, element):
        """
        Recursively parse an XML-RPC value element.
        """
        data_type = element[0].tag
        data_value = element[0].text

        match data_type:
            case "array":
                data_element = element.find("./array/data")
                if data_element is not None:
                    return [
                        self._parse_xmlrpc_value(v)
                        for v in data_element.findall("value")
                    ]
                return []
            case "struct":
                result = {}
                for member in element.findall(".//member"):
                    name = member.find("name").text
                    value = self._parse_xmlrpc_value(
                        member.find("value")
                    )
                    result[name] = value
                return result
            case "int" | "i4":
                return int(data_value)
            case "boolean":
                return data_value == "1"
            case "string":
                return data_value
            case "double":
                return float(data_value)
            case "base64":
                # XML-RPC <base64> wraps binary attachment payloads.
                # data_value is the base64-encoded string from ElementTree;
                # b64decode accepts str directly and returns raw bytes.
                # Empty Binary (<base64/>) -> "" -> b"" (graceful zero-length).
                return base64.b64decode(data_value or "")
            case _:
                return data_value

    def search_tickets(self, query: str) -> Any:
        """
        Search for tickets using a query string.
        """
        return self._rpc_request("ticket", "query", query)

    def get_ticket(self, ticket_id: int) -> Any:
        """
        Get ticket details by ticket ID.
        """
        return self._rpc_request("ticket", "get", ticket_id)

    def get_tickets(self, ticket_ids: list[int]) -> dict[int, Any]:
        """Get several tickets, batched through ``system.multicall``.

        Returns ``{id: <ticket.get result>}`` -- the value ``get_ticket``
        returns -- for each id that loaded; an id that failed is absent.

        - Ids are de-duplicated, order kept. An empty list makes no RPC.
        - Ids go in batches of ``TICKET_FETCH_BATCH``, one ``ticket.get``
          each; the batching and fallback rules are those of
          ``_batched_multicall``.
        """
        return self._batched_multicall(
            list(dict.fromkeys(ticket_ids)),
            TICKET_FETCH_BATCH,
            lambda tid: [{"methodName": "ticket.get", "params": [tid]}],
            lambda tid, values: values[0],
            self._fetch_tickets_singly,
        )

    def _fetch_tickets_singly(self, ids: list[int]) -> dict[int, Any]:
        """``get_ticket`` per id, skipping any that fail."""
        out: dict[int, Any] = {}
        for tid in ids:
            try:
                out[tid] = self.get_ticket(tid)
            except Exception:
                continue
        return out

    def _batched_multicall(
        self,
        items: list[K],
        batch_size: int,
        calls_for: Callable[[K], list[dict[str, Any]]],
        decode: Callable[[K, list[Any]], V | object],
        fetch_singly: Callable[[list[K]], dict[K, V]],
    ) -> dict[K, V]:
        """Read ``items`` in ``system.multicall`` batches, falling back
        to ``fetch_singly``; ``{item: value}`` for each that loaded.

        The one batch-then-fallback loop behind ``get_tickets`` and the
        wiki search's page fetch (Trac /trac_mcp_server #172).

        - ``calls_for(item)`` is the multicall entries
          (``{"methodName", "params"}``) that read one item; the count
          may differ per item.
        - ``decode(item, values)`` turns the item's result values (one
          per entry, in order) into its value, or returns ``_SKIP`` to
          drop it; any other return, ``None`` included, is the value.
          It runs only when every entry of the item is a value.
        - ``fetch_singly(items)`` is the one-call-per-item read.

        No items makes no RPC. While the server has ``system.multicall``
        the items go in batches of ``batch_size``:

        - a reply that is not a list of exactly the batch's entry count
          sends that batch to ``fetch_singly``, and is not remembered;
        - an entry that is a fault leaves its item absent, with no
          single retry;
        - a ``Fault`` that says ``system.multicall`` is missing is
          remembered (``self._multicall``, shared by every caller) and
          the rest, that batch included, goes to ``fetch_singly``;
        - any other ``Fault``, or any other exception (a timeout, a
          dropped connection, an unparseable reply), sends just that
          batch to ``fetch_singly`` -- one batch failing must not fail
          the whole read -- and batching goes on.

        ``fetch_singly`` runs outside the ``try``: an error it raises
        propagates, and never triggers a second single read.
        """
        out: dict[K, V] = {}
        done = 0
        if items and self._multicall.available():
            while done < len(items):
                batch = items[done : done + batch_size]
                fallback = False
                try:
                    got = self._multicall_batch(
                        batch, calls_for, decode
                    )
                    if got is None:
                        fallback = True
                    else:
                        out.update(got)
                except xmlrpc.client.Fault as err:
                    if self._multicall.note_fault(err):
                        break  # no multicall: the rest goes singly
                    fallback = True
                except Exception:
                    fallback = True
                if fallback:
                    out.update(fetch_singly(batch))
                done += len(batch)
        out.update(fetch_singly(items[done:]))
        return out

    def _multicall_batch(
        self,
        batch: list[K],
        calls_for: Callable[[K], list[dict[str, Any]]],
        decode: Callable[[K, list[Any]], V | object],
    ) -> dict[K, V] | None:
        """One ``system.multicall`` for ``batch``; see
        ``_batched_multicall``. ``None`` if the reply is not a list of
        one result per call. Raises whatever the RPC raises."""
        per_item = [calls_for(item) for item in batch]
        calls = [call for item_calls in per_item for call in item_calls]
        results = self._rpc_request("system", "multicall", calls)
        if not isinstance(results, list) or len(results) != len(calls):
            return None
        out: dict[K, V] = {}
        offset = 0
        for item, item_calls in zip(batch, per_item, strict=True):
            entries = results[offset : offset + len(item_calls)]
            offset += len(item_calls)
            decoded = [_multicall_value(entry) for entry in entries]
            if not all(ok for ok, _ in decoded):
                continue
            value = decode(item, [v for _, v in decoded])
            if value is not _SKIP:
                out[item] = cast(V, value)
        return out

    def get_ticket_changelog(self, ticket_id: int) -> Any:
        """
        Get ticket changelog by ticket ID.
        """
        return self._rpc_request("ticket", "changeLog", ticket_id)

    def get_ticket_change_at(
        self, ticket_id: int, ts: str
    ) -> list | None:
        """Read the one change set stored at ``ts`` via the plugin's
        ``ticket.getChangeAt`` (Trac /trac_mcp_server #132).

        ``ts`` is the microsecond ``_ts`` a ``ticket.update`` returned,
        as a string (XML-RPC ints are 32-bit). Returns the plugin's
        reply: ``[cnum, author, comment]`` -- ``cnum`` as Trac stores it,
        a string that is dotted for a threaded reply -- or ``[]`` when no
        change, or none with a comment, is stored at that time.

        Returns ``None`` when the server has no such method -- the
        ``tracrpc_comment`` plugin (1.2.0 or later) is not installed or
        not enabled -- and the caller falls back to reading the
        changelog. That absence is remembered for
        ``TICKET_CHANGE_AT_ABSENT_TTL_SECONDS``, during which ``None``
        is returned with no RPC posted.

        Raises:
            xmlrpc.client.Fault: Any other fault (permission denied, no
                such ticket, a malformed ``ts``), unchanged.
        """
        if not self._ticket_change_at.available():
            return None
        try:
            return self._rpc_request(
                "ticket", "getChangeAt", ticket_id, ts
            )
        except xmlrpc.client.Fault as err:
            if self._ticket_change_at.note_fault(err):
                return None
            raise

    def edit_ticket_comment(
        self, ticket_id: int, cnum: int, comment: str
    ) -> Any:
        """
        Replace the body of comment ``cnum`` on a ticket.

        Requires the ``tracrpc_comment`` plugin on the Trac server (see
        ``plugins/`` in this repo); stock XmlRpcPlugin has no comment-edit
        method. The previous text is kept as a revision, readable through
        ``get_ticket_comment_history``.
        """
        return self._rpc_request(
            "ticket", "editComment", ticket_id, cnum, comment
        )

    def delete_ticket_comment(
        self, ticket_id: int, cnum: int, force: bool = False
    ) -> Any:
        """
        Delete comment ``cnum`` on a ticket. Irreversible.

        The server refuses unless the comment stands alone, because Trac
        deletes the whole change-set at that timestamp and reverts any field
        changes recorded with it. ``force`` overrides that; the refusal names
        the fields at risk.

        Requires the ``tracrpc_comment`` plugin on the Trac server.
        """
        return self._rpc_request(
            "ticket", "deleteComment", ticket_id, cnum, force
        )

    def get_ticket_comment_history(
        self, ticket_id: int, cnum: int
    ) -> Any:
        """
        Get the edit revisions of comment ``cnum`` on a ticket.

        Requires the ``tracrpc_comment`` plugin on the Trac server.
        """
        return self._rpc_request(
            "ticket", "getCommentHistory", ticket_id, cnum
        )

    def reply_to_ticket_comment(
        self,
        ticket_id: int,
        cnum: int,
        comment: str,
        quote: bool = False,
    ) -> Any:
        """
        Post a threaded reply to comment ``cnum``. Returns the new number.

        Not the same as ``update_ticket`` with quoted text: Trac records
        threading in the comment row itself, and ``ticket.update`` cannot set
        it, so a comment posted that way is always flat.

        Requires the ``tracrpc_comment`` plugin on the Trac server.
        """
        return self._rpc_request(
            "ticket", "replyToComment", ticket_id, cnum, comment, quote
        )

    def get_timeline_filters(self) -> Any:
        """
        Get the available timeline event kinds as (name, label) pairs.

        These are the names accepted by ``get_timeline_events``'s
        ``filters`` argument. Which filters exist depends on the enabled
        components, so this is worth reading rather than assuming.

        Requires the ``tracrpc_comment`` plugin on the Trac server.
        """
        return self._rpc_request("timeline", "getFilters")

    def get_timeline_events(
        self,
        start_ts: int,
        stop_ts: int,
        filters: list[str],
        max_results: int,
    ) -> Any:
        """
        Get timeline events between start_ts and stop_ts (unix
        timestamps, seconds since epoch), newest first. filters is a
        list of filter names from ``get_timeline_filters``; pass every
        name to get everything. max_results is capped server-side at
        1000.

        Each event is a struct with kind, date, author, title,
        description and url. title/description are the provider's own
        server-side rendering with markup stripped to plain text -- not
        TracWiki or Markdown, and this method does not convert them.

        Requires the ``tracrpc_comment`` plugin on the Trac server.
        """
        return self._rpc_request(
            "timeline",
            "getEvents",
            xmlrpc.client.DateTime(start_ts),
            xmlrpc.client.DateTime(stop_ts),
            filters,
            max_results,
        )

    def validate_connection(self) -> str:
        """
        Validate connection by calling system.getAPIVersion().
        Returns the API version string if successful.
        """
        version = self._rpc_request("system", "getAPIVersion")
        return str(version) if version is not None else ""

    def get_server_time(self) -> datetime:
        """
        Get the Trac server's current wall-clock time.

        Reads the HTTP ``Date`` response header of a lightweight RPC
        round trip, not any Trac resource's ``lastModified`` timestamp.
        A resource's last-modified time only reflects when it was last
        edited -- it drifts stale the moment nothing on it changes, and
        was found to be ~4 months behind actual server time when this
        server used a wiki page's lastModified as a stand-in (ticket
        #33). The ``Date`` header is set by the web server on every
        response and requires no dedicated "get current time" RPC
        method, which Trac's XML-RPC API doesn't provide.

        Returns:
            Timezone-aware datetime (UTC) of the server's response.

        Raises:
            RuntimeError: If the response has no Date header.
            requests.exceptions.RequestException: On connection failure.
        """
        response = self._rpc_call("system", "getAPIVersion")
        date_header = response.headers.get("Date")
        if not date_header:
            raise RuntimeError(
                "Trac server response did not include an HTTP Date header"
            )
        return parsedate_to_datetime(date_header)

    def list_methods(self) -> Any:
        """
        List available RPC methods.
        """
        return self._rpc_request("system", "listMethods")

    def create_ticket(
        self,
        summary: str,
        description: str,
        ticket_type: str | None = None,
        attributes: dict[str, Any] | None = None,
        notify: bool = False,
    ) -> int:
        """
        Create a new ticket in Trac.

        Args:
            summary: Ticket title (required)
            description: Ticket body with WikiFormatting (required)
            ticket_type: Ticket type string. If None, uses default from ticket_types.yaml. Any type configured in Trac is valid.
            attributes: Optional fields (priority, milestone, component, owner, cc, keywords)
            notify: Send email notifications

        Returns:
            Ticket ID (int)

        Raises:
            ValueError: If summary or description is empty
            xmlrpc.client.Fault: If server validation fails or permissions denied
            TicketCreateTimeout: If the create timed out and we could
                establish whether the ticket landed. ``ticket_id`` says
                which: set if it was created anyway, None if it was not.
            requests.exceptions.Timeout: If the create timed out and that
                check could not be completed, so nothing is known.

        On a read timeout (config.rpc_timeout, default 60s) the create may
        still have succeeded server-side. This checks whether it did and
        reports the answer; it deliberately does NOT retry, because
        ticket.create is not idempotent -- see TicketCreateTimeout.
        """
        # Validate required fields
        if not summary or not summary.strip():
            raise ValueError("Summary is required and cannot be empty")
        if not description or not description.strip():
            raise ValueError(
                "Description is required and cannot be empty"
            )

        # Use hardcoded default ticket type (standalone server, no YAML config)
        if ticket_type is None:
            ticket_type = "defect"

        attrs: dict[str, Any] = attributes.copy() if attributes else {}
        attrs["type"] = ticket_type

        sent_at = time.time()
        try:
            result = self._rpc_request(
                "ticket", "create", summary, description, attrs, notify
            )
            return int(result)
        except requests.exceptions.Timeout as timeout_err:
            # The request may have reached Trac and created the ticket
            # despite the client-side read timeout. Find out which, and
            # tell the caller -- do not act on it here.
            reporter = attrs.get("reporter", self.config.username)
            try:
                existing_id = self._find_recently_created_ticket(
                    summary, reporter, sent_at
                )
            except Exception:
                # Nothing is known about the outcome. Surface the original
                # timeout rather than implying either answer.
                raise timeout_err from None

            if existing_id is not None:
                raise TicketCreateTimeout(
                    f"ticket.create timed out after "
                    f"{self.config.rpc_timeout}s, but the ticket was "
                    f"created anyway as #{existing_id}. Do not retry; "
                    f"read #{existing_id} to confirm its contents.",
                    ticket_id=existing_id,
                ) from timeout_err

            raise TicketCreateTimeout(
                f"ticket.create timed out after {self.config.rpc_timeout}s "
                "and no ticket matching this summary was found afterwards, "
                "so it most likely was not created. Retrying is probably "
                "safe. If several tickets share this summary, or many were "
                "created concurrently, confirm with a search first.",
                ticket_id=None,
            ) from timeout_err

    # How far back a post-timeout match is considered plausible. Generous
    # enough to absorb the timeout itself plus client/server clock skew.
    _CREATE_MATCH_WINDOW_SECONDS = 600

    def _find_recently_created_ticket(
        self, summary: str, reporter: str, sent_at: float
    ) -> int | None:
        """
        Look for a ticket matching ``summary`` among ``reporter``'s recent
        tickets, to establish whether a timed-out create actually landed.

        Only tickets created at or after ``sent_at`` (minus a skew
        allowance) count as a match, so an unrelated older ticket that
        happens to share the summary is not mistaken for this one.

        Returns the matching ticket id, or None if the search completed
        and found no plausible match. Exceptions from the search itself
        propagate: "could not check" and "checked, nothing there" lead to
        different messages and must not be conflated.
        """
        ticket_ids = self._rpc_request(
            "ticket",
            "query",
            f"reporter={reporter}&order=id&desc=1&max=20",
        )

        cutoff = sent_at - self._CREATE_MATCH_WINDOW_SECONDS
        for tid in ticket_ids:
            try:
                ticket_data = self._rpc_request("ticket", "get", tid)
            except Exception:
                continue
            attrs = ticket_data[3]
            if attrs.get("summary") != summary:
                continue
            created = self._parse_trac_time(ticket_data[1])
            # Unparseable timestamp: keep the match rather than claim the
            # ticket was not created, which would invite a duplicate.
            if created is None or created >= cutoff:
                return int(tid)
        return None

    @staticmethod
    def parse_comment_number(oldvalue: Any) -> CommentNumber | None:
        """Parse the ``oldvalue`` of a ``ticket.changeLog`` comment row.

        In a changeLog row ``[time, author, field, oldvalue, newvalue,
        permanent]`` with ``field == "comment"``, ``oldvalue`` holds the
        comment number. A plain comment's is ``"5"``. A reply made in the
        web UI is threaded: ``"2.5"`` is comment 5, a reply to comment 2.
        Trac itself takes the LAST dotted part as the comment's number
        (``cnum``); the ``trac-change-5-...`` HTML ids, ``#comment:5``
        anchors and ``comment:5`` links all use it. So ``"5"`` gives
        ``CommentNumber(5, None)`` and ``"2.5"`` gives
        ``CommentNumber(5, 2)``.

        Accepts a str (surrounding whitespace ignored) or an int. Returns
        None for anything else: None, the empty string, non-numeric text,
        more than two dotted parts or an empty part (``"2."``, ``".5"``),
        a non-positive number, a float, a bool. Each part must be ASCII
        digits and is then parsed with ``int()``. Neither test alone is
        enough: ``"²".isdigit()`` is True but ``int("²")``
        raises, and ``int()`` alone accepts ``"1_0"``, ``"+5"`` and
        ``"٥"``.
        """
        if isinstance(oldvalue, bool):
            return None
        if isinstance(oldvalue, int):
            return (
                CommentNumber(oldvalue, None) if oldvalue > 0 else None
            )
        if not isinstance(oldvalue, str):
            return None
        parts = oldvalue.strip().split(".")
        if len(parts) > 2:
            return None
        # ASCII digits only: int() alone also takes "1_0", "+5" and
        # non-ASCII digits such as "\u0665". isdigit() is only the
        # pre-filter (it passes "\u00b2"); int() still does the parsing.
        if not all(part.isascii() and part.isdigit() for part in parts):
            return None
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            return None
        if any(n <= 0 for n in numbers):
            return None
        reply_to = numbers[0] if len(numbers) == 2 else None
        return CommentNumber(numbers[-1], reply_to)

    @staticmethod
    def _parse_trac_time(value: Any) -> float | None:
        """Convert a Trac timestamp to a UTC epoch, or None if unrecognised.

        Trac's XML-RPC layer returns compact iso8601 strings such as
        "20260812T07:46:13" (UTC, no separators, no offset), and older
        setups may return an xmlrpc DateTime.
        """
        if isinstance(value, xmlrpc.client.DateTime):
            value = str(value)
        if not isinstance(value, str):
            return None
        for fmt in ("%Y%m%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return calendar.timegm(
                    datetime.strptime(value, fmt).timetuple()
                )
            except ValueError:
                continue
        return None

    def update_ticket(
        self,
        ticket_id: int,
        comment: str = "",
        attributes: dict[str, Any] | None = None,
        notify: bool = False,
        base_ts: str | int | None = None,
    ) -> list[Any]:
        """
        Update an existing ticket.

        Args:
            ticket_id: Ticket number to update
            comment: Comment to add (supports WikiFormatting, max 10000 chars)
            attributes: Fields to update (status, priority, owner, resolution, etc.)
            notify: Send email notifications
            base_ts: The ticket's ``_ts`` token as last seen by the caller
                (from ``get_ticket``'s attributes, or a prior update's
                result), forwarded to Trac verbatim as the optimistic-lock
                token. Sent as a string regardless of the type passed in --
                Trac's ``_ts`` is a microsecond-precision value that
                overflows XML-RPC's 32-bit ``<int>`` type, so Trac itself
                represents it as a string on the wire (confirmed against a
                live ticket.get response). This is the only way this method
                actually detects a concurrent change: Trac rejects the
                write if the ticket was touched since ``base_ts`` was read
                (see ``TicketUpdateConflict`` below). When omitted, this
                method mints its own token from a fresh ``ticket.get``
                immediately before writing, which *cannot* fail the check
                -- it matches by construction and only guards against a
                change landing in the instant between that read and the
                write, not anything since the caller's own read (ticket
                #50). Kept for backward compatibility; prefer passing
                ``base_ts``.

        Returns:
            Updated ticket data [id, created, modified, attributes]

        Raises:
            ValueError: If comment exceeds 10000 characters
            TicketUpdateConflict: If base_ts was supplied and is stale --
                the ticket changed since that token was read
            xmlrpc.client.Fault: If ticket not found, validation fails, or
                (when base_ts is omitted) another other server-side error
        """
        # Validate comment length
        if comment and len(comment) > MAX_COMMENT_CHARS:
            raise ValueError(
                f"Comment exceeds maximum length of {MAX_COMMENT_CHARS} "
                "characters"
            )

        update_attrs: dict[str, Any] = (
            attributes.copy() if attributes else {}
        )

        if base_ts is not None:
            # Forward the caller's own token, coerced to str -- see the
            # base_ts docstring above for why this is the only path that
            # actually locks anything, and why it must be a string.
            update_attrs["_ts"] = str(base_ts)
        else:
            # Legacy fallback: mint a token immediately before writing.
            # This can never trip the lock -- see the docstring.
            ticket_data = self._rpc_request("ticket", "get", ticket_id)
            if (
                not isinstance(ticket_data, list)
                or len(ticket_data) < 4
            ):
                raise ValueError(
                    "Invalid ticket data format from server"
                )
            current_attrs = ticket_data[
                3
            ]  # [id, created, modified, {attributes}]
            if not isinstance(current_attrs, dict):
                raise ValueError(
                    "Invalid ticket attributes format from server"
                )
            update_attrs["_ts"] = current_attrs["_ts"]

        # Default action to "leave" for simple field updates (no workflow transition)
        if "action" not in update_attrs:
            update_attrs["action"] = "leave"

        try:
            result = self._rpc_request(
                "ticket",
                "update",
                ticket_id,
                comment,
                update_attrs,
                notify,
            )
        except xmlrpc.client.Fault as err:
            if base_ts is not None and self._is_mid_air_collision(err):
                raise self._build_update_conflict(
                    ticket_id, base_ts, err
                ) from err
            raise
        return result

    @staticmethod
    def _is_mid_air_collision(err: xmlrpc.client.Fault) -> bool:
        """Whether a ticket.update Fault is Trac's stale-``_ts`` rejection.

        Trac raises faultCode 1 for several unrelated conditions (e.g.
        "ticket does not exist"), so the message is what actually
        distinguishes a collision -- Trac's own wording is "has been
        modified since you started editing".
        """
        return (
            err.faultCode == 1
            and "modified since" in err.faultString.lower()
        )

    def _build_update_conflict(
        self,
        ticket_id: int,
        base_ts: Any,
        err: xmlrpc.client.Fault,
    ) -> TicketUpdateConflict:
        """Turn a raw mid-air-collision Fault into a TicketUpdateConflict
        naming what changed, by diffing the changelog against base_ts.

        Best-effort: if the changelog itself can't be fetched, the
        conflict is still raised, just without the detail.
        """
        changes: list[dict[str, Any]] = []
        try:
            # Changelog timestamps only have second resolution; base_ts has
            # microsecond resolution. Floor base_ts to whole seconds before
            # comparing, so a change that landed in the *same* second as
            # the caller's read (its microsecond fraction truncated away)
            # still compares >= rather than being spuriously excluded --
            # confirmed against a live ticket where this actually happened
            # (ticket #50 verification). A false positive here just
            # over-informs; a false negative hides the exact thing this
            # exists to surface.
            base_epoch_floor = int(base_ts) // 1_000_000
            changelog = self.get_ticket_changelog(ticket_id)
            for entry in changelog or []:
                timestamp, author, field, oldvalue, newvalue = entry[:5]
                entry_epoch = self._parse_trac_time(timestamp)
                if (
                    entry_epoch is not None
                    and entry_epoch >= base_epoch_floor
                ):
                    changes.append(
                        {
                            "timestamp": timestamp,
                            "author": author,
                            "field": field,
                            "oldvalue": oldvalue,
                            "newvalue": newvalue,
                        }
                    )
        except Exception:
            changes = []

        if changes:
            summary = "; ".join(
                f"{c['field']} by {c['author']}" for c in changes
            )
            message = (
                f"Ticket #{ticket_id} changed since base_ts={base_ts}: "
                f"{summary}"
            )
        else:
            message = (
                f"Ticket #{ticket_id} changed since base_ts={base_ts} "
                f"({err.faultString})"
            )
        return TicketUpdateConflict(
            message,
            ticket_id=ticket_id,
            base_ts=base_ts,
            changes=changes,
        )

    def get_ticket_actions(self, ticket_id: int) -> list[Any]:
        """
        Get available workflow actions for a ticket's current state.

        Args:
            ticket_id: Ticket number to get actions for

        Returns:
            List of action tuples [action_name, label, hints, input_fields]
            where hints contains allowed status transitions

        Raises:
            xmlrpc.client.Fault: If ticket not found or method not available
        """
        result = self._rpc_request("ticket", "getActions", ticket_id)
        return result

    def list_wiki_pages(self) -> list[str]:
        """
        List all wiki page names in Trac.

        Returns:
            List of wiki page names (e.g., ["WikiStart", "UserGuide", "API/Reference"])

        Raises:
            xmlrpc.client.Fault: If server returns error or permissions denied
        """
        result = self._rpc_request("wiki", "getAllPages")
        return result

    def get_wiki_page(
        self, page_name: str, version: int | None = None
    ) -> str:
        """
        Get wiki page content in raw TracWiki format.

        Args:
            page_name: Name of wiki page (e.g., "WikiStart")
            version: Optional version number (default: latest)

        Returns:
            Raw TracWiki markup as string

        Raises:
            xmlrpc.client.Fault: If page not found or permissions denied
        """
        if version is None:
            result = self._rpc_request("wiki", "getPage", page_name)
        else:
            result = self._rpc_request(
                "wiki", "getPageVersion", page_name, version
            )
        return result

    def get_wiki_page_info(
        self, page_name: str, version: int | None = None
    ) -> dict[str, Any]:
        """
        Get wiki page metadata.

        Args:
            page_name: Name of wiki page
            version: Optional version number (default: latest)

        Returns:
            Dict with keys: name, author, version, lastModified

        Raises:
            xmlrpc.client.Fault: If page not found or permissions denied
        """
        if version is None:
            result = self._rpc_request("wiki", "getPageInfo", page_name)
        else:
            result = self._rpc_request(
                "wiki", "getPageInfoVersion", page_name, version
            )
        return result

    def get_wiki_page_with_metadata(
        self, page_name: str
    ) -> dict[str, Any]:
        """
        Get wiki page content with full metadata.
        This method provides helpful error messages with suggestions for missing pages.

        Args:
            page_name: Name of wiki page

        Returns:
            Dict with keys: name, content, version, author, lastModified

        Raises:
            ValueError: If page not found, includes suggestions for similar pages
            xmlrpc.client.Fault: For other server errors
        """
        try:
            content = self.get_wiki_page(page_name)
            info = self.get_wiki_page_info(page_name)

            return {
                "name": page_name,
                "content": content,
                "version": info.get("version"),
                "author": info.get("author"),
                "lastModified": info.get("lastModified"),
            }
        except xmlrpc.client.Fault as err:
            if err.faultCode == 1:  # Page not found
                # Find similar pages by substring matching
                all_pages = self.list_wiki_pages()
                query_lower = page_name.lower()
                suggestions = [
                    p for p in all_pages if query_lower in p.lower()
                ][:5]

                suggestion_text = ""
                if suggestions:
                    suggestion_text = (
                        f" Similar pages: {', '.join(suggestions)}"
                    )

                raise ValueError(
                    f"Page '{page_name}' not found.{suggestion_text}"
                ) from None
            else:
                # Re-raise other faults
                raise

    def search_wiki_pages_by_title(
        self, query: str, max_results: int = 10
    ) -> list[dict[str, Any]]:
        """
        Search wiki pages by title using substring matching.

        Args:
            query: Search string (case-insensitive substring match)
            max_results: Maximum number of results to return (default: 10)

        Returns:
            List of dicts with keys: name, snippet (matched portion of title)

        Raises:
            xmlrpc.client.Fault: If server returns error
        """
        all_pages = self.list_wiki_pages()
        query_lower = query.lower()
        matches = []

        for page_name in all_pages:
            if query_lower in page_name.lower():
                # Find match position for snippet
                match_pos = page_name.lower().index(query_lower)
                snippet_start = max(0, match_pos - 20)
                snippet_end = min(
                    len(page_name), match_pos + len(query) + 20
                )
                snippet = page_name[snippet_start:snippet_end]

                matches.append({"name": page_name, "snippet": snippet})

                if len(matches) >= max_results:
                    break

        return matches

    def search_wiki_pages(
        self, query: str, prefix: str | None = None
    ) -> list[dict[str, Any]]:
        """Ranked keyword search (BM25) over wiki page names and text
        (Trac /trac_mcp_server #154).

        Every page whose name starts with ``prefix`` (all pages when it is
        empty or ``None``) and that has a positive score is returned,
        best first, ties broken by page name. ``prefix`` filters before
        ranking: idf and the average section length are those of the
        pages in scope. There is no result cap.

        Page text is cached on this client. The first search reads every
        page; a later one makes one call (``wiki.getRecentChanges`` from
        the epoch, which lists every readable page with its version and
        modification time) and re-reads only the pages that are new or
        whose (version, modification time) differs from the cached pair.

        Args:
            query: Words to look for; compound and CamelCase names are
                also split into their parts. Not a phrase match.
            prefix: Page-name prefix to restrict the search to.

        Returns:
            List of dicts with keys: name, score, snippet. The snippet is
            a slice of the stored TracWiki, byte-for-byte, with ``...``
            on a cut end.

        Raises:
            xmlrpc.client.Fault: If listing the pages fails. A page that
                cannot be read is skipped.
        """
        scope = prefix or ""
        with self._wiki_cache_lock:
            self._refresh_wiki_cache()
            index = self._wiki_scope_index.get(scope)
            if index is not None:
                self._wiki_scope_index.move_to_end(scope)
            else:
                index = BM25(
                    [
                        section
                        for name, page in self._wiki_cache.items()
                        if name.startswith(scope)
                        for section in page.sections
                    ]
                )
                self._wiki_scope_index[scope] = index
                while (
                    len(self._wiki_scope_index) > WIKI_SCOPE_INDEX_MAX
                ):
                    self._wiki_scope_index.popitem(last=False)
        return search_pages(index, query)

    def _refresh_wiki_cache(self) -> None:
        """Bring the page cache up to date (caller holds the lock).

        One ``wiki.getRecentChanges`` from the epoch is the authority: it
        lists every readable page with its latest version and
        ``lastModified`` (``get_recent_wiki_changes`` falls back to
        ``getAllPages`` + ``getPageInfo`` on a server without it). A
        cached page that is not listed is dropped. A listed page is read
        again when it is not cached or when its (version, lastModified)
        pair differs from the cached one -- the pair as ``getPageInfo``
        returned it when the page was read, so a page edited between a
        listing and its read differs from the next listing and is read
        again. Nothing is inferred from a time watermark: a deleted and
        recreated page, a deleted latest version and a page without a
        version are all seen, and so is an edit made while the cache was
        being filled. The per-scope indexes are discarded only if
        something changed.
        """
        listing = self.get_recent_wiki_changes(0)
        current: dict[str, tuple[int | None, str | None]] = {}
        for entry in listing if isinstance(listing, list) else []:
            name = (
                entry.get("name") if isinstance(entry, dict) else None
            )
            if isinstance(name, str):
                current[name] = (
                    _wiki_version(entry.get("version")),
                    _wiki_time_key(entry.get("lastModified")),
                )
        changed = False
        for gone in [n for n in self._wiki_cache if n not in current]:
            del self._wiki_cache[gone]
            changed = True

        # Not cached: new pages, and any that could not be read last time.
        stale = []
        for name, key in current.items():
            held = self._wiki_cache.get(name)
            if held is None or (held.version, held.modified) != key:
                stale.append(name)

        fetched = self._fetch_wiki_pages(stale)
        for name in stale:
            record = fetched.get(name)
            if record is None:
                # Unreadable: skipped. Dropping a cached copy is right
                # too -- it is either stale or no longer readable.
                changed |= self._wiki_cache.pop(name, None) is not None
                continue
            version, modified, text = record
            self._wiki_cache[name] = _CachedWikiPage(
                version, modified, split_sections(name, text)
            )
            changed = True
        if changed:
            self._wiki_scope_index.clear()

    def _fetch_wiki_pages(
        self, names: list[str]
    ) -> dict[str, tuple[int | None, str | None, str]]:
        """Read the version, modification time and text of ``names``.

        Pages go in batches of ``WIKI_FETCH_BATCH_PAGES``; the batching
        and fallback rules are those of ``_batched_multicall``. A page
        that cannot be read is left out of the result.
        """
        # Each page is a getPageInfo then a getPage (in that order, so a
        # page edited in between is cached under the older version and
        # re-read next time).
        return self._batched_multicall(
            names,
            WIKI_FETCH_BATCH_PAGES,
            lambda name: [
                {"methodName": "wiki.getPageInfo", "params": [name]},
                {"methodName": "wiki.getPage", "params": [name]},
            ],
            _decode_wiki_page,
            self._fetch_wiki_singly,
        )

    def _fetch_wiki_singly(
        self, names: list[str]
    ) -> dict[str, tuple[int | None, str | None, str]]:
        """Read ``names`` one call per page (``getPageInfo`` then
        ``getPage``), skipping any that cannot be read."""
        out: dict[str, tuple[int | None, str | None, str]] = {}
        for name in names:
            try:
                record = _wiki_page_record(
                    self.get_wiki_page_info(name),
                    self.get_wiki_page(name),
                )
            except Exception:
                # Skip pages that can't be read (permission denied, etc.)
                continue
            if record is not None:
                out[name] = record
        return out

    def put_wiki_page(
        self,
        page_name: str,
        content: str,
        comment: str,
        version: int | None = None,
    ) -> dict[str, Any]:
        """
        Create or update a wiki page with optimistic locking.

        Args:
            page_name: Name of the wiki page to create/update
            content: Page content in TracWiki format
            comment: Comment describing the change
            version: Optional version the caller read (prevents concurrent
                edits). Trac's stock ``wiki.putPage`` ignores its
                ``version`` attribute, so the check is made one of two
                ways. Where the server has the tracrpc_comment plugin's
                ``wiki.putPageIfVersion`` (Trac /trac_mcp_server #129),
                the write goes through it: the server compares and saves
                in one database transaction, so no other write can land in
                between. Where it has not (a method-not-found fault), the
                check is made client-side, immediately before
                ``wiki.putPage`` (ticket #120), which leaves a narrow race
                between the two; a write that lost it is flagged in the
                result (``possible_overwrite``, below). "No plugin" is
                remembered for ``WIKI_CONDITIONAL_PUT_ABSENT_TTL_SECONDS``.
                ``None`` (create, and unversioned callers) skips both and
                posts ``wiki.putPage`` with no extra RPC. Versions start
                at 1, so a ``version`` below 1 (0 or negative) never takes
                the plugin path: the client-side check refuses it with
                ``WikiPageConflict``, whether or not the plugin is
                installed, and the page is not created.

        Returns:
            Dict with keys: name, version, author, lastModified, url,
            possible_overwrite. ``possible_overwrite`` is ``True`` when
            ``version`` was given and the read-back version is not
            ``version + 1``: another write landed in the race window
            above (and was replaced by this one) or just after it. The
            dict then also carries ``intervening_versions``, the versions
            ``version + 1 .. new - 1`` this call did not write (empty if
            the read-back version is not above ``version + 1``). The page
            is stored either way; a create never sets the flag (Trac
            /trac_mcp_server #130). Through the plugin method only the
            "just after" case is possible, but the check runs on both
            paths.

        Raises:
            WikiPageConflict: If ``version`` is not the page's current
                version (or the page does not exist): the client's
                precheck found it, and putPage was not called, or the
                plugin method refused, and stored nothing. A
                ``ValueError`` subclass.
            WikiPageNotModified: If the content is identical to the stored
                page (Trac stored nothing; ticket #174). A ``ValueError``
                subclass.
            ValueError: If page_name or content validation fails
            xmlrpc.client.Fault: If server returns error or permissions
                denied (a putPage fault is a ``WikiPageConflict`` instead
                when the page's version moved past ``version``)
            WikiPageStoredReadbackFailed: If putPage returned ``True`` (the
                page IS stored) but reading back its new version failed.
                Every other exception above means nothing was stored.
        """
        # Validate page name
        is_valid, error_msg = validate_page_name(page_name)
        if not is_valid:
            raise ValueError(f"Invalid page name: {error_msg}")

        # Validate content
        is_valid, error_msg = validate_content(content)
        if not is_valid:
            raise ValueError(f"Invalid content: {error_msg}")

        result = None
        answered_by_plugin = False
        if (
            version is not None
            and self._wiki_conditional_put.available()
        ):
            # The server-side check (#129). None back means the server
            # has no such method: fall through to the precheck below.
            result = self._put_wiki_page_if_version(
                page_name, content, comment, version
            )
            answered_by_plugin = result is not None

        if not answered_by_plugin:
            result = self._put_wiki_page_checked_here(
                page_name, content, comment, version
            )
        if result is not True:
            raise ValueError(f"Failed to update page '{page_name}'")

        return self._read_back_stored_page(page_name, version)

    def _put_wiki_page_if_version(
        self,
        page_name: str,
        content: str,
        comment: str,
        version: int,
    ) -> Any:
        """Post ``wiki.putPageIfVersion`` (Trac /trac_mcp_server #129).

        Returns its result (``True`` once stored), or ``None`` when the
        server has no such method -- the plugin is not installed or not
        enabled -- in which case nothing was stored and the caller falls
        back to the client-side precheck. That absence is remembered for
        ``WIKI_CONDITIONAL_PUT_ABSENT_TTL_SECONDS``. Also ``None``, with
        no RPC posted, for a ``version`` that is not a whole number or is
        below 1: no page has such a version.

        Raises:
            WikiPageConflict: The plugin's conflict fault: ``version`` is
                not the page's current version, checked in the same
                transaction as the save. Nothing was stored.
            WikiPageNotModified: "Page not modified": the content is
                identical to the stored page, and nothing was stored.
                A ``ValueError`` subclass.
            xmlrpc.client.Fault: Any other fault, with TracXMLRPC's
                "while executing" wrapper removed (see
                ``_unwrap_tracrpc_fault``). Nothing was stored.
        """
        # A JSON-schema "integer" admits 3.0; the plugin takes an int.
        expected = int(version)
        if expected != version or expected < 1:
            # Not a whole number, or below 1 (versions start at 1): no
            # page is at that version. The plugin would read 0 as "create
            # only if absent" and fault on a negative with a message
            # `wiki_update` mislabels as a modified page, so let the
            # precheck refuse it with the usual conflict.
            return None
        try:
            return self._rpc_request(
                "wiki",
                WIKI_CONDITIONAL_PUT_METHOD,
                page_name,
                content,
                {"comment": comment},
                expected,
            )
        except xmlrpc.client.Fault as err:
            if self._wiki_conditional_put.note_fault(err):
                return None
            conflict = (
                _WIKI_CONFLICT_MARKER.match(err.faultString)
                if err.faultCode == _WIKI_CONFLICT_FAULT_CODE
                else None
            )
            if conflict is not None:
                found = conflict.group(1)
                current = None if found == "none" else int(found)
                if current is None:
                    detail = "the page does not exist"
                else:
                    detail = f"its current version is {current}"
                raise WikiPageConflict(
                    f"Version conflict on '{page_name}': you tried to "
                    f"update version {version} but {detail}",
                    page_name,
                    version,
                    current,
                ) from None
            if _WIKI_NOT_MODIFIED_MARKER in err.faultString.lower():
                raise self._wiki_not_modified(
                    page_name, version
                ) from None
            raise _unwrap_tracrpc_fault(err) from None

    def _wiki_not_modified(
        self, page_name: str, version: int | None
    ) -> WikiPageNotModified:
        """The no-op exception for ``page_name``, with the page's current
        version re-read (ticket #174). Best effort: any failure of the
        read, or a reply that is not a non-empty dict, leaves
        ``current_version`` None, and never replaces the no-op."""
        current: int | None = None
        try:
            info = self.get_wiki_page_info(page_name)
            if isinstance(info, dict) and info:
                current = _wiki_version(info.get("version"))
        except Exception:
            current = None
        return WikiPageNotModified(
            "Page not modified (content identical)",
            page_name,
            version,
            current,
        )

    def _put_wiki_page_checked_here(
        self,
        page_name: str,
        content: str,
        comment: str,
        version: int | None,
    ) -> Any:
        """The ticket #120 path: compare ``version`` with a fresh
        ``wiki.getPageInfo`` (when given), then post ``wiki.putPage``.

        Used for creates and unversioned writes, and for versioned ones
        where the server lacks ``wiki.putPageIfVersion``. Returns
        putPage's result.

        A ``putPage`` fault stored nothing. With ``version`` given, the
        page's version is re-read (best effort) and, if it differs from
        ``version``, the fault is raised as ``WikiPageConflict`` with the
        current version: the page moved between the precheck and the save
        (a server-side save collision). Otherwise -- the version is
        unchanged, or the re-read failed -- the original fault is
        re-raised unchanged. The fault text is never consulted for that
        (Trac /trac_mcp_server #185).
        """
        attrs: dict[str, Any] = {"comment": comment}
        if version is not None:
            attrs["version"] = version

            # Trac ignores the version attr, so compare it here.
            try:
                info = self.get_wiki_page_info(page_name)
            except xmlrpc.client.Fault as err:
                # A not-found Fault means the page is missing, same as
                # the 0 return below; any other Fault propagates.
                fault_str = err.faultString.lower()
                if (
                    "not found" not in fault_str
                    and "does not exist" not in fault_str
                ):
                    raise
                info = None
            # Some Trac instances return 0 (int) instead of raising a
            # Fault for a non-existent page.
            current = (
                info.get("version")
                if isinstance(info, dict) and info
                else None
            )
            if current != version:
                if current is None:
                    detail = "the page does not exist"
                else:
                    detail = f"its current version is {current}"
                raise WikiPageConflict(
                    f"Version conflict on '{page_name}': you tried to "
                    f"update version {version} but {detail}",
                    page_name,
                    version,
                    current,
                )

        # The write. Only a fault from putPage itself is translated here:
        # every exception raised from this block means nothing was stored.
        try:
            result = self._rpc_request(
                "wiki", "putPage", page_name, content, attrs
            )
        except xmlrpc.client.Fault as err:
            # Handle specific fault conditions
            fault_str = err.faultString.lower()

            if _WIKI_NOT_MODIFIED_MARKER in fault_str:
                raise self._wiki_not_modified(
                    page_name, version
                ) from None
            if version is None:
                # A create / unversioned write: nothing to compare.
                raise
            # A fault is a conflict only if the page moved, which the
            # fault text cannot tell: a save collision's wording is
            # backend-specific (sqlite's "UNIQUE constraint failed:
            # wiki.name, wiki.version" is one of several), and the old
            # `"version" in fault_str` test also caught faults that
            # merely name a page like VersionNotes (Trac
            # /trac_mcp_server #185). So decide by state.
            now: int | None = None
            try:
                info = self.get_wiki_page_info(page_name)
                if isinstance(info, dict) and info:
                    now = _wiki_version(info.get("version"))
            except Exception:
                now = None
            if now is not None and now != version:
                raise WikiPageConflict(
                    f"Version conflict on '{page_name}': you tried to "
                    f"update version {version} but its current "
                    f"version is {now}",
                    page_name,
                    version,
                    now,
                ) from None
            raise
        return result

    def _read_back_stored_page(
        self, page_name: str, version: int | None
    ) -> dict[str, Any]:
        """Read back a page ``put_wiki_page`` has just stored, by either
        path, and build its result dict (tickets #140 and #130)."""
        # The page is stored from here on. The read-back is a separate
        # step (Trac /trac_mcp_server #140): any failure in it -- a fault
        # (even "not found", if the page was deleted in between), a
        # transport error, or a non-dict reply -- is raised as
        # WikiPageStoredReadbackFailed, never as the plain error a caller
        # would read as "nothing stored" and retry.
        page_url = (
            f"{self.config.trac_url.rstrip('/')}/wiki/{page_name}"
        )
        try:
            info = self.get_wiki_page_info(page_name)
        except Exception as err:
            raise WikiPageStoredReadbackFailed(
                f"Page '{page_name}' was stored, but reading back its "
                f"new version failed: {_describe_error(err)}",
                page_name,
                page_url,
                err,
            ) from err
        if not isinstance(info, dict) or not info:
            raise WikiPageStoredReadbackFailed(
                f"Page '{page_name}' was stored, but reading back its "
                f"new version returned no page info ({info!r})",
                page_name,
                page_url,
                None,
            )

        new_version = info.get("version")
        stored: dict[str, Any] = {
            "name": page_name,
            "version": new_version,
            "author": info.get("author"),
            "lastModified": info.get("lastModified"),
            "url": page_url,
            "possible_overwrite": False,
        }
        # Detect, don't prevent (Trac /trac_mcp_server #130; preventing
        # is #129). An update of version N stores N+1 when nothing else
        # wrote the page. Any other read-back version means another write
        # landed between the precheck above and putPage -- so putPage
        # replaced it -- or right after putPage, before the read-back.
        # Either way the caller is told; the write itself succeeded.
        # A create (version=None) has no expectation to compare with.
        if (
            version is not None
            and isinstance(new_version, int)
            and not isinstance(new_version, bool)
            and new_version != version + 1
        ):
            stored["possible_overwrite"] = True
            # int(): a JSON-schema "integer" admits 3.0, which passes the
            # precheck (3 == 3.0) but would make range() raise here --
            # after the page was stored.
            stored["intervening_versions"] = list(
                range(int(version) + 1, new_version)
            )
        return stored

    def get_wiki_page_html(
        self, page_name: str, version: int | None = None
    ) -> str:
        """
        Get rendered HTML for a wiki page.

        Args:
            page_name: Name of wiki page
            version: Optional version number (default: latest)

        Returns:
            Rendered HTML as string

        Raises:
            xmlrpc.client.Fault: If page not found or permissions denied
        """
        if version is None:
            result = self._rpc_request("wiki", "getPageHTML", page_name)
        else:
            result = self._rpc_request(
                "wiki", "getPageHTMLVersion", page_name, version
            )
        return result

    def wiki_to_html(self, text: str) -> str:
        """
        Render arbitrary TracWiki markup to HTML server-side, with no write.

        Wraps ``wiki.wikiToHtml``. Unlike ``get_wiki_page_html``, this does
        not read (or require) an existing page -- it renders ``text`` in
        isolation, which is what makes ``convert_preview`` possible without
        a scratch-page round trip (ticket #56).

        Args:
            text: TracWiki markup to render.

        Returns:
            Rendered HTML as string.

        Raises:
            xmlrpc.client.Fault: If the server rejects the call (e.g.
                permissions denied).
        """
        return self._rpc_request("wiki", "wikiToHtml", text)

    def delete_wiki_page(self, page_name: str) -> bool:
        """
        Delete a wiki page.

        Args:
            page_name: Name of wiki page to delete

        Returns:
            True if successful

        Raises:
            xmlrpc.client.Fault: If page not found or permissions denied
        """
        result = self._rpc_request("wiki", "deletePage", page_name)
        return result

    # Wiki attachment operations

    def put_wiki_attachment(
        self,
        page_name: str,
        filename: str,
        description: str,
        data: xmlrpc.client.Binary,
        replace: bool = False,
    ) -> str:
        """
        Upload a file as an attachment to a wiki page.

        Wraps ``wiki.putAttachmentEx``, NOT the bare ``wiki.putAttachment``:
        the ``Ex`` variant accepts both a description and a replace flag,
        while the bare form drops the description and forces replace=True.

        The XML-RPC signature is
        ``putAttachmentEx(pagename, filename, description, data, replace)``
        (note: data BEFORE replace, separate pagename + filename — NOT a
        single ``"page/file"`` path like the bare ``putAttachment``).

        Args:
            page_name: Wiki page name. The target wiki page must already
                exist or Trac will raise ``ResourceNotFound``.
            filename: Attachment filename (basename only).
            description: Attachment description.
            data: Attachment payload wrapped in ``xmlrpc.client.Binary``.
            replace: If True, overwrite an existing attachment with the
                same name; if False and a collision occurs, Trac will
                rename the new attachment and return the new filename.

        Returns:
            Server-returned filename of the stored attachment (typically
            ``filename``; may differ on collision when replace=False).

        Raises:
            xmlrpc.client.Fault: If wiki page does not exist
                (ResourceNotFound), or permissions denied
                (requires WIKI_MODIFY).
        """
        return self._rpc_request(
            "wiki",
            "putAttachmentEx",
            page_name,
            filename,
            description,
            data,
            replace,
        )

    def get_wiki_attachment(self, page_path: str) -> bytes:
        """
        Download a wiki attachment as raw bytes.

        Relies on ``_parse_xmlrpc_value`` decoding the XML-RPC ``<base64>``
        payload into ``bytes``.

        Args:
            page_path: Attachment path of the form ``"PageName/filename"``.

        Returns:
            Raw attachment bytes.

        Raises:
            xmlrpc.client.Fault: If wiki page or attachment not found,
                or permissions denied (requires WIKI_VIEW).
        """
        return self._rpc_request("wiki", "getAttachment", page_path)

    def list_wiki_attachments(self, page_name: str) -> list[str]:
        """
        List attachments on a wiki page.

        Note: ``wiki.listAttachments`` returns a flat ``list[str]`` of
        ``"PageName/filename"`` paths — NOT a list of ``(filename,
        description, size, time, author)`` tuples like the ticket
        equivalent. This asymmetry is preserved here intentionally.

        Args:
            page_name: Wiki page name to list attachments for.

        Returns:
            List of ``"PageName/filename"`` path strings.

        Raises:
            xmlrpc.client.Fault: If wiki page not found or permissions
                denied (requires WIKI_VIEW).
        """
        return self._rpc_request("wiki", "listAttachments", page_name)

    def delete_wiki_attachment(self, page_path: str) -> bool:
        """
        Delete a wiki attachment.

        Args:
            page_path: Attachment path of the form ``"PageName/filename"``.

        Returns:
            True on success.

        Raises:
            xmlrpc.client.Fault: If page or attachment not found, or
                permissions denied (requires WIKI_DELETE).
        """
        return self._rpc_request("wiki", "deleteAttachment", page_path)

    def get_recent_wiki_changes(
        self, since_timestamp: int = 0
    ) -> list[dict[str, Any]]:
        """
        Get recently modified wiki pages.

        Args:
            since_timestamp: Unix timestamp (seconds since epoch). Returns pages modified since this time.
                           Default 0 returns all recent changes.

        Returns:
            List of change dicts with keys: name, author, lastModified, version
            Sorted by modification date (newest first)

        Raises:
            xmlrpc.client.Fault: If method not available or permissions denied
        """
        try:
            # Try getRecentChanges if available
            # DateTime(0) is "now", not the epoch: ask for everything
            # with the epoch spelled out.
            dt = xmlrpc.client.DateTime(
                since_timestamp if since_timestamp else _WIKI_EPOCH
            )
            result = self._rpc_request("wiki", "getRecentChanges", dt)
            return result
        except xmlrpc.client.Fault as e:
            # Fall back to getAllPages + getPageInfo if getRecentChanges not available
            if (
                "not found" in str(e).lower()
                or "no such method" in str(e).lower()
            ):
                pages = self.list_wiki_pages()
                changes = []
                for page in pages:
                    try:
                        info = self.get_wiki_page_info(page)
                        # Filter by timestamp if provided
                        last_modified = info.get("lastModified", 0)
                        if isinstance(
                            last_modified, xmlrpc.client.DateTime
                        ):
                            # Convert DateTime to timestamp
                            import time

                            last_modified = int(
                                time.mktime(last_modified.timetuple())
                            )
                        if (
                            since_timestamp == 0
                            or last_modified >= since_timestamp
                        ):
                            changes.append(info)
                    except Exception:
                        continue
                # Sort by lastModified descending
                changes.sort(
                    key=lambda x: x.get("lastModified", 0), reverse=True
                )
                return changes
            raise

    # Milestone operations

    def get_all_milestones(self) -> list[str]:
        """
        List all milestone names in Trac.

        Returns:
            List of milestone names (e.g., ["v1.0", "v2.0", "Future"])

        Raises:
            xmlrpc.client.Fault: If server returns error or permissions denied (requires TICKET_VIEW)
        """
        result = self._rpc_request("ticket.milestone", "getAll")
        return result

    def get_milestone(self, name: str) -> dict[str, Any]:
        """
        Get milestone details by name.

        Args:
            name: Milestone name

        Returns:
            Dict with keys: name, due (DateTime or 0), completed (DateTime or 0), description

        Raises:
            xmlrpc.client.Fault: If milestone not found or permissions denied (requires TICKET_VIEW)
        """
        result = self._rpc_request("ticket.milestone", "get", name)
        return result

    def create_milestone(
        self, name: str, attributes: dict[str, Any]
    ) -> None:
        """
        Create a new milestone.

        Args:
            name: Milestone name
            attributes: Dict with optional keys: due (DateTime), completed (DateTime or 0), description (str)

        Raises:
            xmlrpc.client.Fault: If milestone exists, validation fails, or permissions denied (requires TICKET_ADMIN)
        """
        self._rpc_request(
            "ticket.milestone", "create", name, attributes
        )

    def update_milestone(
        self, name: str, attributes: dict[str, Any]
    ) -> None:
        """
        Update an existing milestone.

        Args:
            name: Milestone name
            attributes: Dict with keys to update: due (DateTime), completed (DateTime or 0), description (str)

        Raises:
            xmlrpc.client.Fault: If milestone not found, validation fails, or permissions denied (requires TICKET_ADMIN)
        """
        self._rpc_request(
            "ticket.milestone", "update", name, attributes
        )

    def delete_milestone(self, name: str) -> None:
        """
        Delete a milestone.

        Args:
            name: Milestone name

        Raises:
            xmlrpc.client.Fault: If milestone not found or permissions denied (requires TICKET_ADMIN)
        """
        self._rpc_request("ticket.milestone", "delete", name)

    # Ticket admin (components and enums)

    def list_components(self) -> list[dict[str, Any]]:
        """
        Get all ticket components with their attributes.

        Returns:
            List of dicts with keys: name, owner, description.

        Raises:
            xmlrpc.client.Fault: If permissions denied (requires TICKET_VIEW).
        """
        names = self._rpc_request("ticket.component", "getAll")
        result: list[dict[str, Any]] = []
        for name in names:
            attrs = self._rpc_request("ticket.component", "get", name)
            # attrs is typically a dict; normalize to ensure name is present.
            entry: dict[str, Any] = {
                "name": name,
                "owner": "",
                "description": "",
            }
            if isinstance(attrs, dict):
                entry.update(
                    {
                        k: v
                        for k, v in attrs.items()
                        if k in ("owner", "description")
                    }
                )
            result.append(entry)
        return result

    def create_component(
        self,
        name: str,
        description: str = "",
        owner: str = "",
    ) -> None:
        """
        Create a new ticket component.

        Args:
            name: Component name (required, must be unique).
            description: Optional description (default: empty string).
            owner: Optional default owner username (default: empty string).

        Raises:
            xmlrpc.client.Fault: If component exists, validation fails,
                or permissions denied (requires TICKET_ADMIN).
        """
        attributes: dict[str, Any] = {
            "description": description,
            "owner": owner,
        }
        self._rpc_request(
            "ticket.component", "create", name, attributes
        )

    def delete_component(self, name: str) -> None:
        """
        Delete a ticket component.

        Args:
            name: Component name to delete.

        Raises:
            xmlrpc.client.Fault: If component doesn't exist, or
                permissions denied (requires TICKET_ADMIN).
        """
        self._rpc_request("ticket.component", "delete", name)

    def list_enum(self, enum_type: str) -> list[str]:
        """
        Get all values for a Trac enum field.

        Args:
            enum_type: One of "priority", "resolution", "severity", "type",
                "version". Must be a valid Trac enum service name.

        Returns:
            List of enum value names, in Trac's configured order.

        Raises:
            ValueError: If enum_type is not in the supported whitelist.
            xmlrpc.client.Fault: If permissions denied (requires TICKET_VIEW).
        """
        if enum_type not in {
            "priority",
            "resolution",
            "severity",
            "type",
            "version",
        }:
            raise ValueError(
                f"Unsupported enum_type '{enum_type}'. "
                "Expected one of: priority, resolution, severity, type, version."
            )
        return self._rpc_request(f"ticket.{enum_type}", "getAll")

    def create_enum(
        self, enum_type: str, name: str, value: int = 0
    ) -> None:
        """
        Create a new value for a Trac enum field.

        Args:
            enum_type: One of "priority", "resolution", "severity", "type",
                "version". Must be a valid Trac enum service name.
            name: New enum value name.
            value: Sort-order integer (default: 0). Trac requires this
                positional argument; passing 0 appends to the end.

        Raises:
            ValueError: If enum_type is not in the supported whitelist.
            xmlrpc.client.Fault: If value exists, validation fails, or
                permissions denied (requires TICKET_ADMIN).
        """
        if enum_type not in {
            "priority",
            "resolution",
            "severity",
            "type",
            "version",
        }:
            raise ValueError(
                f"Unsupported enum_type '{enum_type}'. "
                "Expected one of: priority, resolution, severity, type, version."
            )
        # Trac's enum.create requires both name and value (sort order).
        self._rpc_request(f"ticket.{enum_type}", "create", name, value)

    def delete_enum(self, enum_type: str, name: str) -> None:
        """
        Delete a value from a Trac enum field.

        Args:
            enum_type: One of "priority", "resolution", "severity", "type",
                "version". Must be a valid Trac enum service name.
            name: Enum value name to delete.

        Raises:
            ValueError: If enum_type is not in the supported whitelist.
            xmlrpc.client.Fault: If value doesn't exist, or permissions
                denied (requires TICKET_ADMIN).
        """
        if enum_type not in {
            "priority",
            "resolution",
            "severity",
            "type",
            "version",
        }:
            raise ValueError(
                f"Unsupported enum_type '{enum_type}'. "
                "Expected one of: priority, resolution, severity, type, version."
            )
        self._rpc_request(f"ticket.{enum_type}", "delete", name)

    # Ticket field metadata

    def delete_ticket(self, ticket_id: int) -> bool:
        """
        Delete a ticket.

        Args:
            ticket_id: Ticket number to delete

        Returns:
            True if successful

        Raises:
            xmlrpc.client.Fault: If ticket not found or permissions denied (requires TICKET_ADMIN)
        """
        self._rpc_request("ticket", "delete", ticket_id)
        # Server returns 0 (int) on success; errors raise xmlrpc.client.Fault.
        # Return True explicitly to match documented bool return type.
        return True

    def get_ticket_fields(self) -> list[dict[str, Any]]:
        """
        Get all ticket field definitions (standard + custom fields).

        Returns:
            List of dicts with keys: name, type, label, options (for select fields), custom (bool)

        Raises:
            xmlrpc.client.Fault: If server returns error or permissions denied (requires TICKET_VIEW)
        """
        result = self._rpc_request("ticket", "getTicketFields")
        return result

    # Ticket attachment operations

    def put_ticket_attachment(
        self,
        ticket_id: int,
        filename: str,
        description: str,
        data: xmlrpc.client.Binary,
        replace: bool = False,
    ) -> Any:
        """
        Upload a file as an attachment to a ticket.

        Args:
            ticket_id: Ticket number to attach to
            filename: Attachment filename (basename only)
            description: Attachment description
            data: Attachment payload wrapped in ``xmlrpc.client.Binary``
            replace: If True, overwrite an existing attachment of the same name

        Returns:
            Server-returned identifier for the stored attachment (typically the
            stored filename string).

        Raises:
            xmlrpc.client.Fault: If ticket not found, permissions denied
                (requires TICKET_APPEND), or attachment exists and replace=False
        """
        return self._rpc_request(
            "ticket",
            "putAttachment",
            ticket_id,
            filename,
            description,
            data,
            replace,
        )

    def get_ticket_attachment(
        self, ticket_id: int, filename: str
    ) -> bytes:
        """
        Download a ticket attachment as raw bytes.

        Relies on ``_parse_xmlrpc_value`` decoding the XML-RPC ``<base64>``
        payload into ``bytes``.

        Args:
            ticket_id: Ticket number the attachment belongs to
            filename: Attachment filename

        Returns:
            Raw attachment bytes.

        Raises:
            xmlrpc.client.Fault: If ticket or attachment not found, or
                permissions denied (requires TICKET_VIEW)
        """
        return self._rpc_request(
            "ticket", "getAttachment", ticket_id, filename
        )

    def list_ticket_attachments(self, ticket_id: int) -> list[Any]:
        """
        List attachments on a ticket.

        Args:
            ticket_id: Ticket number to list attachments for

        Returns:
            List of attachment tuples [filename, description, size, time, author]

        Raises:
            xmlrpc.client.Fault: If ticket not found or permissions denied
                (requires TICKET_VIEW)
        """
        return self._rpc_request("ticket", "listAttachments", ticket_id)

    def delete_ticket_attachment(
        self, ticket_id: int, filename: str
    ) -> bool:
        """
        Delete a ticket attachment.

        Args:
            ticket_id: Ticket number the attachment belongs to
            filename: Attachment filename to delete

        Returns:
            True on success.

        Raises:
            xmlrpc.client.Fault: If ticket or attachment not found, or
                permissions denied (requires TICKET_ADMIN)
        """
        return self._rpc_request(
            "ticket", "deleteAttachment", ticket_id, filename
        )
