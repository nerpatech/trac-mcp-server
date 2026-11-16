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


def _fetched(
    text, instance=INST, missing=(), data=None, fmt="markdown"
):
    out = {}
    for ref in assets.find_refs(text, instance, fmt):
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
    return assets.plan_assets(
        text,
        instance,
        _fetched(text, instance, fmt=fmt, **kw),
        assets_dir,
        output,
        url,
        fmt=fmt,
    )


# --- finding -----------------------------------------------------------


def test_golden_markdown_one_attachment_two_urls():
    refs = assets.find_refs(GOLDEN_MD, INST)
    assert refs == [AttachmentRef("wiki", GOLDEN_PAGE, "part1.png")]


def test_golden_markdown_both_links_point_at_the_local_file():
    plan = _plan(GOLDEN_MD, output="out.md", assets_dir="img")
    where = "img/b-node/blog/scope/part1.png"
    assert plan.text == (
        f'[![Part 1: ctrl_meas]({where} "Part 1: ctrl_meas")]({where})\n'
    )
    assert plan.files == {
        "b-node/blog/scope/part1.png": b"bytes-of-part1.png"
    }
    assert plan.missing == [] and plan.warnings == []


def test_golden_html_both_attributes_rewritten_as_written():
    plan = _plan(GOLDEN_HTML, output="out.html", assets_dir="img")
    where = "img/b-node/blog/scope/part1.png"
    assert plan.text == (
        f'<a href="{where}" style="padding:0; border:none">'
        f'<img alt="Part 1" src="{where}" title="Part 1" /></a>\n'
    )
    assert list(plan.files) == ["b-node/blog/scope/part1.png"]


def test_html_single_quoted_and_entity_encoded_attribute():
    text = (
        "<img src='https://h/bcs/raw-attachment/wiki/P/a&amp;b.png'>"
        '<a href="/bcs/attachment/wiki/P/a&amp;b.png">x</a>'
    )
    refs = assets.find_refs(text, INST)
    assert refs == [AttachmentRef("wiki", "P", "a&b.png")]
    refs = assets.find_refs(text, INST, "html")
    assert refs == [AttachmentRef("wiki", "P", "a&b.png")]
    plan = _plan(text, output="o.html", assets_dir="d", fmt="html")
    assert plan.text == (
        "<img src='d/P/a%26b.png'><a href=\"d/P/a%26b.png\">x</a>"
    )


def test_another_host_than_trac_url_still_matches():
    text = (
        "![i](http://internal.lan:8000/bcs/raw-attachment/wiki/P/x.png)"
    )
    assert assets.find_refs(text, INST) == [
        AttachmentRef("wiki", "P", "x.png")
    ]


def test_root_relative_urls_match():
    text = (
        "![i](/bcs/raw-attachment/wiki/P/x.png) "
        "[l](/bcs/attachment/wiki/P/x.png)"
    )
    assert assets.find_refs(text, INST) == [
        AttachmentRef("wiki", "P", "x.png")
    ]
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.text == "![i](d/P/x.png) [l](d/P/x.png)"


def test_another_instance_does_not_match():
    text = "![i](https://h/other/raw-attachment/wiki/P/x.png)"
    assert assets.find_refs(text, INST) == []
    assert (
        assets.find_refs(
            "![i](/bcsx/raw-attachment/wiki/P/x.png)", INST
        )
        == []
    )


def test_instance_at_the_root():
    text = "![i](https://h/raw-attachment/wiki/P/x.png)"
    for inst in ("https://h/", "https://h", "http://H:8000/"):
        assert assets.find_refs(text, inst) == [
            AttachmentRef("wiki", "P", "x.png")
        ]
    rel = "![i](/raw-attachment/wiki/P/x.png)"
    for inst in ("", "/", "https://h/"):
        assert assets.find_refs(rel, inst) == [
            AttachmentRef("wiki", "P", "x.png")
        ]


