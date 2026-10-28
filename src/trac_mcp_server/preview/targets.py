"""Capped live probe for cross-instance InterTrac targets.

Row 1 and row 15 of ticket #56's acceptance suite render *identically*
(both ``class="ext-link"`` to the InterTrac dispatcher) -- an InterTrac
link to a real page and to a nonexistent one are indistinguishable from
rendered HTML alone. Only a live fetch of the dispatcher target tells them
apart, and per the ticket's own note the check has to be for page
*content*: a nonexistent Trac page still returns HTTP 200, rendering a
"describe this page" stub instead of a 404 (the trap ticket #44
documented). Confirmed against the live daemon 2026-09-01: a missing
page's response contains ``id="content" class="wiki create ...``; an
existing page's does not.

Both realms are probed. Ticket #82 measured what covering only the wiki
realm cost: across 998 documents on two stores, cross-instance *ticket*
references outnumbered wiki ones better than three to one, and not one of
them was ever fetched -- ``missing_cross_instance_target`` was structurally
incapable of firing for a dead one. Two dispatcher shapes carry them, and
both are handled: ``/intertrac/%23N`` for the ``prefix:#N`` short link and
``/intertrac/ticket%3AN`` for the realm form.

**The ticket realm classifies the opposite way round from the wiki realm,
and getting it backwards is the expensive mistake.** Measured against the
live dispatcher 2026-09-04:

===================================  ======  =========================
target                               status  body
===================================  ======  =========================
ticket that exists                   200     redirected to /ticket/N
ticket that does not                 500     empty
instance that does not exist         404     short error
no/invalid credentials               401     short error
wiki page, existing OR missing       200     the page, or the stub
===================================  ======  =========================

So existence is cheap and certain -- a 200 is positive proof -- while
*absence* is not: a bare bodiless 500 is indistinguishable from the remote
instance being down or misconfigured. Treating every non-200 as missing,
the way a naive port of the wiki logic would, reports every cross-instance
ticket link in a document as dead the moment the far end hiccups, and under
ticket #64's blocking gate that refuses the write while telling the author
to fix links that are fine.

Hence the **control probe**: before any 500 is called missing, the same
instance is asked for a *wiki*-realm dispatcher target, which answers 200
whether or not that page exists. A 200 there means the instance is up and
the credentials work, so the 500 is evidence; anything else means the probe
learned nothing, and the candidate degrades to ``ERROR`` -- reported as
unchecked, never as a broken link. It is deliberately not ticket ``#1`` on
the remote instance: a deleted ticket 1 would fail the control permanently
and silently disable the check, and a gate that always reports uncertainty
looks exactly like one that always passes.

Known boundary, not covered because it cannot be produced here: this host
enforces authentication at the web layer, so a permission denial is a 401
that takes the control down with it. A Trac using fine-grained
``TICKET_VIEW`` restrictions might answer 500 for a ticket that exists but
is invisible to the probing account, which would read as missing.
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote, urljoin, urlsplit

import requests

from ..core.client import TracClient

# Trac's own new-page template: `<div id="content" class="wiki create ...">`
# on the WikiModule's "describe this page" stub. Confirmed against a known-
# missing and a known-existing page on the live daemon -- both return HTTP
# 200, so this content check (not status code) is what actually discriminates.
_MISSING_WIKI_PAGE_RE = re.compile(
    r'id="content"\s+class="wiki create\b'
)

# An InterTrac dispatcher href for the wiki realm, e.g.
# ".../intertrac/wiki%3ARules/trac/RenderVerify".
_INTERTRAC_WIKI_HREF_RE = re.compile(r"/intertrac/wiki%3A")

# An InterTrac dispatcher href for the TICKET realm, in both shapes Trac
# emits: `prefix:#N` becomes ".../intertrac/%23N" and the realm form
# `prefix:ticket:N` becomes ".../intertrac/ticket%3AN". Ticket #82 found
# the second one only by rendering it -- a fix keyed on `%23` alone would
# have left 10 of the corpus's references invisible, the same false
# negative one door over.
#
# Not anchored at the end: a short link that swallowed trailing
# punctuation (ticket #70) still names the realm, and what the target
# resolves to is the fetch's business, not the pattern's.
_INTERTRAC_TICKET_HREF_RE = re.compile(
    r"/intertrac/(?:%23|ticket%3A)\d+"
)

# Ticket #86. Grabs the dispatcher TARGET -- everything after
# `/intertrac/`, stopping short of a query string or fragment if either
# is present. Used to classify the realm-less/slashed shape below, not
# to select probeable candidates (that stays `is_probeable_href`'s job).
_INTERTRAC_TARGET_RE = re.compile(r"/intertrac/([^?#]+)")

# The control target: a WIKI-realm dispatcher path on the same instance.
# Trac answers 200 for it whether or not the page exists, on this host and
# on trac.edgewall.org alike, so a non-200 means the instance did not
# answer -- never that the page is missing. The name is deliberately one
# nobody would create; its existence would not change the outcome anyway.
_CONTROL_TARGET = "/intertrac/wiki%3ATracMcpServerProbeLivenessControl"

# Ticket #80. The cap keeps the FIRST N hrefs in document order, so
# whatever it drops is at the END of the document -- which is exactly
# where an agent appends. A write-time gate whose blind spot is the
# content just written is inverted relative to its own purpose, and
# ticket #64 is about to make these checks blocking.
#
# The number was 10 because the probe was SEQUENTIAL, not because
# probing is expensive: 50 targets at the timeout below is 250s of wall
# clock in a `for` loop. Probing concurrently removes that constraint,
# so the cap can sit above anything real content reaches -- measured
# across 998 documents on two stores, the largest was 15 unique
# probeable targets, and that was a stock Trac help page.
#
# It stays a cap rather than becoming unbounded: a pathological document
# should not be able to aim an arbitrary number of simultaneous requests
# at the store's own host.
DEFAULT_TARGET_CAP = 50
DEFAULT_TARGET_TIMEOUT_SECONDS = 5.0

# Worst case is ceil(cap / concurrency) * timeout -- 35s at these
# values, and only if every single target times out, which means the
# server is down and everything else has failed too. The typical case is
# one round trip's latency.
DEFAULT_TARGET_CONCURRENCY = 8

# Probe outcomes.
EXISTS = "exists"
MISSING = "missing"
SKIPPED = "skipped"
ERROR = "error"

# Ticket #110. The `reason` on a SKIPPED outcome for an href whose
# instance is outside the caller's allowlist; a SKIPPED with no reason is
# the probe cap.
NOT_ALLOWED_REASON = "instance_not_allowed"

# Ticket #110. Under an allowlist, redirects are followed by hand so each
# hop is checked before it is requested. Trac's own dispatcher needs one
# (`/intertrac/wiki%3AX` -> `/wiki/X`); five is generous.
_MAX_SCOPED_REDIRECTS = 5


class _OutOfScope(Exception):
    """A scoped fetch would have left the caller's allowlist."""


