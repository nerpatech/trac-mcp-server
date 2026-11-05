"""Unit tests for core/wiki_search.py (Trac /trac_mcp_server #154).

The module is a port of the BM25 that auto_pm:#257 measured, so these
tests pin the behaviour that was measured: the tokenizer, the section
cut, the BM25 arithmetic (k1 1.2, b 0.75) and the page score (a page
scores as its best section). Nothing here touches a client.
"""

import math

import pytest

from trac_mcp_server.core.wiki_search import (
    BM25,
    STOPWORDS,
    make_snippet,
    search_pages,
    split_sections,
    tokenize,
)


def _sections(pages: dict[str, str]):
    out = []
    for name, text in pages.items():
        out.extend(split_sections(name, text))
    return out


# --------------------------------------------------------------------
# tokenize
# --------------------------------------------------------------------


def test_tokenize_lowercases_and_drops_stopwords_and_short_tokens():
    assert tokenize("Why was my Status change ignored, I think") == [
        "status",
        "change",
        "ignored",
        "think",
    ]
    assert {"the", "why", "was", "my"} <= STOPWORDS


def test_tokenize_splits_camelcase_names_into_parts():
    toks = tokenize("CloseViaWorkflowAction")
    assert toks[0] == "closeviaworkflowaction"
    assert toks[1:] == ["close", "via", "workflow", "action"]


def test_tokenize_splits_compound_names_into_parts():
    toks = tokenize("ticket_update")
    assert toks == ["ticket_update", "ticket", "update"]
    toks = tokenize("Rules/trac/RenderVerify")
    assert toks[0] == "rules/trac/renderverify"
    assert {"rules", "trac", "renderverify", "render", "verify"} <= set(
        toks
    )


def test_tokenize_keeps_digits_and_acronym_runs_apart():
    assert "http" in tokenize("HTTPServer2")
    assert "server" in tokenize("HTTPServer2")
    assert "2" not in tokenize("HTTPServer2")  # min length is 2


# --------------------------------------------------------------------
# split_sections
# --------------------------------------------------------------------


def test_split_sections_cuts_at_every_heading_and_prefixes_the_head():
    text = "intro words\n= One =\nbody one\n== Two ==\nbody two\n"
    secs = split_sections("Some/Page", text)
    assert [(s.heading, s.body) for s in secs] == [
        ("", "intro words"),
        ("One", "body one"),
        ("Two", "body two"),
    ]
    # The page name and the heading are indexed with the section: a
    # heading-only word finds the section, as in the measured arm.
    assert "one" in secs[1].counts and "page" in secs[1].counts
    assert secs[0].head == "Some/Page"
    assert secs[1].head == "Some/Page › One"


def test_split_sections_drops_empty_sections():
    secs = split_sections("P", "= A =\n= B =\ntext\n")
    assert [s.heading for s in secs] == ["B"]


def test_split_sections_body_is_a_substring_of_the_stored_text():
    # CRLF, as the Trac editor stores it. A body rebuilt with "\n" would
    # not be found in `text`.
    text = "lead\r\n\r\n= H =\r\nfirst line\r\nsecond line\r\n"
    for s in split_sections("P", text):
        assert s.body in text
    secs = split_sections("P", text)
    assert secs[1].body == "first line\r\nsecond line"


def test_split_sections_long_section_parts_are_substrings():
    paras = [f"paragraph {i} " + "word " * 300 for i in range(8)]
    text = "= Big =\n" + "\n\n".join(paras) + "\n"
    secs = split_sections("P", text, max_chars=4000)
    assert len(secs) > 1
    for s in secs:
        assert s.body in text
        assert len(s.body) <= 4000
    # CRLF paragraphs split too
    crlf = text.replace("\n", "\r\n")
    secs = split_sections("P", crlf, max_chars=4000)
    assert len(secs) > 1
    for s in secs:
        assert s.body in crlf


def test_split_sections_hard_cuts_one_overlong_paragraph():
    text = "x" * 9000
    secs = split_sections("P", text, max_chars=4000)
    assert [len(s.body) for s in secs] == [4000, 4000, 1000]


# --------------------------------------------------------------------
# BM25
# --------------------------------------------------------------------


def test_bm25_ranks_the_rarer_word_higher():
    secs = _sections(
        {
            "A": "common common common rarity",
            "B": "common common common common",
            "C": "common common other",
        }
    )
    ranked = BM25(secs).search("rarity common")
    assert [s.page for s, _ in ranked][0] == "A"
    assert {s.page for s, _ in ranked} == {"A", "B", "C"}


def test_bm25_score_matches_the_measured_formula():
    secs = _sections({"Pa": "alpha beta", "Pb": "beta gamma"})
    index = BM25(secs)
    [(sec, score)] = index.search("alpha")
    n = 2
    df = 1
    idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
    # section texts are "Pa\nalpha beta" and "Pb\nbeta gamma": 3 tokens
    # each ("pa", "alpha", "beta")
    avgdl = 3.0
    tf = 1
    k1, b = 1.2, 0.75
    norm = 1 - b + b * 3 / avgdl
    expected = idf * tf * (k1 + 1) / (tf + k1 * norm)
    assert sec.page == "Pa"
    assert score == expected


