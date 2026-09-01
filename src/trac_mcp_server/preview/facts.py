"""Extract structured facts from Trac-rendered HTML.

Pure function of the HTML string -- no knowledge of whether that HTML came
from a dry-run ``wiki.wikiToHtml`` call (``convert_preview``, ticket #56)
or a live page (``verify``, ticket #55). Keeping this ignorant of its
caller is what lets both tools share it.
"""

from dataclasses import dataclass, field

from lxml import etree
from lxml import html as lxml_html


@dataclass(frozen=True, slots=True)
class Anchor:
    """One ``<a>`` element from rendered HTML.

    Attributes:
        classes: The element's ``class`` attribute, split on whitespace
            (e.g. ``["ext-link"]``, ``["missing", "wiki"]``,
            ``["closed", "ticket"]``). Empty list if no class attribute.
        href: The ``href`` attribute, or None if absent.
        title: The ``title`` attribute, or None if absent. Trac packs
            useful detail here -- an InterTrac target's resolved project
            name, a ticket's resolved summary and status.
        text: The anchor's rendered text content.
    """

    classes: tuple[str, ...]
    href: str | None
    title: str | None
    text: str


@dataclass(frozen=True, slots=True)
class CodeBlock:
    """One fenced code block (``<pre>``) from rendered HTML.

    Attributes:
        highlighted: Whether Trac's Pygments highlighter processed this
            block (nested ``<div class="wiki-code"><div class="code">
            <pre>``) versus an unhighlighted ``<pre class="wiki">`` block
            (e.g. plain ``{{{ }}}`` with no ``#!lang`` header). Verified
            live 2026-09-01: the rendered HTML never names which
            language was requested -- Pygments' token spans (``class=
            "k"``, ``"nf"``, ...) are lexical categories, not a language
            label -- so "which language" isn't recoverable from the
            render at all, only "was it highlighted".
        text: The block's text content.
    """

    highlighted: bool
    text: str


@dataclass(frozen=True, slots=True)
class PreviewFacts:
    """Structured facts pulled from one rendered-HTML document.

    Attributes:
        anchors: Every ``<a>`` element, in document order.
        code_spans: Text content of every ``<code>`` element, in document
            order -- inline code spans, not fenced ``<pre>`` blocks.
        plain_text: The document's full visible text content, whitespace
            collapsed. Used for patterns that never produce their own
            element (e.g. literal TracWiki markup that failed to convert).
        code_blocks: Every ``<pre>`` element, in document order.
        prose_text: Like ``plain_text``, but excluding ``<pre>``/``<code>``
            subtrees -- a page that legitimately documents TracWiki syntax
            inside a code block must not trip a scan for that same syntax
            surviving in prose (ticket #55).
    """

    anchors: tuple[Anchor, ...] = field(default_factory=tuple)
    code_spans: tuple[str, ...] = field(default_factory=tuple)
    plain_text: str = ""
    code_blocks: tuple[CodeBlock, ...] = field(default_factory=tuple)
    prose_text: str = ""


def extract_facts(rendered_html: str) -> PreviewFacts:
    """Parse Trac-rendered HTML into a :class:`PreviewFacts`.

    Args:
        rendered_html: HTML as returned by ``wiki.wikiToHtml`` or
            ``wiki.getPageHTML``.

    Returns:
        Empty ``PreviewFacts`` for blank/whitespace-only input (Trac
        renders an empty document to nothing worth parsing); otherwise
        the anchors, code spans, and plain text found in it.
    """
    if not rendered_html or not rendered_html.strip():
        return PreviewFacts()

    tree = lxml_html.fromstring(rendered_html)

    anchors = tuple(
        Anchor(
            classes=tuple((a.get("class") or "").split()),
            href=a.get("href"),
            title=a.get("title"),
            text=a.text_content(),
        )
        for a in tree.xpath("//a")
    )
    code_spans = tuple(c.text_content() for c in tree.xpath("//code"))
    plain_text = tree.text_content()

    code_blocks = tuple(
        CodeBlock(
            highlighted=_is_highlighted_code_block(pre),
            text=pre.text_content(),
        )
        for pre in tree.xpath("//pre")
    )

    # Wrapped in a `<div>` before parsing: a section whose ENTIRE body is
    # one `<pre>` (a real Trac shape -- a description/comment that's
    # nothing but a `{{{ }}}` block) parses with that `<pre>` AS the
    # document root, and `strip_elements` cannot remove a tree's own
    # root (found live: row08's pin fixture leaked its `<pre>` text into
    # prose_text entirely before this wrap was added). Wrapping gives
    # `<pre>`/`<code>` a parent to be stripped FROM in every case.
    prose_tree = lxml_html.fromstring(f"<div>{rendered_html}</div>")
    # with_tail=False: a stripped element's tail text is prose (the text
    # right after a `</pre>`/`</code>`), not part of the code block --
    # plain `parent.remove(el)` discards the tail along with the element
    # and silently eats that adjacent prose (found live: text right
    # after an empty `<code></code>` vanished from prose_text entirely).
    etree.strip_elements(prose_tree, "pre", "code", with_tail=False)
    prose_text = prose_tree.text_content()

    return PreviewFacts(
        anchors=anchors,
        code_spans=code_spans,
        plain_text=plain_text,
        code_blocks=code_blocks,
        prose_text=prose_text,
    )


def _is_highlighted_code_block(pre) -> bool:
    """Whether ``pre`` sits inside Trac's highlighter wrapper.

    Highlighted output nests as ``<div class="wiki-code"><div
    class="code"><pre>``; a plain ``<pre class="wiki">`` block (no
    highlighter) has no such ancestor.
    """
    for ancestor in pre.iterancestors():
        if "code" in (ancestor.get("class") or "").split():
            return True
    return False
