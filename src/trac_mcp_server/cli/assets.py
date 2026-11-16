"""Download the Trac attachments an exported page references, and point
its links at the local copies (Trac /trac_mcp_server #215).

Shared by ``trac-mcp wiki-pull --assets`` and ``trac-convert --from-wiki
--assets``; nothing here knows how an attachment is fetched. A CLI scans
the text with :func:`scan_text`, opens an :class:`AssetStage`, fetches
each reference its own way and hands every result to
:meth:`AssetStage.add`, which checks the path an attachment that will be
saved would go to (:meth:`AssetStage.preflight`: a fetch only reads, and
a missing or too-large attachment has no path to check) and writes the
bytes to a temporary sibling of the final file at once (so no payload is
kept in memory). :meth:`AssetStage.plan` then names the rewritten text, the
output is staged the same way, and :meth:`AssetStage.commit` renames
everything into place, assets first and the output last. Until the
commit the stage can be dropped (:meth:`AssetStage.abort`, or leaving
its ``with`` block) and the assets directory and the output are exactly
as they were.

Trac renders an attachment as absolute URLs: an image as
``<a href=".../attachment/wiki/PAGE/x.png"><img
src=".../raw-attachment/wiki/PAGE/x.png"></a>``, which the Markdown
export keeps as ``[![alt](.../raw-attachment/...)](.../attachment/...)``.
Both URLs of one attachment map to the same local file.

Layout: an attachment's local path is decided by its identity alone,
never by which other attachments the page has or which were found. A
wiki attachment is ``DIR/<page path>/<file>`` (the page path's segments
become directories: page ``blog/my-post``, file ``x.png`` ->
``DIR/blog/my-post/x.png``), a ticket attachment
``DIR/ticket-<N>/<file>``.

A URL is an attachment of THIS instance when its path, percent-decoded
segment by segment, is ``<instance>/raw-attachment/<realm>/<parent>/<file>``
or ``<instance>/attachment/...``, with realm ``wiki`` or ``ticket``. When
the instance has a path (``/proj``) the scheme and host are ignored,
because the base URL Trac renders with can name another host or port than
the one the CLI connects to. An instance served from the root has no path
to tell its URLs from any other Trac's, so an absolute URL there must be on
the instance's own host (hostname only: not scheme or port). A URL with a
query or fragment is not an attachment (``?format=raw``, ``#L3``), and an
external URL never matches. A path segment (or the file name) that is
empty, ``.`` or ``..``, or holds a ``/``, a backslash, a ``:`` or a NUL, is
not a reference: it could not be a local file name everywhere.

Only URL positions are looked at -- a Markdown link or image target, an
autolink, and a quoted ``src``/``href`` attribute value -- and never code:
see :func:`_code_regions`.
"""

from __future__ import annotations

import html
import os
import re
import secrets
import shutil
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import SplitResult, quote, unquote, urlsplit


@dataclass(frozen=True)
class AttachmentRef:
    """One attachment: ``realm`` is ``wiki`` or ``ticket``; ``parent`` is
    the wiki page name (it may contain ``/``) or the ticket number."""

    realm: str
    parent: str
    filename: str

    def describe(self) -> str:
        if self.realm == "ticket":
            return f"ticket #{self.parent}"
        return f"wiki page '{self.parent}'"

    def parts(self) -> list[str]:
        """The local path under the assets directory, as segments."""
        if self.realm == "ticket":
            return [f"ticket-{self.parent}", self.filename]
        return [*self.parent.split("/"), self.filename]


def instance_prefix(instance: str) -> str:
    """The URL path prefix of an instance, from its path (``/proj``) or a
    URL (``http://h/proj``); the empty string for an instance at the
    root."""
    if "://" in instance:
        instance = urlsplit(instance).path
    return instance.rstrip("/")


def _instance_host(instance: str) -> str | None:
    """The hostname of an instance given as a URL; None when it is only
    a path."""
    if "://" not in instance:
        return None
    try:
        return urlsplit(instance).hostname
    except ValueError:
        return None


