"""Trac-rendered HTML -> Markdown (Trac #209, phase 1 of #206).

``trac_html_to_markdown`` turns the HTML Trac renders for a wiki page,
ticket description or comment into Markdown. It is a subclass of
markdownify's ``MarkdownConverter`` that overrides only what is
Trac-specific; everything else is markdownify's own mapping. Nothing
calls it yet (that is #210): it exists with its tests and its golden
files, and the old TracWiki -> Markdown read leg is untouched.

The rules come from #208's measurements of 2,542 real documents:

* A code block is ``div.wiki-code``, which wraps ``div.code > pre``,
  ``table.code`` (``lineno=``/``marks=``) and ``div.diff``. Trac's HTML
  does not say which language it was, so each block is paired with the
  ``{{{#!lang`` opener of the TracWiki *source* (``source=``). A paired
  block emits the source body, not the HTML text: ``table.code``
  interleaves line numbers with the code and ``div.diff`` has lost its
  ``+``/``-`` columns.
* Anything the converter does not cover passes through as HTML with a
  warning that names it (an allowlist of tags, classes and attributes),
  so no content is lost silently.
"""

from __future__ import annotations

import copy
import re
import string
import textwrap

from bs4 import BeautifulSoup, NavigableString, Tag
from markdownify import (
    ATX,
    BACKSLASH,
    MarkdownConverter,
    abstract_inline_conversion,
    chomp,
)

from .common import ConversionResult, tracwiki_to_markdown_lang

__all__ = ["trac_html_to_markdown"]

# =============================================================================
# The TracWiki source: which {{{ }}} blocks make a code block
# =============================================================================
#
# Trac's WikiParser reads a processor name as [\w+-][\w+/-]*, so
# `{{{#!/bin/sh` opens a plain block and keeps the line as content.

_OPEN_RE = re.compile(
    r"^\s*\{\{\{\s*(?:#!([\w+-][\w+/-]*)(?:\s.*)?|#!.*)?$"
)
_PROC_LINE_RE = re.compile(r"^\s*#!([\w+-][\w+/-]*)(?:\s|$)")
_CLOSE_RE = re.compile(r"^\s*\}\}\}\s*$")

#: Processors whose body is parsed as wiki markup: a block nested in one
#: is a block of its own.
_WIKI_PROCESSORS = frozenset({"div", "span", "table", "tr", "td", "th"})
#: Processors that never produce ``div.wiki-code``.
_NO_CODE_PROCESSORS = _WIKI_PROCESSORS | {
    "comment",
    "html",
    "htmlcomment",
    "default",
    "Workflow",
}


def _scan_code_blocks(source: str) -> list[tuple[str, str]]:
    """Return ``(processor, body)`` for each block that renders as code.

    In document order. A block nested in a wiki processor counts; a block
    nested in a plain or code block is content, and so is a plain block
    (``pre.wiki`` is not ``div.wiki-code``).
    """
    lines = source.splitlines()
    stack: list[dict] = []
    blocks: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        literal = any(not b["wiki"] for b in stack)
        opener = _OPEN_RE.match(line)
        if opener:
            name = opener.group(1)
            if name is None and i + 1 < len(lines) and not literal:
                proc = _PROC_LINE_RE.match(lines[i + 1])
                if proc:
                    name = proc.group(1)
                    i += 1
            for b in stack:
                if not b["wiki"]:
                    b["body"].append(line)
            block = {
                "name": name or "",
                "literal": literal,
                "wiki": name in _WIKI_PROCESSORS and not literal,
                "body": [],
            }
            stack.append(block)
            blocks.append(block)
        else:
            closing = _CLOSE_RE.match(line) and stack
            if closing:
                stack.pop()
            for b in stack:
                if not b["wiki"]:
                    b["body"].append(line)
        i += 1
    return [
        (b["name"], textwrap.dedent("\n".join(b["body"])))
        for b in blocks
        if not b["literal"]
        and b["name"]
        and b["name"] not in _NO_CODE_PROCESSORS
    ]


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


#: The lines of a unified diff that Trac's ``div.diff`` does not show.
_DIFF_HEADER_RE = re.compile(r"^(?:---|\+\+\+|@@)")


