"""The write-time link gate (ticket #64).

``convert_preview`` is a pre-write check nobody has to call, and
``ticket_render_check``/``wiki_render_check`` are post-write checks that
report after the broken link is published -- and on this host a ticket
comment cannot be edited afterwards (ticket #38), so the correction
becomes a second comment. Measured over one day of writing on the
auto_pm store, that is not a theoretical gap: auto_pm:#89 records six
different link forms for one job, most of them wrong, all written by an
agent that had the checking tools available.

So this module runs the checks on the write itself, and refuses when a
blocking finding is present. One helper, called from every write path.
**Not a check re-implemented per handler**: two call sites for one check
is exactly how ticket #77's blind spot arose, where the pre-write gate
was blind to a code the post-write one ran.

The policy lives in ``preview.gate`` and is pure. This module is the
part that cannot be -- it renders the candidate through Trac and probes
cross-instance targets -- and it holds nothing else, so which findings
refuse a write stays decidable offline.

Two deliberate asymmetries, both of the same kind: a checker that could
not do its job must never be able to *pass* content silently, and must
never charge the author for its own failure.

``check_targets`` is not a parameter here.
    ``convert_preview`` has it because previewing is voluntary. On a
    write it would be an off switch for the cross-instance half of a
    blocking gate, per ticket #64 ruling 3. ``target_cap`` IS exposed,
    because raising it is strictly *more* checking and is the only way
    out of a ``target_check_capped`` refusal.

A render that fails does not refuse the write.
    If ``wiki_to_html`` raises, the gate could not run at all. Refusing
    would make every write on the store fail whenever the renderer
    hiccups, for a fault the author did not cause -- the same argument
    that keeps ``target_check_failed`` advisory. The write proceeds and
    the response says, in the affirmative, that the checks did not run:
    silence would read as "checked and clean", which is the failure
    ticket #64 section 3 refuses to ship.
"""

import logging

import mcp.types as types

from ...core.async_utils import run_sync
from ...core.client import TracClient
from ...preview.checks import build_warnings
from ...preview.facts import extract_facts
from ...preview.gate import (
    classify,
    corrective_action,
    format_refusal,
)
from ...preview.targets import (
    DEFAULT_TARGET_CAP,
    is_probeable_href,
    probe_targets,
)
from .errors import build_error_response
from .instances import local_intertrac_bases, own_intertrac_prefix

logger = logging.getLogger(__name__)

#: Schema fragment for the one probe parameter a write path exposes.
#: Shared so the nine call sites cannot drift into describing it
#: differently -- the same reason ``source_format`` is one module.
TARGET_CAP_SCHEMA = {
    "type": "integer",
    "description": (
        "Maximum cross-instance targets the write-time link check "
        "probes (default: 50). Anything beyond the cap is reported as "
        "target_check_capped, which BLOCKS the write -- an unchecked "
        "target must not read as a clean one. Raise this for a "
        "legitimately link-dense document; there is deliberately no "
        "way to switch the check off."
    ),
    "default": DEFAULT_TARGET_CAP,
    "minimum": 1,
    "maximum": 500,
}


class GateOutcome:
    """What the gate decided about one field.

    Carries the advisory findings even when the write is allowed: the
    author is editing this text right now and will not get a cheaper
    chance to see them than the response to their own write.
    """

    def __init__(
        self,
        refusal: types.CallToolResult | None,
        advisory: list[dict],
        checked: bool,
        note: str | None = None,
        refusal_text: str = "",
    ):
        self.refusal = refusal
        self.advisory = advisory
        self.checked = checked
        self.note = note
        #: The same refusal as plain text. The batch tools report a
        #: refused item in their own per-item `error` field rather than
        #: returning a CallToolResult, so they need the message without
        #: the envelope.
        self.refusal_text = refusal_text

    @property
    def refused(self) -> bool:
        return self.refusal is not None

    def summary_lines(self) -> list[str]:
        """Lines a successful write appends to its own response."""
        lines = []
        if self.note:
            lines.append(self.note)
        for warning in self.advisory:
            lines.append(
                f"- [{warning['severity']}] {warning['code']}: "
                f"{warning['message']}"
            )
        return lines