def is_probeable_wiki_href(href: str | None) -> bool:
    """Whether ``href`` is an InterTrac wiki-dispatcher link this module
    can classify."""
    return bool(href) and bool(_INTERTRAC_WIKI_HREF_RE.search(href))


def is_probeable_ticket_href(href: str | None) -> bool:
    """Whether ``href`` is an InterTrac ticket-dispatcher link, in either
    the ``prefix:#N`` short-link or ``prefix:ticket:N`` realm shape."""
    return bool(href) and bool(_INTERTRAC_TICKET_HREF_RE.search(href))


def is_probeable_href(href: str | None) -> bool:
    """Whether ``href`` is worth fetching at all.

    The union the callers want: they select candidates, this module
    decides how each realm is classified.
    """
    return is_probeable_wiki_href(href) or is_probeable_ticket_href(
        href
    )


def is_missing_realm_href(href: str | None) -> bool:
    """Whether ``href`` is an InterTrac dispatcher link whose target is
    realm-less AND slashed -- ticket #86's shape, always dead on a
    dispatcher that shares this host's bug, whether or not the target
    page exists.

    A realm-form target always carries the encoded colon ``%3A`` before
    any ``/`` (``wiki%3ARules/trac/Foo``); a realm-less slashed target
    has a ``/`` with no ``%3A`` before it, or none at all. Single-segment
    realm-less targets (``/intertrac/WikiStart``) have no ``/`` at all
    and are the form that resolves correctly even on this host, so they
    are excluded here.

    Shape only: says nothing about whether ``href``'s dispatcher is one
    of ours. Upstream Trac (``trac.edgewall.org``) resolves the
    identical shape correctly -- see the module docstring's scoping
    trap -- so a caller must additionally confirm the dispatcher base
    with ``dispatcher_base`` before treating this as a defect.
    """
    if not href:
        return False
    match = _INTERTRAC_TARGET_RE.search(href)
    if not match:
        return False
    target = match.group(1)
    slash_index = target.find("/")
    if slash_index == -1:
        return False
    colon_index = target.find("%3A")
    return colon_index == -1 or slash_index < colon_index


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
    """The probe scope for a client at ``own_url`` (ticket #110): the
    allowed instance URLs on that client's OWN host, each as
    :func:`_sent_location` gives it, prepared once per probe run.

    Same host only, because the probe session carries this client's
    credentials. An allowed URL on another host is a declared instance
    with its own credentials (#102 gap 3), and probing it -- or
    following a redirect to it -- would hand this caller's password to
    a host it was never configured for. ``requests`` strips auth on a
    cross-host redirect it follows itself; the hand-followed hops in
    :func:`_get` would not.
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
    X``) must be an allowed instance URL or below one. A URL that cannot
    be prepared, or that holds a dot segment, is out of scope.
    """
    try:
        where = _sent_location(url)
    except Exception:
        return False
    base = dispatcher_base(where)
    if base is not None:
        return base in bases
    return any(where == b or where.startswith(b + "/") for b in bases)


