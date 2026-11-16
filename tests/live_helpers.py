"""Helpers for the live (``--run-live``) rows. Trac /trac_mcp_server #173.

Five things live here so the rows and conftest share one copy:

  * ``trac_test_url()`` / ``trac_test_client()`` /
    ``trac_test_registry()``: the destructive-test instance. It is
    ``TRAC_TEST_URL`` when that is set (the instance has its own origin,
    Trac /trac_mcp_server #203), else ``/trac_test`` on the host and
    port ``TRAC_URL`` names, whatever project or sub-path ``TRAC_URL``
    itself carries.
  * the origin probe (``probe_trac_test``): one XML-RPC question put to
    ``trac_test_url()`` before a live run, answering ``None`` when an
    XML-RPC reply comes back and otherwise one line naming the URL, what
    it answered and what to check (``TRAC_TEST_URL``, the credentials,
    the certificate or the allowlist, by cause). conftest ends the run
    with that line (exit code 2, "not run") instead of letting dozens of
    rows fail on a wrong origin (Trac /trac_mcp_server #224).
  * the runtime write guard (``live_write_guard``): while it is
    installed, a TracClient XML-RPC WRITE whose client is not on
    ``/trac_test`` is recorded and refused before any byte is sent.
  * ``post_description_reply``: a comment made as a reply to a ticket's
    description, which only Trac's web form can make (Trac
    /trac_mcp_server #207).
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
import re
import xmlrpc.client
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlsplit, urlunsplit

import requests
from lxml import html as lxml_html

from trac_mcp_server.config import Config
from trac_mcp_server.core.client import TracClient
from trac_mcp_server.core.scope import DestinationOutOfScope
from trac_mcp_server.instances import InstanceRegistry, InstanceSpec

logger = logging.getLogger(__name__)

TEST_INSTANCE = "/trac_test"

# ---------------------------------------------------------------------
# The /trac_test URL and client
# ---------------------------------------------------------------------


def _trac_test_url_override() -> str:
    """``TRAC_TEST_URL`` stripped; ``""`` when unset or blank."""
    return os.environ.get("TRAC_TEST_URL", "").strip()


def live_credentials_set() -> bool:
    """Whether ``TRAC_URL``, ``TRAC_USERNAME`` and ``TRAC_PASSWORD`` are
    all set and non-empty in the environment: the condition under which
    a live run has a Trac to talk to (the sweep and the probe both gate
    on it)."""
    return all(
        os.environ.get(var)
        for var in ("TRAC_URL", "TRAC_USERNAME", "TRAC_PASSWORD")
    )


def trac_test_url() -> str:
    """The ``/trac_test`` instance every live row writes to.

    ``TRAC_TEST_URL`` (trailing ``/`` stripped) when it is set and
    non-empty: rookery #69 moves /trac_test to its own origin, and a
    308 across ports drops Basic credentials, so a row cannot reach it
    through ``TRAC_URL``'s host and port any more. Otherwise
    ``/trac_test`` on the scheme, host and port ``TRAC_URL`` names.

    Raises ``ValueError`` when ``TRAC_TEST_URL`` is not an absolute
    http(s) URL with a host (a scheme-less value would be joined onto
    ``TRAC_URL``'s host by the registry, i.e. the OLD origin), or does
    not end with ``/trac_test`` (the write guard, ``is_trac_test_url``,
    would refuse every write to it). The message names the variable and
    the defect, never the value: it can carry userinfo, and conftest's
    sweep logs the exception.
    """
    override = _trac_test_url_override()
    if override:
        url = override.rstrip("/")
        parsed = urlparse(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
        ):
            raise ValueError(
                "TRAC_TEST_URL must be an absolute http(s) URL with a "
                "host (scheme or host missing or not http/https)"
            )
        if not is_trac_test_url(url):
            raise ValueError(
                f"TRAC_TEST_URL path {parsed.path!r} does not end "
                f"with {TEST_INSTANCE}: the live write guard would "
                "refuse every write to it"
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
    return _PinnedTestRegistry(default_config, spec)


class _PinnedTestRegistry(InstanceRegistry):
    """An InstanceRegistry that keeps ``/trac_test`` declared.

    ``_reload_if_changed`` replaces ``_declared`` with
    ``load_declared_instances()`` when a config file or the
    ``TRAC_INSTANCES`` file changes mtime; mid-run that would drop the
    ``/trac_test`` spec and resolve would fall back to the ad-hoc join
    onto ``TRAC_URL``'s host. Re-pin it after every reload.
    """

    def __init__(self, default_config: Config, spec: InstanceSpec):
        self._pinned = spec
        super().__init__(default_config, {spec.name: spec})

    def _reload_if_changed(self) -> None:
        super()._reload_if_changed()
        with self._lock:
            # The reload built a fresh dict; no loader state is shared.
            self._declared[self._pinned.name] = self._pinned


# ---------------------------------------------------------------------
# A reply to a ticket's description (Trac /trac_mcp_server #207)
# ---------------------------------------------------------------------


def _form_input_value(html: str, name: str) -> str:
    """The ``value`` of the ``<input name="NAME">`` tag in ``html``.

    Parsed with lxml, not a regex: a regex on ``name=``/``value=`` also
    matches inside ``data-name=``/``data-value=`` and cuts a value at
    the other quote character (#207 review). lxml decodes entities.
    Raises ``AssertionError`` naming the field when there is no such
    tag or it has no value: the form changed, and a POST built without
    it would only come back as a bare 400.
    """
    tree = lxml_html.fromstring(html)
    for node in tree.xpath("//input[@name=$n]", n=name):
        value = node.get("value")
        if value is not None:
            return value
    raise AssertionError(
        f"the ticket page has no <input name={name!r}> with a value"
    )


def post_description_reply(ticket_id: int, text: str) -> int:
    """Comment on a /trac_test ticket as a REPLY TO ITS DESCRIPTION.

    Returns the new comment's number. Trac stores such a comment's
    changelog ``oldvalue`` as ``"description.N"``, not ``"N"`` or
    ``"M.N"``, and only the web form can make one: XML-RPC
    ``ticket.update`` has no ``replyto``, and the tracrpc_comment
    plugin's ``replyToComment`` needs a numeric parent comment. So this
    posts Trac's own ticket form, the recipe verified against /trac_test
    (2026-11-15):

      1. Basic auth on every request (``TRAC_USERNAME`` /
         ``TRAC_PASSWORD``); no login cookie is needed.
      2. GET ``/ticket/<id>`` and take ``__FORM_TOKEN``, ``view_time``
         and ``start_time`` from its ``<input>`` tags.
      3. Set the cookie ``trac_form_token`` to that token: Trac's CSRF
         check compares the form field with the cookie, and without the
         cookie the POST is a bare 400.
      4. POST ``/ticket/<id>`` with those three, ``comment``,
         ``replyto=description``, ``action=leave`` and ``submit``, a
         ``Referer`` of the ticket page and redirects off. Success is a
         303 whose ``Location`` ends ``#comment:N``.

    ``live_write_guard`` only patches the XML-RPC call, so it cannot see
    this POST. The ``is_trac_test_url`` check below is the same
    PATH-only test that guard applies, kept as a belt-and-braces refusal
    before anything is sent; ``trac_test_url()`` already guarantees the
    path, and neither checks the host. Fails loudly (``AssertionError``)
    on any other answer. Messages name the URL with its userinfo
    stripped: ``TRAC_TEST_URL`` may carry ``user:password@``.
    """
    config = trac_test_config()
    base = config.trac_url.rstrip("/")
    if not is_trac_test_url(base):
        raise LiveWriteRefused(
            "post_description_reply refuses a URL that is not "
            f"{TEST_INSTANCE}"
        )
    url = f"{base}/ticket/{ticket_id}"
    shown = strip_userinfo(url)
    with requests.Session() as session:
        session.auth = (config.username, config.password)
        session.verify = not config.insecure
        page = session.get(url, timeout=30)
        assert page.status_code == 200, (
            f"GET {shown} answered {page.status_code}"
        )
        token = _form_input_value(page.text, "__FORM_TOKEN")
        fields = {
            "__FORM_TOKEN": token,
            "view_time": _form_input_value(page.text, "view_time"),
            "start_time": _form_input_value(page.text, "start_time"),
            "comment": text,
            "replyto": "description",
            "action": "leave",
            "submit": "Submit changes",
        }
        session.cookies.set("trac_form_token", token)
        response = session.post(
            url,
            data=fields,
            headers={"Referer": url},
            timeout=30,
            allow_redirects=False,
        )
    location = response.headers.get("Location", "")
    match = re.search(r"#comment:(\d+)$", location)
    assert response.status_code == 303 and match, (
        f"POST {shown} answered {response.status_code}, Location "
        f"{location!r}: expected 303 to #comment:N"
    )
    return int(match.group(1))


# ---------------------------------------------------------------------
# The origin probe
# ---------------------------------------------------------------------


def strip_userinfo(url: str) -> str:
    """``url`` without any ``user[:password]@``.

    Everything up to and including the last ``@`` of the netloc is
    dropped, whatever the URL parser makes of the host (``http://u:pw@
    :8080/x`` has no hostname, and a hostname-based rebuild would hand
    the password back). A URL with no ``@`` in its netloc is returned
    unchanged; one the parser rejects is replaced by a placeholder, so
    nothing it held can leak.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "(unparseable URL)"
    if "@" not in parts.netloc:
        return url
    netloc = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit(parts._replace(netloc=netloc))


_CREDENTIALS_ADVICE = (
    "check TRAC_USERNAME/TRAC_PASSWORD (the account needs XML_RPC on "
    "/trac_test)"
)


def probe_trac_test() -> str | None:
    """Ask ``/trac_test`` one XML-RPC question; ``None`` when answered.

    POSTs ``system.getAPIVersion`` to the ``trac_test_client()``'s RPC
    URL through the client's own session (its auth, TLS setting and
    scope), without following a redirect: a 308 to another origin is the
    defect this exists to name, and a followed hop would drop the
    credentials and answer 401 instead. Only a well-formed XML-RPC
    result is a pass. A fault is not: ``system.getAPIVersion`` needs
    nothing beyond ``XML_RPC``, so a healthy Trac never faults on it and
    a fault means the rows would fail one by one.

    Anything else returns one line shaped ``live suite not run:
    /trac_test at <url> <answer>; <advice>``, the advice following the
    cause: ``TRAC_TEST_URL`` for a wrong origin (redirect, unreachable,
    not XML-RPC, other status), the credentials for 401/403 or a fault,
    the certificate for a TLS failure, the allowlist for a scope refusal.
    The URL is printed without userinfo and the password never appears;
    a malformed ``TRAC_TEST_URL`` is described by ``trac_test_url()``'s
    message, which never carries the value.
    """
    set_by = (
        "set"
        if _trac_test_url_override()
        else "unset, derived from TRAC_URL"
    )
    url_advice = (
        "set TRAC_TEST_URL in .env to /trac_test's own origin "
        f"(it is {set_by})"
    )
    try:
        url = trac_test_url()
    except ValueError as exc:
        return f"live suite not run: {exc}; {url_advice}"
    where = f"/trac_test at {strip_userinfo(url)}"

    def line(answer: str, advice: str = url_advice) -> str:
        return f"live suite not run: {where} {answer}; {advice}"

    client = trac_test_client()
    try:
        # The probe builds its own POST: ``_rpc_call`` follows redirects
        # and raises on status, and the probe needs the raw first answer.
        response = client._get_session().post(
            client.rpc_url,
            data=xmlrpc.client.dumps(
                (), methodname="system.getAPIVersion"
            ),
            headers={"Content-Type": "text/xml"},
            timeout=(10, client.config.rpc_timeout),
            allow_redirects=False,
        )
    except requests.exceptions.SSLError as exc:
        return line(
            f"failed TLS ({type(exc).__name__})",
            "check the certificate or set TRAC_INSECURE",
        )
    except requests.RequestException as exc:
        return line(f"is unreachable ({type(exc).__name__})")
    except DestinationOutOfScope:
        # Not a RequestException: the session refused the request unsent
        # because the client's allowlist excludes /trac_test.
        return line(
            "is outside the client's destination allowlist",
            "the client's allowed instances exclude it",
        )
    finally:
        client.close()
    code = response.status_code
    if 300 <= code < 400:
        location = response.headers.get("Location")
        target = (
            strip_userinfo(location) if location else "(no Location)"
        )
        return line(f"answered {code} redirect to {target}")
    if code in (401, 403):
        return line(f"answered HTTP {code}", _CREDENTIALS_ADVICE)
    if code != 200:
        return line(f"answered HTTP {code}")
    try:
        xmlrpc.client.loads(response.content)
    except xmlrpc.client.Fault as fault:
        text = " ".join(str(fault.faultString).split())[:120]
        return line(
            f"answered XML-RPC fault {fault.faultCode}: {text}",
            _CREDENTIALS_ADVICE,
        )
    except Exception:
        # ExpatError, ResponseError and ValueError all mean the body is
        # not an XML-RPC reply.
        content_type = response.headers.get("Content-Type") or "none"
        return line(
            "answered 200 but not an XML-RPC reply "
            f"(Content-Type: {content_type})"
        )
    return None


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
    "Scratch/Ticket219HtmlHrefLive",
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