async def _run_checks(
    client: TracClient,
    content: str,
    target_cap: int,
    known_comment_numbers: frozenset[int] | None,
) -> list[dict]:
    """Render, probe, and assemble findings. Every step that can touch
    the network or the parser lives here, so `check_write`'s one guard
    covers all of it -- a guard around only the render would leave
    extraction and probing able to raise past it, which is the same
    partial-coverage mistake in miniature."""
    rendered_html = await run_sync(client.wiki_to_html, content)
    facts = extract_facts(rendered_html)

    probes: dict[str, dict] = {}
    probeable = [
        a.href for a in facts.anchors if is_probeable_href(a.href)
    ]
    if probeable:
        probes = await run_sync(
            probe_targets, client, probeable, target_cap
        )

    return build_warnings(
        markdown_source=None,
        tracwiki=content,
        facts=facts,
        probes=probes,
        # Always. There is no way to ask a write path not to check --
        # ticket #64 ruling 3.
        check_targets=True,
        source_format="tracwiki",
        local_intertrac_bases=local_intertrac_bases(),
        own_prefix=own_intertrac_prefix(client),
        known_comment_numbers=known_comment_numbers,
    )


async def check_write(
    client: TracClient,
    content: str | None,
    *,
    field: str,
    recheck_with: str | None,
    target_cap: int = DEFAULT_TARGET_CAP,
    known_comment_numbers: frozenset[int] | None = None,
) -> GateOutcome:
    """Run the link checks on one field of a pending write.

    Args:
        client: Used to render the candidate and probe targets. The only
            reason this function is not pure.
        content: The candidate text. ``None`` or empty is not a finding
            -- a write that carries no text for this field simply has
            nothing to check.
        field: Which field is being written (``description``,
            ``comment``, ``content``), so a multi-field write's refusal
            says which half failed.
        recheck_with: The tool that re-runs these checks on this
            content after the fact, named in the note the render-failed
            path emits, or ``None`` when no tool covers this surface.
            Keyword-only and mandatory on purpose: this used to be
            inferred from ``field`` (``content`` -> wiki, anything else
            -> ticket), which silently produced ``ticket_render_check``
            for the milestone description ticket #87 added -- a tool
            that cannot see a milestone. An inference that is right for
            every caller that exists is still wrong for the next one,
            and the note it lands in is the one place a wrong answer
            costs the most: see the module docstring on why a check
            that could not run must not read as a check that passed.
        target_cap: Maximum cross-instance targets to probe.
        known_comment_numbers: Ticket #105. The comment numbers that
            exist on the ticket this write targets, plus the number
            this write's own comment will become, for
            ``dangling_comment_ref``. ``None`` (the default) when there
            is no ticket to check against (a wiki/milestone write) or
            the caller hasn't computed it -- that check simply does not
            run.

    Returns:
        A :class:`GateOutcome`. ``refusal`` is non-None exactly when a
        blocking finding was present.
    """
    if not content:
        return GateOutcome(None, [], checked=False)

    try:
        warnings = await _run_checks(
            client, content, target_cap, known_comment_numbers
        )
    except Exception as exc:
        # The gate could not do its job. See the module docstring: a
        # failure HERE is not the author's fault, and refusing would
        # take every write on the store down with whatever broke. Say
        # so out loud instead -- silence would read as "checked and
        # clean", which is the shape ticket #64 section 3 refuses.
        logger.warning(
            "write gate could not check %s: %s: %s",
            field,
            type(exc).__name__,
            exc,
        )
        recheck = (
            f"Re-check it with {recheck_with}."
            if recheck_with
            else (
                "No render-check tool covers this field, so the only "
                "way to re-check it is to read the rendered page."
            )
        )
        return GateOutcome(
            None,
            [],
            checked=False,
            note=(
                f"NOTE: the link check could not run on {field} "
                f"({type(exc).__name__}), so this content is "
                f"UNCHECKED -- not verified clean. {recheck}"
            ),
        )

    blocking, advisory = classify(warnings)
    if not blocking:
        return GateOutcome(None, advisory, checked=True)

    message = format_refusal(blocking, advisory, field=field)
    return GateOutcome(
        build_error_response(
            "link_check_failed", message, corrective_action(blocking)
        ),
        advisory,
        checked=True,
        refusal_text=message,
    )