def test_bm25_returns_only_positive_scores_and_no_cap():
    secs = _sections({f"P{i:02d}": "needle hay" for i in range(60)})
    ranked = BM25(secs).search("needle")
    assert len(ranked) == 60
    assert all(score > 0 for _, score in ranked)


def test_bm25_empty_corpus_and_unknown_term():
    assert BM25([]).search("anything") == []
    assert BM25(_sections({"A": "alpha"})).search("zzzz") == []


def test_bm25_multiword_query_matches_words_that_are_not_adjacent():
    secs = _sections(
        {"A": "the status of a ticket can change, later ignored"}
    )
    ranked = BM25(secs).search("why was my status change ignored")
    assert [s.page for s, _ in ranked] == ["A"]


# --------------------------------------------------------------------
# page level
# --------------------------------------------------------------------


def test_a_page_scores_as_its_best_section():
    pages = {
        "Multi": (
            "= Weak =\nrare\n"
            "= Strong =\nrare rare rare rare\n"
            "= Other =\nfiller filler\n"
        ),
        "Single": "rare filler filler filler filler filler",
    }
    index = BM25(_sections(pages))
    hits = search_pages(index, "rare")
    by_name = {h["name"]: h for h in hits}
    best = max(
        score
        for sec, score in index.search("rare")
        if sec.page == "Multi"
    )
    assert by_name["Multi"]["score"] == best
    # one entry per page
    assert [h["name"] for h in hits].count("Multi") == 1
    # and the snippet comes from the best section, not the first
    assert "rare rare rare" in by_name["Multi"]["snippet"]


def test_ties_are_broken_by_page_name():
    # Same-length names with one token each, so the scores tie exactly.
    pages = {"P3": "needle", "P1": "needle", "P2": "needle"}
    hits = search_pages(BM25(_sections(pages)), "needle")
    assert [h["name"] for h in hits] == ["P1", "P2", "P3"]
    assert len({h["score"] for h in hits}) == 1


def test_results_are_ordered_by_descending_score():
    pages = {
        "Low": "needle filler filler filler filler filler filler",
        "High": "needle",
    }
    hits = search_pages(BM25(_sections(pages)), "needle")
    assert [h["name"] for h in hits] == ["High", "Low"]
    assert hits[0]["score"] > hits[1]["score"]


# --------------------------------------------------------------------
# snippet
# --------------------------------------------------------------------


def _strip_ellipsis(snippet: str) -> str:
    if snippet.startswith("..."):
        snippet = snippet[3:]
    if snippet.endswith("..."):
        snippet = snippet[:-3]
    return snippet


def test_snippet_is_a_substring_of_the_stored_text():
    text = (
        "= Intro =\n"
        + "filler " * 40
        + "the Needle is here, in '''bold''' [[Macro]] markup"
        + " filler" * 40
        + "\n"
    )
    hits = search_pages(BM25(_sections({"Page": text})), "needle")
    snippet = hits[0]["snippet"]
    assert snippet.startswith("...") and snippet.endswith("...")
    assert "Needle" in snippet  # as stored, not lowercased
    assert _strip_ellipsis(snippet) in text


def test_snippet_never_contains_the_synthetic_head_line():
    text = "= Heading =\nmatch in the body\n"
    hits = search_pages(BM25(_sections({"Zed/Page": text})), "zed")
    # "zed" matches only the page name in the head line
    snippet = hits[0]["snippet"]
    assert "›" not in snippet
    assert snippet in text


def test_snippet_falls_back_to_the_start_of_the_section_body():
    # "close" is only a CamelCase part: it never occurs literally
    # (case-insensitively) as a token of the query.
    text = "Body start. " + "x" * 200
    hits = search_pages(
        BM25(_sections({"CloseViaWorkflowAction": text})), "close"
    )
    snippet = hits[0]["snippet"]
    assert snippet.startswith("Body start.")
    assert _strip_ellipsis(snippet) in text


def test_snippet_has_no_ellipsis_when_nothing_is_cut():
    hits = search_pages(
        BM25(_sections({"P": "short needle text"})), "needle"
    )
    assert hits[0]["snippet"] == "short needle text"


def test_make_snippet_is_case_insensitive_and_about_50_chars_each_side():
    body = "a" * 100 + "NEEDLE" + "b" * 100
    sec = split_sections("P", body)[0]
    snippet = make_snippet(sec, ["needle"])
    assert snippet == "..." + "a" * 50 + "NEEDLE" + "b" * 50 + "..."


# --------------------------------------------------------------------
# split_sections follows Trac's own rules (auto_pm:#268)
# --------------------------------------------------------------------


def _headings(text: str) -> list[str]:
    return [s.heading for s in split_sections("P", text)]


@pytest.mark.parametrize(
    "block_open",
    ["{{{", "{{{#!sh", "{{{#!python", "  {{{", "{{{ "],
)
def test_a_heading_shaped_line_inside_a_code_block_does_not_split(
    block_open,
):
    text = (
        f"= Real =\nbefore\n{block_open}\n= not a heading =\n"
        "== nor this ==\n}}}\nafter\n"
    )
    secs = split_sections("P", text)
    assert [s.heading for s in secs] == ["Real"]
    # the block lines stay body text, and are indexed
    assert "= not a heading =" in secs[0].body
    assert "heading" in secs[0].counts