def _get(
    client: TracClient,
    url: str,
    timeout: float,
    scope: frozenset[str] | None,
):
    """``GET url`` on the client's session, kept inside ``scope`` (from
    :func:`scope_bases`).

    Unrestricted (``scope is None``): exactly the request this module
    always made. Scoped: every hop, the first included, is checked with
    :func:`in_scope` BEFORE it is requested, and redirects are followed
    by hand. An allowed instance's dispatcher redirects a nested
    InterTrac target onward -- measured live, ``/trac_test/intertrac/
    auto_pm%3Awiki%3AIndex`` answers 302 to ``/auto_pm/...`` -- and
    ``requests`` would follow that with the caller's credentials.

    Raises:
        _OutOfScope: A hop would leave ``scope``. Nothing was sent to it.
    """
    session = client.session
    if scope is None:
        return session.get(url, timeout=timeout, allow_redirects=True)
    for _ in range(_MAX_SCOPED_REDIRECTS + 1):
        if not in_scope(url, scope):
            raise _OutOfScope(url)
        response = session.get(
            url, timeout=timeout, allow_redirects=False
        )
        location = (
            response.headers.get("Location")
            if 300 <= response.status_code < 400
            else None
        )
        if not location:
            return response
        url = urljoin(response.url or url, location)
    # Still redirecting: hand back the last 3xx, which classifies as a
    # probe that learned nothing.
    return response


def _control_url(href: str) -> str | None:
    """The liveness-control URL for the instance ``href`` dispatches on.

    An InterTrac href is ``<instance base>/intertrac/<target>``, and the
    instance base is all this needs -- including for a prefix pointing at
    a foreign Trac (``https://trac.edgewall.org/intertrac/%233754``),
    where "is the instance up" is a question about that host, not ours.
    """
    base = dispatcher_base(href)
    return None if base is None else base + _CONTROL_TARGET


