"""The HTML -> Markdown translator against the #208 fixtures (Trac #209).

Three checks over the 19 documents in ``tests/fixtures/trac_html/``:

* **Golden files.** ``<slug>.golden.md`` is what the converter emits for
  ``<slug>.html`` with ``<slug>.tracwiki`` as the source. Each was read
  by hand once before it was committed. After an intended change,
  ``UPDATE_GOLDEN=1 pytest tests/test_html_to_markdown_fixtures.py``
  rewrites them; read the diff before committing it.
* **Structure self-check.** The emitted Markdown is rendered with
  markdown-it-py (CommonMark plus tables) and its *block skeleton* (the
  nested tree of ``ul``/``ol``/``li``/``blockquote``/``pre``/``table``/
  ``h1``-``h6``/``hr``/``dl``) is compared with Trac's. A golden file can
  be wrong and still equal itself; this check asks whether the Markdown
  means the same structure, with no human in the loop.
* **The check is sensitive.** With nested list items flattened in the
  converter the self-check must fail (a check is not trusted until it
  has been seen failing on a deliberately broken input).
"""

import json
import os
from pathlib import Path

import pytest
from lxml import html as lxml_html
from markdown_it import MarkdownIt

from trac_mcp_server.converters import html_to_markdown as h2m
from trac_mcp_server.converters.html_to_markdown import (
    trac_html_to_markdown,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "trac_html"
SLUGS = sorted(p.stem for p in FIXTURE_DIR.glob("*.html"))

SKELETON_TAGS = frozenset(
    {"ul", "ol", "li", "blockquote", "table", "hr", "dl"}
    | {f"h{n}" for n in range(1, 7)}
)

#: What the converter drops by design, so the skeleton does not expect
#: it from Trac's side: tables of contents, TicketQuery's paging and
#: progress chrome, the workflow graph placeholder and its system
#: message.
DROPPED_BY_DESIGN = (
    "//div[contains(concat(' ', normalize-space(@class), ' '),"
    " ' wiki-toc ')]",
    "//div[contains(concat(' ', normalize-space(@class), ' '),"
    " ' wiki-toc-un ')]",
    "//div[contains(concat(' ', normalize-space(@class), ' '),"
    " ' paging ')]",
    "//div[contains(concat(' ', normalize-space(@class), ' '),"
    " ' trac-groupprogress ')]",
    "//div[contains(concat(' ', normalize-space(@class), ' '),"
    " ' system-message ')]",
    "//div[contains(concat(' ', normalize-space(@class), ' '),"
    " ' trac-noscript ')]",
    "//noscript",
    # A macro that rendered no items: no markup to carry over.
    "//ul[not(li)]",
    "//ol[not(li)]",
    # What is left of a quote whose only content was dropped chrome.
    "//p[not(*) and not(normalize-space())]",
    "//blockquote[not(*) and not(normalize-space())]",
)


def _load(slug):
    return (
        (FIXTURE_DIR / f"{slug}.html").read_text(encoding="utf-8"),
        (FIXTURE_DIR / f"{slug}.tracwiki").read_text(encoding="utf-8"),
    )


def _convert(slug):
    html, source = _load(slug)
    return trac_html_to_markdown(html, source)


def _is_code(el):
    classes = (el.get("class") or "").split()
    return el.tag == "pre" or (
        el.tag == "div" and "wiki-code" in classes
    )


def _walk(el):
    out = []
    for child in el:
        if not isinstance(child.tag, str):
            continue
        if _is_code(child):
            out.append(("pre", ()))
            continue
        kids = _walk(child)
        if child.tag in SKELETON_TAGS:
            out.append((child.tag, tuple(kids)))
        else:
            out.extend(kids)
    return out


def skeleton(markup, *, trac=False):
    """The nested tree of block elements in ``markup``."""
    root = lxml_html.fragment_fromstring(markup, create_parent="div")
    if trac:
        for xpath in DROPPED_BY_DESIGN:
            for el in root.xpath(xpath):
                el.drop_tree()
    return _walk(root)


def render(markdown):
    return MarkdownIt("commonmark").enable("table").render(markdown)


def _without(nodes, tag):
    """``nodes`` with every ``tag`` node replaced by its children."""
    out = []
    for name, kids in nodes:
        kids = tuple(_without(kids, tag))
        if name == tag:
            out.extend(kids)
        else:
            out.append((name, kids))
    return out


def _compare(slug, text=None):
    html, _ = _load(slug)
    if text is None:
        text = _convert(slug).text
    return (
        skeleton(html, trac=True),
        skeleton(render(text)),
    )


#: Fixtures whose skeletons differ for a reason that is not a defect.
#: Each maps to ``(reason, normalise)``: the skeletons must differ
#: as they are and agree once ``normalise`` is applied to both, so an
#: entry cannot hide a second, unrelated difference.
def _no_dl(nodes):
    return _without(nodes, "dl")


EXPECTED_EXCEPTIONS = {
    "stock_TicketQuery": (
        "definition lists: CommonMark has no `dl`, so markdown-it "
        "renders the converter's `term` / `:   definition` form (Pandoc "
        "and PHP Markdown Extra, chosen on purpose) as a paragraph. A "
        "`[[TicketQuery(id=123)]]` result inside a quote is a `dl`; the "
        "rest of the skeleton matches",
        _no_dl,
    ),
}


def _golden_path(slug):
    return FIXTURE_DIR / f"{slug}.golden.md"


# --- golden files -----------------------------------------------------------


def test_every_fixture_has_a_golden_file():
    have = {
        p.name[: -len(".golden.md")]
        for p in FIXTURE_DIR.glob("*.golden.md")
    }
    assert have == set(SLUGS)


def test_golden_files_are_listed_in_the_manifest_set():
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text())
    assert set(SLUGS) == set(manifest)


