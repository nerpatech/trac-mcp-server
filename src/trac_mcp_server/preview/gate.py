"""Blocking policy for the write-time link gate (ticket #64).

``build_warnings`` says what is wrong with a candidate. This module says
which of those findings are allowed to *refuse a write*, and how to tell
the author what to do about it. It is deliberately pure -- no client, no
network, no I/O -- so every row of #64's severity table is decided
offline, in a unit test, without a server.

**Severity IS the blocking column.** Ticket #64 section 4 is written as a
severity table (``error -- blocks the write`` against ``warning --
reported, does not block``) and the operator approved it as written, so
``severity == "error"`` is the mechanism rather than a second parallel
notion layered on top of it. A code cannot be an error and non-blocking,
or a warning and blocking; there is one fact and one place it lives.

``BLOCKING_CODES`` is therefore an *inventory*, not the mechanism. It
exists so a new check cannot join the blocking column silently: the
severity a rule passes to ``_warning`` is easy to change in isolation and
hard to notice in review, and promoting a code to blocking is a decision
somebody has to make on purpose. ``test_preview_gate.py`` asserts the two agree
across the whole fixture corpus, in both directions, so this list going
stale is a test failure rather than a surprise refusal in production.

Three codes here were settled by ruling rather than by inference, and are
recorded next to the entries themselves so the argument does not have to
be reconstructed from the ticket later.
"""

from typing import Any

#: Codes whose presence refuses a write. Keep in step with the severity
#: each rule emits -- `test_blocking_codes_match_severity` enforces it.
BLOCKING_CODES = frozenset(
    {
        # Ticket #64 section 4, approved in that ticket's comment 1.
        "missing_local_target",
        "intertrac_target_captured_punctuation",
        "missing_cross_instance_target",
        # Section 4 too, but PROMOTED from `warning` by #64: the table
        # puts it in the error column and the code did not agree. It
        # only became reachable before a write at all when ticket #77
        # moved it out of the verify-only path.
        "literal_markup_in_render",
        # Ruling 1 on #64 comment 7. Section 4's table does not name it;
        # it is provable breakage of exactly the kind that column
        # describes -- a `%21` in a stored target resolves to a dead
        # page -- and it is this suite's original seeded defect, so it
        # arrives with a watched failure already in the corpus.
        "escaped_link_target",
        # Ruling 2, confirming what ticket #83 had already written into
        # `_check_target_probes`. Promoted from `info`: the document is
        # denser than the probe cap and the AUTHOR can act (raise
        # target_cap, or split the document). Its two siblings stay
        # advisory and are listed below with the reason.
        "target_check_capped",
        # Not reachable on an inline write path -- TracWiki-declared
        # input skips it, and since ticket #69 every inline write path
        # is TracWiki. It is reachable on `wiki_file_push`, which
        # converts, and which already refuses on it by its own route
        # (ticket #68). Blocking is what that path already does; listing
        # it here makes the two agree instead of merely coinciding.
        "code_block_indentation_loss",
    }
)

#: Advisory codes, listed for the same reason as the blocking ones: so
#: the split is reviewable in one place. Not consulted at runtime.
#:
#: * `bare_ticket_ref` -- fired 10 times on one correct ticket
#:   (auto_pm:#89); blocking it would refuse essentially every ticket
#:   that cites another ticket.
#: * `link_ref_in_code_span` -- DEMOTED from `error` by #64: section 4
#:   puts it advisory, and a page documenting link syntax is its
#:   legitimate population.
#: * `unconfigured_intertrac_prefix` -- a prefix quoted deliberately.
#: * `incidental_wiki_autolink` -- ticket #79's whole subject: 110
#:   findings that were correct documents, now reported without
#:   refusing.
#: * `tracwiki_markup_in_markdown`, `conversion_warning` -- Markdown-path
#:   findings, advisory in section 4's table.
#: * `target_check_failed` -- the CHECKER could not do its job and the
#:   author can do nothing about it; blocking would stop every write on
#:   the store while a remote instance is down.
#: * `target_check_disabled` -- nobody asked for the check.
ADVISORY_CODES = frozenset(
    {
        "bare_ticket_ref",
        "link_ref_in_code_span",
        "unconfigured_intertrac_prefix",
        "incidental_wiki_autolink",
        "tracwiki_markup_in_markdown",
        "conversion_warning",
        "target_check_failed",
        "target_check_disabled",
    }
)

