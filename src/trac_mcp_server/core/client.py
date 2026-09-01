import base64
import calendar
import socket
import threading
import time
import xmlrpc.client
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection

from ..config import Config
from ..validators import validate_content, validate_page_name

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


class TracClient:
    def __init__(self, config: Config):
        self.config = config
        self._thread_local = threading.local()
        self.rpc_url = self._get_rpc_url()

    @property
    def session(self) -> requests.Session:
        """Backward-compatible accessor for the session (returns current thread's session)."""
        return self._get_session()

    def _get_rpc_url(self) -> str:
        # Construct path to XML-RPC endpoint
        return f"{self.config.trac_url.rstrip('/')}/login/rpc"

    def _get_session(self) -> requests.Session:
        """Get or create a thread-local requests.Session."""
        if not hasattr(self._thread_local, "session"):
            self._thread_local.session = self._create_session()
        return self._thread_local.session

    def _create_session(self) -> requests.Session:
        session = requests.Session()
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

    def get_ticket_changelog(self, ticket_id: int) -> Any:
        """
        Get ticket changelog by ticket ID.
        """
        return self._rpc_request("ticket", "changeLog", ticket_id)

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
        if comment and len(comment) > 10000:
            raise ValueError(
                "Comment exceeds maximum length of 10000 characters"
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

    def search_wiki_pages_by_content(
        self, query: str, max_results: int = 10
    ) -> list[dict[str, Any]]:
        """
        Search wiki pages by content (full-text search).

        Args:
            query: Search string (case-insensitive)
            max_results: Maximum number of results to return (default: 10)

        Returns:
            List of dicts with keys: name, snippet (matching context ~100 chars)

        Raises:
            xmlrpc.client.Fault: If server returns error when listing pages
        """
        all_pages = self.list_wiki_pages()
        query_lower = query.lower()
        matches = []

        for page_name in all_pages:
            try:
                content = self.get_wiki_page(page_name)
                content_lower = content.lower()

                if query_lower in content_lower:
                    # Extract snippet around match (~100 chars)
                    match_pos = content_lower.index(query_lower)
                    snippet_start = max(0, match_pos - 50)
                    snippet_end = min(
                        len(content), match_pos + len(query) + 50
                    )
                    snippet = content[snippet_start:snippet_end].strip()

                    # Add ellipsis if truncated
                    if snippet_start > 0:
                        snippet = "..." + snippet
                    if snippet_end < len(content):
                        snippet = snippet + "..."

                    matches.append(
                        {"name": page_name, "snippet": snippet}
                    )

                    if len(matches) >= max_results:
                        break
            except Exception:
                # Skip pages that can't be read (permission denied, etc.)
                continue

        return matches

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
            version: Optional version number for optimistic locking (prevents concurrent edits)

        Returns:
            Dict with keys: name, version, author, lastModified, url

        Raises:
            ValueError: If page_name or content validation fails, or version conflict detected
            xmlrpc.client.Fault: If server returns error or permissions denied
        """
        # Validate page name
        is_valid, error_msg = validate_page_name(page_name)
        if not is_valid:
            raise ValueError(f"Invalid page name: {error_msg}")

        # Validate content
        is_valid, error_msg = validate_content(content)
        if not is_valid:
            raise ValueError(f"Invalid content: {error_msg}")

        # Build attributes dict
        attrs: dict[str, Any] = {"comment": comment}
        if version is not None:
            attrs["version"] = version

        # Make the RPC call
        try:
            result = self._rpc_request(
                "wiki", "putPage", page_name, content, attrs
            )

            # If successful, get updated page info
            if result is True:
                info = self.get_wiki_page_info(page_name)

                # Construct URL from config
                page_url = f"{self.config.trac_url.rstrip('/')}/wiki/{page_name}"

                return {
                    "name": page_name,
                    "version": info.get("version"),
                    "author": info.get("author"),
                    "lastModified": info.get("lastModified"),
                    "url": page_url,
                }
            else:
                raise ValueError(f"Failed to update page '{page_name}'")

        except xmlrpc.client.Fault as err:
            # Handle specific fault conditions
            fault_str = err.faultString.lower()

            if "not modified" in fault_str:
                raise ValueError(
                    "Page not modified (content identical)"
                ) from None
            elif "version" in fault_str:
                raise ValueError(
                    "Version conflict - page was modified by another user"
                ) from None
            else:
                # Re-raise other faults
                raise

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
            dt = xmlrpc.client.DateTime(since_timestamp)
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
