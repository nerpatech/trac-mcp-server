"""Gate tests for ticket #97, and its ticket #99 regression.

The MCP SDK validates call arguments against a tool's advertised
inputSchema *before* dispatch ever reaches registry.py's
``_normalize_arg_aliases``. An alias entry (exact or prefix) is only
reachable if both its key and its canonical target are accepted
properties on the matching tool's schema -- otherwise jsonschema rejects
the call before the normalizer ever runs.

``with_strict_schema``'s ``additionalProperties: false`` (also #97) has
its own failure mode: ``ticket_update``'s workflow-action fields are
dynamic (``action_<action>_<action>_<field>``, e.g.
``action_resolve_resolve_resolution``), so they can never be declared as
static properties. Deployed without a ``patternProperties`` carve-out,
strict-schema rejected every one of them -- observed live against the
real daemon (#99). ``TestActionFieldSchema`` below is that gate.
"""

import unittest

import jsonschema

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
from trac_mcp_server.mcp.tools.ticket_write import TICKET_WRITE_SPECS


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


class TestActionFieldSchema(unittest.TestCase):
    """Ticket #99: with_strict_schema's additionalProperties: false must
    not reject ticket_update's documented dynamic action_* fields.
    """

    def _ticket_update_spec(self):
        return next(
            s
            for s in TICKET_WRITE_SPECS
            if s.tool.name == "ticket_update"
        )

    def test_strict_schema_still_accepts_a_documented_action_field(
        self,
    ):
        processed = with_strict_schema([self._ticket_update_spec()])
        schema = processed[0].tool.inputSchema
        # This is the exact call that failed live against the real
        # daemon before the fix -- reproduce it directly against the
        # tool's own advertised schema, the same jsonschema.validate
        # call the MCP SDK makes before dispatch.
        jsonschema.validate(
            instance={
                "ticket_id": 92,
                "action": "resolve",
                "action_resolve_resolve_resolution": "fixed",
            },
            schema=schema,
        )

    def test_strict_schema_still_rejects_a_genuinely_unknown_key(self):
        """The carve-out must stay narrow -- an unrelated unknown key
        is exactly what #97 wanted rejected, and must still be.
        """
        processed = with_strict_schema([self._ticket_update_spec()])
        schema = processed[0].tool.inputSchema
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                instance={"ticket_id": 92, "bogus_unrelated_key": "x"},
                schema=schema,
            )


if __name__ == "__main__":
    unittest.main()
