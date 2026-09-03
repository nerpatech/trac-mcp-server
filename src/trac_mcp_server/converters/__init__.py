"""Format conversion between Markdown and TracWiki."""

from .common import (
    ConversionResult,
    auto_convert,
    describe_indentation_loss,
    detect_format_heuristic,
    find_code_block_indentation_loss,
    markdown_to_tracwiki_lang,
    tracwiki_to_markdown_lang,
)
from .markdown_to_tracwiki import (
    TracWikiRenderer,
    convert_with_warnings,
    markdown_to_tracwiki,
)
from .tracwiki_to_markdown import tracwiki_to_markdown

__all__ = [
    "ConversionResult",
    "TracWikiRenderer",
    "auto_convert",
    "convert_with_warnings",
    "describe_indentation_loss",
    "detect_format_heuristic",
    "find_code_block_indentation_loss",
    "markdown_to_tracwiki",
    "markdown_to_tracwiki_lang",
    "tracwiki_to_markdown",
    "tracwiki_to_markdown_lang",
]
