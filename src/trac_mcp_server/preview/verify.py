"""Verify-only warning assembly for a live-rendered document (ticket #55).

Companion to ``checks.build_warnings`` (ticket #56): that path checks a
dry-run render against its Markdown source; this path checks a page Trac
has already rendered, where there is no Markdown candidate to check
against.

Ticket #55 also gave this module the one check that looks at what came
OUT of a render rather than what went in,
``_check_literal_markup_in_render``. Ticket #77 moved that check into
``checks.build_warnings``: it takes only ``facts``, which every caller
already has, and keeping it here made ``convert_preview`` -- the only
PRE-write gate -- blind to markup that survived unconverted. What remains
here is the render-path wrapper: no Markdown source, so the two
Markdown-source rules are skipped.
"""

from .checks import build_warnings
from .facts import PreviewFacts


def build_verify_warnings(
    tracwiki: str,
    facts: PreviewFacts,
    probes: dict[str, dict],
    check_targets: bool,
) -> list[dict]:
    """Run every check applicable to a live render and return the list.

    Args:
        tracwiki: The stored TracWiki source paired with this render
            (description or comment body), used by the InterTrac-prefix
            check. When no source could be paired with the render, pass
            ``""`` -- the caller is responsible for reporting that skip
            explicitly (``source_paired: false``), since this function
            has no way to distinguish "no source" from "empty source".
        facts: Extracted from the live-rendered HTML.
        probes: Live-probe results for cross-instance targets, from
            :func:`trac_mcp_server.preview.targets.probe_targets`.
        check_targets: Whether the live probe actually ran.

    Returns:
        List of warning dicts, each ``{code, severity, message,
        evidence}`` -- the shared ``build_warnings`` rules with the
        Markdown-source rules skipped.
    """
    return build_warnings(
        markdown_source=None,
        tracwiki=tracwiki,
        facts=facts,
        probes=probes,
        check_targets=check_targets,
    )