def _code_agrees(el: Tag, body: str) -> bool:
    """Whether the source ``body`` is the text Trac rendered in ``el``.

    Whitespace does not count. A ``div.diff`` has lost the ``+``/``-``
    columns (and the header lines, which a renderer may leave out), so
    the body is compared with its leading column removed from each line.
    A ``table.code`` is compared on its ``td`` cells only, which is what
    ``_code_text`` returns.
    """
    want = _squash(_code_text(el))
    if el.select_one("div.diff") is None:
        return want == _squash(body)
    lines = body.split("\n")
    shown = [ln for ln in lines if not _DIFF_HEADER_RE.match(ln)]
    return want in {
        _squash("\n".join(ln[1:] for ln in kept))
        for kept in (lines, shown)
    }


def _code_text(el: Tag) -> str:
    """The text of a ``div.wiki-code`` as Trac rendered it.

    A ``table.code`` or ``div.diff`` keeps its code in ``td`` cells (the
    ``th`` cells are line numbers); the header row is not code.
    """
    if el.find("table") is None:
        return el.get_text()
    cells = [
        td.get_text()
        for td in el.find_all("td")
        if td.find_parent("thead") is None
    ]
    return "".join(c if c.endswith("\n") else c + "\n" for c in cells)


# =============================================================================
# What the converter covers
# =============================================================================

_TABLE_TAGS = frozenset(
    {
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "caption",
        "colgroup",
        "col",
    }
)
_COVERED_TAGS = _TABLE_TAGS | {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "dd",
    "del",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "u",
    "ul",
}
#: Container elements that are not inline: a pass-through puts blank
#: lines around their tags so the Markdown inside is still parsed.
_BLOCK_TAGS = frozenset(
    {
        "blockquote",
        "center",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "noscript",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "ul",
    }
)
#: Parents in which a text node at the start sits at the start of a line.
_LINE_START_PARENTS = frozenset(
    {
        "[document]",
        "html",
        "body",
        "p",
        "li",
        "blockquote",
        "div",
        "dd",
        "dt",
        "section",
        "article",
    }
)
#: Inline parents that add no markup of their own: a text node at their
#: start sits where they sit.
_TRANSPARENT_INLINE = frozenset({"span", "small"})

_LETTERED_LISTS = frozenset(
    {"loweralpha", "upperalpha", "lowerroman", "upperroman"}
)
_ANY = None
_ALLOWED_CLASSES: dict[str, frozenset[str] | None] = {
    "a": _ANY,  # status classes (closed ticket, ext-link, ...)
    "li": _ANY,  # active / False in a TOC, entry
    "ol": _LETTERED_LISTS,
    "blockquote": frozenset({"citation"}),
    "pre": frozenset({"wiki"}),
    "dl": frozenset({"wiki", "compact"}),
    "span": frozenset(
        {
            "underline",
            "icon",
            "wikianchor",
            "numresults",
            "current",
            "next",
            "query_no_results",
            "trac-author",
            "trac-author-user",
        }
    ),
    "div": frozenset(
        {
            "wiki-toc",
            "wiki-toc-un",
            "titleindex",
            "tracini",
            "trac-subscriberlist",
            "trac-macrolist",
            "mimetypes",
        }
    ),
}
for _tag in _TABLE_TAGS:  # Trac's row/column/macro chrome
    _ALLOWED_CLASSES[_tag] = _ANY
for _level in range(1, 7):
    _ALLOWED_CLASSES[f"h{_level}"] = frozenset(
        {"section", "report-result"}
    )

_ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title", "rel", "style", "name"}),
    "img": frozenset(
        {"src", "alt", "title", "crossorigin", "width", "height"}
    ),
    "ol": frozenset({"start"}),
    "pre": frozenset({"data-trac-lang"}),
    "td": frozenset({"colspan", "rowspan", "style", "align"}),
    "th": frozenset({"colspan", "rowspan", "style", "align"}),
}
_ALWAYS_ALLOWED_ATTRS = frozenset({"id", "class"})

#: Macro output translated through the lists and tables it contains, by
#: the selector that identifies it. One warning per macro.
_MACRO_OUTPUT = (
    ("div.titleindex", "TitleIndex"),
    ("div.tracini", "TracIni"),
    ("div.trac-subscriberlist", "SubscriberList"),
    ("div.trac-macrolist", "MacroList"),
    ("div.mimetypes", "KnownMimeTypes"),
    ("table.intertrac", "InterTrac"),
    ("table.interwiki", "InterWiki"),
    ("table.listing.tickets", "TicketQuery"),
    ("dl.compact", "TicketQuery"),
    ("h2.report-result", "TicketQuery"),
)
#: TicketQuery's paging and progress chrome: dropped.
_MACRO_CHROME = "div.paging, div.trac-groupprogress"

