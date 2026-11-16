"""The shared ``--assets`` module (Trac /trac_mcp_server #215): finding the
attachment URLs of an exported page, naming and writing the local files,
and the rewritten links. No CLI and no Trac here; the CLIs' own tests
drive it through a fake server."""

from pathlib import Path

import pytest

from trac_mcp_server.cli import assets
from trac_mcp_server.cli.assets import AttachmentRef

HOST = "https://trac.example.org"
INST = "/bcs"
GOLDEN_PAGE = "b-node/blog/scope"
# The shape of tests/fixtures/trac_html/blog_scope_verification.golden.md
# line 27: an image wrapped in a link to its attachment page.
GOLDEN_MD = (
    "[![Part 1: ctrl_meas](https://trac.example.org/bcs/raw-attachment/"
    "wiki/b-node/blog/scope/part1.png "
    '"Part 1: ctrl_meas")](https://trac.example.org/bcs/attachment/wiki/'
    "b-node/blog/scope/part1.png)\n"
)
GOLDEN_HTML = (
    '<a href="https://trac.example.org/bcs/attachment/wiki/b-node/blog/'
    'scope/part1.png" style="padding:0; border:none"><img alt="Part 1" '
    'src="https://trac.example.org/bcs/raw-attachment/wiki/b-node/blog/'
    'scope/part1.png" title="Part 1" /></a>\n'
)


def _refs(text, instance=INST, fmt="markdown"):
    return list(assets.scan_text(text, instance, fmt).refs)


def _fetched(
    text,
    instance=INST,
    missing=(),
    data=None,
    fmt="markdown",
    too_large=(),
):
    out = {}
    for ref in _refs(text, instance, fmt):
        if ref.filename in too_large:
            out[ref] = assets.TooLarge(100)
            continue
        if ref.filename in missing:
            out[ref] = None
        else:
            out[ref] = (data or {}).get(
                ref.filename, b"bytes-of-" + ref.filename.encode()
            )
    return out


def _plan(
    text,
    instance=INST,
    assets_dir="static/images",
    output=None,
    url=None,
    fmt="markdown",
    **kw,
):
    return _plan_of(
        text,
        instance,
        _fetched(text, instance, fmt=fmt, **kw),
        assets_dir,
        output,
        fmt,
        url,
    )


def _plan_of(
    text,
    instance,
    outcomes,
    assets_dir,
    output,
    fmt="markdown",
    url=None,
):
    """The plan for ``text`` when each reference came back as in
    ``outcomes`` (ref -> bytes | None | TooLarge)."""
    scan = assets.scan_text(text, instance, fmt)
    planner = assets.AssetPlanner(scan, assets_dir, output, url)
    for ref in scan.refs:
        planner.place(ref, outcomes[ref])
    return planner.result(text)


# --- finding -----------------------------------------------------------


def test_golden_markdown_one_attachment_two_urls():
    refs = _refs(GOLDEN_MD, INST)
    assert refs == [AttachmentRef("wiki", GOLDEN_PAGE, "part1.png")]


def test_golden_markdown_both_links_point_at_the_local_file():
    plan = _plan(GOLDEN_MD, output="out.md", assets_dir="img")
    where = "img/b-node/blog/scope/part1.png"
    assert plan.text == (
        f'[![Part 1: ctrl_meas]({where} "Part 1: ctrl_meas")]({where})\n'
    )
    assert plan.saved == ["b-node/blog/scope/part1.png"]
    assert plan.missing == [] and plan.warnings == []


def test_golden_html_both_attributes_rewritten_as_written():
    plan = _plan(GOLDEN_HTML, output="out.html", assets_dir="img")
    where = "img/b-node/blog/scope/part1.png"
    assert plan.text == (
        f'<a href="{where}" style="padding:0; border:none">'
        f'<img alt="Part 1" src="{where}" title="Part 1" /></a>\n'
    )
    assert plan.saved == ["b-node/blog/scope/part1.png"]


def test_html_single_quoted_and_entity_encoded_attribute():
    text = (
        "<img src='https://h/bcs/raw-attachment/wiki/P/a&amp;b.png'>"
        '<a href="/bcs/attachment/wiki/P/a&amp;b.png">x</a>'
    )
    refs = _refs(text, INST)
    assert refs == [AttachmentRef("wiki", "P", "a&b.png")]
    refs = _refs(text, INST, "html")
    assert refs == [AttachmentRef("wiki", "P", "a&b.png")]
    plan = _plan(text, output="o.html", assets_dir="d", fmt="html")
    assert plan.text == (
        "<img src='d/P/a%26b.png'><a href=\"d/P/a%26b.png\">x</a>"
    )


def test_another_host_than_trac_url_still_matches():
    text = (
        "![i](http://internal.lan:8000/bcs/raw-attachment/wiki/P/x.png)"
    )
    assert _refs(text, INST) == [AttachmentRef("wiki", "P", "x.png")]


def test_root_relative_urls_match():
    text = (
        "![i](/bcs/raw-attachment/wiki/P/x.png) "
        "[l](/bcs/attachment/wiki/P/x.png)"
    )
    assert _refs(text, INST) == [AttachmentRef("wiki", "P", "x.png")]
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.text == "![i](d/P/x.png) [l](d/P/x.png)"


def test_another_instance_does_not_match():
    text = "![i](https://h/other/raw-attachment/wiki/P/x.png)"
    assert _refs(text, INST) == []
    assert _refs("![i](/bcsx/raw-attachment/wiki/P/x.png)", INST) == []


def test_instance_at_the_root():
    text = "![i](https://h/raw-attachment/wiki/P/x.png)"
    for inst in ("https://h/", "https://h", "http://H:8000/"):
        assert _refs(text, inst) == [
            AttachmentRef("wiki", "P", "x.png")
        ]
    rel = "![i](/raw-attachment/wiki/P/x.png)"
    for inst in ("", "/", "https://h/"):
        assert _refs(rel, inst) == [AttachmentRef("wiki", "P", "x.png")]


