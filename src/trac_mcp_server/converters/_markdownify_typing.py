"""Typing shim for ``markdownify`` (Trac /trac_mcp_server #236).

The ``markdownify`` wheel ships a ``.pyi`` that declares only
``markdownify()`` and a minimal ``MarkdownConverter``. ``html_to_markdown``
subclasses the real class and relies on the parts the stub leaves out: the
``options`` dict, ``DefaultOptions``, the per-tag ``convert_*`` methods it
extends through ``super()``, ``process_tag``/``process_text``, and the
``chomp``/``abstract_inline_conversion`` helpers.

Under a type checker this module declares those members; at runtime it is
a plain re-export of the real objects, so nothing about behaviour changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from markdownify import MarkdownConverter

    class MarkdownifyBase(MarkdownConverter):
        class DefaultOptions: ...

        options: dict[str, Any]

        def process_tag(
            self, node: Any, parent_tags: Any = None
        ) -> str: ...
        def process_text(
            self, el: Any, parent_tags: Any = None
        ) -> str: ...
        def convert_list(
            self, el: Any, text: str, parent_tags: Any
        ) -> str: ...
        def convert_br(
            self, el: Any, text: str, parent_tags: Any
        ) -> str: ...
        def convert_td(
            self, el: Any, text: str, parent_tags: Any
        ) -> str: ...
        def convert_th(
            self, el: Any, text: str, parent_tags: Any
        ) -> str: ...
        def convert_dt(
            self, el: Any, text: str, parent_tags: Any
        ) -> str: ...
        def convert_dd(
            self, el: Any, text: str, parent_tags: Any
        ) -> str: ...

    def chomp(text: str) -> tuple[str, str, str]: ...
    def abstract_inline_conversion(
        markup_fn: Callable[[Any], str],
    ) -> Callable[..., str]: ...

else:
    from markdownify import (
        MarkdownConverter as MarkdownifyBase,
    )
    from markdownify import abstract_inline_conversion, chomp

__all__ = ["MarkdownifyBase", "abstract_inline_conversion", "chomp"]
