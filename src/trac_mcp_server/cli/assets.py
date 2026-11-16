"""Download the Trac attachments an exported page references, and point
its links at the local copies (Trac /trac_mcp_server #215).

Shared by ``trac-mcp wiki-pull --assets`` and ``trac-convert --from-wiki
--assets``; nothing here knows how an attachment is fetched. A CLI scans
the text with :func:`scan_text`, fetches each reference its own way, and
passes the bytes to :func:`plan_assets`, which names the local files and
rewrites the text. Nothing touches the disk until :func:`write_assets`,
so a CLI that fetches everything first can stop on a failed fetch with
nothing written.

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
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


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
# exporter percent-encodes the ones that are not).
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


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    kind: str  # "md" | "autolink" | "attr"
    ref: AttachmentRef


@dataclass(frozen=True)
class Scan:
    """The attachment URLs of one text: ``hits`` in document order (what
    :func:`plan_assets` rewrites) and the distinct ``refs`` in order of
    first appearance. One scan serves the fetch and the rewrite."""

    hits: tuple[_Hit, ...]
    refs: tuple[AttachmentRef, ...]


def _bad_segment(segment: str) -> bool:
    return (
        not segment
        or segment in (".", "..")
        or any(c in segment for c in "/\\:\0")
    )


def _parse_url(
    url: str, prefix: str, host: str | None
) -> AttachmentRef | None:
    """The attachment ``url`` names, or None."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.query or parts.fragment or "?" in url or "#" in url:
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
            element = _ELEMENT_OPEN.search(text, pos, limit)
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
        backslashes = 0
        k = start
        while k > begin and text[k - 1] == "\\":
            backslashes += 1
            k -= 1
        if backslashes % 2:  # the first backtick is escaped, a literal
            if length == 1:
                pos = end
                continue
            start += 1
            length -= 1
        # The closer is the next run of exactly ``length`` (an escaped
        # first backtick shortens the opener by one).
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

    def in_code(pos: int) -> bool:
        i = bisect_right(starts, pos) - 1
        return i >= 0 and pos < regions[i][1]

    def add(m: re.Match, group: int, kind: str, unescape: bool = False):
        raw = m.group(group)
        if raw is None:
            return
        where = m.start() if kind == "autolink" else m.start(group)
        if in_code(where):
            return
        ref = _parse_url(
            html.unescape(raw) if unescape else raw, prefix, host
        )
        if ref is None:
            return
        if kind == "autolink":
            hits.append(_Hit(m.start(), m.end(), kind, ref))
        else:
            hits.append(_Hit(m.start(group), m.end(group), kind, ref))

    if fmt != "html":
        angled: set[int] = set()  # where a ``](<URL>)`` target's "<" is
        for m in _MD_TARGET.finditer(text):
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
    return Scan(tuple(kept), tuple(seen))


def find_refs(
    text: str, instance: str, fmt: str = "markdown"
) -> list[AttachmentRef]:
    """The distinct attachments ``text`` references, in order of first
    appearance."""
    return list(scan_text(text, instance, fmt).refs)


def fetch_all(
    refs: list[AttachmentRef],
    fetch: Callable[[AttachmentRef], bytes | None],
) -> dict[AttachmentRef, bytes | None]:
    """Fetch every reference through ``fetch``, in order. ``fetch``
    returns the bytes, None for an attachment that does not exist, and
    raises for any other failure -- which stops here, with the later ones
    not fetched."""
    return {ref: fetch(ref) for ref in refs}


@dataclass
class AssetPlan:
    """What ``--assets`` will do, decided and not yet done."""

    text: str  # the text with its links rewritten
    files: dict[str, bytes] = field(
        default_factory=dict
    )  # path under the assets directory, "/"-separated -> bytes
    missing: list[AttachmentRef] = field(default_factory=list)
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
    start = (
        os.path.abspath(os.path.dirname(output_path) or ".")
        if output_path is not None
        else os.getcwd()
    )
    rel = os.path.relpath(
        os.path.abspath(os.path.join(assets_dir, *parts)), start
    )
    return quote(Path(rel).as_posix(), safe="/")


