"""Verify-only warning assembly for a live-rendered document (ticket #55).

Companion to ``checks.build_warnings`` (ticket #56): that path checks a
dry-run render against its Markdown source; this path checks a page Trac
has already rendered, where there is no Markdown candidate to check
against, but there IS a live document whose surviving-markup defects
``build_warnings`` alone can't see (it only ever looked at Markdown
*input*, never at what came out the other end).
"""

import re

from .checks import build_warnings
from .facts import PreviewFacts

# TracWiki table-row syntax (`|=Header=|`) that failed to render as a
# table and survived as literal text in the page body.
_TRACWIKI_TABLE_RE = re.compile(r"\|=[^|\n]*=\|")

# An unclosed or otherwise-literal `{{{`/`}}}` code-block delimiter.
_TRACWIKI_BLOCK_RE = re.compile(r"\{\{\{|\}\}\}")

# Markdown residue that should have been converted to TracWiki (bold,
# fenced code, inline link) but survived as literal text in the render --
# the inverse of `checks._TRACWIKI_BOLD_RE`/`_TRACWIKI_BLOCK_RE`: those
# scan Markdown SOURCE for TracWiki syntax that won't convert; this scans
# a RENDER for Markdown syntax that never got converted to TracWiki in
# the first place (e.g. hand-edited via `raw=true`, or a converter defect
# that let Markdown straight through).
_MARKDOWN_BOLD_RE = re.compile(r"\*\*[^*\n]+\*\*")
_MARKDOWN_FENCE_RE = re.compile(r"```")
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]\n]+\]\([^)\n]+\)")


def _check_literal_markup_in_render(facts: PreviewFacts) -> list[dict]:
    """Markup that should have been converted but survived as literal
    text in the rendered page -- scoped to ``facts.prose_text`` (NOT
    ``facts.plain_text``), which excludes ``<pre>``/``<code>`` subtrees.
    A page that legitimately documents this syntax inside a code block
    must stay silent; scanning ``plain_text`` instead would warn on every
    such block (the over-correction pin ticket #55 calls out)."""
    warnings = []
    for pattern, label in (
        (_TRACWIKI_TABLE_RE, "TracWiki table"),
        (_TRACWIKI_BLOCK_RE, "TracWiki code-block delimiter"),
        (_MARKDOWN_BOLD_RE, "Markdown bold"),
        (_MARKDOWN_FENCE_RE, "Markdown code fence"),
        (_MARKDOWN_LINK_RE, "Markdown link"),
    ):
        match = pattern.search(facts.prose_text)
        if match:
            warnings.append(
                {
                    "code": "literal_markup_in_render",
                    "severity": "warning",
                    "message": (
                        f"{label} syntax ('{match.group(0)}') appears "
                        "as literal text in the rendered page instead "
                        "of being rendered -- it did not convert "
                        "cleanly."
                    ),
                    "evidence": {"matched": match.group(0)},
                }
            )
    return warnings


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
        evidence}`` -- the shared ``build_warnings`` rules (Markdown-
        source rule skipped) plus the render-only literal-markup check.
    """
    warnings = build_warnings(
        markdown_source=None,
        tracwiki=tracwiki,
        facts=facts,
        probes=probes,
        check_targets=check_targets,
    )
    warnings.extend(_check_literal_markup_in_render(facts))
    return warnings
