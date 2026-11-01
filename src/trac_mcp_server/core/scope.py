"""Where a ``TracClient`` session may send a request, and with what
credentials (tickets #110 and #118).

Enforced in :meth:`ScopedSession.send`, the one call every request on
the session goes through: the first request AND every redirect hop
``requests`` follows (``resolve_redirects`` sends each hop through
``send()``, after its own ``rebuild_auth``). So the check sees the final
URL and headers of everything the session puts on the wire, whichever
code path asked for it.

Two rules:

* **Credentials stay on the client's own origin** (#118). The session
  carries ``auth=(username, password)``, which ``requests`` turns into
  ``Authorization: Basic ...`` on every request, whatever its host. The
  link probe fetches hrefs an author wrote, so without this an authored
  ``http://attacker.example/intertrac/wiki%3AX`` received the caller's
  Trac password. A request to another origin goes out anonymously
  instead: a public foreign Trac stays checkable, and a private one
  answers 401/403, which the probe reports as unchecked, never as a
  broken link. "Same origin" is ``requests``' own
  ``should_strip_auth`` rule, the one it already applies to a redirect:
  same hostname, and the same scheme and port, except that an
  http -> https upgrade on the default ports keeps the credentials.

* **A scoped client never leaves its allowlist** (#110). With
  ``allowed_instances`` set, a request outside :func:`in_scope` raises
  :class:`DestinationOutOfScope` before anything is sent.
"""

from urllib.parse import unquote, urlsplit

import requests

# Trac's own dispatcher needs one hop (`/intertrac/wiki%3AX` ->
# `/wiki/X`); five is generous, and it bounds a redirect loop.
MAX_REDIRECTS = 5


class DestinationOutOfScope(Exception):
    """A request would have left the client's allowlist; it was not
    sent."""

    def __init__(self, url: str):
        super().__init__(
            f"Destination outside the allowed instances: {url}"
        )
        self.url = url


def dispatcher_base(href: str) -> str | None:
    """The instance base ``href`` dispatches on -- everything before
    ``/intertrac/`` -- or ``None`` if ``href`` is not an InterTrac
    dispatcher link at all.

    Works for a prefix pointing at a foreign Trac
    (``https://trac.edgewall.org/intertrac/%233754``) exactly the same
    as one of ours; it is the caller's job to decide which bases count
    as local.
    """
    marker = "/intertrac/"
    index = href.find(marker)
    if index < 0:
        return None
    return href[:index]


def _sent_location(url: str) -> str:
    """``scheme://host/path`` of the request ``requests`` would SEND for
    ``url``, trailing slash stripped -- query and fragment dropped.

    The URL as sent, not as written, is what a scope check has to judge:
    ``requests`` resolves dot segments while preparing, so
    ``/trac_test/intertrac/x/../../../auto_pm/...`` is fetched from
    ``/auto_pm`` (measured on requests 2.32.5 / urllib3 2.6.3, ticket
    #110).

    Raises:
        ValueError: The sent path still holds a dot segment, literal or
            percent-encoded. ``%2e%2e`` survives preparation as a literal
            ``..``, and the server resolves it: measured live,
            ``/trac_test/%2e%2e/auto_pm/intertrac/wiki%3AIndex`` answers
            302 into ``/auto_pm``. No legitimate Trac href has one, so
            fail closed rather than model the server's resolution.
    """
    sent = urlsplit(requests.Request("GET", url).prepare().url)
    if any(seg in (".", "..") for seg in unquote(sent.path).split("/")):
        raise ValueError(f"Dot segment in {url!r}")
    return f"{sent.scheme}://{sent.netloc}{sent.path}".rstrip("/")


def scope_bases(
    allowed: frozenset[str], own_url: str
) -> frozenset[str]:
    """The scope for a client at ``own_url`` (ticket #110): the allowed
    instance URLs on that client's OWN host, each as
    :func:`_sent_location` gives it.

    Same host only, because the session carries this client's
    credentials. An allowed URL on another host is a declared instance
    with its own credentials (#102 gap 3) and its own client; this
    client's session has no business there.
    """
    own = urlsplit(_sent_location(own_url))
    bases: set[str] = set()
    for base in allowed:
        try:
            prepared = _sent_location(base)
        except Exception:
            continue
        where = urlsplit(prepared)
        if (where.scheme, where.netloc) == (own.scheme, own.netloc):
            bases.add(prepared)
    return frozenset(bases)


def in_scope(url: str, bases: frozenset[str]) -> bool:
    """Whether a request for ``url`` stays inside ``bases`` (from
    :func:`scope_bases`; ticket #110).

    A dispatcher URL (``.../intertrac/...``) must belong to an allowed
    instance EXACTLY -- the same exact match ``resolve()`` applies, so
    a host-root entry allows a Trac at the root, not every instance
    below it. Anything else (a redirect hop such as ``/trac_test/wiki/
    X``, or the RPC endpoint) must be an allowed instance URL or below
    one. A URL that cannot be prepared, or that holds a dot segment, is
    out of scope.
    """
    try:
        where = _sent_location(url)
    except Exception:
        return False
    base = dispatcher_base(where)
    if base is not None:
        return base in bases
    return any(where == b or where.startswith(b + "/") for b in bases)


class ScopedSession(requests.Session):
    """A ``requests.Session`` that holds every request it sends to the
    two rules in the module docstring.

    Args:
        own_url: The client's ``trac_url``; its origin is the only one
            that receives the session's credentials.
        scope: :func:`scope_bases` for a client with an allowlist, or
            ``None`` for an unrestricted one.
    """

    def __init__(
        self, own_url: str, scope: frozenset[str] | None = None
    ):
        super().__init__()
        self.own_url = own_url
        self.scope = scope
        self.max_redirects = MAX_REDIRECTS

    def send(self, request, **kwargs):
        if self.scope is not None and not in_scope(
            request.url, self.scope
        ):
            raise DestinationOutOfScope(request.url)
        if self.should_strip_auth(self.own_url, request.url):
            request.headers.pop("Authorization", None)
        return super().send(request, **kwargs)
