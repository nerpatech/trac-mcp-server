"""Capped live probe for cross-instance InterTrac wiki targets.

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

Scoped to InterTrac *wiki* dispatcher hrefs only (``/intertrac/wiki%3A...``)
-- the only shape ticket #56's suite requires. A ticket-realm InterTrac
link (``auto_pm:#87``) redirects to a ticket page, which fails differently
(a distinct not-found template) and isn't covered here.
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

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


def is_probeable_wiki_href(href: str | None) -> bool:
    """Whether ``href`` is an InterTrac wiki-dispatcher link this module
    can classify."""
    return bool(href) and bool(_INTERTRAC_WIKI_HREF_RE.search(href))


def _probe_one(client: TracClient, href: str, timeout: float) -> dict:
    """Fetch and classify one dispatcher href.

    ``client.session`` is read HERE, inside the worker, and that is
    load-bearing rather than incidental: ``TracClient`` hands out a
    thread-local session, so hoisting it into the dispatching thread --
    which the sequential implementation did, harmlessly -- would hand
    every worker one thread's session and quietly defeat that design.
    """
    try:
        response = client.session.get(
            href, timeout=timeout, allow_redirects=True
        )
    except Exception:
        return {"status": ERROR, "resolved_url": None}
    if response.status_code != 200:
        return {"status": ERROR, "resolved_url": None}
    status = (
        MISSING
        if _MISSING_WIKI_PAGE_RE.search(response.text)
        else EXISTS
    )
    return {"status": status, "resolved_url": response.url}


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
        concurrency: Maximum simultaneous fetches (ticket #80).

    Returns:
        ``{href: {"status": EXISTS | MISSING | SKIPPED | ERROR,
        "resolved_url": str | None}}`` for every unique probeable href in
        ``hrefs`` (non-probeable hrefs are simply omitted, since the
        caller has nothing to report for them). ``resolved_url`` is the
        URL the dispatcher actually redirected to (``response.url`` after
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
    unique: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        if not is_probeable_wiki_href(href) or href in seen:
            continue
        seen.add(href)
        unique.append(href)

    to_probe = unique[:cap]
    probed: dict[str, dict] = {}
    if to_probe:
        with ThreadPoolExecutor(
            max_workers=max(1, min(concurrency, len(to_probe)))
        ) as pool:
            futures = {
                pool.submit(_probe_one, client, href, timeout): href
                for href in to_probe
            }
            for future in as_completed(futures):
                probed[futures[future]] = future.result()

    return {
        href: probed.get(
            href, {"status": SKIPPED, "resolved_url": None}
        )
        for href in unique
    }