def test_root_instance_matches_an_absolute_url_only_on_its_own_host():
    other = "https://trac.edgewall.org/raw-attachment/wiki/P/x.png"
    own = "https://trac.example.org/raw-attachment/wiki/P/x.png"
    text = f"![a]({other}) ![b]({own}) [c]({other}) <{other}>"
    inst = "https://trac.example.org/"
    assert assets.find_refs(text, inst) == [
        AttachmentRef("wiki", "P", "x.png")
    ]
    plan = _plan(text, instance=inst, output="o.md", assets_dir="d")
    assert plan.text == (
        f"![a]({other}) ![b](d/P/x.png) [c]({other}) <{other}>"
    )
    # the host alone is compared: not its scheme, port or letter case
    assert assets.find_refs(
        "![a](http://TRAC.example.org:8001/raw-attachment/wiki/P/x.png)",
        inst,
    ) == [AttachmentRef("wiki", "P", "x.png")]


def test_root_instance_protocol_relative_url_is_host_checked():
    inst = "https://trac.example.org"
    assert (
        assets.find_refs(
            "![a](//trac.edgewall.org/raw-attachment/wiki/P/x.png)",
            inst,
        )
        == []
    )
    assert assets.find_refs(
        "![a](//trac.example.org/raw-attachment/wiki/P/x.png)", inst
    ) == [AttachmentRef("wiki", "P", "x.png")]


def test_root_instance_with_no_known_host_matches_no_absolute_url():
    text = (
        "![a](https://h/raw-attachment/wiki/P/x.png) "
        "![b](/raw-attachment/wiki/P/y.png)"
    )
    for inst in ("", "/"):
        assert assets.find_refs(text, inst) == [
            AttachmentRef("wiki", "P", "y.png")
        ]


def test_an_instance_with_a_path_ignores_the_host():
    # Trac's base URL can name another host or port than TRAC_URL.
    text = "![a](https://elsewhere:9/bcs/raw-attachment/wiki/P/x.png)"
    for inst in ("/bcs", "https://trac.example.org/bcs"):
        assert assets.find_refs(text, inst) == [
            AttachmentRef("wiki", "P", "x.png")
        ]


def test_nested_wiki_parent_keeps_its_slashes():
    text = "![i](https://h/bcs/raw-attachment/wiki/A/B/C/pic.png)"
    assert assets.find_refs(text, INST) == [
        AttachmentRef("wiki", "A/B/C", "pic.png")
    ]


