"""Gate test for ticket #97.

The MCP SDK validates call arguments against a tool's advertised
inputSchema *before* dispatch ever reaches registry.py's
``_normalize_arg_aliases``. An alias entry (exact or prefix) is only
reachable if both its key and its canonical target are accepted
properties on the matching tool's schema -- otherwise jsonschema rejects
the call before the normalizer ever runs.
"""

import unittest

from trac_mcp_server.mcp.server import PING_SPEC
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.registry import (
    _ARG_ALIASES,
    _ARG_ALIASES_EXACT,
    ToolSpec,
    with_instance_param,
    with_page_alias,
    with_strict_schema,
)


def _alias_schema_gaps(specs: list[ToolSpec]) -> list[str]:
    """Return one description per alias key/canonical pair that isn't
    an accepted property on its tool's schema.
    """
    by_name = {spec.tool.name: spec for spec in specs}
    gaps: list[str] = []

    for tool_name, aliases in _ARG_ALIASES_EXACT.items():
        spec = by_name.get(tool_name)
        if spec is None:
            continue
        properties = (spec.tool.inputSchema or {}).get(
            "properties"
        ) or {}
        for alias, canonical in aliases.items():
            for key in (alias, canonical):
                if key not in properties:
                    gaps.append(
                        f"{tool_name}: {key!r} not an accepted property"
                    )

    for prefix, aliases in _ARG_ALIASES.items():
        for spec in specs:
            if not spec.tool.name.startswith(prefix):
                continue
            properties = (spec.tool.inputSchema or {}).get(
                "properties"
            ) or {}
            for alias, canonical in aliases.items():
                # Only tools that actually declare the canonical field
                # are expected to also accept its alias -- not every
                # wiki_* tool takes a page/page_name argument at all.
                if (
                    canonical not in properties
                    and alias not in properties
                ):
                    continue
                for key in (alias, canonical):
                    if key not in properties:
                        gaps.append(
                            f"{spec.tool.name}: {key!r} not an accepted "
                            "property"
                        )
    return gaps


class TestSchemaAliasConsistency(unittest.TestCase):
    def test_gate_fires_on_the_unprocessed_specs(self):
        """Seeded-defect-first: raw ALL_SPECS (before with_page_alias is
        applied) is exactly the originally-reported broken state --
        wiki_get's real schema requires page_name and never declares
        page at all. A gate that has never been observed failing looks
        exactly like one that always passes; confirm this one actually
        catches the defect it exists for.
        """
        gaps = _alias_schema_gaps(ALL_SPECS)
        self.assertTrue(
            any("wiki_get" in gap and "'page'" in gap for gap in gaps),
            f"expected a wiki_get/page gap in the raw specs, got: {gaps}",
        )

    def test_no_gaps_after_the_real_processing_pipeline(self):
        """The exact pipeline server.py applies before building the
        registry must close every alias gap.
        """
        processed = with_strict_schema(
            with_instance_param(
                with_page_alias([PING_SPEC] + ALL_SPECS), []
            )
        )
        gaps = _alias_schema_gaps(processed)
        self.assertEqual(
            gaps, [], f"unexpected alias/schema gaps: {gaps}"
        )


if __name__ == "__main__":
    unittest.main()