# A Markdown link or image target: ``](URL)``, ``](URL "title")``,
# ``](<URL>)``. The URL has no space; a parenthesis in it is balanced (the
# exporter percent-encodes the ones that are not). A ``]`` that is
# backslash-escaped closes no label (:func:`_escaped`): a match there is
# left out by the scan.
_MD_TARGET = re.compile(
    r"\]\(\s*(?:<([^<>\s]*)>|((?:[^\s()<>]|\([^\s()<>]*\))*))"
)
# An autolink, as the exporter writes a link whose text is its URL.
_AUTOLINK = re.compile(r"<(https?://[^\s<>]+)>")
# A quoted src/href attribute value, in HTML and in the raw HTML the
# Markdown export passes through.
_HTML_ATTR = re.compile(
    r"(?<![\w:-])(?:src|href)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')",
    re.IGNORECASE,
)

# An HTML tag opener with its attributes, as ``_ELEMENT_OPEN`` reads one
# (the length bound is the same); used to find the tags Markdown prose
# escapes (``\<img src="...">``), whose attributes are text.
_TAG_OPEN = re.compile(
    r"<[A-Za-z][\w:-]*(?:[^>\"']|\"[^\"]*\"|'[^']*'){0,1000}>"
)


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    kind: str  # "md" | "autolink" | "attr"
    ref: AttachmentRef


@dataclass(frozen=True)
class Scan:
    """The attachment URLs of one text: ``hits`` in document order (what
    :meth:`AssetPlanner.result` rewrites) and the distinct ``refs`` in
    order of first appearance. One scan serves the fetch and the rewrite.

    ``unmatched`` counts the URLs that have an attachment's path shape but
    are not under the instance path (``prefix``): Trac behind a proxy with
    another base path renders such URLs. They are left alone."""

    hits: tuple[_Hit, ...]
    refs: tuple[AttachmentRef, ...]
    unmatched: int = 0
    prefix: str = ""


def _bad_segment(segment: str) -> bool:
    return (
        not segment
        or segment in (".", "..")
        or any(c in segment for c in "/\\:\0")
    )


