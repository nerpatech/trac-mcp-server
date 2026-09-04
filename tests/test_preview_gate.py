"""Blocking policy for the write-time gate (ticket #64).

Two kinds of test here, and the second is the one that matters.

The first kind is ordinary: `classify` splits a list, `format_refusal`
renders a message. Cheap, and it would pass on a policy that was
completely wrong about which codes block.

The second kind is the **recall gate** ticket #64 section 8 asks for,
and the reason it exists is specific: this change RE-CLASSIFIES codes,
and re-classification is precisely where a check quietly stops firing
(the repeated finding on auto_pm:#74 and auto_pm:#71). Three codes moved
column here -- `link_ref_in_code_span` down, `literal_markup_in_render`
and `target_check_capped` up -- and when they were moved, the entire
offline suite stayed green, because **no test asserted any code's
severity at all**. A blocking gate keyed on severity over an untested
severity field is a gate whose behaviour nobody has pinned.

So `test_blocking_codes_match_severity_across_the_corpus` replays every
fixture row and asserts, per finding, that `severity == "error"` agrees
with `BLOCKING_CODES` membership. A future check that emits a new code,
or an edit that flips a severity, fails here rather than silently
changing what refuses a write in production.
"""

import json
from pathlib import Path

import pytest

from trac_mcp_server.preview.checks import build_warnings
from trac_mcp_server.preview.facts import extract_facts
from trac_mcp_server.preview.gate import (
    ADVISORY_CODES,
    BLOCKING_CODES,
    classify,
    corrective_action,
    format_refusal,
    is_blocking,
    suggestion_for,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "convert_preview"
MANIFEST = json.loads((FIXTURES_DIR / "manifest.json").read_text())


def _warning(code, severity, message="m", evidence=None):
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "evidence": evidence,
    }


# ---------------------------------------------------------------------
# The severity/inventory agreement -- the recall gate.
# ---------------------------------------------------------------------


def _all_findings():
    """Every finding the fixture corpus produces, as (code, severity).

    Runs the same assembly a TracWiki-declared write runs, which is what
    the gate will call. `check_targets=False` because the fixtures carry
    no probe results; the three `target_check_*` codes are covered by
    their own cases below, since they cannot arise from a fixture alone.
    """
    for name, data in MANIFEST.items():
        html = (FIXTURES_DIR / f"{name}.html").read_text()
        tracwiki = data["tracwiki"]
        for warning in build_warnings(
            data["markdown_input"] or tracwiki,
            tracwiki,
            extract_facts(html),
            probes={},
            check_targets=False,
            source_format=data.get("source_format", "markdown"),
        ):
            yield name, warning["code"], warning["severity"]


def test_blocking_codes_match_severity_across_the_corpus():
    """Severity and the declared inventory must agree, both directions.

    This is the guard that makes `severity == "error"` safe as the
    blocking mechanism. Without it, promoting a code is a one-word edit
    with no test in its way.
    """
    disagreements = []
    for name, code, severity in _all_findings():
        blocking_by_severity = severity == "error"
        blocking_by_inventory = code in BLOCKING_CODES
        if blocking_by_severity != blocking_by_inventory:
            disagreements.append(
                f"{name}: {code} severity={severity} "
                f"in BLOCKING_CODES={blocking_by_inventory}"
            )
    assert not disagreements, "\n".join(disagreements)


def test_every_emitted_code_is_declared_somewhere():
    """A code in neither inventory is a code nobody ruled on.

    Catches the real failure mode: a new check lands, picks a severity
    by habit, and joins or misses the blocking column without anyone
    deciding. Both sets are documentation; this is what keeps them
    honest.
    """
    known = BLOCKING_CODES | ADVISORY_CODES
    undeclared = {
        code for _, code, _ in _all_findings() if code not in known
    }
    assert not undeclared, undeclared


def test_the_two_inventories_are_disjoint():
    assert not (BLOCKING_CODES & ADVISORY_CODES)


@pytest.mark.parametrize(
    "code",
    [
        "missing_local_target",
        "intertrac_target_captured_punctuation",
        "missing_cross_instance_target",
        "literal_markup_in_render",
        "escaped_link_target",
        "target_check_capped",
        "code_block_indentation_loss",
    ],
)
def test_section_4_error_column_blocks(code):
    """Ticket #64 section 4's left column, plus the two additions the
    operator ruled on in comment 7.8 (`escaped_link_target`,
    `target_check_capped`). Spelled out row by row so the approved
    table is readable in the test file, not only in the ticket."""
    assert code in BLOCKING_CODES


@pytest.mark.parametrize(
    "code",
    [
        "bare_ticket_ref",
        "link_ref_in_code_span",
        "unconfigured_intertrac_prefix",
        "conversion_warning",
        "incidental_wiki_autolink",
        "target_check_failed",
        "target_check_disabled",
    ],
)
def test_section_4_advisory_column_does_not_block(code):
    """The right-hand column. `bare_ticket_ref` is the load-bearing one:
    it fired 10 times on a single correct ticket (auto_pm:#89), so
    promoting it would refuse essentially every ticket that cites
    another ticket."""
    assert code not in BLOCKING_CODES


# ---------------------------------------------------------------------
# The three codes that CHANGED column, asserted explicitly.
# ---------------------------------------------------------------------


def test_link_ref_in_code_span_was_demoted():
    """Was `error`, is `warning` (ticket #64 section 4).

    Pinned by hand because the corpus guard above would happily accept
    it moving back, as long as the inventory moved with it. This says
    which way the ruling went.
    """
    row = "row46_ticket_code_span"
    data = MANIFEST[row]
    html = (FIXTURES_DIR / f"{row}.html").read_text()
    found = [
        w
        for w in build_warnings(
            data["markdown_input"] or data["tracwiki"],
            data["tracwiki"],
            extract_facts(html),
            probes={},
            check_targets=False,
            source_format=data.get("source_format", "markdown"),
        )
        if w["code"] == "link_ref_in_code_span"
    ]
    assert found, "the check must still FIRE, only at lower severity"
    assert all(w["severity"] == "warning" for w in found), found
    assert not any(is_blocking(w) for w in found)


