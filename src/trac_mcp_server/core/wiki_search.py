"""Ranked keyword search over wiki pages (Trac /trac_mcp_server #154).

A port of the BM25 that auto_pm:#257 measured (auto-pm
``scripts/store_index/lexical.py`` and ``corpus.py`` at ``ac538c2``),
with the same settings: k1 1.2, b 0.75, sections cut at every heading,
parts of at most 4000 characters, the page name and heading put in
front of each section's text, tokens of at least two characters. A page
scores as its best section.

Standard library only, and no I/O: ``TracClient`` owns the page cache
and hands this module the sections of the pages in scope.

One departure from the measured arm, forced by the tool's contract
("snippets are as stored: TracWiki, byte-for-byte"): a section keeps
its ``body`` as an exact slice of the page text (line endings and the
blank lines between paragraphs included), where the measured code
rebuilt it from ``splitlines()``. The tokens, and so the scores, are
the same; only the whitespace the tokenizer ignores differs.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# k1/b and the part size are the measured arm's settings.
K1 = 1.2
B = 0.75
MAX_SECTION_CHARS = 4000

# Snippet width on each side of the first query word found.
SNIPPET_RADIUS = 50
# Snippet length when no query word occurs literally in the section.
SNIPPET_FALLBACK_CHARS = 2 * SNIPPET_RADIUS

# Trac's own heading and code-block rules (trac/wiki/parser.py and
# formatter.py, Trac 1.6), as auto_pm:#268 applies them to the splitter
# the measured arm used: a heading is one to six "=" and whitespace, then
# its text; a line inside a {{{ }}} block is never a heading.
HEADING = re.compile(
    r"^\s*(?P<hdepth>={1,6})\s(?P<htext>.*?)"
    r"(?P<hanchor>#[\w:](?<!\d)[\w:.-]*?)?\s*$"
)
_STARTBLOCK = re.compile(r"^\s*\{\{\{(?:(\s*)#!([\w+-][\w+-/]*)|\s*$)")

# A word is letters and digits of any script (``[^\W_]``): Cyrillic
# and accented words are indexed, ``café`` is one token. Only the
# CamelCase split below stays ASCII.
_TOKEN = re.compile(r"[^\W_]+(?:[._/-][^\W_]+)*")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
_SPLIT = re.compile(r"[._/-]")
_PARAGRAPH_BREAK = re.compile(r"\r?\n[ \t]*\r?\n")

STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "did", "do",
        "does", "for", "from", "had", "has", "have", "how", "i", "if", "in", "into", "is",
        "it", "its", "me", "my", "no", "not", "of", "on", "or", "so", "than", "that", "the",
        "their", "then", "there", "these", "they", "this", "to", "was", "we", "were", "what",
        "when", "where", "which", "who", "why", "will", "with", "would", "you", "your",
    }
)  # fmt: skip


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens of ``text``, compound and CamelCase names
    also split into their parts, stopwords and one-character tokens
    dropped."""
    out: list[str] = []
    for tok in _TOKEN.findall(text):
        out.append(tok.lower())
        subs = _SPLIT.split(tok)
        if len(subs) > 1:
            out.extend(s.lower() for s in subs)
        for s in subs:
            # CamelCase parts are cut by ASCII case; a part of a
            # non-ASCII word would be a fragment of it.
            parts = _CAMEL.findall(s) if s.isascii() else ()
            if len(parts) > 1:
                out.extend(p.lower() for p in parts)
    return [t for t in out if len(t) >= 2 and t not in STOPWORDS]


@dataclass(frozen=True)
class Section:
    """The part of a page under one heading (or one part of a long one).

    ``body`` is an exact slice of the stored page text. ``counts`` and
    ``length`` are the term frequencies and token count of the indexed
    text, ``head`` + newline + ``body``, tokenized once when the page is
    cached.
    """

    page: str
    heading: str  # "" for the part before the first heading
    head: str  # "<page> › <heading>", or just the page name
    body: str
    counts: dict[str, int]
    length: int


def _parts(body: str, max_chars: int) -> list[str]:
    """Cut ``body`` into slices of at most ``max_chars``, at paragraph
    breaks where possible, hard where one paragraph is longer.

    Every part is a slice of ``body``: paragraphs that are packed
    together keep the break between them as it was written.
    """
    if len(body) <= max_chars:
        return [body]
    spans: list[tuple[int, int]] = []
    pos = 0
    for brk in _PARAGRAPH_BREAK.finditer(body):
        spans.append((pos, brk.start()))
        pos = brk.end()
    spans.append((pos, len(body)))

    parts: list[str] = []
    cur: tuple[int, int] | None = None
    for start, end in spans:
        while start < end and body[start] in "\r\n":
            start += 1
        while end > start and body[end - 1] in "\r\n":
            end -= 1
        if not body[start:end].strip():
            continue
        while end - start > max_chars:  # one paragraph too long: cut
            if cur is not None:
                parts.append(body[cur[0] : cur[1]])
                cur = None
            parts.append(body[start : start + max_chars])
            start += max_chars
        if start >= end:
            continue
        if cur is not None and end - cur[0] <= max_chars:
            cur = (cur[0], end)
        else:
            if cur is not None:
                parts.append(body[cur[0] : cur[1]])
            cur = (start, end)
    if cur is not None:
        parts.append(body[cur[0] : cur[1]])
    return parts


