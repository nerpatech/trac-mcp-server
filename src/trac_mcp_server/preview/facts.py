"""Extract structured facts from Trac-rendered HTML.

Pure function of the HTML string -- no knowledge of whether that HTML came
from a dry-run ``wiki.wikiToHtml`` call (``convert_preview``, ticket #56)
or a live page (``verify``, ticket #55). Keeping this ignorant of its
caller is what lets both tools share it.
"""

from dataclasses import dataclass, field

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
class PreviewFacts:
    """Structured facts pulled from one rendered-HTML document.

    Attributes:
        anchors: Every ``<a>`` element, in document order.
        code_spans: Text content of every ``<code>`` element, in document
            order -- inline code spans, not fenced ``<pre>`` blocks.
        plain_text: The document's full visible text content, whitespace
            collapsed. Used for patterns that never produce their own
            element (e.g. literal TracWiki markup that failed to convert).
    """

    anchors: tuple[Anchor, ...] = field(default_factory=tuple)
    code_spans: tuple[str, ...] = field(default_factory=tuple)
    plain_text: str = ""


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

    return PreviewFacts(
        anchors=anchors, code_spans=code_spans, plain_text=plain_text
    )