def test_literal_markup_in_render_was_promoted():
    """Was `warning`, is `error` -- and it must still fire.

    The recall half matters more than the severity half: this check
    only became reachable before a write at all when ticket #77 moved
    it out of the verify path, so a regression that silenced it would
    restore exactly the blind spot #64 section 3 named as a
    prerequisite.
    """
    facts = extract_facts(
        "<p>See [the ticket](auto_pm:#87) for detail.</p>"
    )
    found = [
        w
        for w in build_warnings(
            None, "", facts, probes={}, check_targets=False
        )
        if w["code"] == "literal_markup_in_render"
    ]
    assert found, "check must still fire"
    assert all(is_blocking(w) for w in found), found


def test_target_check_capped_blocks_and_its_siblings_do_not():
    """Ruling 2 on ticket #64, confirming ticket #83's own argument.

    The three exist as separate codes precisely so they can answer
    differently here. Asserted together, because the value of the split
    is the CONTRAST -- a test that only checked `capped` would pass on
    an implementation that blocked all three and stopped every write on
    the store whenever a remote instance was down.
    """
    assert is_blocking(_warning("target_check_capped", "error"))
    assert not is_blocking(_warning("target_check_failed", "info"))
    assert not is_blocking(_warning("target_check_disabled", "info"))


# ---------------------------------------------------------------------
# classify / is_blocking
# ---------------------------------------------------------------------


def test_classify_splits_and_preserves_order():
    warnings = [
        _warning("missing_local_target", "error", "first"),
        _warning("bare_ticket_ref", "warning", "second"),
        _warning("escaped_link_target", "error", "third"),
        _warning("target_check_failed", "info", "fourth"),
    ]
    blocking, advisory = classify(warnings)
    assert [w["message"] for w in blocking] == ["first", "third"]
    assert [w["message"] for w in advisory] == ["second", "fourth"]


def test_a_malformed_finding_cannot_refuse_a_write():
    """No severity key means non-blocking.

    Failing open is right for this one case and only this one: a
    finding so malformed it has no severity is a defect in the checker,
    and charging the author for it would refuse a write with a message
    nobody can act on.
    """
    assert not is_blocking({"code": "x", "message": "m"})


def test_empty_warning_list_is_not_a_refusal():
    blocking, advisory = classify([])
    assert blocking == [] and advisory == []


# ---------------------------------------------------------------------
# Fix suggestions and the refusal message (section 5).
# ---------------------------------------------------------------------


def test_suggestion_is_read_from_evidence():
    w = _warning("x", "error", evidence={"suggestion": "[a b]"})
    assert suggestion_for(w) == "[a b]"


@pytest.mark.parametrize(
    "evidence",
    [None, {}, {"suggestion": None}, {"suggestion": 42}, "notadict"],
)
def test_suggestion_absent_or_malformed_is_none(evidence):
    assert (
        suggestion_for(_warning("x", "error", evidence=evidence))
        is None
    )


def test_refusal_names_the_field_the_codes_and_the_suggestion():
    blocking = [
        _warning(
            "intertrac_target_captured_punctuation",
            "error",
            "captured trailing text",
            {"suggestion": "[auto_pm:#87 #87]'s"},
        )
    ]
    text = format_refusal(blocking, [], field="comment")

    assert "Refusing to write comment" in text
    assert "1 blocking link error." in text
    assert "intertrac_target_captured_punctuation" in text
    # Section 5's whole point: the corrected string, not a description.
    assert "Write instead: [auto_pm:#87 #87]'s" in text
    # Section 6's hatch, scoped to the code actually hit.
    assert (
        "preview-checks: allow intertrac_target_captured_punctuation"
        in text
    )


def test_refusal_pluralises_and_lists_every_blocking_finding():
    blocking = [
        _warning("missing_local_target", "error", "dead link one"),
        _warning("escaped_link_target", "error", "dead link two"),
    ]
    text = format_refusal(blocking, [], field="content")
    assert "2 blocking link errors." in text
    assert "dead link one" in text and "dead link two" in text
    # Codes in the pragma hint are sorted and de-duplicated.
    assert (
        "preview-checks: allow escaped_link_target,missing_local_target"
        in text
    )


def test_refusal_reports_advisories_without_implying_they_blocked():
    text = format_refusal(
        [_warning("missing_local_target", "error", "dead")],
        [_warning("bare_ticket_ref", "warning", "resolves to #12")],
        field="description",
    )
    assert "Also reported, not blocking (1)" in text
    assert "resolves to #12" in text
    assert "1 blocking link error." in text


def test_refusal_hint_survives_a_finding_with_no_suggestion():
    """Most codes have nothing mechanical to suggest. The refusal must
    still say how to proceed -- a refusal that does not is where an
    author starts guessing."""
    text = format_refusal(
        [_warning("missing_local_target", "error", "dead")],
        [],
        field="content",
    )
    assert "Write instead:" not in text
    assert "preview-checks: allow missing_local_target" in text


def test_corrective_action_prefers_a_concrete_suggestion():
    with_suggestion = corrective_action(
        [
            _warning(
                "x", "error", "m", {"suggestion": "[auto_pm:#87 #87]"}
            )
        ]
    )
    assert "[auto_pm:#87 #87]" in with_suggestion

    without = corrective_action([_warning("y", "error", "m")])
    assert "preview-checks" in without
