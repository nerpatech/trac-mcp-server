"""What a link of a Trac export points at, for Markdown and for HTML.

Trac renders every link absolute, and an in-page anchor (a
``[[PageOutline]]`` entry, ``[#Heading x]``) as ``<page URL>#Heading``,
which a published export would follow back into Trac. Two exports
decide a link's target and they must decide it the same way, so the
decision lives here once (Trac /trac_mcp_server #219, extracted from the
Markdown converter of #209/#213):

* ``LinkTargets`` -- one ``href``'s target: an in-page anchor is
  ``#frag``, else the longest matching ``link_map`` prefix is replaced.
  Image sources are not links and are never passed to it.
* ``rewrite_hrefs`` -- the HTML export's use of it: Trac's HTML comes
  back with only the values of ``href`` attributes changed.
"""

from __future__ import annotations

import re
from html import escape, unescape
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit


def _absolute_path(url: str) -> str | None:
    """The unquoted path of an absolute ``http(s)`` URL with no query,
    without one trailing ``/``; None for any other URL (relative, or
    carrying a query, which makes it another document)."""
    parts = urlsplit(url)
    if not (parts.scheme and parts.netloc) or parts.query:
        return None
    return unquote(parts.path).rstrip("/")


class LinkTargets:
    """The target of a link: in-page anchors and ``link_map``.

    ``link_map`` maps a URL prefix to its replacement (longest prefix
    first, whatever the dict's order); ``document_url`` is the URL Trac
    gives the document being exported, which is what makes a link into
    it an in-page anchor. Either may be absent.
    """

    def __init__(
        self,
        link_map: dict[str, str] | None,
        document_url: str | None = None,
    ):
        self._link_map = sorted(
            (link_map or {}).items(), key=lambda kv: -len(kv[0])
        )
        # (unquoted URL, unquoted path when it is absolute) -- see
        # in_page_anchor.
        self._document = (
            (
                unquote(document_url).rstrip("/"),
                _absolute_path(document_url),
            )
            if document_url
            else None
        )

    def in_page_anchor(self, url: str) -> str | None:
        """``#frag`` when ``url`` points into this document, else None.

        Trac renders ``[#Frag x]`` as ``<document URL>#Frag`` (the
        page's own URL from ``getPageHTML``, the instance root from
        ``wikiToHtml``), not as a bare ``#Frag``; bare ones come only
        from macro output. Both are in-page. When both URLs are absolute
        only the PATH is compared, unquoted and ignoring one trailing
        ``/``: Trac builds its absolute URLs from its own base URL, which
        can name another scheme or host than the daemon's ``TRAC_URL``
        (a LAN address against the public name), while the path is what
        names the page. Otherwise the whole URL before the ``#`` is
        compared, unquoted. A URL with a query is another document. The
        same document with no fragment is an ordinary link.
        """
        base, sep, frag = url.partition("#")
        if not sep:
            return None
        if base == "":
            return url  # a bare "#frag" (or "#"), whatever the map says
        if frag and self._document is not None:
            whole, path = self._document
            base_path = _absolute_path(base)
            if path is not None and base_path is not None:
                same = base_path == path
            else:
                same = unquote(base).rstrip("/") == whole
            if same:
                return f"#{frag}"
        return None

    def map(self, url: str) -> str:
        """The target of one ``href``: an in-page anchor as ``#frag``,
        else ``link_map`` applied, longest prefix first.

        An in-page anchor is never rewritten, whatever the map says.
        Images are not mapped at all (#215).
        """
        if not url:
            return url
        anchor = self.in_page_anchor(url)
        if anchor is not None:
            return anchor
        for prefix, replacement in self._link_map:
            if url.startswith(prefix):
                return replacement + url[len(prefix) :]
        return url


# --- the HTML export --------------------------------------------------------

