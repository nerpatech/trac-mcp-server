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
from dataclasses import dataclass
from html import escape, unescape
from html.entities import html5
from html.parser import HTMLParser, attrfind_tolerant, tagfind_tolerant
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

        An in-page anchor is written ``#frag`` and never mapped by
        ``link_map``, whatever the map says.
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

# An attribute value is not text: HTML5 leaves a named character
# reference written without its ";" literal when the next character is
# an ASCII letter, digit or "=", so "?a=1&not=2" is the URL it looks like
# and not "?a=1" + "\u00ac" + "=2". html.unescape (what HTMLParser uses)
# decodes it either way. Same pattern as html.unescape's own.
_CHARREF = re.compile(
    r"&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)"
)


def _attribute_reference(match: re.Match[str]) -> str:
    name = match.group(1)
    if name[0] != "#" and name not in html5:
        # The longest legacy (";"-less) name the text starts with, as
        # html.unescape finds it; whether it decodes depends on what
        # follows it.
        for length in range(len(name) - 1, 1, -1):
            if name[:length] in html5:
                follower = name[length]
                if (follower.isascii() and follower.isalnum()) or (
                    follower == "="
                ):
                    return match.group(0)
                break
    return unescape(match.group(0))


def unescape_attribute(value: str) -> str:
    """``value`` of an attribute with its character references decoded
    the HTML5 way (see ``_CHARREF``)."""
    if "&" not in value:
        return value
    return _CHARREF.sub(_attribute_reference, value)


@dataclass(frozen=True)
class _Attribute:
    """One attribute of a start tag's raw text."""

    name: str  # lower-cased
    value: str | None  # raw, without quotes; None when there is no "="
    span: tuple[int, int]  # of the value text, quotes not included
    quoted: bool


def _attributes(raw: str) -> list[_Attribute]:
    """The attributes of a start tag's raw text, read the way
    ``HTMLParser.parse_starttag`` reads them: its own regexes, its own
    loop (a name may start with ``=``, ``href==x`` is one attribute, and
    the first text that is not an attribute ends the list)."""
    found: list[_Attribute] = []
    name = tagfind_tolerant.match(raw, 1)
    pos = name.end() if name else 1
    while pos < len(raw):
        match = attrfind_tolerant.match(raw, pos)
        if match is None:
            break
        pos = match.end()
        attr_name, equals, value = match.group(1, 2, 3)
        if not equals:
            found.append(
                _Attribute(
                    attr_name.lower(), None, match.span(1), False
                )
            )
            continue
        start, end = match.span(3)
        quoted = value[:1] in ("'", '"')
        if quoted:
            start, end = start + 1, end - 1
            value = value[1:-1]
        found.append(
            _Attribute(attr_name.lower(), value, (start, end), quoted)
        )
    return found


# Elements whose content HTML5 reads as text (RCDATA, RAWTEXT), whatever
# it looks like, up to the element's end tag. html.parser does this only
# for script and style.
_TEXT_ELEMENTS = frozenset(
    {"title", "textarea", "xmp", "iframe", "noembed", "noframes"}
)


class _StartTags(HTMLParser):
    """The offset and raw text of every start tag of a document.

    ``html.parser`` knows what is a tag and what is text, a comment or
    script/style content; it is asked only where the tags are, and the
    attributes are read from their raw text by the caller. The elements
    of ``_TEXT_ELEMENTS`` hold text up to their end tag, and
    ``<plaintext>`` holds the rest of the document, so no tag in them is
    reported.
    """

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self._text = text
        # getpos() is (line, column) with lines split at "\n" only.
        self._line_starts = [0] + [
            m.end() for m in re.finditer("\n", text)
        ]
        self._floor = 0
        self._plaintext = False
        self.tags: list[tuple[int, str]] = []

    def _note(self) -> None:
        if self._plaintext:
            return
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
        if tag == "plaintext":
            self._plaintext = True
        elif tag in _TEXT_ELEMENTS and self.cdata_elem is None:
            self.set_cdata_mode(tag)

    def handle_startendtag(self, tag, attrs):
        self._note()


def _href_edits(
    offset: int, raw: str, targets: LinkTargets
) -> list[tuple[int, int, str]]:
    """The ``(start, end, replacement)`` edits of one start tag's
    ``href`` values, as absolute offsets into the document."""
    edits: list[tuple[int, int, str]] = []
    for attr in _attributes(raw):
        if attr.name != "href" or not attr.value:
            continue
        original = unescape_attribute(attr.value)
        mapped = targets.map(original)
        if mapped == original:
            continue
        new = escape(mapped, quote=True)
        if not attr.quoted:
            new = f'"{new}"'
        start, end = attr.span
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
    the start tags; each tag's own text is read for its attributes with
    the same regexes and loop ``html.parser`` uses, so ``data-href`` and
    ``href=`` inside another attribute's value are not taken for an
    ``href``. Text, ``<pre>`` content, comments, script/style content
    and the content of ``title``, ``textarea`` and the like are not tags
    and are never touched. The value is unescaped (the HTML5 attribute
    way) before it is mapped; one whose target is unchanged keeps its
    bytes, else the new target is escaped into the same quote (double
    quotes for a value that had none). An empty ``href`` is left alone,
    and ``src`` is never looked at.
    """
    targets = LinkTargets(link_map, document_url)
    parser = _StartTags(html)
    parser.feed(html)
    parser.close()
    edits: list[tuple[int, int, str]] = []
    for offset, raw in parser.tags:
        edits.extend(_href_edits(offset, raw, targets))
    if not edits:
        return html
    # Tags come in document order and an edit lies inside its tag, so
    # the edits are in order and disjoint: one forward pass.
    parts: list[str] = []
    pos = 0
    for start, end, new in edits:
        parts.append(html[pos:start])
        parts.append(new)
        pos = end
    parts.append(html[pos:])
    return "".join(parts)