_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_PUNCTUATION = frozenset(string.punctuation)


def _classes(el: Tag) -> list[str]:
    value = el.get("class") or []
    return value.split() if isinstance(value, str) else list(value)


def _describe(el: Tag, bad_attrs=()) -> str:
    desc = el.name + "".join("." + c for c in _classes(el))
    if bad_attrs:
        desc += "[" + ",".join(sorted(bad_attrs)) + "]"
    return desc


def _text_align_only(style: str) -> bool:
    props = [
        d.split(":", 1)[0].strip().lower()
        for d in style.split(";")
        if d.strip()
    ]
    return bool(props) and all(p == "text-align" for p in props)


def _dest(url: str) -> str:
    """A link destination that cannot end the link early."""
    url = re.sub(r"[ <>]", lambda m: f"%{ord(m.group()):02X}", url)
    depth = 0
    for ch in url:
        depth += (ch == "(") - (ch == ")")
        if depth < 0:
            break
    if depth != 0:
        url = url.replace("(", "%28").replace(")", "%29")
    return url


_PRE_RE = re.compile(r"<pre\b.*?</pre>", re.S)
_BLANK_RUN_RE = re.compile(r"[ \t]*\n(?:[ \t]*\n)+[ \t]*")


def _without_blank_lines(markup: str) -> str:
    """``markup`` with no blank line in it (it would end an HTML block).

    Outside a ``pre`` a run of blank lines is one line break. A blank
    line inside a ``pre`` is kept as a line holding ``&#32;``, so the
    code text survives.
    """
    out = []
    pos = 0
    for pre in _PRE_RE.finditer(markup):
        out.append(_BLANK_RUN_RE.sub("\n", markup[pos : pre.start()]))
        out.append(
            "\n".join(
                f"{ln}&#32;" if not ln.strip(" \t") else ln
                for ln in pre.group().split("\n")
            )
        )
        pos = pre.end()
    out.append(_BLANK_RUN_RE.sub("\n", markup[pos:]))
    return "".join(out)


# Markdown text escaping ------------------------------------------------

_LINE_START_ESCAPES = (
    (re.compile(r"^(#{1,6})(?=\s|$)"), lambda m: "\\" + m.group(0)),
    (re.compile(r"^>"), lambda m: "\\>"),
    (re.compile(r"^[-+](?=\s|$)"), lambda m: "\\" + m.group(0)),
    (re.compile(r"^(?=[-=]+\s*$)."), lambda m: "\\" + m.group(0)),
    (
        re.compile(r"^(\d{1,9})([.)])(?=\s|$)"),
        lambda m: m.group(1) + "\\" + m.group(2),
    ),
)


def _escape_line_start(text: str, first_line: bool) -> str:
    lines = text.split("\n")
    for n, line in enumerate(lines):
        if n == 0 and not first_line:
            continue
        for pattern, repl in _LINE_START_ESCAPES:
            line, hits = pattern.subn(repl, line, count=1)
            if hits:
                break
        lines[n] = line
    return "\n".join(lines)


# =============================================================================
# The converter
# =============================================================================