def _label(name: str) -> str:
    return re.sub(r"([\\\[\]])", r"\\\1", name)


def plan_assets(
    text: str,
    instance: str,
    fetched: Mapping[AttachmentRef, bytes | None],
    assets_dir: str | os.PathLike,
    output_path: str | None,
    assets_url: str | None = None,
    *,
    fmt: str = "markdown",
    scan: Scan | None = None,
) -> AssetPlan:
    """Name the local files and rewrite ``text``.

    ``fetched`` maps every reference of ``text`` (:func:`find_refs`) to
    its bytes, or None for one that was not found: that one keeps its
    original URLs. ``output_path`` is the file the text will be written
    to (None for stdout, which links from the current directory).
    ``assets_url``, when given, is the URL prefix the files are served
    from and replaces the relative path as the link. ``scan`` is the
    :func:`scan_text` the caller already made of this ``text``.

    Two attachments whose paths differ only in letter case (or one a file
    where the other needs a directory) cannot both be saved on a
    case-insensitive file system: the first, in order of appearance, is
    saved and the later one is warned about and keeps its URLs.
    """
    if scan is None:
        scan = scan_text(text, instance, fmt)
    plan = AssetPlan(text=text)
    links: dict[AttachmentRef, str] = {}
    file_keys: dict[tuple[str, ...], str] = {}
    dir_keys: dict[tuple[str, ...], str] = {}
    for ref in scan.refs:
        data = fetched[ref]
        if data is None:
            plan.missing.append(ref)
            plan.warnings.append(
                f"attachment '{ref.filename}' of {ref.describe()} was "
                "not found; its links are left as they are"
            )
            continue
        parts = ref.parts()
        path = "/".join(parts)
        key = tuple(p.casefold() for p in parts)
        other = file_keys.get(key) or dir_keys.get(key)
        for n in range(1, len(key)):
            other = other or file_keys.get(key[:n])
        if other is not None:
            plan.warnings.append(
                f"attachment '{ref.filename}' of {ref.describe()} is "
                f"not saved: its path '{path}' clashes with '{other}' "
                "(the same path apart from letter case, or a file and "
                "a directory of one name); its links are left as they "
                "are"
            )
            continue
        file_keys[key] = path
        for n in range(1, len(key)):
            dir_keys.setdefault(key[:n], path)
        plan.files[path] = data
        links[ref] = _link(parts, assets_dir, output_path, assets_url)

    out: list[str] = []
    pos = 0
    for hit in scan.hits:
        link = links.get(hit.ref)
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
    plan.text = "".join(out)
    return plan


def write_assets(
    plan: AssetPlan, assets_dir: str | os.PathLike
) -> tuple[int, int]:
    """Write the planned files under ``assets_dir`` (and the directories
    between, created if needed): a file already there with identical
    bytes is left alone, one with other bytes is overwritten -- it is the
    same attachment, updated. Returns ``(written, unchanged)``."""
    if not plan.files:
        return 0, 0
    directory = Path(assets_dir)
    written = unchanged = 0
    for name, data in plan.files.items():
        path = directory.joinpath(*name.split("/"))
        if path.is_file() and path.read_bytes() == data:
            unchanged += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written += 1
    return written, unchanged


def summary(
    plan: AssetPlan, assets_dir: str | os.PathLike, unchanged: int
) -> str:
    """The one-line report: ``Saved N attachments to DIR (M unchanged, K
    missing)``, where N counts every attachment now in DIR and M those
    of them that were already there with identical bytes."""
    n = len(plan.files)
    noun = "attachment" if n == 1 else "attachments"
    return (
        f"Saved {n} {noun} to {assets_dir} ({unchanged} unchanged, "
        f"{len(plan.missing)} missing)"
    )
