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
    describe_list_item_split,
    find_list_item_splits,
    markdown_to_tracwiki,
)

__all__ = [
    "ConversionResult",
    "TracWikiRenderer",
    "auto_convert",
    "convert_with_warnings",
    "describe_indentation_loss",
    "describe_list_item_split",
    "detect_format_heuristic",
    "find_code_block_indentation_loss",
    "find_list_item_splits",
    "markdown_to_tracwiki",
    "markdown_to_tracwiki_lang",
    "tracwiki_to_markdown_lang",
]