def test_ticket_realm():
    text = (
        "![i](https://h/bcs/raw-attachment/ticket/42/log.png)\n"
        "[l](https://h/bcs/attachment/ticket/42/log.png)\n"
    )
    assert assets.find_refs(text, INST) == [
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
    assert assets.find_refs(text, INST) == []
    assert _plan(text, output="o.md").text == text


def test_query_or_fragment_is_skipped():
    text = (
        "[a](https://h/bcs/raw-attachment/wiki/P/x.png?format=raw) "
        "[b](https://h/bcs/attachment/wiki/P/x.png#L3) "
        "![c](https://h/bcs/raw-attachment/wiki/P/y.png)"
    )
    assert assets.find_refs(text, INST) == [
        AttachmentRef("wiki", "P", "y.png")
    ]
    plan = _plan(text, output="o.md", assets_dir="d")
    assert "x.png?format=raw)" in plan.text
    assert "x.png#L3)" in plan.text
    assert "![c](d/P/y.png)" in plan.text


def test_external_image_is_untouched():
    text = "![i](https://cdn.example.com/raw-attachment/wiki/P/x.png)\n"
    plan = _plan(text, output="o.md")
    assert plan.text == text
    assert plan.files == {} and plan.missing == []


def test_only_url_positions_are_looked_at():
    url = "https://h/bcs/raw-attachment/wiki/P/x.png"
    text = (
        f"prose mentioning {url} and `code {url}`\n"
        f'<p title="{url}">t</p> <a name="{url}">n</a>\n'
    )
    assert assets.find_refs(text, INST) == []
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
    refs = assets.find_refs(text, INST)
    assert refs == [AttachmentRef("wiki", "P", "my picé.png")]
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.files == {
        "P/my picé.png": b"bytes-of-my pic\xc3\xa9.png"
    }
    assert plan.text == "![i](d/P/my%20pic%C3%A9.png)"


def test_each_attachment_is_fetched_once_in_first_appearance_order():
    text = (
        "![b](/bcs/raw-attachment/wiki/P/b.png) "
        "![a](/bcs/raw-attachment/wiki/P/a.png) "
        "[b](/bcs/attachment/wiki/P/b.png)"
    )
    calls = []

    def fetch(ref):
        calls.append(ref.filename)
        return b"x"

    refs = assets.find_refs(text, INST)
    assets.fetch_all(refs, fetch)
    assert calls == ["b.png", "a.png"]


def test_a_fetch_that_raises_stops_the_rest():
    refs = [AttachmentRef("wiki", "P", n) for n in ("a", "b", "c")]
    calls = []

    def fetch(ref):
        calls.append(ref.filename)
        if ref.filename == "b":
            raise OSError("boom")
        return b"x"

    with pytest.raises(OSError):
        assets.fetch_all(refs, fetch)
    assert calls == ["a", "b"]


# --- layout ----------------------------------------------------------------


def test_wiki_attachment_lands_under_its_page_path():
    text = "![i](/bcs/raw-attachment/wiki/blog/my-post/x.png)"
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.files == {"blog/my-post/x.png": b"bytes-of-x.png"}
    assert plan.text == "![i](d/blog/my-post/x.png)"
    assert plan.warnings == []


def test_ticket_attachment_lands_under_ticket_n():
    text = "![i](/bcs/raw-attachment/ticket/7/x.png)"
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.files == {"ticket-7/x.png": b"bytes-of-x.png"}
    assert plan.text == "![i](d/ticket-7/x.png)"


def test_same_file_name_on_two_pages_gets_two_directories_and_links():
    text = (
        "![1](/bcs/raw-attachment/wiki/A/b/diagram.png) "
        "![2](/bcs/raw-attachment/wiki/C/diagram.png) "
        "![3](/bcs/raw-attachment/ticket/7/diagram.png)"
    )
    plan = _plan(text, output="o.md", assets_dir="d")
    assert list(plan.files) == [
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
    first = assets.plan_assets(
        text,
        INST,
        {
            AttachmentRef("wiki", "A", "x.png"): None,
            AttachmentRef("wiki", "B", "x.png"): b"bx",
            AttachmentRef("wiki", "B", "y.png"): b"by",
        },
        "d",
        "o.md",
    )
    # the second run: it is there
    second = assets.plan_assets(
        text,
        INST,
        {
            AttachmentRef("wiki", "A", "x.png"): b"ax",
            AttachmentRef("wiki", "B", "x.png"): b"bx",
            AttachmentRef("wiki", "B", "y.png"): b"by",
        },
        "d",
        "o.md",
    )
    assert first.files == {"B/x.png": b"bx", "B/y.png": b"by"}
    assert "![2](d/B/x.png) ![3](d/B/y.png)" in first.text
    assert "![2](d/B/x.png) ![3](d/B/y.png)" in second.text
    assert second.files == {"A/x.png": b"ax", **first.files}
    assert list(before.files) == ["A/x.png", "B/x.png", "B/y.png"]
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
    assert list(plan.files) == ["P/Diagram.png"]
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
    assert list(plan.files) == ["Blog/x.png"]
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
    assert list(plan.files) == ["a/b"]
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
    assert list(plan.files) == ["P/diagram.png"]
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
    refs = assets.find_refs(code, INST)
    assert refs == []
    calls = []
    assets.fetch_all(refs, lambda ref: calls.append(ref) or b"x")
    assert calls == []
    plan = _plan(code, output="o.md", assets_dir="d")
    assert plan.text == code
    assert plan.files == {} and plan.warnings == []


def test_the_same_url_outside_code_is_rewritten():
    text = (
        f"{IMG}\n\n`{IMG}`\n\n```\n{IMG}\n```\n\n"
        f"<pre>{IMG}</pre>\n\n{IMG}\n"
    )
    assert len(assets.find_refs(text, INST)) == 1
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.text == (
        "![i](d/P/x.png)\n\n`" + IMG + "`\n\n```\n" + IMG + "\n```\n\n"
        f"<pre>{IMG}</pre>\n\n![i](d/P/x.png)\n"
    )
    assert plan.files == {"P/x.png": b"bytes-of-x.png"}


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
    assert assets.find_refs(text, INST) == [
        AttachmentRef("wiki", "P", "x.png")
    ]
    assert (
        "d/P/x.png" in _plan(text, output="o.md", assets_dir="d").text
    )


def test_urls_in_html_code_elements_are_skipped_and_others_rewritten():
    attr = f'<a href="{U}">x</a>'
    for tag in ("pre", "code", "script", "style", "textarea"):
        text = f"<{tag}>{attr}</{tag}>"
        assert assets.find_refs(text, INST, "html") == [], tag
        assert _plan(text, output="o.html", fmt="html").text == text
    text = f"<pre>{attr}</pre>{attr}"
    plan = _plan(text, output="o.html", assets_dir="d", fmt="html")
    assert plan.text == f'<pre>{attr}</pre><a href="d/P/x.png">x</a>'


def test_html_output_has_no_markdown_code():
    # backticks and fences are not code in an HTML document
    text = f'`<a href="{U}">x</a>`\n```\n<img src="{U}">\n```\n'
    plan = _plan(text, output="o.html", assets_dir="d", fmt="html")
    assert plan.text.count("d/P/x.png") == 2
    assert plan.files == {"P/x.png": b"bytes-of-x.png"}


def test_an_unclosed_html_code_element_runs_to_the_end():
    text = f"<pre>{IMG}"
    assert assets.find_refs(text, INST) == []


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
    assert list(plan.files) == ["P/a.png"]
    assert plan.missing == [AttachmentRef("wiki", "P", "b.png")]
    assert len(plan.warnings) == 1
    assert "'b.png'" in plan.warnings[0]
    assert "wiki page 'P'" in plan.warnings[0]


# --- writing -------------------------------------------------------------


def test_write_creates_the_directory_and_the_files(tmp_path):
    plan = _plan(
        GOLDEN_MD, output="o.md", assets_dir=tmp_path / "a" / "b"
    )
    assert assets.write_assets(plan, tmp_path / "a" / "b") == (1, 0)
    saved = (
        tmp_path / "a" / "b" / "b-node" / "blog" / "scope" / "part1.png"
    )
    assert saved.read_bytes() == b"bytes-of-part1.png"


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
    plan = _plan(
        text,
        assets_dir=d,
        output="o.md",
        data={
            "same.png": b"same",
            "changed.png": b"new",
            "new.png": b"n",
        },
    )
    assert assets.write_assets(plan, d) == (2, 1)
    assert same.stat().st_mtime == old_mtime
    assert changed.read_bytes() == b"new"
    assert (d / "P" / "new.png").read_bytes() == b"n"


def test_write_with_nothing_to_write_does_not_create_the_directory(
    tmp_path,
):
    plan = _plan("no attachments", output="o.md")
    assert assets.write_assets(plan, tmp_path / "never") == (0, 0)
    assert not (tmp_path / "never").exists()


def test_summary_line():
    text = (
        "![a](/bcs/raw-attachment/wiki/P/a.png) "
        "![b](/bcs/raw-attachment/wiki/P/b.png) "
        "![c](/bcs/raw-attachment/wiki/P/c.png)"
    )
    plan = _plan(text, output="o.md", missing=("c.png",))
    assert assets.summary(plan, "img", 1) == (
        "Saved 2 attachments to img (1 unchanged, 1 missing)"
    )
    one = _plan("![a](/bcs/raw-attachment/wiki/P/a.png)", output="o.md")
    assert assets.summary(one, Path("img"), 0) == (
        "Saved 1 attachment to img (0 unchanged, 0 missing)"
    )
