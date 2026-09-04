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

logger = logging.getLogger(__name__)

#: Schema fragment for the one probe parameter a write path exposes.
#: Shared so the seven call sites cannot drift into describing it
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
    client: TracClient, content: str, target_cap: int
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
    )


async def check_write(
    client: TracClient,
    content: str | None,
    *,
    field: str,
    target_cap: int = DEFAULT_TARGET_CAP,
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
        target_cap: Maximum cross-instance targets to probe.

    Returns:
        A :class:`GateOutcome`. ``refusal`` is non-None exactly when a
        blocking finding was present.
    """
    if not content:
        return GateOutcome(None, [], checked=False)

    try:
        warnings = await _run_checks(client, content, target_cap)
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
        return GateOutcome(
            None,
            [],
            checked=False,
            note=(
                f"NOTE: the link check could not run on {field} "
                f"({type(exc).__name__}), so this content is "
                "UNCHECKED -- not verified clean. Re-check it with "
                f"{'wiki' if field == 'content' else 'ticket'}"
                "_render_check."
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


async def gate_or_refuse(
    client: TracClient,
    fields: dict[str, str | None],
    args: dict,
) -> tuple[types.CallToolResult | None, list[str]]:
    """Gate several fields of one write, refusing on the first failure.

    The common shape for a handler: pass the fields this call is
    writing, get back either a refusal to return immediately, or the
    advisory lines to append to a successful response.

    Fields are checked in the order given, and the FIRST refusal wins
    rather than all of them being collected. Each check costs a render
    round trip, and an author who must fix the description will re-send
    the comment with it anyway.
    """
    if not gate_enabled(client):
        return None, []

    target_cap = args.get("target_cap", DEFAULT_TARGET_CAP)
    lines: list[str] = []
    for field, content in fields.items():
        outcome = await check_write(
            client, content, field=field, target_cap=target_cap
        )
        if outcome.refused:
            return outcome.refusal, []
        lines.extend(outcome.summary_lines())
    return None, lines
