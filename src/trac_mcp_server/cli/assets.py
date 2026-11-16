"""Download the Trac attachments an exported page references, and point
its links at the local copies (Trac /trac_mcp_server #215).

Shared by ``trac-mcp wiki-pull --assets`` and ``trac-convert --from-wiki
--assets``; nothing here knows how an attachment is fetched. A CLI finds
the references with :func:`find_refs`, fetches each one its own way, and
passes the bytes to :func:`plan_assets`, which names the local files and
rewrites the text. Nothing touches the disk until :func:`write_assets`,
so a CLI that fetches everything first can stop on a failed fetch with
nothing written.

Trac renders an attachment as absolute URLs: an image as
``<a href=".../attachment/wiki/PAGE/x.png"><img
src=".../raw-attachment/wiki/PAGE/x.png"></a>``, which the Markdown
export keeps as ``[![alt](.../raw-attachment/...)](.../attachment/...)``.
Both URLs of one attachment map to the same local file.

A URL is an attachment of THIS instance when its path, percent-decoded
segment by segment, is ``<instance>/raw-attachment/<realm>/<parent>/<file>``
or ``<instance>/attachment/...``, with realm ``wiki`` or ``ticket``. The
scheme and host are ignored, because the base URL Trac renders with can
name another host than the one the CLI connects to. A URL with a query or
fragment is not one of these (``?format=raw``, ``#L3``), and an external
URL never matches. Only URL positions are looked at: a Markdown link or
image target, an autolink, and a quoted ``src``/``href`` attribute value.
"""

from __future__ import annotations

import html
import os
import re
import xmlrpc.client
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


def instance_prefix(instance: str) -> str:
    """The URL path prefix of an instance, from its path (``/proj``) or a
    URL (``http://h/proj``); the empty string for an instance at the
    root."""
    if "://" in instance:
        instance = urlsplit(instance).path
    return instance.rstrip("/")


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


def _parse_url(url: str, prefix: str) -> AttachmentRef | None:
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
        pass  # root-relative, or protocol-relative: the host is ignored
    else:
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
    if any(not s or "/" in s or "\0" in s for s in decoded):
        return None
    if decoded[-1] in (".", ".."):
        return None
    if realm == "ticket":
        if len(decoded) != 2 or not decoded[0].isdigit():
            return None
        return AttachmentRef("ticket", decoded[0], decoded[1])
    return AttachmentRef("wiki", "/".join(decoded[:-1]), decoded[-1])


def _scan(text: str, instance: str) -> list[_Hit]:
    prefix = instance_prefix(instance)
    hits: list[_Hit] = []

    def add(m: re.Match, group: int, kind: str, unescape: bool = False):
        raw = m.group(group)
        if raw is None:
            return
        ref = _parse_url(
            html.unescape(raw) if unescape else raw, prefix
        )
        if ref is None:
            return
        if kind == "autolink":
            hits.append(_Hit(m.start(), m.end(), kind, ref))
        else:
            hits.append(_Hit(m.start(group), m.end(group), kind, ref))

    for m in _MD_TARGET.finditer(text):
        add(m, 1 if m.group(1) is not None else 2, "md")
    for m in _AUTOLINK.finditer(text):
        if text[: m.start()].rstrip().endswith("]("):
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
    return kept


def find_refs(text: str, instance: str) -> list[AttachmentRef]:
    """The distinct attachments ``text`` references, in order of first
    appearance."""
    seen: dict[AttachmentRef, None] = {}
    for hit in _scan(text, instance):
        seen.setdefault(hit.ref)
    return list(seen)


def as_bytes(data: object) -> bytes:
    """An XML-RPC attachment payload as bytes."""
    if isinstance(data, xmlrpc.client.Binary):
        return data.data
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        return data.encode("utf-8")
    raise ValueError(
        f"unexpected attachment payload type: {type(data).__name__}"
    )


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
    )  # name -> bytes
    missing: list[AttachmentRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_UNSAFE = re.compile(r"[^\w.-]", re.UNICODE)


def _safe(parent: str) -> str:
    return _UNSAFE.sub("-", parent).lstrip(".") or "-"


def _local_names(
    refs: list[AttachmentRef], warnings: list[str]
) -> dict[AttachmentRef, str]:
    """A distinct file name per attachment: the attachment's own name,
    else ``<parent>--<name>`` when another attachment already took it
    (and, only if that is taken too, ``<parent>--<n>--<name>``)."""
    names: dict[AttachmentRef, str] = {}
    taken: set[str] = set()
    for ref in refs:
        name = ref.filename
        if name in taken:
            name = f"{_safe(ref.parent)}--{ref.filename}"
            n = 2
            while name in taken:
                name = f"{_safe(ref.parent)}--{n}--{ref.filename}"
                n += 1
            warnings.append(
                f"attachment '{ref.filename}' of {ref.describe()} is "
                f"saved as '{name}': '{ref.filename}' is taken by "
                "another attachment"
            )
        taken.add(name)
        names[ref] = name
    return names


def _link(
    name: str,
    assets_dir: str | os.PathLike,
    output_path: str | None,
    assets_url: str | None,
) -> str:
    if assets_url is not None:
        return assets_url.rstrip("/") + "/" + quote(name)
    start = (
        os.path.abspath(os.path.dirname(output_path) or ".")
        if output_path is not None
        else os.getcwd()
    )
    rel = os.path.relpath(
        os.path.abspath(os.path.join(assets_dir, name)), start
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
) -> AssetPlan:
    """Name the local files and rewrite ``text``.

    ``fetched`` maps every reference of ``text`` (:func:`find_refs`) to
    its bytes, or None for one that was not found: that one keeps its
    original URLs. ``output_path`` is the file the text will be written
    to (None for stdout, which links from the current directory).
    ``assets_url``, when given, is the URL prefix the files are served
    from and replaces the relative path as the link.
    """
    plan = AssetPlan(text=text)
    refs = find_refs(text, instance)
    present = [r for r in refs if fetched[r] is not None]
    names = _local_names(present, plan.warnings)
    for ref in present:
        plan.files[names[ref]] = fetched[ref]  # type: ignore[assignment]
    for ref in refs:
        if fetched[ref] is None:
            plan.missing.append(ref)
            plan.warnings.append(
                f"attachment '{ref.filename}' of {ref.describe()} was "
                "not found; its links are left as they are"
            )

    out: list[str] = []
    pos = 0
    for hit in _scan(text, instance):
        if hit.ref not in names:
            continue
        name = names[hit.ref]
        link = _link(name, assets_dir, output_path, assets_url)
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
    """Write the planned files into ``assets_dir`` (created if needed):
    a file already there with identical bytes is left alone, one with
    other bytes is overwritten. Returns ``(written, unchanged)``."""
    if not plan.files:
        return 0, 0
    directory = Path(assets_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written = unchanged = 0
    for name, data in plan.files.items():
        path = directory / name
        if path.is_file() and path.read_bytes() == data:
            unchanged += 1
            continue
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
