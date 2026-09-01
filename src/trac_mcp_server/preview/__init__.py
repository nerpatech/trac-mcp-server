"""Dry-run preview of a Markdown write: convert, render, and warn.

Built for ticket #56 (``convert_preview``). ``facts`` extracts structured
data from Trac-rendered HTML and knows nothing about where the HTML came
from -- ticket #55 (post-write verification) reuses it against live page
HTML the same way this package's own tool reuses it against a dry-run
render.
"""

from .checks import build_warnings
from .facts import Anchor, PreviewFacts, extract_facts

__all__ = [
    "Anchor",
    "PreviewFacts",
    "extract_facts",
    "build_warnings",
]