def test_root_instance_matches_an_absolute_url_only_on_its_own_host():
    other = "https://trac.edgewall.org/raw-attachment/wiki/P/x.png"
    own = "https://trac.example.org/raw-attachment/wiki/P/x.png"
    text = f"![a]({other}) ![b]({own}) [c]({other}) <{other}>"
    inst = "https://trac.example.org/"
    assert _refs(text, inst) == [AttachmentRef("wiki", "P", "x.png")]
    plan = _plan(text, instance=inst, output="o.md", assets_dir="d")
    assert plan.text == (
        f"![a]({other}) ![b](d/P/x.png) [c]({other}) <{other}>"
    )
    # the host alone is compared: not its scheme, port or letter case
    assert _refs(
        "![a](http://TRAC.example.org:8001/raw-attachment/wiki/P/x.png)",
        inst,
    ) == [AttachmentRef("wiki", "P", "x.png")]


def test_root_instance_protocol_relative_url_is_host_checked():
    inst = "https://trac.example.org"
    assert (
        _refs(
            "![a](//trac.edgewall.org/raw-attachment/wiki/P/x.png)",
            inst,
        )
        == []
    )
    assert _refs(
        "![a](//trac.example.org/raw-attachment/wiki/P/x.png)", inst
    ) == [AttachmentRef("wiki", "P", "x.png")]


def test_root_instance_with_no_known_host_matches_no_absolute_url():
    text = (
        "![a](https://h/raw-attachment/wiki/P/x.png) "
        "![b](/raw-attachment/wiki/P/y.png)"
    )
    for inst in ("", "/"):
        assert _refs(text, inst) == [
            AttachmentRef("wiki", "P", "y.png")
        ]


def test_an_instance_with_a_path_ignores_the_host():
    # Trac's base URL can name another host or port than TRAC_URL.
    text = "![a](https://elsewhere:9/bcs/raw-attachment/wiki/P/x.png)"
    for inst in ("/bcs", "https://trac.example.org/bcs"):
        assert _refs(text, inst) == [
            AttachmentRef("wiki", "P", "x.png")
        ]


def test_nested_wiki_parent_keeps_its_slashes():
    text = "![i](https://h/bcs/raw-attachment/wiki/A/B/C/pic.png)"
    assert _refs(text, INST) == [
        AttachmentRef("wiki", "A/B/C", "pic.png")
    ]


def test_ticket_realm():
    text = (
        "![i](https://h/bcs/raw-attachment/ticket/42/log.png)\n"
        "[l](https://h/bcs/attachment/ticket/42/log.png)\n"
    )
    assert _refs(text, INST) == [
        AttachmentRef("ticket", "42", "log.png")
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://h/bcs/attachment/ticket/abc/log.png",
        "https://h/bcs/attachment/ticket/42/sub/log.png",
        "https://h/bcs/attachment/wiki/log.png",
        "https://h/bcs/attachment/changeset/1/log.png",
        "https://h/bcs/attachment/wiki/P/",
        "https://h/bcs/attachment/wiki/P/..",
        "https://h/bcs/attachment/wiki/P/a%2Fb.png",
        # a backslash or a colon in a file name or a page segment
        "https://h/bcs/attachment/wiki/P/a%5Cb.png",
        "https://h/bcs/attachment/wiki/P/a%3Ab.png",
        "https://h/bcs/attachment/wiki/P/C:x.png",
        "https://h/bcs/attachment/wiki/A%5CB/x.png",
        "https://h/bcs/attachment/wiki/A%3AB/x.png",
        "https://h/bcs/attachment/ticket/42/a%3Ab.png",
        # a dot segment anywhere in the path, or a NUL
        "https://h/bcs/attachment/wiki/A/%2E%2E/x.png",
        "https://h/bcs/attachment/wiki/A/./x.png",
        "https://h/bcs/attachment/wiki/%2E%2E/%2E%2E/x.png",
        "https://h/bcs/attachment/wiki/A/b%00c/x.png",
        "https://h/bcs/attachment/wiki/A//x.png",
        # a ticket number is ASCII digits: superscript two, Arabic-Indic
        # two, a mixed one
        "https://h/bcs/attachment/ticket/%C2%B2/x.png",
        "https://h/bcs/attachment/ticket/%D9%A2/x.png",
        "https://h/bcs/attachment/ticket/4%C2%B2/x.png",
        "https://h/bcs/attachment/ticket//x.png",
    ],
)
def test_malformed_attachment_paths_are_not_references(url):
    text = f"![i]({url})"
    assert _refs(text, INST) == []
    assert _plan(text, output="o.md").text == text


def test_query_or_fragment_is_skipped():
    text = (
        "[a](https://h/bcs/raw-attachment/wiki/P/x.png?format=raw) "
        "[b](https://h/bcs/attachment/wiki/P/x.png#L3) "
        "![c](https://h/bcs/raw-attachment/wiki/P/y.png)"
    )
    assert _refs(text, INST) == [AttachmentRef("wiki", "P", "y.png")]
    plan = _plan(text, output="o.md", assets_dir="d")
    assert "x.png?format=raw)" in plan.text
    assert "x.png#L3)" in plan.text
    assert "![c](d/P/y.png)" in plan.text


def test_external_image_is_untouched():
    text = "![i](https://cdn.example.com/raw-attachment/wiki/P/x.png)\n"
    plan = _plan(text, output="o.md")
    assert plan.text == text
    assert plan.saved == [] and plan.missing == []


def test_only_url_positions_are_looked_at():
    url = "https://h/bcs/raw-attachment/wiki/P/x.png"
    text = (
        f"prose mentioning {url} and `code {url}`\n"
        f'<p title="{url}">t</p> <a name="{url}">n</a>\n'
    )
    assert _refs(text, INST) == []
    assert _plan(text, output="o.md").text == text


def test_autolink_becomes_an_inline_link_with_the_file_name():
    text = "see <https://h/bcs/attachment/wiki/P/we[ird].png> now"
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.text == "see [we\\[ird\\].png](d/P/we%5Bird%5D.png) now"