@pytest.mark.parametrize("slug", SLUGS)
def test_golden_markdown(slug):
    text = _convert(slug).text + "\n"
    path = _golden_path(slug)
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(text, encoding="utf-8")
    assert text == path.read_text(encoding="utf-8")


# --- the structure self-check ----------------------------------------------


@pytest.mark.parametrize(
    "slug", [s for s in SLUGS if s not in EXPECTED_EXCEPTIONS]
)
def test_markdown_skeleton_matches_trac(slug):
    trac, mine = _compare(slug)
    assert mine == trac


@pytest.mark.parametrize("slug", sorted(EXPECTED_EXCEPTIONS))
def test_expected_exception_is_exactly_its_stated_reason(slug):
    _reason, normalise = EXPECTED_EXCEPTIONS[slug]
    trac, mine = _compare(slug)
    assert mine != trac, "no longer differs: drop the exception"
    assert normalise(mine) == normalise(trac)


def test_every_exception_names_a_fixture_and_a_reason():
    for slug, (reason, normalise) in EXPECTED_EXCEPTIONS.items():
        assert slug in SLUGS
        assert reason.strip() and callable(normalise)


def test_self_check_fails_when_nested_items_are_flattened(monkeypatch):
    """Seeded defect: nested list items lose their indentation."""
    expected = set(_nested_list_fixtures())  # before the seed
    assert expected, "no fixture nests content: nothing to seed"
    original = h2m._TracConverter.convert_li

    def flattened(self, el, text, parent_tags):
        out = original(self, el, text, parent_tags)
        return "\n".join(line.lstrip(" ") for line in out.split("\n"))

    monkeypatch.setattr(h2m._TracConverter, "convert_li", flattened)
    failing = []
    for slug in SLUGS:
        trac, mine = _compare(slug, _convert(slug).text)
        if slug in EXPECTED_EXCEPTIONS:
            normalise = EXPECTED_EXCEPTIONS[slug][1]
            trac, mine = normalise(trac), normalise(mine)
        if mine != trac:
            failing.append(slug)
    assert failing, "the check did not see the flattening"
    # Every fixture with a list nested in a list item is caught.
    assert expected <= set(failing)


def _nested_list_fixtures():
    """Fixtures whose Markdown (not a pass-through table) nests content.

    That is a ``li`` holding a list, a code block or a quote, written
    as indentation: the only place flattening can bite.
    """

    def has_nested(nodes, inside_li=False):
        for name, kids in nodes:
            if name == "table":
                continue  # raw HTML: indentation means nothing there
            if inside_li and name in ("ul", "ol", "pre", "blockquote"):
                return True
            if has_nested(kids, inside_li or name == "li"):
                return True
        return False

    return [
        s
        for s in SLUGS
        if has_nested(skeleton(render(_convert(s).text)))
    ]
