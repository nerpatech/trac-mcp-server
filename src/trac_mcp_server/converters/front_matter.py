"""YAML front matter on a Markdown export of a Trac page (ticket #212).

Every Markdown export (``wiki_file_pull``, ``trac-mcp wiki-pull``,
``trac-convert --from-wiki``) starts with a front matter block naming the
Trac page it came from. Every push path refuses Markdown carrying
``trac_page`` unless forced: the export is one-way (Trac's HTML
translated to Markdown), and pushing it back re-shapes the page.

The block is a guard against the accidental loop, not against a
determined user: deleting it by hand defeats it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import yaml


def _scalar(value: str) -> str:
    # A JSON string is a valid YAML double-quoted scalar.
    return json.dumps(value, ensure_ascii=False)


def build_front_matter(
    *,
    title: str,
    source: str,
    trac_instance: str,
    trac_page: str,
    trac_version: int | None,
    exported: datetime | None = None,
) -> str:
    """The front matter block, closing fence and one blank line included."""
    when = exported if exported is not None else datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    else:
        when = when.astimezone(UTC)
    version = "null" if trac_version is None else str(int(trac_version))
    return (
        "---\n"
        f"title: {_scalar(title)}\n"
        f"source: {_scalar(source)}\n"
        f"trac_instance: {_scalar(trac_instance)}\n"
        f"trac_page: {_scalar(trac_page)}\n"
        f"trac_version: {version}\n"
        f"exported: {_scalar(when.strftime('%Y-%m-%dT%H:%M:%SZ'))}\n"
        "---\n\n"
    )


def split_front_matter(text: str) -> tuple[str | None, str]:
    """Split a front matter block off the start of ``text``.

    Purely syntactic. A block is an optional ``\\ufeff``, a first line
    that is exactly ``---`` (LF or CRLF), at least one line, then a line
    that is exactly ``---`` followed by LF, CRLF or the end of the text.
    Returns ``(raw text between the fences, the remainder with leading
    CR/LF stripped)``, or ``(None, text)`` unchanged when there is none.
    """
    body = text[1:] if text.startswith("﻿") else text
    if body.startswith("---\r\n"):
        start = 5
    elif body.startswith("---\n"):
        start = 4
    else:
        return None, text
    pos = start
    size = len(body)
    while pos < size:
        nl = body.find("\n", pos)
        end = size if nl == -1 else nl
        line = body[pos:end]
        if line.endswith("\r"):
            line = line[:-1]
        if line == "---" and pos > start:
            raw = body[start:pos]
            raw = raw.removesuffix("\n").removesuffix("\r")
            return raw, body[end + 1 :].lstrip("\r\n")
        if line == "---":
            # Nothing between the fences: not a block.
            return None, text
        if nl == -1:
            break
        pos = nl + 1
    return None, text


def exported_from(text: str) -> dict[str, Any] | None:
    """The front matter, if it says the text was exported from Trac.

    A dict whose ``trac_page`` is a non-empty string; None otherwise (no
    block, a YAML error, a non-mapping, or no usable ``trac_page``).
    """
    raw, _ = split_front_matter(text)
    if raw is None:
        return None
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    page = data.get("trac_page")
    if isinstance(page, str) and page:
        return data
    return None


def instance_path(trac_url: str) -> str:
    """The instance path of a Trac URL: ``http://h:8000/proj`` -> ``/proj``."""
    return urlsplit(trac_url).path.rstrip("/") or "/"