def test_angle_bracket_target_is_not_also_an_autolink():
    text = "[l](<https://h/bcs/attachment/wiki/P/x.png>)"
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.text == "[l](<d/P/x.png>)"


def test_percent_encoded_filename_is_fetched_decoded_and_relinked_encoded():
    text = (
        "![i](https://h/bcs/raw-attachment/wiki/P/my%20pic%C3%A9.png)"
    )
    refs = _refs(text, INST)
    assert refs == [AttachmentRef("wiki", "P", "my picé.png")]
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.saved == ["P/my picé.png"]
    assert plan.text == "![i](d/P/my%20pic%C3%A9.png)"


def test_each_attachment_is_found_once_in_first_appearance_order():
    text = (
        "![b](/bcs/raw-attachment/wiki/P/b.png) "
        "![a](/bcs/raw-attachment/wiki/P/a.png) "
        "[b](/bcs/attachment/wiki/P/b.png)"
    )
    assert [r.filename for r in _refs(text)] == ["b.png", "a.png"]


# --- layout ----------------------------------------------------------------


def test_wiki_attachment_lands_under_its_page_path():
    text = "![i](/bcs/raw-attachment/wiki/blog/my-post/x.png)"
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.saved == ["blog/my-post/x.png"]
    assert plan.text == "![i](d/blog/my-post/x.png)"
    assert plan.warnings == []


def test_ticket_attachment_lands_under_ticket_n():
    text = "![i](/bcs/raw-attachment/ticket/7/x.png)"
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.saved == ["ticket-7/x.png"]
    assert plan.text == "![i](d/ticket-7/x.png)"


def test_same_file_name_on_two_pages_gets_two_directories_and_links():
    text = (
        "![1](/bcs/raw-attachment/wiki/A/b/diagram.png) "
        "![2](/bcs/raw-attachment/wiki/C/diagram.png) "
        "![3](/bcs/raw-attachment/ticket/7/diagram.png)"
    )
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.saved == [
        "A/b/diagram.png",
        "C/diagram.png",
        "ticket-7/diagram.png",
    ]
    assert plan.text == (
        "![1](d/A/b/diagram.png) ![2](d/C/diagram.png) "
        "![3](d/ticket-7/diagram.png)"
    )
    assert plan.warnings == []


def test_a_path_does_not_depend_on_which_other_attachments_exist():
    text = (
        "![1](/bcs/raw-attachment/wiki/A/x.png) "
        "![2](/bcs/raw-attachment/wiki/B/x.png) "
        "![3](/bcs/raw-attachment/wiki/B/y.png)"
    )
    before = _plan(text, output="o.md", assets_dir="d")
    # the first run: A/x.png is not attached yet
    first = _plan_of(
        text,
        INST,
        {
            AttachmentRef("wiki", "A", "x.png"): None,
            AttachmentRef("wiki", "B", "x.png"): b"bx",
            AttachmentRef("wiki", "B", "y.png"): b"by",
        },
        "d",
        "o.md",
        "markdown",
    )
    # the second run: it is there
    second = _plan_of(
        text,
        INST,
        {
            AttachmentRef("wiki", "A", "x.png"): b"ax",
            AttachmentRef("wiki", "B", "x.png"): b"bx",
            AttachmentRef("wiki", "B", "y.png"): b"by",
        },
        "d",
        "o.md",
        "markdown",
    )
    assert first.saved == ["B/x.png", "B/y.png"]
    assert "![2](d/B/x.png) ![3](d/B/y.png)" in first.text
    assert "![2](d/B/x.png) ![3](d/B/y.png)" in second.text
    assert second.saved == ["A/x.png", *first.saved]
    assert before.saved == ["A/x.png", "B/x.png", "B/y.png"]
    assert first.warnings[0].startswith(
        "attachment 'x.png' of wiki page"
    )
    assert len(first.warnings) == 1 and second.warnings == []


def test_the_layout_is_deterministic():
    text = (
        "![1](/bcs/raw-attachment/wiki/P/x.png) "
        "![2](/bcs/raw-attachment/wiki/Q/x.png)"
    )
    assert (
        _plan(text, output="o.md").text
        == _plan(text, output="o.md").text
    )


def test_a_case_insensitive_clash_keeps_the_second_ones_urls():
    text = (
        "![1](/bcs/raw-attachment/wiki/P/Diagram.png) "
        "![2](/bcs/raw-attachment/wiki/P/diagram.png) "
        "[2](/bcs/attachment/wiki/P/diagram.png)"
    )
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.saved == ["P/Diagram.png"]
    assert plan.text == (
        "![1](d/P/Diagram.png) ![2](/bcs/raw-attachment/wiki/P/diagram.png) "
        "[2](/bcs/attachment/wiki/P/diagram.png)"
    )
    assert plan.missing == []
    assert len(plan.warnings) == 1
    assert "'diagram.png'" in plan.warnings[0]
    assert "wiki page 'P'" in plan.warnings[0]
    assert "'P/Diagram.png'" in plan.warnings[0]


def test_a_case_clash_between_page_directories_too():
    text = (
        "![1](/bcs/raw-attachment/wiki/Blog/x.png) "
        "![2](/bcs/raw-attachment/wiki/blog/x.png)"
    )
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.saved == ["Blog/x.png"]
    assert plan.text.endswith(
        "![2](/bcs/raw-attachment/wiki/blog/x.png)"
    )
    assert len(plan.warnings) == 1


def test_a_file_and_a_directory_of_one_name_clash_the_same_way():
    # page "a" has a file "b"; page "a/b" is a directory "a/b".
    text = (
        "![1](/bcs/raw-attachment/wiki/a/b) "
        "![2](/bcs/raw-attachment/wiki/a/b/c.png)"
    )
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.saved == ["a/b"]
    assert plan.text.endswith(
        "![2](/bcs/raw-attachment/wiki/a/b/c.png)"
    )
    assert len(plan.warnings) == 1