def _scoped(
    url: str, prefix: str, host: str | None
) -> SplitResult | None:
    """The parts of ``url`` when it can be a URL of this instance by
    scheme and host: ``http(s)`` or root-relative, and -- for an instance
    at the root, whose path says nothing -- on its own host. None
    otherwise."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme in ("http", "https"):
        pass
    elif parts.scheme == "" and url.startswith("/"):
        pass  # root-relative, or protocol-relative
    else:
        return None
    if not prefix and parts.netloc:
        # An instance at the root: the path says nothing, so the host
        # must be its own. Root-relative URLs have no host and match.
        try:
            named = parts.hostname
        except ValueError:
            return None
        if host is None or named != host:
            return None
    return parts


def _parse_url(
    url: str, prefix: str, host: str | None
) -> AttachmentRef | None:
    """The attachment ``url`` names, or None."""
    parts = _scoped(url, prefix, host)
    if parts is None:
        return None
    if parts.query or parts.fragment or "?" in url or "#" in url:
        return None
    segments = parts.path.split("/")
    # parts.path starts with "/": segments[0] is "".
    want = [*prefix.split("/")[1:]] if prefix else []
    head = segments[1:]
    if head[: len(want)] != want:
        return None
    rest = head[len(want) :]
    if len(rest) < 4 or rest[0] not in ("raw-attachment", "attachment"):
        return None
    realm = rest[1]
    if realm not in ("wiki", "ticket"):
        return None
    decoded = [unquote(s) for s in rest[2:]]
    if any(_bad_segment(s) for s in decoded):
        return None
    if realm == "ticket":
        number = decoded[0]
        if len(decoded) != 2 or not (
            number.isascii() and number.isdigit()
        ):
            return None
        return AttachmentRef("ticket", number, decoded[1])
    return AttachmentRef("wiki", "/".join(decoded[:-1]), decoded[-1])


_SHAPE = re.compile(r"/(?:raw-)?attachment/(?:wiki|ticket)/")


def _misses_instance(url: str, prefix: str, host: str | None) -> bool:
    """Whether ``url`` has the path of a Trac attachment but is not under
    the instance path: a URL :func:`_parse_url` did not take that is not
    just malformed or another host's. Where the host decides (an instance
    at the root), another host's URL is expected -- a link to another
    Trac -- and is no miss."""
    parts = _scoped(url, prefix, host)
    if parts is None:
        return False
    shape = _SHAPE.search(parts.path)
    return shape is not None and parts.path[: shape.start()] != prefix


# --- code is not a reference ----------------------------------------------
#
# A URL inside code is text about a URL, not a link: fetching it would
# download something the page only quotes, and rewriting it would change
# the quoted text. The regions below are found first; a URL that starts
# inside one is skipped.
#
# Markdown: fenced code blocks (CommonMark: ``` or ~~~, a closing fence of
# the same character and at least the opening length; one that is never
# closed runs to the end), inline code spans (a backtick run closed by a
# run of equal length, within one paragraph), and the content of the raw
# HTML elements below. HTML: the content of those elements only.
#
# Indented code blocks are NOT handled: the converter never emits one (it
# always fences), and a four-space indent is also how a list item
# continues, so reading it as code would hide real links.

_CODE_ELEMENTS = "pre|code|script|style|textarea"
# The open tag is at most 1000 characters (attributes, quoted values may
# hold ``>``): an unbounded match makes a text of many ``<pre `` with no
# ``>`` quadratic.
_ELEMENT_OPEN = re.compile(
    rf"<({_CODE_ELEMENTS})(?![\w:-])"
    r"(?:[^>\"']|\"[^\"]*\"|'[^']*'){0,1000}>",
    re.IGNORECASE,
)
_ELEMENT_CLOSE = {
    name: re.compile(rf"</{name}\s*>", re.IGNORECASE)
    for name in _CODE_ELEMENTS.split("|")
}
# A fence line: any indentation and block-quote markers (the fence may be
# inside a list item or a quote), then the fence run and an info string.
_FENCE_OPEN = re.compile(r"[ \t>]*(`{3,}|~{3,})([^\n]*)")
_FENCE_CLOSE = re.compile(r"[ \t>]*(`{3,}|~{3,})[ \t\r]*")
_TICKS = re.compile(r"`+")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def _fenced_blocks(text: str) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    opened: tuple[int, str, int] | None = None  # start, char, length
    pos = 0
    size = len(text)
    while pos < size:
        nl = text.find("\n", pos)
        end = size if nl == -1 else nl
        if opened is None:
            m = _FENCE_OPEN.fullmatch(text, pos, end)
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                opened = (pos, m.group(1)[0], len(m.group(1)))
        else:
            m = _FENCE_CLOSE.fullmatch(text, pos, end)
            if (
                m
                and m.group(1)[0] == opened[1]
                and len(m.group(1)) >= opened[2]
            ):
                blocks.append((opened[0], end))
                opened = None
        pos = end + 1
    if opened is not None:
        blocks.append((opened[0], size))
    return blocks


def _element_content(
    text: str, el: re.Match, limit: int
) -> tuple[tuple[int, int] | None, int]:
    """The content span of the code element opened by ``el`` and where
    scanning resumes. An element never closed runs to ``limit``."""
    if el.group(0).endswith("/>"):
        return None, el.end()
    close = _ELEMENT_CLOSE[el.group(1).lower()].search(
        text, el.end(), limit
    )
    if close is None:
        return (el.end(), limit), limit
    return (el.end(), close.start()), close.end()


def _escaped(text: str, pos: int, begin: int) -> bool:
    """Whether the character at ``pos`` is backslash-escaped: an odd
    number of backslashes (back to ``begin``) precede it."""
    k = pos
    while k > begin and text[k - 1] == "\\":
        k -= 1
    return (pos - k) % 2 == 1


def _html_regions(
    text: str, begin: int, limit: int, spans: list[tuple[int, int]]
) -> None:
    pos = begin
    while True:
        el = _ELEMENT_OPEN.search(text, pos, limit)
        if el is None:
            return
        span, pos = _element_content(text, el, limit)
        if span is not None:
            spans.append(span)


def _inline_regions(
    text: str, begin: int, limit: int, spans: list[tuple[int, int]]
) -> None:
    """Inline code spans and raw code elements of Markdown prose, left to
    right: whichever opens first wins, so a literal ``<code>`` inside a
    span opens no element."""

    def next_element(at: int) -> re.Match | None:
        # The exporter writes a literal tag in prose as ``\\<pre>``: an
        # escaped ``<`` opens nothing.
        while True:
            el = _ELEMENT_OPEN.search(text, at, limit)
            if el is None or not _escaped(text, el.start(), begin):
                return el
            at = el.start() + 1

    runs = [
        (m.start(), m.end())
        for m in _TICKS.finditer(text, begin, limit)
    ]
    starts = [r[0] for r in runs]
    by_length: dict[int, list[int]] = {}
    for i, (s, e) in enumerate(runs):
        by_length.setdefault(e - s, []).append(i)
    blanks = [
        m.start() for m in _BLANK_LINE.finditer(text, begin, limit)
    ]
    element: re.Match | None = None
    elements_done = False
    pos = begin
    while pos < limit:
        i = bisect_left(starts, pos)
        tick = starts[i] if i < len(runs) else limit
        if not elements_done and (
            element is None or element.start() < pos
        ):
            element = next_element(pos)
            elements_done = element is None
        if element is not None and element.start() < tick:
            span, pos = _element_content(text, element, limit)
            if span is not None:
                spans.append(span)
            continue
        if i == len(runs):
            return
        start, end = runs[i]
        length = end - start
        if _escaped(
            text, start, begin
        ):  # the first backtick is a literal
            if length == 1:
                pos = end
                continue
            start += 1
            length -= 1
        # The closer is the next run of exactly ``length`` (an escaped
        # first backtick shortens the opener by one). A backslash before
        # a closer does not escape it: in a code span backslash is
        # literal (CommonMark), and ``C:\\dir\\`` is a span.
        same = by_length.get(length, [])
        nxt = bisect_right(same, i)
        if nxt == len(same):
            pos = end
            continue
        close_start, close_end = runs[same[nxt]]
        b = bisect_left(blanks, end)
        if b < len(blanks) and blanks[b] < close_start:
            pos = end  # a blank line ends the paragraph: no span
            continue
        spans.append((start, close_end))
        pos = close_end


def _code_regions(text: str, fmt: str) -> list[tuple[int, int]]:
    """The ``(start, end)`` spans of ``text`` that are code, sorted."""
    spans: list[tuple[int, int]] = []
    if fmt == "html":
        _html_regions(text, 0, len(text), spans)
        return spans
    pos = 0
    for start, end in _fenced_blocks(text):
        spans.append((start, end))
        _inline_regions(text, pos, start, spans)
        pos = end
    _inline_regions(text, pos, len(text), spans)
    spans.sort()
    return spans


def scan_text(text: str, instance: str, fmt: str = "markdown") -> Scan:
    """Find the attachment URLs of ``text``, an export of an ``instance``
    page. ``fmt`` is ``markdown`` or ``html``; ``instance`` is its path
    (``/proj``) or URL (needed for the host of a root-served instance)."""
    prefix = instance_prefix(instance)
    host = _instance_host(instance)
    regions = _code_regions(text, fmt)
    starts = [r[0] for r in regions]
    hits: list[_Hit] = []
    unmatched = 0

    def in_code(pos: int) -> bool:
        i = bisect_right(starts, pos) - 1
        return i >= 0 and pos < regions[i][1]

    # Markdown prose: a tag whose ``<`` is backslash-escaped is text, so
    # the attributes inside it are not URL positions.
    escaped_tags: list[tuple[int, int]] = []
    if fmt != "html":
        escaped_tags = [
            (m.start(), m.end())
            for m in _TAG_OPEN.finditer(text)
            if _escaped(text, m.start(), 0)
        ]
    tag_starts = [t[0] for t in escaped_tags]

    def in_escaped_tag(pos: int) -> bool:
        i = bisect_right(tag_starts, pos) - 1
        return i >= 0 and pos < escaped_tags[i][1]

    def add(m: re.Match, group: int, kind: str, unescape: bool = False):
        nonlocal unmatched
        raw = m.group(group)
        if raw is None:
            return
        where = m.start() if kind == "autolink" else m.start(group)
        if in_code(where) or (kind == "attr" and in_escaped_tag(where)):
            return
        url = html.unescape(raw) if unescape else raw
        ref = _parse_url(url, prefix, host)
        if ref is None:
            if _misses_instance(url, prefix, host):
                unmatched += 1
            return
        if kind == "autolink":
            hits.append(_Hit(m.start(), m.end(), kind, ref))
        else:
            hits.append(_Hit(m.start(group), m.end(group), kind, ref))

    if fmt != "html":
        angled: set[int] = set()  # where a ``](<URL>)`` target's "<" is
        for m in _MD_TARGET.finditer(text):
            if _escaped(text, m.start(), 0):
                continue  # ``\\](URL)``: the label is not closed here
            if m.group(1) is not None:
                angled.add(m.start(1) - 1)
            add(m, 1 if m.group(1) is not None else 2, "md")
        for m in _AUTOLINK.finditer(text):
            if m.start() in angled:
                continue  # ``](<URL>)``: a link target, found above
            add(m, 1, "autolink")
    for m in _HTML_ATTR.finditer(text):
        add(
            m, 1 if m.group(1) is not None else 2, "attr", unescape=True
        )

    hits.sort(key=lambda h: h.start)
    kept: list[_Hit] = []
    for h in hits:
        if kept and h.start < kept[-1].end:
            continue  # inside another hit's span
        kept.append(h)
    seen: dict[AttachmentRef, None] = {}
    for h in kept:
        seen.setdefault(h.ref)
    return Scan(tuple(kept), tuple(seen), unmatched, prefix)


class AssetError(Exception):
    """The assets or the output could not be written; the message says
    what, and the stage has removed what it had written so far."""


class AssetPathError(AssetError):
    """A planned path cannot hold the attachment, found before anything
    was fetched or written: a local-path problem, not a failed write."""


@dataclass(frozen=True)
class TooLarge:
    """The outcome of a fetch the server refused for size: ``limit`` is
    the server's inline limit in bytes, None if it did not say."""

    limit: int | None = None