class _TracConverter(MarkdownConverter):
    class Options(MarkdownConverter.DefaultOptions):
        bs4_options = "lxml"
        heading_style = ATX
        bullets = "-*+"
        newline_style = BACKSLASH
        sup_symbol = "<sup>"
        sub_symbol = "<sub>"
        autolinks = True
        keep_inline_images_in = [
            "a",
            "p",
            "li",
            "strong",
            "em",
            "td",
            "th",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        ]

    def __init__(
        self,
        source: str | None,
        link_map: dict[str, str] | None,
        keep_toc: bool,
        deflist: str,
    ):
        super().__init__(code_language_callback=self._pre_language)
        self._source = source
        self._link_map = sorted(
            (link_map or {}).items(), key=lambda kv: -len(kv[0])
        )
        self._keep_toc = keep_toc
        self._deflist = deflist
        self.warnings: list[str] = []

    # --- entry --------------------------------------------------------

    def convert(self, html):
        soup = BeautifulSoup(html, **self.options["bs4_options"])
        self._prepare(soup)
        return self.convert_soup(soup)

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    # --- a pass over the tree before conversion ------------------------

    def _prepare(self, soup: BeautifulSoup) -> None:
        self._pair_code_blocks(soup)
        for selector, macro in _MACRO_OUTPUT:
            if soup.select_one(selector) is not None:
                self._warn(
                    f"{macro} output exported as rendered, live content"
                )
        for el in soup.select("ol"):
            for cls in _classes(el):
                if cls in _LETTERED_LISTS:
                    self._warn(
                        f"{cls} list exported as a numbered list "
                        "(Markdown has no lettered or roman lists)"
                    )
        self._drop(soup, "span.icon, span.wikianchor")
        self._drop(soup, _MACRO_CHROME)
        for el in soup.select("div.system-message"):
            quoted = " ".join(el.get_text().split())
            self._warn(f'dropped system message: "{quoted}"')
            el.decompose()
        if soup.select_one("div.trac-noscript") is not None:
            self._warn(
                "dropped the workflow graph placeholder "
                "(a #!Workflow block needs JavaScript)"
            )
            self._drop(soup, "div.trac-noscript")
        for el in soup.select("noscript"):
            quoted = " ".join(el.get_text().split())
            if quoted:
                self._warn(f'dropped noscript content: "{quoted}"')
            el.decompose()
        if not self._keep_toc and soup.select_one(
            "div.wiki-toc, div.wiki-toc-un"
        ):
            self._warn(
                "table of contents dropped (a PageOutline or "
                "TracGuideToc macro); pass keep_toc=True to keep it"
            )
            self._drop(soup, "div.wiki-toc, div.wiki-toc-un")

    @staticmethod
    def _drop(soup: BeautifulSoup, selector: str) -> None:
        for el in soup.select(selector):
            if not el.decomposed:
                el.decompose()

    def _pair_code_blocks(self, soup: BeautifulSoup) -> None:
        """Replace each ``div.wiki-code`` by a ``pre`` holding its text.

        The ``pre`` carries the language as ``data-trac-lang``;
        ``convert_pre`` reads it through ``code_language_callback``.
        """
        blocks = soup.select("div.wiki-code")
        if not blocks:
            return
        pairs: list[tuple[str, str] | None] = [None] * len(blocks)
        if self._source is not None:
            found = _scan_code_blocks(self._source)
            if len(found) == len(blocks) and all(
                _code_agrees(el, body)
                for el, (_, body) in zip(blocks, found, strict=True)
            ):
                pairs = list(found)
            else:
                pairs = self._pair_by_text(blocks, found)
                missing = pairs.count(None)
                if missing:
                    self._warn(
                        f"{missing} code block"
                        f"{'s' if missing != 1 else ''} with no "
                        "matching source block: exported without a "
                        "language, from the rendered text"
                    )
        for el, pair in zip(blocks, pairs, strict=True):
            if pair is None:
                lang, body = "", _code_text(el)
            else:
                lang, body = tracwiki_to_markdown_lang(pair[0]), pair[1]
            pre = soup.new_tag("pre")
            pre["data-trac-lang"] = lang
            pre.append(NavigableString(body))
            el.replace_with(pre)

    @staticmethod
    def _pair_by_text(blocks, found):
        """Pair by content when the counts differ or a pair disagrees.

        That happens with macro-emitted code (``[[TracIni]]``), whose
        blocks have no opener in the source. A block with no source
        block of its text stays unpaired.
        """
        unused = list(range(len(found)))
        pairs: list[tuple[str, str] | None] = []
        for el in blocks:
            hit = None
            for k in unused:
                if _code_agrees(el, found[k][1]):
                    hit = k
                    break
            if hit is None:
                pairs.append(None)
            else:
                unused.remove(hit)
                pairs.append(found[hit])
        return pairs

    # --- what is covered, and what passes through ------------------------

    def process_tag(self, node, parent_tags=None):
        if parent_tags is None:
            parent_tags = set()
        name = node.name
        if name == "table":
            reason = self._table_reason(node)
            if reason:
                self._warn(f"{reason}; passed through as HTML")
                return "\n\n" + self._html_of(node) + "\n\n"
        elif name not in ("[document]", "html", "body"):
            desc = self._uncovered(node)
            if desc:
                self._warn(
                    f"{desc} is not covered; passed through as HTML"
                )
                return self._passthrough(node, parent_tags)
            if name == "img" and (
                "width" in node.attrs or "height" in node.attrs
            ):
                # Markdown cannot size an image: HTML, and no warning
                # (nothing is lost).
                return self._passthrough(node, parent_tags)
        return super().process_tag(node, parent_tags)

    def _uncovered(self, el: Tag) -> str | None:
        """A description of ``el`` if the converter does not cover it."""
        if el.name not in _COVERED_TAGS:
            return _describe(el)
        allowed = _ALLOWED_CLASSES.get(el.name, frozenset())
        classes = _classes(el)
        if (
            classes
            and allowed is not _ANY
            and not set(classes) <= allowed
        ):
            return _describe(el)
        permitted = _ALWAYS_ALLOWED_ATTRS | _ALLOWED_ATTRS.get(
            el.name, frozenset()
        )
        bad = set(el.attrs) - permitted
        if el.name in ("td", "th") and "style" in el.attrs:
            if _text_align_only(str(el.get("style"))):
                bad.discard("style")
            else:
                bad.add("style")
        if bad:
            return _describe(el, bad)
        return None

    def _table_reason(self, table: Tag) -> str | None:
        """Why this table cannot be a pipe table, if it cannot."""
        desc = self._uncovered(table)  # find_all below skips the table
        if desc:
            return f"{desc} is not covered"
        for el in table.find_all(["td", "th"]):
            if el.has_attr("colspan") or el.has_attr("rowspan"):
                return (
                    "table with colspan/rowspan cells (a pipe table "
                    "cannot express spans)"
                )
        for cell in table.find_all(["td", "th"]):
            block = cell.find(
                [
                    "ul",
                    "ol",
                    "pre",
                    "table",
                    "blockquote",
                    "dl",
                    "hr",
                    "div",
                    "form",
                ]
            )
            if block is not None:
                return (
                    f"table with a {block.name} inside a cell (a "
                    "pipe table cell holds one line)"
                )
        for el in table.find_all(True):
            if el.name in _TABLE_TAGS:
                desc = self._uncovered(el)
                if desc:
                    return f"table holding {desc}"
        return None

    def _passthrough(self, el: Tag, parent_tags) -> str:
        """An element no rule covers: its own HTML, as Trac rendered it.

        Nothing inside is converted: Markdown written inside raw tags
        would be read as text or as a second block, so the whole subtree
        is serialised. Its uncovered descendants are named in warnings,
        as they would be had the converter reached them.
        """
        for inner in el.find_all(True):
            if inner.name == "table":
                reason = self._table_reason(inner)
                if reason:
                    self._warn(f"{reason}; passed through as HTML")
                continue
            desc = self._uncovered(inner)
            if desc:
                self._warn(
                    f"{desc} is not covered; passed through as HTML"
                )
        markup = self._html_of(el)
        if el.name in _BLOCK_TAGS and "_inline" not in parent_tags:
            return f"\n\n{markup}\n\n"
        return markup

    def _html_of(self, el: Tag) -> str:
        """The element as clean HTML, with no blank line in it.

        Trac's chrome is removed (link and image attributes, a cell's
        text alignment, the classes of covered tags, ``missing`` links),
        ``link_map`` is applied to every ``href`` (never to a ``src``)
        and nothing else changes. A blank line would end a Markdown HTML
        block, so none is left in the markup.
        """
        el = copy.copy(el)
        for a in el.find_all("a"):
            if "missing" in _classes(a):
                a.unwrap()
        for tag in (el, *el.find_all(True)):
            if tag.get("href"):
                tag["href"] = self._map_url(tag["href"])
            if tag.name == "a":
                for attr in ("title", "rel"):
                    tag.attrs.pop(attr, None)
            elif tag.name == "img":
                tag.attrs.pop("crossorigin", None)
            elif tag.name in ("td", "th"):
                style = tag.get("style")
                if style is not None and _text_align_only(str(style)):
                    del tag["style"]
                tag.attrs.pop("align", None)
            if tag.name in _COVERED_TAGS and tag.name not in (
                "div",
                "span",
            ):
                self._strip_chrome_classes(tag)
        for pre in el.find_all("pre"):
            if "data-trac-lang" not in pre.attrs:
                continue  # Trac's own pre: as it is
            lang = pre.attrs.pop("data-trac-lang")
            text = pre.get_text()
            pre.clear()
            code = Tag(name="code")
            if lang:
                code["class"] = f"language-{lang}"
            code.append(NavigableString(text))
            pre.append(code)
        return _without_blank_lines(str(el))

    @staticmethod
    def _strip_chrome_classes(tag: Tag) -> None:
        """Drop the classes Trac gives a covered tag; keep unknown ones."""
        if "class" not in tag.attrs:
            return
        allowed = _ALLOWED_CLASSES.get(tag.name, frozenset())
        if allowed is _ANY:
            del tag["class"]
            return
        kept = [c for c in _classes(tag) if c not in allowed]
        if kept:
            tag["class"] = kept
        else:
            del tag["class"]

    # --- links and images ------------------------------------------------

    def _map_url(self, url):
        """Apply ``link_map`` to one ``href``, longest prefix first.

        An in-page anchor (``#...``) is never rewritten, whatever the
        map says. Images are not mapped at all (#215).
        """
        if url and not url.startswith("#"):
            for prefix, replacement in self._link_map:
                if url.startswith(prefix):
                    return replacement + url[len(prefix) :]
        return url

    def convert_a(self, el, text, parent_tags):
        if "_noformat" in parent_tags or "missing" in _classes(el):
            return text
        prefix, suffix, text = chomp(text)
        if not text:
            return ""
        href = el.get("href")
        if not href:
            return f"{prefix}{text}{suffix}"
        target = self._map_url(href)
        if (
            target == href
            and el.get_text().strip() == href
            and _URL_SCHEME_RE.match(href)
            and not re.search(r"[\s<>]", href)
        ):
            return f"{prefix}<{href}>{suffix}"
        return f"{prefix}[{text}]({_dest(target)}){suffix}"

    def convert_img(self, el, text, parent_tags):
        alt = (el.get("alt") or "").replace("]", "\\]")
        if (
            "_inline" in parent_tags
            and el.parent.name
            not in self.options["keep_inline_images_in"]
        ):
            return alt
        src = el.get("src") or ""
        title = " ".join((el.get("title") or "").split())
        if title:
            title = title.replace("\\", "\\\\").replace('"', '\\"')
            return f'![{alt}]({_dest(src)} "{title}")'
        return f"![{alt}]({_dest(src)})"

    # --- code ---------------------------------------------------------------

    @staticmethod
    def _pre_language(el):
        return el.get("data-trac-lang") or ""

    def convert_pre(self, el, text, parent_tags):
        if not text:
            return ""
        lang = self.options["code_language_callback"](el)
        text = text.strip("\n")
        run = max((len(m) for m in re.findall(r"`+", text)), default=0)
        fence = "`" * max(3, run + 1)
        return f"\n\n{fence}{lang}\n{text}\n{fence}\n\n"

    # --- lists and line breaks ---------------------------------------------

    def convert_list(self, el, text, parent_tags):
        out = super().convert_list(el, text, parent_tags)
        prev = el.previous_sibling
        while isinstance(prev, NavigableString) and not prev.strip():
            prev = prev.previous_sibling
        if isinstance(prev, Tag) and prev.name == el.name:
            # Trac closes a list and opens the next; two lists in a
            # row would merge into one, so a comment keeps them apart.
            out = "\n\n<!-- -->" + out
        return out

    convert_ul = convert_list
    convert_ol = convert_list

    def convert_br(self, el, text, parent_tags):
        rest = [
            s
            for s in el.next_siblings
            if not isinstance(s, NavigableString) or s.strip()
        ]
        if not rest and not text:
            return ""  # a break at the end of a block breaks nothing
        return super().convert_br(el, text, parent_tags)

    # --- inline markup --------------------------------------------------------

    convert_u = abstract_inline_conversion(lambda self: "<u>")

    def convert_span(self, el, text, parent_tags):
        if "underline" in _classes(el):
            return self.convert_u(el, text, parent_tags)
        return text

    def convert_small(self, el, text, parent_tags):
        return text

    # --- tables -----------------------------------------------------------------

    def convert_td(self, el, text, parent_tags):
        return super().convert_td(
            el, text.replace("|", "\\|"), parent_tags
        )

    def convert_th(self, el, text, parent_tags):
        return super().convert_th(
            el, text.replace("|", "\\|"), parent_tags
        )

    # --- definition lists ----------------------------------------------------------

    def convert_dt(self, el, text, parent_tags):
        if self._deflist != "bold":
            return super().convert_dt(el, text, parent_tags)
        text = " ".join((text or "").split())
        if "_inline" in parent_tags:
            return f" {text} "
        return f"\n\n**{text}**\n\n" if text else "\n"

    def convert_dd(self, el, text, parent_tags):
        if self._deflist != "bold":
            return super().convert_dd(el, text, parent_tags)
        text = (text or "").strip()
        if "_inline" in parent_tags:
            return f" {text} "
        return f"\n\n{text}\n\n" if text else "\n"

    # --- escaping ----------------------------------------------------------------------

    def escape(self, text, parent_tags):
        """Escape only what could change the Markdown structure."""
        if not text:
            return ""
        out = []
        for i, ch in enumerate(text):
            before = text[i - 1] if i else ""
            after = text[i + 1] if i + 1 < len(text) else ""
            if ch == "\\":
                bad = (
                    not after
                    or after in _PUNCTUATION
                    or after in "\r\n"
                )
                out.append("\\\\" if bad else ch)
            elif ch in "*`":
                out.append("\\" + ch)
            elif ch == "_":
                inside = before.isalnum() and after.isalnum()
                out.append(ch if inside else "\\_")
            elif ch == "<" and re.match(r"[A-Za-z/!?]", after):
                out.append("\\<")
            elif ch == "&" and re.match(r"#?\w+;", text[i + 1 :]):
                out.append("\\&")
            elif ch in "[]~":  # `~` because `del` is written `~~`
                out.append("\\" + ch)
            else:
                out.append(ch)
        return "".join(out)

    def process_text(self, el, parent_tags=None):
        text = super().process_text(el, parent_tags)
        if text and "_noformat" not in (parent_tags or ()):
            text = _escape_line_start(text, self._at_line_start(el))
        return text

    @staticmethod
    def _at_line_start(el) -> bool:
        prev = el.previous_sibling
        while isinstance(prev, NavigableString) and not prev.strip():
            prev = prev.previous_sibling
        if prev is None:
            parent = el.parent
            if parent.name in _LINE_START_PARENTS:
                return True
            if parent.name in _TRANSPARENT_INLINE and (
                "underline" not in _classes(parent)
            ):
                return _TracConverter._at_line_start(parent)
            return False
        return isinstance(prev, Tag) and (
            prev.name == "br" or prev.name in _BLOCK_TAGS
        )