# One attribute of a start tag's raw text: the name, then optionally "="
# and a double-quoted, single-quoted or unquoted value (the grammar
# html.parser itself uses for them). Group "value" is the value without
# its quotes; exactly one of "dq", "sq" and "bare" matches.
_ATTRIBUTE = re.compile(
    r"""
    [\s/]*
    (?P<name>[^\s/>=][^\s/>=]*)
    (?:
        \s*=\s*
        (?:
            "(?P<dq>[^"]*)" | '(?P<sq>[^']*)' | (?P<bare>[^\s>]*)
        )
    )?
    """,
    re.VERBOSE,
)
_TAG_NAME = re.compile(r"<[^\s/>]*")


class _StartTags(HTMLParser):
    """The offset and raw text of every start tag of a document.

    ``html.parser`` knows what is a tag and what is text, a comment or
    script/style content; it is asked only where the tags are, and the
    attributes are read from their raw text by the caller.
    """

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self._text = text
        # getpos() is (line, column) with lines split at "\n" only.
        self._line_starts = [0] + [
            m.end() for m in re.finditer("\n", text)
        ]
        self._floor = 0
        self.tags: list[tuple[int, str]] = []

    def _note(self) -> None:
        raw = self.get_starttag_text()
        if raw is None:  # pragma: no cover - a start tag always has it
            return
        line, column = self.getpos()
        offset = self._line_starts[line - 1] + column
        if self._text[offset : offset + len(raw)] != raw:
            # Not where the position arithmetic says: find it rather
            # than rewrite another tag's bytes.
            offset = self._text.find(raw, self._floor)
            if offset < 0:  # pragma: no cover
                return
        self._floor = offset + len(raw)
        self.tags.append((offset, raw))

    def handle_starttag(self, tag, attrs):
        self._note()

    def handle_startendtag(self, tag, attrs):
        self._note()


def _href_edits(
    offset: int, raw: str, targets: LinkTargets
) -> list[tuple[int, int, str]]:
    """The ``(start, end, replacement)`` edits of one start tag's
    ``href`` values, as absolute offsets into the document."""
    edits: list[tuple[int, int, str]] = []
    name = _TAG_NAME.match(raw)
    pos = name.end() if name else 0
    while True:
        attr = _ATTRIBUTE.match(raw, pos)
        if attr is None:
            break
        pos = attr.end()
        if attr.group("name").lower() != "href":
            continue
        for group in ("dq", "sq", "bare"):
            value = attr.group(group)
            if value is not None:
                break
        else:
            continue  # "href" with no value
        if not value:
            continue
        original = unescape(value)
        mapped = targets.map(original)
        if mapped == original:
            continue
        start, end = attr.span(group)
        new = escape(mapped, quote=True)
        if group == "bare":
            new = f'"{new}"'
        edits.append((offset + start, offset + end, new))
    return edits


def rewrite_hrefs(
    html: str,
    link_map: dict[str, str] | None,
    document_url: str | None,
) -> str:
    """``html`` with the value of every ``href`` attribute mapped by
    ``LinkTargets(link_map, document_url).map``, and nothing else
    changed: every other byte, quote and entity is the input's own.

    The document is not parsed into a tree and written back, which would
    normalise quotes and entities everywhere. ``html.parser`` locates
    the start tags; each tag's own text is read for its attributes, so
    ``data-href`` and ``href=`` inside another attribute's value are not
    taken for an ``href``. Text, ``<pre>`` content, comments and
    script/style content are not tags and are never touched. The value
    is unescaped before it is mapped; one whose target is unchanged
    keeps its bytes, else the new target is escaped into the same quote
    (double quotes for a value that had none). An empty ``href`` is left
    alone, and ``src`` is never looked at.
    """
    targets = LinkTargets(link_map, document_url)
    parser = _StartTags(html)
    parser.feed(html)
    parser.close()
    edits: list[tuple[int, int, str]] = []
    for offset, raw in parser.tags:
        edits.extend(_href_edits(offset, raw, targets))
    for start, end, new in reversed(edits):
        html = html[:start] + new + html[end:]
    return html
