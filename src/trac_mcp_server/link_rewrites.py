"""The link-rewrite map of the HTML -> Markdown export (Trac /trac_mcp_server
#213): parsing and validation, shared by every place a map comes from.

A map is a list of ``{from, to}`` rules: an ``href`` starting with a rule's
``from`` has that prefix replaced by ``to`` (longest ``from`` first, see
``converters/html_to_markdown.py``). The same rules arrive four ways --
the YAML ``export.link_rewrites`` list, the ``TRAC_MCP_LINK_REWRITES``
environment variable, a ``link_rewrites`` argument of ``wiki_file_pull``,
and the ``--link-map FROM=TO`` flag of ``trac-convert`` / ``trac-mcp`` --
and all four are checked here, so a bad rule reads the same everywhere.

This module imports nothing from the package: config.py, config_schema.py,
the tool and both CLIs all import it.
"""

from collections.abc import Iterable, Mapping
from typing import Any

# A map as the rest of the package carries it: (from, to) pairs, in the
# order they were given. The order has no meaning; the translator sorts.
LinkRewrites = tuple[tuple[str, str], ...]


def validate_link_rewrites(
    entries: Any, where: str = "link_rewrites"
) -> LinkRewrites:
    """Check a list of ``{from, to}`` rules and return it as pairs.

    Raises:
        ValueError: naming ``where`` and the index of the first bad rule:
            the list is not a list, a rule is not an object with exactly
            ``from`` and ``to``, ``from`` is empty or either value is not
            a string, or two rules share a ``from``.
    """
    if not isinstance(entries, (list, tuple)):
        raise ValueError(
            f"{where} must be a list of {{from, to}} objects, "
            f"not {type(entries).__name__}"
        )
    pairs: list[tuple[str, str]] = []
    seen: dict[str, int] = {}
    for i, entry in enumerate(entries):
        label = f"{where}[{i}]"
        if not isinstance(entry, Mapping):
            raise ValueError(
                f"{label} must be an object with 'from' and 'to', "
                f"not {type(entry).__name__}"
            )
        extra = sorted(set(entry) - {"from", "to"}, key=str)
        if extra:
            raise ValueError(
                f"{label} has unknown key(s) {extra!r}: only 'from' "
                "and 'to' are allowed"
            )
        for key in ("from", "to"):
            if key not in entry:
                raise ValueError(f"{label} has no '{key}'")
            if not isinstance(entry[key], str):
                raise ValueError(
                    f"{label} '{key}' must be a string, "
                    f"not {type(entry[key]).__name__}"
                )
        source, target = entry["from"], entry["to"]
        if not source:
            raise ValueError(f"{label} 'from' must not be empty")
        if source in seen:
            raise ValueError(
                f"{label} repeats 'from' {source!r} "
                f"(already {where}[{seen[source]}])"
            )
        seen[source] = i
        pairs.append((source, target))
    return tuple(pairs)


def parse_pair(text: str, where: str) -> dict[str, str]:
    """``FROM=TO`` split at the first ``=`` into a ``{from, to}`` rule.

    Raises:
        ValueError: ``text`` has no ``=``.
    """
    source, sep, target = text.partition("=")
    if not sep:
        raise ValueError(
            f"{where}: {text!r} is not FROM=TO (no '=' in it)"
        )
    return {"from": source, "to": target}


def parse_pairs(
    texts: Iterable[str], where: str = "link map"
) -> LinkRewrites:
    """A list of ``FROM=TO`` strings, validated."""
    return validate_link_rewrites(
        [parse_pair(t, where) for t in texts], where
    )


def parse_env(
    raw: str, where: str = "TRAC_MCP_LINK_REWRITES"
) -> LinkRewrites:
    """The ``TRAC_MCP_LINK_REWRITES`` value: whitespace-separated
    ``FROM=TO`` pairs. Empty or all-blank gives an empty map."""
    return parse_pairs(raw.split(), where)


def as_dict(rewrites: LinkRewrites | None) -> dict[str, str]:
    """The map in the form ``trac_html_to_markdown(link_map=)`` takes."""
    return dict(rewrites or ())


def as_entries(rewrites: LinkRewrites) -> list[dict[str, str]]:
    """The map as ``{from, to}`` objects: a tool argument or YAML list."""
    return [{"from": f, "to": t} for f, t in rewrites]