def trac_html_to_markdown(
    html: str,
    source: str | None = None,
    *,
    link_map: dict[str, str] | None = None,
    keep_toc: bool = False,
    deflist: str = "extra",
) -> ConversionResult:
    """Convert the HTML Trac rendered for a document to Markdown.

    Args:
        html: Trac's HTML for the document.
        source: The TracWiki the HTML was rendered from. It names each
            code block's language and supplies its exact text; without
            it, code blocks carry no language.
        link_map: URL prefix -> replacement prefix, applied to
            ``a[href]`` (also inside raw HTML), longest prefix first.
            An in-page anchor (``#...``) and ``img[src]`` are never
            rewritten.
        keep_toc: Keep the table of contents a ``[[PageOutline]]`` or
            ``[[TracGuideToc]]`` macro renders; it is dropped otherwise.
        deflist: ``"extra"`` writes a definition list as ``term`` /
            ``:   definition`` (Pandoc, PHP Markdown Extra); ``"bold"``
            writes a bold term followed by the definition paragraph.

    Returns:
        A ``ConversionResult`` whose warnings name every lossy mapping,
        each once.
    """
    if deflist not in ("extra", "bold"):
        raise ValueError(
            f"deflist must be 'extra' or 'bold', not {deflist!r}"
        )
    converter = _TracConverter(source, link_map, keep_toc, deflist)
    text = converter.convert(html)
    return ConversionResult(
        text=text,
        source_format="html",
        target_format="markdown",
        converted=True,
        warnings=converter.warnings,
    )
