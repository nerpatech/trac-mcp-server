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

DEFAULT_TARGET_CAP = 10
DEFAULT_TARGET_TIMEOUT_SECONDS = 5.0

# Probe outcomes.
EXISTS = "exists"
MISSING = "missing"
SKIPPED = "skipped"
ERROR = "error"


def is_probeable_wiki_href(href: str | None) -> bool:
    """Whether ``href`` is an InterTrac wiki-dispatcher link this module
    can classify."""
    return bool(href) and bool(_INTERTRAC_WIKI_HREF_RE.search(href))


def probe_targets(
    client: TracClient,
    hrefs: list[str],
    cap: int = DEFAULT_TARGET_CAP,
    timeout: float = DEFAULT_TARGET_TIMEOUT_SECONDS,
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
        timeout: Per-request timeout in seconds.

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
    """
    unique: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        if not is_probeable_wiki_href(href) or href in seen:
            continue
        seen.add(href)
        unique.append(href)

    results: dict[str, dict] = {}
    session = client.session
    for index, href in enumerate(unique):
        if index >= cap:
            results[href] = {"status": SKIPPED, "resolved_url": None}
            continue
        try:
            response = session.get(
                href, timeout=timeout, allow_redirects=True
            )
        except Exception:
            results[href] = {"status": ERROR, "resolved_url": None}
            continue
        if response.status_code != 200:
            results[href] = {"status": ERROR, "resolved_url": None}
            continue
        status = (
            MISSING
            if _MISSING_WIKI_PAGE_RE.search(response.text)
            else EXISTS
        )
        results[href] = {"status": status, "resolved_url": response.url}
    return results