def _probe_one(
    client: TracClient,
    href: str,
    timeout: float,
    scope: frozenset[str] | None = None,
) -> dict:
    """Fetch and classify one dispatcher href.

    ``client.session`` is read HERE, inside the worker, and that is
    load-bearing rather than incidental: ``TracClient`` hands out a
    thread-local session, so hoisting it into the dispatching thread --
    which the sequential implementation did, harmlessly -- would hand
    every worker one thread's session and quietly defeat that design.

    A ticket-realm result carrying ``needs_control`` is PROVISIONAL: the
    caller must confirm the instance answered a control before that
    ``MISSING`` may be believed. See the module docstring.

    Under an allowlist, an href whose redirect chain would leave it comes
    back ``SKIPPED`` with :data:`NOT_ALLOWED_REASON`, like one refused
    up front (ticket #110).
    """
    try:
        response = _get(client, href, timeout, scope)
    except _OutOfScope:
        return {
            "status": SKIPPED,
            "resolved_url": None,
            "reason": NOT_ALLOWED_REASON,
        }
    except Exception:
        return {"status": ERROR, "resolved_url": None}

    if is_probeable_ticket_href(href):
        return _classify_ticket(response)

    if response.status_code != 200:
        return {"status": ERROR, "resolved_url": None}
    status = (
        MISSING
        if _MISSING_WIKI_PAGE_RE.search(response.text)
        else EXISTS
    )
    return {"status": status, "resolved_url": response.url}


def _classify_ticket(response) -> dict:
    """Classify a ticket-realm dispatcher response.

    On STATUS, deliberately, not on the body: this host answers a missing
    ticket with a bare 500 and an empty body, while trac.edgewall.org
    answers the same 500 with a 9 KB error page. Two Trac deployments,
    two bodies, one status -- so the status is the part that is about
    Trac rather than about a particular install.
    """
    if response.status_code == 200:
        return {"status": EXISTS, "resolved_url": response.url}
    if response.status_code == 500:
        # Provisional. A 500 is equally consistent with "no such ticket"
        # and with the instance being unwell, and only the control tells
        # those apart.
        return {
            "status": MISSING,
            "resolved_url": None,
            "needs_control": True,
        }
    # 404 (no such instance), 401/403 (credentials), anything else: the
    # probe learned nothing. ERROR is reported as unchecked, never as a
    # broken link.
    return {"status": ERROR, "resolved_url": None}


def _instance_answers(
    client: TracClient,
    control_url: str,
    timeout: float,
    scope: frozenset[str] | None = None,
) -> bool:
    """Whether the instance answers its wiki-realm control with 200.

    A control that would leave the allowlist counts as no answer, so the
    candidate degrades to unchecked rather than being called missing.
    """
    try:
        response = _get(client, control_url, timeout, scope)
    except Exception:
        return False
    return response.status_code == 200


def _confirm_provisional(
    client: TracClient,
    probed: dict[str, dict],
    timeout: float,
    concurrency: int,
    scope: frozenset[str] | None = None,
) -> None:
    """Resolve every provisional ``MISSING`` against its instance control.

    One control request per distinct instance, and only when something on
    that instance came back 500 -- a document whose cross-instance ticket
    links are all live costs nothing extra. Control 200: the instance is
    up, so the 500 was evidence and the MISSING stands. Anything else: the
    candidate degrades to ERROR, which reports as unchecked rather than as
    a broken link (ticket #82's third acceptance row, and the one that
    would otherwise turn one unreachable instance into a document full of
    errors under ticket #64).
    """
    pending = {
        href: outcome
        for href, outcome in probed.items()
        if outcome.pop("needs_control", False)
    }
    if not pending:
        return

    # Sorted, so the pairing below is over a stable sequence rather than
    # a set iterated twice.
    controls = sorted(
        {
            url
            for url in (_control_url(href) for href in pending)
            if url is not None
        }
    )
    healthy: dict[str, bool] = {}
    if controls:
        with ThreadPoolExecutor(
            max_workers=max(1, min(concurrency, len(controls)))
        ) as pool:
            healthy = dict(
                zip(
                    controls,
                    pool.map(
                        lambda url: _instance_answers(
                            client, url, timeout, scope
                        ),
                        controls,
                    ),
                    strict=True,
                )
            )

    for href, outcome in pending.items():
        control = _control_url(href)
        if control is None or not healthy.get(control, False):
            outcome["status"] = ERROR
            outcome["resolved_url"] = None