def gate_enabled(client: TracClient) -> bool:
    """Whether the write gate runs at all (ticket #64 ruling 5).

    This lands on a daemon every project on this host writes through, so
    a gate misbehaving in production needs an answer cheaper than a
    revert and a redeploy. Default on: the switch exists to be turned
    off in an emergency, not to be opted into.

    Read through ``getattr`` so a ``Config`` built by an older caller --
    or by a test that constructs one positionally -- keeps the default
    rather than raising.
    """
    return bool(getattr(client.config, "write_gate", True))


async def _known_comment_numbers(
    client: TracClient, ticket_id: int
) -> frozenset[int] | None:
    """Ticket #105. The comment numbers ``ticket_id`` has right now,
    plus the number this write's own comment will become.

    Every ``ticket.update`` call -- even a comment-less field edit --
    consumes the next number in sequence (``ticket_comment.py``'s
    module docstring: "description edits consume comment numbers
    without producing a comment"), so the highest number ANY changelog
    entry with ``field == "comment"`` carries, plus one, is what this
    write's own comment will become if it posts one. A comment number
    only counts as an EXISTING comment when that entry's ``newvalue``
    is non-empty -- the same population ``ticket_read.py``'s
    ``_extract_comments`` shows a caller, not duplicated from there
    since that helper returns display-ready dicts, not a bare set of
    ints this module can compare against.

    ``None`` if the changelog can't be fetched: fail OPEN, same as
    every other checker-failure path in this module -- a ticket the
    author cannot currently read is not the author's fault, and this
    check simply does not run rather than refusing on a guess.
    """
    try:
        changelog = await run_sync(
            client.get_ticket_changelog, ticket_id
        )
    except Exception:
        return None

    known: set[int] = set()
    max_cnum = 0
    for entry in changelog or []:
        if not isinstance(entry, (list, tuple)) or len(entry) < 5:
            continue
        _timestamp, _author, field, oldvalue, newvalue = entry[:5]
        if field != "comment":
            continue
        try:
            cnum = int(str(oldvalue).strip())
        except (TypeError, ValueError):
            continue
        max_cnum = max(max_cnum, cnum)
        if newvalue:
            known.add(cnum)
    known.add(max_cnum + 1)
    return frozenset(known)


async def gate_or_refuse(
    client: TracClient,
    fields: dict[str, str | None],
    args: dict,
    *,
    recheck_with: str | None,
    ticket_id: int | None = None,
) -> tuple[types.CallToolResult | None, list[str]]:
    """Gate several fields of one write, refusing on the first failure.

    The common shape for a handler: pass the fields this call is
    writing, get back either a refusal to return immediately, or the
    advisory lines to append to a successful response.

    Fields are checked in the order given, and the FIRST refusal wins
    rather than all of them being collected. Each check costs a render
    round trip, and an author who must fix the description will re-send
    the comment with it anyway.

    ``recheck_with`` names the after-the-fact checker for this handler's
    surface -- one value per handler rather than per field, because the
    tool that re-checks a write is a property of what was written to,
    not of which field of it. See :func:`check_write` on why it is not
    inferred.

    ``ticket_id``, ticket #105: pass the ticket THIS write targets so
    ``dangling_comment_ref`` can run -- omitted by every caller with no
    ticket to check against (wiki, milestone) or no ticket yet
    (``ticket_create``). Fetching the changelog is skipped unless a
    field actually contains the substring ``comment:``, so an ordinary
    write pays no extra round trip for a check that could not fire.
    """
    if not gate_enabled(client):
        return None, []

    target_cap = args.get("target_cap", DEFAULT_TARGET_CAP)
    known_comment_numbers: frozenset[int] | None = None
    if ticket_id is not None and any(
        content and "comment:" in content for content in fields.values()
    ):
        known_comment_numbers = await _known_comment_numbers(
            client, ticket_id
        )

    lines: list[str] = []
    for field, content in fields.items():
        outcome = await check_write(
            client,
            content,
            field=field,
            recheck_with=recheck_with,
            target_cap=target_cap,
            known_comment_numbers=known_comment_numbers,
        )
        if outcome.refused:
            return outcome.refusal, []
        lines.extend(outcome.summary_lines())
    return None, lines