def test_a_clash_with_a_missing_attachment_is_no_clash():
    text = (
        "![1](/bcs/raw-attachment/wiki/P/Diagram.png) "
        "![2](/bcs/raw-attachment/wiki/P/diagram.png)"
    )
    plan = _plan(
        text, output="o.md", assets_dir="d", missing=("Diagram.png",)
    )
    assert plan.saved == ["P/diagram.png"]
    assert "![2](d/P/diagram.png)" in plan.text
    assert len(plan.warnings) == 1 and "not found" in plan.warnings[0]


# --- code is not a reference -------------------------------------------------

U = "https://h/bcs/raw-attachment/wiki/P/x.png"
IMG = f"![i]({U})"
CODE_MD = {
    "inline span": f"see `{IMG}` here\n",
    "double-backtick span": f"``a ` {IMG}``\n",
    "span across a line": f"`a\n{IMG}`\n",
    "backtick fence": f"```\n{IMG}\n```\n",
    "fence with an info string": f"```md\n{IMG}\n```\n",
    "tilde fence": f"~~~\n{IMG}\n~~~\n",
    "longer fence holds a shorter one": f"````\n```\n{IMG}\n```\n````\n",
    "tilde line does not close a backtick fence": (
        f"```\n~~~\n{IMG}\n~~~\n```\n"
    ),
    "a closer with trailing text does not close": (
        f"```\n``` x\n{IMG}\n```\n"
    ),
    "unclosed fence runs to the end": f"```\n{IMG}\n",
    "fence in a list item": f"- a\n\n  ```\n  {IMG}\n  ```\n",
    "fence in a block quote": f"> ```\n> {IMG}\n> ```\n",
    "pre": f'<pre>{IMG} <img src="{U}"></pre>\n',
    "code element": f'<code>{IMG}<a href="{U}">x</a></code>\n',
    "script": f"<script>var u = \"<img src='{U}'>\";</script>\n",
    "style": f'<style>a {{ background: url("{U}") }}\n{IMG}</style>\n',
    "textarea with attributes": (
        f'<TEXTAREA rows="3">{IMG}</TEXTAREA>\n'
    ),
    "pre with attributes over lines": (
        f"<pre class=\"a>b\" id='c'>\n{IMG}\n</pre>\n"
    ),
}


@pytest.mark.parametrize(
    "code", list(CODE_MD.values()), ids=list(CODE_MD)
)
def test_urls_in_markdown_code_are_not_fetched_nor_rewritten(code):
    assert _refs(code, INST) == []
    plan = _plan(code, output="o.md", assets_dir="d")
    assert plan.text == code
    assert plan.saved == [] and plan.warnings == []


def test_the_same_url_outside_code_is_rewritten():
    text = (
        f"{IMG}\n\n`{IMG}`\n\n```\n{IMG}\n```\n\n"
        f"<pre>{IMG}</pre>\n\n{IMG}\n"
    )
    assert len(_refs(text, INST)) == 1
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.text == (
        "![i](d/P/x.png)\n\n`" + IMG + "`\n\n```\n" + IMG + "\n```\n\n"
        f"<pre>{IMG}</pre>\n\n![i](d/P/x.png)\n"
    )
    assert plan.saved == ["P/x.png"]


NOT_CODE_MD = {
    "a lone backtick": f"a ` b {IMG}\n",
    "runs of different length": f"``a {IMG} b` x\n",
    "an escaped backtick": f"\\`{IMG}`\n",
    "a span does not cross a blank line": f"`a\n\n{IMG} b`\n",
    "a fence is not a span": f"``` a `\n{IMG}\n",
    "a literal tag in a span opens no element": (
        f"use `<code>` tags: {IMG}\n"
    ),
    "a closed element ends": f"<pre>x</pre>\n{IMG}\n",
    "a tag that only starts with a name": f"<prefix>{IMG}</prefix>\n",
}


@pytest.mark.parametrize(
    "text", list(NOT_CODE_MD.values()), ids=list(NOT_CODE_MD)
)
def test_markdown_that_only_looks_like_code_is_rewritten(text):
    assert _refs(text, INST) == [AttachmentRef("wiki", "P", "x.png")]
    assert (
        "d/P/x.png" in _plan(text, output="o.md", assets_dir="d").text
    )


def test_urls_in_html_code_elements_are_skipped_and_others_rewritten():
    attr = f'<a href="{U}">x</a>'
    for tag in ("pre", "code", "script", "style", "textarea"):
        text = f"<{tag}>{attr}</{tag}>"
        assert _refs(text, INST, "html") == [], tag
        assert _plan(text, output="o.html", fmt="html").text == text
    text = f"<pre>{attr}</pre>{attr}"
    plan = _plan(text, output="o.html", assets_dir="d", fmt="html")
    assert plan.text == f'<pre>{attr}</pre><a href="d/P/x.png">x</a>'


def test_html_output_has_no_markdown_code():
    # backticks and fences are not code in an HTML document
    text = f'`<a href="{U}">x</a>`\n```\n<img src="{U}">\n```\n'
    plan = _plan(text, output="o.html", assets_dir="d", fmt="html")
    assert plan.text.count("d/P/x.png") == 2
    assert plan.saved == ["P/x.png"]


def test_an_unclosed_html_code_element_runs_to_the_end():
    text = f"<pre>{IMG}"
    assert _refs(text, INST) == []


# --- links ---------------------------------------------------------------


def test_relative_link_from_a_nested_output_directory():
    text = "![i](/bcs/raw-attachment/wiki/P/x.png)"
    plan = _plan(
        text, output="content/posts/x.md", assets_dir="static/images"
    )
    assert plan.text == "![i](../../static/images/P/x.png)"


def test_relative_link_when_the_output_is_beside_the_assets_dir():
    text = "![i](/bcs/raw-attachment/wiki/P/x.png)"
    plan = _plan(text, output="x.md", assets_dir="static/images")
    assert plan.text == "![i](static/images/P/x.png)"