def probe_targets(
    client: TracClient,
    hrefs: list[str],
    cap: int = DEFAULT_TARGET_CAP,
    timeout: float = DEFAULT_TARGET_TIMEOUT_SECONDS,
    concurrency: int = DEFAULT_TARGET_CONCURRENCY,
) -> dict[str, dict]:
    """Fetch each unique, probeable href and classify it.

    Args:
        client: The TracClient for the instance being previewed against --
            its session carries the auth this probe reuses (the dispatcher
            requires the same credentials as any other Trac request).
        hrefs: Candidate hrefs (may include duplicates and non-probeable
            ones; both are filtered here).
        cap: Maximum number of unique probeable hrefs to actually fetch.
            Anything beyond the cap is reported ``SKIPPED``, never silently
            dropped -- a capped check must never look like a clean pass.
            The cap keeps the FIRST ``cap`` hrefs in document order, so
            what it drops is the END of the document; see the module
            docstring on why that matters and why 50 rather than 10.
        timeout: Per-request timeout in seconds.
        concurrency: Maximum simultaneous fetches (ticket #80). The
            liveness controls (ticket #82) are extra requests beyond the
            cap -- at most one per distinct instance, and only when a
            ticket-realm target on it came back 500.

    Returns:
        ``{href: {"status": EXISTS | MISSING | SKIPPED | ERROR,
        "resolved_url": str | None}}`` for every unique probeable href
        in ``hrefs`` (non-probeable hrefs are simply omitted, since the
        caller has nothing to report for them). A ``SKIPPED`` href whose
        instance is outside ``client.config.allowed_instances`` also
        carries ``"reason": "instance_not_allowed"`` (ticket #110): it
        was never fetched -- or, if its redirect chain left the allowlist,
        that hop was never requested -- and a refusal up front did not
        count against ``cap``. ``resolved_url`` is the URL the
        dispatcher actually redirected to (``response.url`` after
        following redirects) when a fetch happened, else None -- this is
        what lets a caller report the real target of an ``/intertrac/
        %23N``-shaped dispatcher link instead of the dispatcher href
        itself.

        Keyed in document order regardless of the order results arrive
        in. Nothing downstream depends on that -- ``_check_target_probes``
        iterates ``facts.anchors`` and looks each href up -- but a probe
        whose output order varies run to run would make every diff of a
        warnings list unreadable.
    """
    # Ticket #110. A bare MagicMock client (many tests) has a MagicMock
    # `allowed_instances`, and a hand-rolled one may have no `config` at
    # all, so only a real frozenset counts as a scope.
    allowed = getattr(
        getattr(client, "config", None), "allowed_instances", None
    )
    # Prepared once per run, and confined to this client's own host.
    scope = (
        scope_bases(allowed, client.config.trac_url)
        if isinstance(allowed, frozenset)
        else None
    )

    unique: list[str] = []
    refused: set[str] = set()
    seen: set[str] = set()
    for href in hrefs:
        if not is_probeable_href(href) or href in seen:
            continue
        seen.add(href)
        unique.append(href)
        # Judged where the request would actually go, not on the href as
        # written; another host is never in scope. Redirect hops are
        # checked again in `_get`.
        if scope is not None and not in_scope(href, scope):
            refused.add(href)

    # Filtered BEFORE the cap, so refused hrefs never use up its slots.
    to_probe = [h for h in unique if h not in refused][:cap]
    probed: dict[str, dict] = {}
    if to_probe:
        with ThreadPoolExecutor(
            max_workers=max(1, min(concurrency, len(to_probe)))
        ) as pool:
            futures = {
                pool.submit(
                    _probe_one, client, href, timeout, scope
                ): href
                for href in to_probe
            }
            for future in as_completed(futures):
                probed[futures[future]] = future.result()

    _confirm_provisional(client, probed, timeout, concurrency, scope)

    return {
        href: (
            {
                "status": SKIPPED,
                "resolved_url": None,
                "reason": NOT_ALLOWED_REASON,
            }
            if href in refused
            else probed.get(
                href, {"status": SKIPPED, "resolved_url": None}
            )
        )
        for href in unique
    }