def _heading_name(m: re.Match[str]) -> str:
    """The heading's text: stripped, and without a closing run of the
    same ``=`` as the opening one (Trac's ``htext`` rule)."""
    name = m.group("htext").strip()
    hdepth = m.group("hdepth")
    if name.endswith(hdepth):
        name = name[: -len(hdepth)].strip()
    return name


def split_sections(
    page: str, text: str, max_chars: int = MAX_SECTION_CHARS
) -> list[Section]:
    """Cut a page at its heading lines (any level, Trac's pattern);
    drop empty sections. Lines inside ``{{{ }}}`` blocks, which nest,
    are body text, never headings."""
    raw: list[tuple[str, list[str]]] = [("", [])]
    depth = 0
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        m = HEADING.match(bare) if depth == 0 else None
        if m:
            raw.append((_heading_name(m), []))
            continue
        raw[-1][1].append(line)
        if "}}}" not in bare and _STARTBLOCK.match(bare):
            depth += 1
        elif bare.strip() == "}}}" and depth > 0:
            depth -= 1
    out: list[Section] = []
    for heading, lines in raw:
        body = "".join(lines).strip()
        if not body:
            continue
        head = f"{page} › {heading}" if heading else page
        for part in _parts(body, max_chars):
            counts: dict[str, int] = {}
            tokens = tokenize(f"{head}\n{part}")
            for t in tokens:
                counts[t] = counts.get(t, 0) + 1
            out.append(
                Section(page, heading, head, part, counts, len(tokens))
            )
    return out


class BM25:
    """BM25 over a set of sections: idf and the average length are those
    of the sections given, so an index built over a prefix's pages ranks
    as if the wiki held only those."""

    def __init__(
        self,
        sections: Sequence[Section],
        k1: float = K1,
        b: float = B,
    ):
        self.sections = list(sections)
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.lengths: list[int] = []
        for i, sec in enumerate(self.sections):
            self.lengths.append(sec.length)
            for t, c in sec.counts.items():
                self.postings.setdefault(t, []).append((i, c))
        n = len(self.sections)
        self.avgdl = (sum(self.lengths) / n) if n else 0.0
        self.idf = {
            t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5))
            for t, p in self.postings.items()
        }

    def search(
        self, query: str, k: int | None = None
    ) -> list[tuple[Section, float]]:
        """Sections with a positive score, best first (ties: in index
        order); the best ``k`` of them when ``k`` is given."""
        scores: dict[int, float] = {}
        for term in dict.fromkeys(tokenize(query)):
            for i, tf in self.postings.get(term, ()):
                norm = (
                    1 - self.b + self.b * self.lengths[i] / self.avgdl
                )
                scores[i] = scores.get(i, 0.0) + self.idf[term] * tf * (
                    self.k1 + 1
                ) / (tf + self.k1 * norm)
        ranked = sorted(scores.items(), key=lambda p: (-p[1], p[0]))
        if k is not None:
            ranked = ranked[:k]
        return [(self.sections[i], s) for i, s in ranked]


def make_snippet(section: Section, terms: Sequence[str]) -> str:
    """A slice of the section's ``body`` around the first place any of
    ``terms`` occurs in it (case-insensitively), about
    ``SNIPPET_RADIUS`` characters each side, with ``...`` on a cut end.

    When no term occurs literally (a CamelCase part, say), the start of
    the body. Always cut from the body, never from the synthetic head
    line, so the text between the ellipses is in the stored page.
    """
    body = section.body
    pattern = None
    if terms:
        # Longest first, so at one position the longest term wins.
        alt = "|".join(
            re.escape(t)
            for t in sorted(set(terms), key=len, reverse=True)
        )
        pattern = re.compile(alt, re.IGNORECASE)
    m = pattern.search(body) if pattern else None
    if m is not None:
        start = max(0, m.start() - SNIPPET_RADIUS)
        end = min(len(body), m.end() + SNIPPET_RADIUS)
    else:
        start = 0
        end = min(len(body), SNIPPET_FALLBACK_CHARS)
    snippet = body[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(body):
        snippet = snippet + "..."
    return snippet


def search_pages(index: BM25, query: str) -> list[dict[str, Any]]:
    """Every page with a positive score, as dicts ``name``, ``score``,
    ``snippet``, ordered by (-score, name).

    A page scores as its best section (the first one on a tie), and the
    snippet is cut from that section.
    """
    best: dict[str, tuple[Section, float]] = {}
    for section, score in index.search(query):
        held = best.get(section.page)
        if held is None or score > held[1]:
            best[section.page] = (section, score)
    terms = list(dict.fromkeys(tokenize(query)))
    hits = [
        {
            "name": name,
            "score": score,
            "snippet": make_snippet(section, terms),
        }
        for name, (section, score) in best.items()
    ]
    hits.sort(key=lambda h: (-h["score"], h["name"]))
    return hits