def test_stdout_links_from_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    text = "![i](/bcs/raw-attachment/wiki/P/x.png)"
    plan = _plan(text, output=None, assets_dir="static/images")
    assert plan.text == "![i](static/images/P/x.png)"
    plan = _plan(text, output=None, assets_dir=tmp_path / "up" / "i")
    assert plan.text == "![i](up/i/P/x.png)"


def test_relative_link_never_looks_like_a_scheme():
    text = "![i](/bcs/raw-attachment/wiki/P/x.png)"
    plan = _plan(text, output="o.md", assets_dir="a:b")
    assert plan.text == "![i](a%3Ab/P/x.png)"


def test_assets_url_replaces_the_relative_path():
    text = GOLDEN_MD
    plan = _plan(
        text,
        output="content/posts/x.md",
        assets_dir="static/images",
        url="/images",
    )
    where = "/images/b-node/blog/scope/part1.png"
    assert plan.text.count(f"({where}") == 2
    assert "static" not in plan.text and ".." not in plan.text


def test_assets_url_with_a_nested_page_and_a_ticket():
    text = (
        "![1](/bcs/raw-attachment/wiki/blog/my-post/x.png) "
        "![2](/bcs/raw-attachment/ticket/7/y.png)"
    )
    plan = _plan(
        text, output="o.md", assets_dir="static/images", url="/images"
    )
    assert plan.text == (
        "![1](/images/blog/my-post/x.png) ![2](/images/ticket-7/y.png)"
    )


def test_assets_url_quotes_each_segment():
    text = "![i](/bcs/raw-attachment/wiki/A%20B/c%C3%A9/my%20pic.png)"
    plan = _plan(text, output="o.md", url="https://cdn.example/a//")
    assert plan.text == (
        "![i](https://cdn.example/a/A%20B/c%C3%A9/my%20pic.png)"
    )


def test_assets_url_trailing_slash_and_encoding():
    text = "![i](/bcs/raw-attachment/wiki/P/my pic.png)".replace(
        "my pic", "my%20pic"
    )
    plan = _plan(text, output="o.md", url="https://cdn.example/a/")
    assert plan.text == "![i](https://cdn.example/a/P/my%20pic.png)"
    plan = _plan(text, output="o.md", url="/")
    assert plan.text == "![i](/P/my%20pic.png)"


def test_assets_url_in_html_is_attribute_escaped():
    text = '<img src="/bcs/raw-attachment/wiki/P/x.png">'
    plan = _plan(text, output="o.html", url="/i?a=1&b=2", fmt="html")
    assert plan.text == '<img src="/i?a=1&amp;b=2/P/x.png">'


def test_missing_attachment_keeps_its_urls_and_warns():
    text = (
        "![a](/bcs/raw-attachment/wiki/P/a.png) "
        "![b](/bcs/raw-attachment/wiki/P/b.png) "
        "[b](/bcs/attachment/wiki/P/b.png)"
    )
    plan = _plan(
        text, output="o.md", assets_dir="d", missing=("b.png",)
    )
    assert plan.text == (
        "![a](d/P/a.png) ![b](/bcs/raw-attachment/wiki/P/b.png) "
        "[b](/bcs/attachment/wiki/P/b.png)"
    )
    assert plan.saved == ["P/a.png"]
    assert plan.missing == [AttachmentRef("wiki", "P", "b.png")]
    assert len(plan.warnings) == 1
    assert "'b.png'" in plan.warnings[0]
    assert "wiki page 'P'" in plan.warnings[0]


# --- writing ---------------------------------------------------------------


def _stage(text, assets_dir, output=None, data=None, fmt="markdown"):
    """An AssetStage for ``text`` with every reference added (found, with
    ``data[filename]`` or ``bytes-of-<name>``)."""
    scan = assets.scan_text(text, INST, fmt)
    stage = assets.AssetStage(scan, assets_dir, output)
    for ref in scan.refs:
        stage.add(
            ref,
            (data or {}).get(
                ref.filename, b"bytes-of-" + ref.filename.encode()
            ),
        )
    return stage


def _tree(root):
    return sorted(
        p.relative_to(root).as_posix() for p in Path(root).rglob("*")
    )


def test_commit_creates_the_directory_and_the_files(tmp_path):
    d = tmp_path / "a" / "b"
    stage = _stage(GOLDEN_MD, d, output=str(tmp_path / "o.md"))
    stage.stage_output(b"the output")
    stage.commit()
    saved = d / "b-node" / "blog" / "scope" / "part1.png"
    assert saved.read_bytes() == b"bytes-of-part1.png"
    assert (tmp_path / "o.md").read_bytes() == b"the output"
    assert not [p for p in _tree(tmp_path) if ".tmp-" in p]


def test_nothing_is_in_place_before_the_commit(tmp_path):
    d = tmp_path / "d"
    stage = _stage(GOLDEN_MD, d)
    assert not (d / "b-node" / "blog" / "scope" / "part1.png").exists()
    assert [p for p in _tree(d) if ".tmp-" in p]  # written, not placed
    stage.abort()
    assert not d.exists()


def test_identical_bytes_are_not_rewritten_and_changed_bytes_are(
    tmp_path,
):
    d = tmp_path / "d"
    (d / "P").mkdir(parents=True)
    same = d / "P" / "same.png"
    same.write_bytes(b"same")
    changed = d / "P" / "changed.png"
    changed.write_bytes(b"old")
    old_mtime = 1_000_000_000
    import os

    os.utime(same, (old_mtime, old_mtime))
    text = (
        "![a](/bcs/raw-attachment/wiki/P/same.png) "
        "![b](/bcs/raw-attachment/wiki/P/changed.png) "
        "![c](/bcs/raw-attachment/wiki/P/new.png)"
    )
    stage = _stage(
        text,
        d,
        data={
            "same.png": b"same",
            "changed.png": b"new",
            "new.png": b"n",
        },
    )
    stage.commit()
    assert stage.unchanged == 1
    assert same.stat().st_mtime == old_mtime
    assert changed.read_bytes() == b"new"
    assert (d / "P" / "new.png").read_bytes() == b"n"