Outcome = bytes | TooLarge | None  # None: the attachment does not exist


@dataclass
class AssetPlan:
    """What ``--assets`` did or will do, decided."""

    text: str  # the text with its links rewritten
    saved: list[str] = field(
        default_factory=list
    )  # path under the assets directory, "/"-separated, of every
    # attachment that is (or will be) in it
    unchanged: int = 0  # how many of ``saved`` were already there as is
    missing: list[AttachmentRef] = field(default_factory=list)
    too_large: list[AttachmentRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _link(
    parts: list[str],
    assets_dir: str | os.PathLike,
    output_path: str | None,
    assets_url: str | None,
) -> str:
    if assets_url is not None:
        return (
            assets_url.rstrip("/")
            + "/"
            + "/".join(quote(seg) for seg in parts)
        )
    # The output is written to its real place (a symlink is written
    # through), so the links are relative to the directory of THAT.
    start = (
        os.path.dirname(os.path.realpath(output_path))
        if output_path is not None
        else os.getcwd()
    )
    rel = os.path.relpath(
        os.path.abspath(os.path.join(assets_dir, *parts)), start
    )
    return quote(Path(rel).as_posix(), safe="/")


def _label(name: str) -> str:
    """A file name as the text of a Markdown link: escaped like ordinary
    text, so ``*draft*.png`` renders as written."""
    out = re.sub(r"([\\*_`\[\]<>~#])", r"\\\1", name)
    return "\\" + out if out.startswith("!") else out


class AssetPlanner:
    """Names the local file of each attachment and rewrites the text. Pure:
    it touches no disk and keeps no bytes -- :meth:`place` is told what
    each fetch gave, in order of first appearance, and says where the file
    goes.

    Two attachments cannot both be saved when they have one path (a wiki
    page named ``ticket-5`` and ticket #5), paths that differ only in
    letter case (a case-insensitive file system keeps them apart no
    better), or one is a file where the other needs a directory: the
    first, in order of appearance, is saved and the later one is warned
    about and keeps its URLs.
    """

    def __init__(
        self,
        scan: Scan,
        assets_dir: str | os.PathLike,
        output_path: str | None,
        assets_url: str | None = None,
    ) -> None:
        self._scan = scan
        self._assets_dir = assets_dir
        self._output_path = output_path
        self._assets_url = assets_url
        self._links: dict[AttachmentRef, str] = {}
        self._files: dict[
            tuple[str, ...], tuple[str, AttachmentRef]
        ] = {}
        self._dirs: dict[tuple[str, ...], str] = {}
        self.saved: list[str] = []
        self.missing: list[AttachmentRef] = []
        self.too_large: list[AttachmentRef] = []
        self.warnings: list[str] = []

    def _clash(
        self,
        ref: AttachmentRef,
        parts: list[str],
        path: str,
        key: tuple[str, ...],
    ) -> str | None:
        same = self._files.get(key)
        if same is not None:
            first_path, first = same
            if first_path == path:
                return (
                    f"{first.describe()} and {ref.describe()} both map "
                    f"to {path}; keeping the first"
                )
            return (
                f"its path '{path}' differs from '{first_path}' only in "
                "letter case, which a case-insensitive file system "
                "cannot keep apart"
            )
        directory = self._dirs.get(key)
        if directory is not None:
            return (
                f"its path '{path}' is a file, but '{directory}' needs "
                "it as a directory"
            )
        for n in range(1, len(key)):
            ancestor = self._files.get(key[:n])
            if ancestor is not None:
                return (
                    f"its path '{path}' needs '{'/'.join(parts[:n])}' "
                    f"as a directory, but '{ancestor[0]}' is a file"
                )
        return None

    def place(self, ref: AttachmentRef, outcome: Outcome) -> str | None:
        """Where ``ref``'s file goes (a path under the assets directory,
        "/"-separated), or None when it is not saved: not found, too
        large, or in a clash. Only the kind of ``outcome`` is looked at."""
        if outcome is None:
            self.missing.append(ref)
            self.warnings.append(
                f"attachment '{ref.filename}' of {ref.describe()} was "
                "not found; its links are left as they are"
            )
            return None
        if isinstance(outcome, TooLarge):
            self.too_large.append(ref)
            limit = (
                f"{outcome.limit} bytes (max_inline_bytes)"
                if outcome.limit is not None
                else "its max_inline_bytes"
            )
            self.warnings.append(
                f"attachment '{ref.filename}' of {ref.describe()} is "
                f"over the server's inline limit of {limit} and was not "
                "fetched; its links are left as they are"
            )
            return None
        parts = ref.parts()
        path = "/".join(parts)
        key = tuple(p.casefold() for p in parts)
        reason = self._clash(ref, parts, path, key)
        if reason is not None:
            self.warnings.append(
                f"attachment '{ref.filename}' of {ref.describe()} is "
                f"not saved: {reason}; its links are left as they are"
            )
            return None
        self._files[key] = (path, ref)
        for n in range(1, len(key)):
            self._dirs.setdefault(key[:n], path)
        self.saved.append(path)
        self._links[ref] = _link(
            parts, self._assets_dir, self._output_path, self._assets_url
        )
        return path

    def result(self, text: str, unchanged: int = 0) -> AssetPlan:
        """The plan, with ``text`` rewritten (``text`` is the one the
        scan was made of)."""
        out: list[str] = []
        pos = 0
        for hit in self._scan.hits:
            link = self._links.get(hit.ref)
            if link is None:
                continue
            if hit.kind == "autolink":
                # ``<assets/x.png>`` is not an autolink: a relative link
                # needs the inline form, whose label is the file's name.
                new = f"[{_label(hit.ref.filename)}]({link})"
            elif hit.kind == "attr":
                new = html.escape(link, quote=True)
            else:
                new = link
            out.append(text[pos : hit.start])
            out.append(new)
            pos = hit.end
        out.append(text[pos:])
        warnings = list(self.warnings)
        if self._scan.unmatched:
            n = self._scan.unmatched
            warnings.append(
                f"{n} attachment-like URL{'s' if n != 1 else ''} did "
                "not match instance path "
                f"'{self._scan.prefix or '/'}' (Trac behind a proxy with "
                "another base path, or a link_rewrites rule moved "
                f"{'them' if n != 1 else 'it'}); left as "
                f"{'they are' if n != 1 else 'it is'}"
            )
        return AssetPlan(
            text="".join(out),
            saved=list(self.saved),
            unchanged=unchanged,
            missing=list(self.missing),
            too_large=list(self.too_large),
            warnings=warnings,
        )


def check_output_path(path: str) -> str | None:
    """Why the file ``path`` cannot be written, or None: it is a
    directory, or its directory does not exist or cannot be written to
    (the output is replaced by a rename, which needs the directory)."""
    # The file is replaced at its real place: a symlink is followed, so
    # what must exist is the directory of its target.
    target = Path(os.path.realpath(path))
    if target.is_dir():
        return f"output path is a directory: {target}"
    parent = target.parent
    if not parent.is_dir():
        return f"output directory does not exist: {parent}"
    if not os.access(parent, os.W_OK | os.X_OK):
        return f"output directory is not writable: {parent}"
    return None


def _write_temp(path: Path, data: bytes) -> None:
    """Create ``path`` (it must not exist) holding ``data``, with the
    mode a new file gets."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _same_bytes(path: Path, data: bytes) -> bool:
    try:
        return (
            path.is_file()
            and path.stat().st_size == len(data)
            and path.read_bytes() == data
        )
    except OSError:
        return False


class AssetStage:
    """The attachments and the output of one export, written all or
    nothing. Every file is first written to a temporary sibling (same
    directory, ``.<name>.tmp-<random>``), as soon as it is given; only
    :meth:`commit` renames them into place, the assets first and the
    output last. Anything before the commit leaves the assets directory
    (and any directory made for the files) and the output as they were.

    A file already in place with the same bytes is not written at all.
    A failure in the middle of the renames cannot be undone (a replaced
    file is gone): the rest are not renamed and the error says how many
    were.
    """

    def __init__(
        self,
        scan: Scan,
        assets_dir: str | os.PathLike,
        output_path: str | None = None,
        assets_url: str | None = None,
    ) -> None:
        self.scan = scan
        self.planner = AssetPlanner(
            scan, assets_dir, output_path, assets_url
        )
        self.unchanged = 0
        self._dir = Path(assets_dir)
        self._output_path = output_path
        self._assets: list[tuple[Path, Path]] = []  # temporary, final
        self._output: tuple[Path, Path] | None = None
        self._made: list[Path] = []  # directories this stage created

    def __enter__(self) -> AssetStage:
        return self

    def __exit__(self, *exc) -> None:
        self.abort()

    def preflight(
        self, refs: Iterable[AttachmentRef] | None = None
    ) -> None:
        """Check the paths ``refs`` (default: every reference of the
        scan) plan: no existing ancestor under the assets directory may
        be a file (a directory could not be made there), no target may be
        an existing directory, and the output file may be neither a
        target nor inside or around one (it is renamed into place last, a
        file and a directory of one name cannot both be). Raises
        :class:`AssetPathError`. :meth:`add` runs it for each attachment
        it is going to write, after that attachment was fetched, so a
        path that nothing will be written to is no obstacle."""
        output = (
            Path(os.path.realpath(self._output_path))
            if self._output_path is not None
            else None
        )
        for ref in self.scan.refs if refs is None else refs:
            parts = ref.parts()
            node = self._dir
            if node.exists() and not node.is_dir():
                raise AssetPathError(
                    f"--assets: not a directory: {node}; nothing was "
                    "written"
                )
            for i, segment in enumerate(parts):
                node = node / segment
                if not node.exists():
                    break  # nothing below it exists either
                last = i == len(parts) - 1
                if last and node.is_dir():
                    problem = f"{node} is a directory"
                elif not last and not node.is_dir():
                    problem = f"{node} is a file, not a directory"
                else:
                    continue
                raise AssetPathError(
                    f"--assets: cannot save attachment "
                    f"'{ref.filename}' of {ref.describe()} as "
                    f"{self._dir.joinpath(*parts)}: {problem}; "
                    "nothing was written"
                )
            if output is not None:
                place = Path(
                    os.path.realpath(self._dir.joinpath(*parts))
                )
                if (
                    place == output
                    or place in output.parents
                    or output in place.parents
                ):
                    raise AssetPathError(
                        f"--assets: the output file {self._output_path} "
                        f"and attachment '{ref.filename}' of "
                        f"{ref.describe()} (saved as "
                        f"{self._dir.joinpath(*parts)}) cannot share a "
                        "path; nothing was written"
                    )

    def _make_dir(self, directory: Path) -> None:
        missing: list[Path] = []
        node = directory
        while not node.exists():
            missing.append(node)
            node = node.parent
        for node in reversed(missing):
            node.mkdir()
            self._made.append(node)

    def _stage(
        self, final: Path, data: bytes, output: bool = False
    ) -> None:
        self._make_dir(final.parent)
        # The stem is cut by bytes (at a character boundary): a 255-byte
        # name of four-byte characters plus the suffix is over the limit.
        stem = os.fsencode(final.name)[:60].decode("utf-8", "ignore")
        temp = final.with_name(f".{stem}.tmp-{secrets.token_hex(6)}")
        # Recorded before the write, so a half-written file is removed.
        if output:
            self._output = (temp, final)
        else:
            self._assets.append((temp, final))
        _write_temp(temp, data)
        if final.exists():
            try:
                shutil.copymode(final, temp)
            except OSError:
                pass

    def add(self, ref: AttachmentRef, outcome: Outcome) -> None:
        """Take the result of fetching ``ref``: bytes are written to a
        temporary file now and are not kept. Raises
        :class:`AssetPathError` when the path it would be saved at
        cannot hold it (:meth:`preflight`), :class:`AssetError` when the
        bytes cannot be written."""
        path = self.planner.place(ref, outcome)
        if path is None or not isinstance(outcome, bytes):
            return
        self.preflight([ref])
        final = self._dir.joinpath(*path.split("/"))
        try:
            if _same_bytes(final, outcome):
                self.unchanged += 1
                return
            self._stage(final, outcome)
        except OSError as e:
            raise AssetError(
                f"cannot write the attachments to {self._dir}: "
                f"{path}: {e.strerror or e}; nothing was written"
            ) from e

    def plan(self, text: str) -> AssetPlan:
        """The rewritten ``text`` and the report, once every reference
        was :meth:`add`-ed."""
        return self.planner.result(text, self.unchanged)

    def stage_output(self, data: bytes) -> None:
        """Write the output file's temporary sibling (the stage was made
        with an ``output_path``)."""
        assert self._output_path is not None
        final = Path(os.path.realpath(self._output_path))
        if not final.parent.is_dir():
            # check_output_path refuses this before anything is fetched;
            # never make the directory of a file the user named.
            raise AssetError(
                f"cannot write the output file {self._output_path}: "
                f"output directory does not exist: {final.parent}; "
                "nothing was written"
            )
        try:
            self._stage(final, data, output=True)
        except OSError as e:
            raise AssetError(
                f"cannot write the output file {self._output_path}: "
                f"{e.strerror or e}; nothing was written"
            ) from e

    def commit(self) -> None:
        """Rename every temporary file into place, the output last."""
        pairs = list(self._assets)
        if self._output is not None:
            pairs.append(self._output)
        done = 0
        try:
            for temp, final in pairs:
                os.replace(temp, final)
                done += 1
        except OSError as e:
            moved = done
            self.abort()
            raise AssetError(
                f"cannot move {pairs[done][1]} into place: "
                f"{e.strerror or e}; {moved} of {len(pairs)} files were "
                "already moved, the rest were not written"
            ) from e
        self._assets = []
        self._output = None
        self._made = []

    def abort(self) -> None:
        """Remove every temporary file and every directory this stage
        made. Nothing is left to do after a commit."""
        pairs = list(self._assets)
        if self._output is not None:
            pairs.append(self._output)
        for temp, _ in pairs:
            try:
                temp.unlink()
            except OSError:
                pass
        for directory in reversed(self._made):
            try:
                directory.rmdir()
            except OSError:
                pass
        self._assets = []
        self._output = None
        self._made = []


def summary(plan: AssetPlan, assets_dir: str | os.PathLike) -> str:
    """The one-line report: ``Saved N attachments to DIR (M unchanged, K
    missing)``, where N counts every attachment now in DIR and M those
    of them that were already there with identical bytes; ``, S too
    large`` follows when the server refused any for size."""
    n = len(plan.saved)
    noun = "attachment" if n == 1 else "attachments"
    large = (
        f", {len(plan.too_large)} too large" if plan.too_large else ""
    )
    return (
        f"Saved {n} {noun} to {assets_dir} ({plan.unchanged} unchanged, "
        f"{len(plan.missing)} missing{large})"
    )
