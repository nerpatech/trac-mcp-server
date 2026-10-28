"""Fetching and scoping live-rendered ticket/wiki pages (ticket #55).

No MCP knowledge -- these two functions turn a live Trac page into
:class:`RenderedSection` objects that ``extract_facts``/``build_verify_
warnings`` can consume unchanged, the same way ``convert_preview`` feeds
them a dry-run render (ticket #56).
"""

import re
from dataclasses import dataclass

from lxml import html as lxml_html

from ..core.client import TracClient

# Trac's own template ids/classes for the parts of a ticket page this
# module scopes to. Confirmed live against tickets #55 and #57
# (2026-09-01): the description's outer wrapper is `<div class=
# "description">`, its actual text is the nested `.searchable` div; each
# changelog entry (field change OR comment) is a `<div class="change"
# id="trac-change-<N>-<ts>">`, and only entries that carry a comment also
# have a nested `.comment` div -- a field-only change (e.g. "status
# changed") has none, and is skipped rather than reported as an empty
# section.
_DESCRIPTION_XPATH = (
    '//div[@class="description"]//div[contains(@class,"searchable")]'
)
_CHANGE_DIV_XPATH = '//div[contains(@class,"change")][@id]'
_COMMENT_IN_CHANGE_XPATH = './/div[contains(@class,"comment")]'
_CHANGE_ID_RE = re.compile(r"^trac-change-(\d+)-")


class RenderCheckError(Exception):
    """Raised when a live page can't be scoped the way this module
    expects -- e.g. the description selector matches nothing, which
    means Trac's ticket template changed, not that the ticket is
    unusually empty. Callers must surface this as an error, never as a
    quiet zero-findings result."""


class TicketNotFoundError(RenderCheckError):
    """The ticket page itself returned HTTP 404 -- distinct from
    :class:`RenderCheckError`'s general "the template doesn't look like
    what we expect" case, so a caller can map it to a friendlier
    not-found response instead of a generic server error."""


@dataclass(frozen=True, slots=True)
class RenderedSection:
    """One scoped, absolutized piece of a live-rendered page.

    Attributes:
        kind: ``"description"``, ``"comment"``, or ``"page"``.
        ref: The comment number (as a string) for a ``"comment"``
            section, else None.
        html: The section's HTML, with every ``<a href>`` rewritten to an
            absolute URL (root-relative hrefs come back from Trac as-is,
            e.g. ``/trac_mcp_server/ticket/27``, which ``probe_targets``
            and any caller resolving the URL can't use directly).
        tracwiki: The stored TracWiki source paired with this render, or
            None if no source could be paired (see ``source_paired``).
        source_paired: Whether ``tracwiki`` is real paired source. False
            means the source-driven checks (the InterTrac-prefix rule)
            were skipped for this section -- the caller must report that
            skip explicitly rather than let an unrun check read as a
            pass (``Rules/testing/InstrumentDontInfer``).
    """

    kind: str
    ref: str | None
    html: str
    tracwiki: str | None
    source_paired: bool


def _absolutize(element, base_url: str) -> str:
    """Rewrite every ``<a href>`` under ``element`` to an absolute URL
    (in place) and return its serialized HTML."""
    element.make_links_absolute(base_url)
    return str(lxml_html.tostring(element, encoding="unicode"))


def fetch_wiki_render(
    client: TracClient, page_name: str, version: int | None = None
) -> RenderedSection:
    """Fetch a wiki page's live render, paired with its stored source.

    Args:
        client: TracClient for the instance to fetch from.
        page_name: Wiki page name.
        version: Optional specific version (default: latest).

    Returns:
        A single ``"page"`` :class:`RenderedSection`. ``wiki.getPageHTML``
        returns a clean ``<html><body>...</body></html>`` fragment with no
        chrome and already-absolute external hrefs, so no scoping is
        needed here -- unlike a ticket page.
    """
    html = client.get_wiki_page_html(page_name, version)
    tracwiki = client.get_wiki_page(page_name, version)
    tree = lxml_html.fromstring(html)
    absolutized = _absolutize(tree, client.config.trac_url)
    return RenderedSection(
        kind="page",
        ref=None,
        html=absolutized,
        tracwiki=tracwiki,
        source_paired=True,
    )


def fetch_ticket_sections(
    client: TracClient, ticket_id: int, include_comments: bool = True
) -> list[RenderedSection]:
    """Fetch a ticket's live render, scoped and paired section by section.

    Args:
        client: TracClient for the instance to fetch from.
        ticket_id: Ticket number.
        include_comments: Whether to also scope and pair each comment.

    Returns:
        One ``"description"`` section, followed by one ``"comment"``
        section per changelog entry that actually carries comment text
        (a field-only change, e.g. "status changed", has no comment div
        and is skipped) -- only when ``include_comments`` is True.

    Raises:
        RenderCheckError: If the description selector matches nothing --
            a Trac template change, not a legitimately empty ticket
            (Trac always renders a description div, even for one that's
            blank).
    """
    base_url = client.config.trac_url.rstrip("/")
    response = client.session.get(f"{base_url}/ticket/{ticket_id}")
    if response.status_code == 404:
        raise TicketNotFoundError(f"Ticket #{ticket_id} not found.")
    response.raise_for_status()
    tree = lxml_html.fromstring(response.text)

    desc_nodes = tree.xpath(_DESCRIPTION_XPATH)
    if not desc_nodes:
        raise RenderCheckError(
            f"Could not locate the description in ticket #{ticket_id}'s "
            "rendered page (selector matched nothing) -- Trac's ticket "
            "template may have changed."
        )

    ticket_data = client.get_ticket(ticket_id)
    description_source = ticket_data[3].get("description", "")

    sections = [
        RenderedSection(
            kind="description",
            ref=None,
            html=_absolutize(desc_nodes[0], base_url),
            tracwiki=description_source,
            source_paired=True,
        )
    ]

    if not include_comments:
        return sections

    comment_source_by_number: dict[str, str] = {}
    for entry in client.get_ticket_changelog(ticket_id):
        field, oldvalue, newvalue = entry[2], entry[3], entry[4]
        if field == "comment" and newvalue:
            comment_source_by_number[str(oldvalue)] = newvalue

    for change_div in tree.xpath(_CHANGE_DIV_XPATH):
        match = _CHANGE_ID_RE.match(change_div.get("id") or "")
        if not match:
            continue
        comment_number = match.group(1)

        comment_nodes = change_div.xpath(_COMMENT_IN_CHANGE_XPATH)
        if not comment_nodes:
            # A field-only change (status/owner/etc) -- no comment text
            # was authored here, nothing to render-check.
            continue

        source = comment_source_by_number.get(comment_number)
        sections.append(
            RenderedSection(
                kind="comment",
                ref=comment_number,
                html=_absolutize(comment_nodes[0], base_url),
                tracwiki=source,
                source_paired=source is not None,
            )
        )

    return sections