def test_a_stage_with_nothing_to_write_does_not_create_the_directory(
    tmp_path,
):
    stage = _stage("no attachments", tmp_path / "never")
    stage.commit()
    assert not (tmp_path / "never").exists()


FIVE = " ".join(
    f"![{n}](/bcs/raw-attachment/wiki/P/{n}.png)"
    for n in ("a", "b", "c", "d", "e")
)


def test_a_failure_on_the_third_of_five_temporary_files_leaves_nothing(
    tmp_path, monkeypatch
):
    d = tmp_path / "img"
    out = tmp_path / "o.md"
    out.write_text("the old output")
    real = assets._write_temp
    calls = []

    def flaky(path, data):
        calls.append(path)
        if len(calls) == 3:
            raise OSError(28, "No space left on device")
        real(path, data)

    monkeypatch.setattr(assets, "_write_temp", flaky, raising=False)
    scan = assets.scan_text(FIVE, INST)
    with pytest.raises(assets.AssetError, match="No space left"):
        with assets.AssetStage(scan, d, str(out)) as stage:
            for ref in scan.refs:
                stage.add(ref, b"x")
    assert len(calls) == 3
    assert not d.exists()  # the directory it made is gone too
    assert out.read_text() == "the old output"
    assert _tree(tmp_path) == ["o.md"]


def test_an_existing_assets_directory_comes_back_as_it_was(
    tmp_path, monkeypatch
):
    d = tmp_path / "img"
    (d / "P").mkdir(parents=True)
    (d / "P" / "keep.png").write_bytes(b"keep")
    before = _tree(tmp_path)
    real = assets._write_temp
    calls = []

    def flaky(path, data):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("boom")
        real(path, data)

    monkeypatch.setattr(assets, "_write_temp", flaky, raising=False)
    scan = assets.scan_text(
        FIVE + " ![k](/bcs/raw-attachment/wiki/Q/z.png)", INST
    )
    with pytest.raises(assets.AssetError):
        with assets.AssetStage(scan, d) as stage:
            for ref in scan.refs:
                stage.add(ref, b"x")
    assert _tree(tmp_path) == before


def test_the_output_is_staged_last_and_renamed_last(
    tmp_path, monkeypatch
):
    d = tmp_path / "img"
    out = tmp_path / "o.md"
    order = []
    real = assets.os.replace

    def spy(src, dst):
        order.append(Path(dst).name)
        real(src, dst)

    monkeypatch.setattr(assets.os, "replace", spy)
    stage = _stage(FIVE, d, output=str(out))
    stage.stage_output(b"out")
    stage.commit()
    assert order == [
        "a.png",
        "b.png",
        "c.png",
        "d.png",
        "e.png",
        "o.md",
    ]


def test_a_failed_rename_stops_and_says_how_many_moved(
    tmp_path, monkeypatch
):
    d = tmp_path / "img"
    real = assets.os.replace
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError(13, "Permission denied")
        real(src, dst)

    monkeypatch.setattr(assets.os, "replace", flaky)
    stage = _stage(FIVE, d)
    with pytest.raises(assets.AssetError, match="1 of 5 files"):
        stage.commit()
    assert [p for p in _tree(d) if ".tmp-" in p] == []
    assert _tree(d) == ["P", "P/a.png"]


def test_preflight_refuses_an_ancestor_that_is_a_file(tmp_path):
    d = tmp_path / "img"
    (d / "blog").mkdir(parents=True)
    (d / "blog" / "my-post").write_bytes(b"a file")
    scan = assets.scan_text(
        "![x](/bcs/raw-attachment/wiki/blog/my-post/x.png)", INST
    )
    stage = assets.AssetStage(scan, d)
    with pytest.raises(assets.AssetPathError, match="is a file"):
        stage.preflight()
    assert _tree(tmp_path) == ["img", "img/blog", "img/blog/my-post"]


def test_preflight_refuses_a_target_that_is_a_directory(tmp_path):
    d = tmp_path / "img"
    (d / "blog" / "my-post").mkdir(parents=True)
    scan = assets.scan_text(
        "![x](/bcs/raw-attachment/wiki/blog/my-post)", INST
    )
    with pytest.raises(assets.AssetPathError, match="is a directory"):
        assets.AssetStage(scan, d).preflight()


def test_preflight_refuses_the_assets_directory_being_a_file(tmp_path):
    d = tmp_path / "img"
    d.write_bytes(b"x")
    scan = assets.scan_text(
        "![x](/bcs/raw-attachment/wiki/P/x.png)", INST
    )
    with pytest.raises(assets.AssetPathError):
        assets.AssetStage(scan, d).preflight()


def test_preflight_passes_when_nothing_is_in_the_way(tmp_path):
    d = tmp_path / "img"
    (d / "P").mkdir(parents=True)
    (d / "P" / "x.png").write_bytes(b"old")
    scan = assets.scan_text(
        "![x](/bcs/raw-attachment/wiki/P/x.png) "
        "![y](/bcs/raw-attachment/wiki/Q/R/y.png)",
        INST,
    )
    assets.AssetStage(scan, d).preflight()
    assets.AssetStage(scan, tmp_path / "nowhere").preflight()


def test_check_output_path(tmp_path):
    assert assets.check_output_path(str(tmp_path / "o.md")) is None
    assert "is a directory" in assets.check_output_path(str(tmp_path))
    assert "does not exist" in assets.check_output_path(
        str(tmp_path / "no" / "o.md")
    )
    f = tmp_path / "f"
    f.write_text("x")
    assert "does not exist" in assets.check_output_path(str(f / "o.md"))
    # a bare file name is in the current directory
    assert assets.check_output_path("o.md") is None


def test_an_existing_output_keeps_its_mode(tmp_path):
    import os

    out = tmp_path / "o.md"
    out.write_text("old")
    os.chmod(out, 0o640)
    stage = _stage("no attachments", tmp_path / "img", output=str(out))
    stage.stage_output(b"new")
    stage.commit()
    assert out.read_bytes() == b"new"
    assert out.stat().st_mode & 0o777 == 0o640