#: How an author gets a legitimately broken-looking document through the
#: gate (ticket #64 section 6, the #58 pragma unchanged). Scoped to the
#: codes it names -- never a document-wide mute -- and placed inside a
#: Trac comment block, which renders as nothing at all.
#:
#: The delimiters are assembled rather than interpolated: `str.format`
#: reads `{{{` as an escape and raises on this exact string, which is
#: the sort of thing that is obvious once seen and invisible in review.
_PRAGMA_OPEN = "{{{#!comment"
_PRAGMA_CLOSE = "}}}"


def is_blocking(warning: dict) -> bool:
    """Whether this one finding refuses the write.

    Reads ``severity``, which is the single source of truth -- see the
    module docstring on why this is not a ``BLOCKING_CODES`` membership
    test. A warning with no severity is treated as non-blocking: a
    malformed finding must not be able to refuse a write.
    """
    return warning.get("severity") == "error"


def classify(warnings: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split findings into ``(blocking, advisory)``, order preserved.

    Order matters: the refusal message lists findings in document order,
    which is the order the author reads their own text in.
    """
    blocking = [w for w in warnings if is_blocking(w)]
    advisory = [w for w in warnings if not is_blocking(w)]
    return blocking, advisory


def suggestion_for(warning: dict) -> str | None:
    """The corrected text for a finding, or None if there is none.

    Suggestions live in ``evidence["suggestion"]`` -- the location
    ticket #79 already established for `incidental_wiki_autolink`, and
    which `render_check`'s tests already read -- rather than in a new
    top-level field that would leave the same idea in two shapes.
    """
    evidence = warning.get("evidence")
    if not isinstance(evidence, dict):
        return None
    suggestion = evidence.get("suggestion")
    return suggestion if isinstance(suggestion, str) else None


def format_refusal(
    blocking: list[dict], advisory: list[dict], *, field: str
) -> str:
    """The message a refused write returns.

    Ticket #64 section 5's argument, applied to the whole response
    rather than to one check: emit the corrected string, not just a
    diagnosis. #59 comment 1 is the precedent -- a check reported real
    breakage with the WRONG diagnosis, and acting on the message as
    written would have sent someone to the wrong place. A refusal that
    shows the corrected text cannot mislead that way.

    Args:
        blocking: Findings that refused the write. Must be non-empty --
            this is the refusal path.
        advisory: Findings reported alongside, which did not refuse.
            Included because the author is editing this text right now
            and will not get a cheaper chance to see them.
        field: Which field was refused (``description``, ``comment``,
            ``content``), so a multi-field write says which half failed.
    """
    plural = "s" if len(blocking) != 1 else ""
    lines = [
        f"Refusing to write {field}: "
        f"{len(blocking)} blocking link error{plural}.",
        "",
    ]
    for warning in blocking:
        lines.append(f"- [{warning['code']}] {warning['message']}")
        suggestion = suggestion_for(warning)
        if suggestion:
            lines.append(f"  Write instead: {suggestion}")

    if advisory:
        lines.append("")
        lines.append(f"Also reported, not blocking ({len(advisory)}):")
        for warning in advisory:
            lines.append(f"- [{warning['code']}] {warning['message']}")

    lines.append("")
    lines.extend(_refusal_hint(blocking))
    return "\n".join(lines)


def _refusal_hint(blocking: list[dict]) -> list[str]:
    """The "how to proceed" tail, naming the codes actually hit.

    Scoped to the codes in front of the author rather than a generic
    pointer: the pragma is per-code by design, and a hint that named it
    generically would invite the document-wide mute #58 refused to
    build.
    """
    codes = ",".join(sorted({w["code"] for w in blocking}))
    return [
        "Fix the link, or -- if this document quotes broken-looking "
        "syntax deliberately -- opt out of the specific code by "
        "putting this in the source:",
        "",
        _PRAGMA_OPEN,
        f"preview-checks: allow {codes}",
        _PRAGMA_CLOSE,
    ]


def corrective_action(blocking: list[dict]) -> str:
    """One-line corrective action for the structured error response.

    Separate from `format_refusal` because `build_error_response` takes
    the diagnosis and the action as two arguments and renders them under
    their own headings.
    """
    suggestions = [
        suggestion_for(w) for w in blocking if suggestion_for(w)
    ]
    if suggestions:
        first: Any = suggestions[0]
        return (
            f"Apply the suggested correction (e.g. `{first}`) and "
            "retry the write."
        )
    return (
        "Correct the link and retry the write, or add a "
        "'preview-checks: allow <code>' pragma if the content is "
        "deliberate."
    )