def test_a_heading_shaped_line_in_a_nested_block_does_not_split():
    text = (
        "= Real =\n{{{\nouter\n{{{#!sh\n= inner =\n}}}\n"
        "= still inside =\n}}}\n= After =\nlast\n"
    )
    assert _headings(text) == ["Real", "After"]


def test_a_heading_after_the_block_closes_splits_again():
    text = "{{{\n= in =\n}}}\n= Out =\nbody\n"
    secs = split_sections("P", text)
    assert [s.heading for s in secs] == ["", "Out"]
    assert "= in =" in secs[0].body


def test_a_one_line_block_does_not_open_a_block():
    # "}}}" on the opening line: Trac's _startblock test refuses it
    text = "{{{ inline }}}\n= Heading =\nbody\n"
    assert _headings(text) == ["", "Heading"]


def test_an_unclosed_block_swallows_the_rest_of_the_page():
    assert _headings("= A =\nx\n{{{\n= B =\ny\n") == ["A"]


@pytest.mark.parametrize(
    "line",
    [
        "======",
        "=",
        "=foo",
        "==NoSpace==",
        "======= seven =======",
        "==",
    ],
)
def test_lines_that_are_not_trac_headings_do_not_split(line):
    text = f"= Real =\nbody\n{line}\nmore\n"
    secs = split_sections("P", text)
    assert [s.heading for s in secs] == ["Real"]
    assert line in secs[0].body


@pytest.mark.parametrize(
    "line,name",
    [
        ("= One =", "One"),
        ("== Two ==", "Two"),
        ("=== Three ===", "Three"),
        ("==== Four ====", "Four"),
        ("===== Five =====", "Five"),
        ("====== Six ======", "Six"),
        ("== No closing run", "No closing run"),
        ("== Mixed ===", "Mixed ="),
        ("== Anchored == #the-anchor", "Anchored"),
        ("== Anchored ==#frag", "Anchored"),
        ("== Anchored #frag", "Anchored"),
        ("  == Indented ==", "Indented"),
    ],
)
def test_ordinary_headings_still_split_with_the_same_names(line, name):
    secs = split_sections("P", f"lead\n{line}\nbody\n")
    assert [s.heading for s in secs] == ["", name]


def test_a_crlf_heading_splits():
    secs = split_sections("P", "= One =\r\nbody\r\n")
    assert [s.heading for s in secs] == ["One"]


# --------------------------------------------------------------------
# Non-ASCII words (review fix)
# --------------------------------------------------------------------


def test_accented_and_cyrillic_words_are_tokens():
    assert tokenize("café") == ["café"]
    assert tokenize("Café CAFÉ") == ["café", "café"]
    assert tokenize("Привет, мир") == ["привет", "мир"]
    assert tokenize("настройка/доступа") == [
        "настройка/доступа",
        "настройка",
        "доступа",
    ]


def test_non_ascii_words_are_found():
    pages = {
        "Menu": "Свежий кофе и café au lait\n",
        "Other": "nothing to see\n",
    }
    index = BM25(_sections(pages))
    assert [h["name"] for h in search_pages(index, "кофе")] == ["Menu"]
    assert [h["name"] for h in search_pages(index, "CAFÉ")] == ["Menu"]
    assert "café" in search_pages(index, "café")[0]["snippet"]
    # a word is a whole token: "caf" is not "café"
    assert search_pages(index, "caf") == []


def _ascii_reference_tokenize(text: str) -> list[str]:
    """The tokenizer as measured, ASCII-only (the regexes before the
    non-ASCII change)."""
    import re

    token = re.compile(r"[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*")
    camel = re.compile(
        r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+"
    )
    split = re.compile(r"[._/-]")
    out: list[str] = []
    for tok in token.findall(text):
        out.append(tok.lower())
        subs = split.split(tok)
        if len(subs) > 1:
            out.extend(s.lower() for s in subs)
        for s in subs:
            parts = camel.findall(s)
            if len(parts) > 1:
                out.extend(p.lower() for p in parts)
    return [t for t in out if len(t) >= 2 and t not in STOPWORDS]


@pytest.mark.parametrize(
    "text",
    [
        "Why was my Status change ignored, I think",
        "CloseViaWorkflowAction",
        "ticket_update",
        "Rules/trac/RenderVerify",
        "HTTPServer2",
        "version 1.2.3-rc1 of foo_bar.baz/qux",
        "snake_case and kebab-case and dot.case",
        "= Heading =\n * item_one\n * ItemTwo\n{{{\ncode_block()\n}}}",
        "a-b c_d e.f _lead trail_ -x- 12 345 ABC DEFg hIJ",
        "Source: [wiki:Page/Name label] ''italic'' '''bold'''",
    ],
)
def test_ascii_text_tokenizes_as_it_always_did(text):
    assert tokenize(text) == _ascii_reference_tokenize(text)