def test_an_output_symlink_is_written_through(tmp_path):
    real = tmp_path / "real.md"
    real.write_text("old")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    stage = _stage("no attachments", tmp_path / "img", output=str(link))
    stage.stage_output(b"new")
    stage.commit()
    assert link.is_symlink()
    assert real.read_bytes() == b"new"


def test_summary_line():
    text = (
        "![a](/bcs/raw-attachment/wiki/P/a.png) "
        "![b](/bcs/raw-attachment/wiki/P/b.png) "
        "![c](/bcs/raw-attachment/wiki/P/c.png)"
    )
    plan = _plan(text, output="o.md", missing=("c.png",))
    plan.unchanged = 1
    assert assets.summary(plan, "img") == (
        "Saved 2 attachments to img (1 unchanged, 1 missing)"
    )
    one = _plan("![a](/bcs/raw-attachment/wiki/P/a.png)", output="o.md")
    assert assets.summary(one, Path("img")) == (
        "Saved 1 attachment to img (0 unchanged, 0 missing)"
    )


# --- second review round (Trac /trac_mcp_server #215) -------------------------

A_URL = "/bcs/raw-attachment/wiki/P/a.png"
B_URL = "/bcs/attachment/wiki/P/b.png"


@pytest.mark.parametrize(
    "tag", ["pre", "code", "script", "style", "textarea"]
)
def test_an_escaped_code_tag_in_prose_opens_no_element(tag):
    # The exporter writes a literal tag in prose as ``\<tag>``.
    text = (
        f"Use the \\<{tag}> tag. [a]({A_URL})\n\nAnother [b]({B_URL})"
    )
    assert [r.filename for r in _refs(text)] == ["a.png", "b.png"]


def test_the_reported_reproduction_finds_both_attachments():
    text = (
        "Use the \\<pre> tag. [a](/p/raw-attachment/wiki/P/a.png)\n\n"
        "Another [b](/p/attachment/wiki/P/b.png)"
    )
    refs = assets.scan_text(text, "/p").refs
    assert refs == (
        AttachmentRef("wiki", "P", "a.png"),
        AttachmentRef("wiki", "P", "b.png"),
    )


def test_a_tag_after_an_escaped_backslash_is_a_real_element():
    # ``\\\\<pre>``: an escaped backslash, then a tag that opens.
    text = f"x \\\\<pre>[a]({A_URL})</pre> [b]({B_URL})"
    assert [r.filename for r in _refs(text)] == ["b.png"]


def test_an_escaped_tag_does_not_hide_a_later_real_element():
    text = (
        f"\\<pre> [a]({A_URL}) <pre>[c](/bcs/raw-attachment/wiki/P/c.png)"
        f"</pre> [b]({B_URL})"
    )
    assert [r.filename for r in _refs(text)] == ["a.png", "b.png"]


def test_a_backslash_does_not_escape_a_tag_in_html():
    text = f'\\<pre><a href="{U}">x</a></pre>'
    assert _refs(text, INST, "html") == []


def test_an_escaped_backtick_opens_no_span():
    text = f"\\`not code [a]({A_URL}) \\` and [b]({B_URL})"
    assert [r.filename for r in _refs(text)] == ["a.png", "b.png"]


def test_a_backslash_before_a_closing_backtick_still_closes_the_span():
    # CommonMark: backslash is literal in a code span, so the span ends at
    # the backtick after it (``C:\dir\`` is code); the link after is not.
    text = f"`C:\\dir\\` [a]({A_URL}) `x` [b]({B_URL})"
    assert [r.filename for r in _refs(text)] == ["a.png", "b.png"]


# The attachment references of every exported fixture, written out by hand
# from the files (not produced by the scanner). Only the blog post has
# any: four images, each as ``![alt](raw-attachment/...)`` wrapped in a
# link to ``attachment/...`` (eight URLs, four attachments). The stock
# pages and the ticket fixtures only talk about attachments (``attachment:``
# TracLinks, the ``[attachment]`` section of trac.ini, "attachments" in
# prose) and have no attachment URL; the other blog post has no image.
_SCOPE_PAGE = "b-node/blog/scope-verification-vs-plausible-mqtt"
CORPUS_EXPECTED = {
    "blog_scope_verification": {
        ("wiki", _SCOPE_PAGE, f"bme280-exchange-part{n}.png")
        for n in (1, 2, 3, 4)
    },
}
CORPUS_INSTANCE = {"blog_scope_verification": "/bcs"}  # the rest: below
_FIXTURES = Path(__file__).parent / "fixtures" / "trac_html"
_CORPUS = sorted(p.name.split(".")[0] for p in _FIXTURES.glob("*.html"))


def _found(text, instance, fmt):
    return {
        (r.realm, r.parent, r.filename)
        for r in assets.scan_text(text, instance, fmt).refs
    }


def test_the_corpus_is_the_one_the_expected_sets_were_written_for():
    assert len(_CORPUS) == 19
    assert "blog_scope_verification" in _CORPUS


@pytest.mark.parametrize("name", _CORPUS)
def test_corpus_markdown_attachment_references(name):
    text = (_FIXTURES / f"{name}.golden.md").read_text(encoding="utf-8")
    instance = CORPUS_INSTANCE.get(name, "/trac_mcp_server")
    assert _found(text, instance, "markdown") == CORPUS_EXPECTED.get(
        name, set()
    )


@pytest.mark.parametrize("name", _CORPUS)
def test_corpus_html_attachment_references(name):
    text = (_FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    instance = CORPUS_INSTANCE.get(name, "/trac_mcp_server")
    assert _found(text, instance, "html") == CORPUS_EXPECTED.get(
        name, set()
    )


# --- one path for two identities ---------------------------------------------


def test_a_wiki_page_named_ticket_n_and_ticket_n_share_a_path_and_say_so():
    text = (
        "![1](/bcs/raw-attachment/wiki/ticket-5/x.png) "
        "![2](/bcs/raw-attachment/ticket/5/x.png)"
    )
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.saved == ["ticket-5/x.png"]
    assert plan.text == (
        "![1](d/ticket-5/x.png) ![2](/bcs/raw-attachment/ticket/5/x.png)"
    )
    assert len(plan.warnings) == 1
    assert (
        "wiki page 'ticket-5' and ticket #5 both map to ticket-5/x.png; "
        "keeping the first" in plan.warnings[0]
    )
    assert "letter case" not in plan.warnings[0]


def test_a_case_only_clash_still_says_letter_case():
    text = (
        "![1](/bcs/raw-attachment/wiki/P/Diagram.png) "
        "![2](/bcs/raw-attachment/wiki/P/diagram.png)"
    )
    (warning,) = _plan(text, output="o.md").warnings
    assert "differs from 'P/Diagram.png' only in letter case" in warning
    assert "both map to" not in warning


# --- too large -------------------------------------------------------------------


def test_a_too_large_attachment_keeps_its_urls_and_is_counted():
    text = (
        "![a](/bcs/raw-attachment/wiki/P/a.png) "
        "![b](/bcs/raw-attachment/wiki/P/b.png) "
        "[b](/bcs/attachment/wiki/P/b.png) "
        "![c](/bcs/raw-attachment/wiki/P/c.png)"
    )
    plan = _plan(
        text,
        output="o.md",
        assets_dir="d",
        too_large=("b.png",),
        missing=("c.png",),
    )
    assert plan.saved == ["P/a.png"]
    assert plan.too_large == [AttachmentRef("wiki", "P", "b.png")]
    assert plan.text == (
        "![a](d/P/a.png) ![b](/bcs/raw-attachment/wiki/P/b.png) "
        "[b](/bcs/attachment/wiki/P/b.png) "
        "![c](/bcs/raw-attachment/wiki/P/c.png)"
    )
    large = [w for w in plan.warnings if "'b.png'" in w]
    assert len(large) == 1
    assert "100 bytes (max_inline_bytes)" in large[0]
    assert assets.summary(plan, "d") == (
        "Saved 1 attachment to d (0 unchanged, 1 missing, 1 too large)"
    )


def test_a_too_large_attachment_without_a_stated_limit():
    plan = _plan_of(
        "![b](/bcs/raw-attachment/wiki/P/b.png)",
        INST,
        {AttachmentRef("wiki", "P", "b.png"): assets.TooLarge()},
        "d",
        "o.md",
        "markdown",
    )
    assert "max_inline_bytes" in plan.warnings[0]
    assert "None" not in plan.warnings[0]


# --- attachment URLs under another path --------------------------------------------


def test_urls_under_another_instance_path_are_counted_and_warned_once():
    text = (
        "![a](https://h/proxy/trac/raw-attachment/wiki/P/a.png)\n"
        "[a](/proxy/trac/attachment/wiki/P/a.png)\n"
        '<a href="/x/attachment/ticket/3/f.txt">f</a>\n'
        "![ok](/bcs/raw-attachment/wiki/P/ok.png)\n"
    )
    scan = assets.scan_text(text, INST)
    assert scan.unmatched == 3
    assert [r.filename for r in scan.refs] == ["ok.png"]
    plan = _plan(text, output="o.md", assets_dir="d")
    (warning,) = plan.warnings
    assert "3 URLs" in warning
    assert "'/bcs'" in warning
    assert "proxy" in warning
    # left untouched
    assert plan.text.count("/proxy/trac/") == 2
    assert "/x/attachment/ticket/3/f.txt" in plan.text


def test_one_unmatched_url_is_singular():
    scan = assets.scan_text("[a](/other/attachment/wiki/P/a.png)", INST)
    assert scan.unmatched == 1
    plan = _plan("[a](/other/attachment/wiki/P/a.png)", output="o.md")
    assert "1 URL " in plan.warnings[0]


def test_matched_urls_and_plain_links_are_not_unmatched():
    text = (
        f"[a]({A_URL}) [w](/bcs/wiki/P) [x](https://e.org/page) "
        "[q](/bcs/attachment/wiki/P/a.png?format=raw) "
        "[e](/bcs/attachment/wiki/P/..)"
    )
    assert assets.scan_text(text, INST).unmatched == 0


def test_a_root_instance_ignores_other_hosts_but_counts_its_own_proxy():
    inst = "https://trac.example.org/"
    text = (
        "[other](https://trac.edgewall.org/attachment/wiki/P/a.png) "
        "[other2](https://trac.edgewall.org/trac/attachment/wiki/P/a.png) "
        "[mine](https://trac.example.org/base/attachment/wiki/P/a.png) "
        "[rel](/base/attachment/wiki/P/a.png)"
    )
    scan = assets.scan_text(text, inst)
    assert scan.unmatched == 2
    plan = assets.AssetPlanner(scan, "d", "o.md").result(text)
    assert "'/'" in plan.warnings[0]


def test_urls_in_code_are_not_counted_as_unmatched():
    text = "`[a](/other/attachment/wiki/P/a.png)`"
    assert assets.scan_text(text, INST).unmatched == 0


def test_no_warning_without_unmatched_urls():
    assert _plan(f"[a]({A_URL})", output="o.md").warnings == []


# --- the label of a rewritten autolink ----------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "*draft*.png",
        "_a_b_.png",
        "a`b.png",
        "[x].png",
        "a<b>.png",
        "~~s~~.png",
        "#1.png",
        "!bang.png",
        "plain.png",
    ],
)
def test_an_autolink_label_renders_as_the_file_name(name):
    from urllib.parse import quote

    from markdown_it import MarkdownIt

    url = f"https://h/bcs/attachment/wiki/P/{quote(name)}"
    plan = _plan(f"<{url}>", output="o.md", assets_dir="d")
    assert plan.text.startswith("[")
    html_out = MarkdownIt("commonmark").render(plan.text)
    # one link whose text is the file name, nothing else turned into markup
    import html as htmllib
    import re

    (label,) = re.findall(r"<a href=\"[^\"]*\">(.*)</a>", html_out)
    assert htmllib.unescape(label) == name
    assert label == htmllib.escape(name, quote=False)
