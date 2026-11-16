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


def _fetched(text, instance=INST, missing=(), data=None):
    out = {}
    for ref in assets.find_refs(text, instance):
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
    **kw,
):
    return assets.plan_assets(
        text,
        instance,
        _fetched(text, instance, **kw),
        assets_dir,
        output,
        url,
    )


# --- finding -----------------------------------------------------------


def test_golden_markdown_one_attachment_two_urls():
    refs = assets.find_refs(GOLDEN_MD, INST)
    assert refs == [AttachmentRef("wiki", GOLDEN_PAGE, "part1.png")]


def test_golden_markdown_both_links_point_at_the_local_file():
    plan = _plan(GOLDEN_MD, output="out.md", assets_dir="img")
    assert plan.text == (
        '[![Part 1: ctrl_meas](img/part1.png "Part 1: ctrl_meas")]'
        "(img/part1.png)\n"
    )
    assert plan.files == {"part1.png": b"bytes-of-part1.png"}
    assert plan.missing == [] and plan.warnings == []


def test_golden_html_both_attributes_rewritten_as_written():
    plan = _plan(GOLDEN_HTML, output="out.html", assets_dir="img")
    assert plan.text == (
        '<a href="img/part1.png" style="padding:0; border:none">'
        '<img alt="Part 1" src="img/part1.png" title="Part 1" /></a>\n'
    )
    assert list(plan.files) == ["part1.png"]


def test_html_single_quoted_and_entity_encoded_attribute():
    text = (
        "<img src='https://h/bcs/raw-attachment/wiki/P/a&amp;b.png'>"
        '<a href="/bcs/attachment/wiki/P/a&amp;b.png">x</a>'
    )
    refs = assets.find_refs(text, INST)
    assert refs == [AttachmentRef("wiki", "P", "a&b.png")]
    plan = _plan(text, output="o.html", assets_dir="d")
    assert plan.text == (
        "<img src='d/a%26b.png'><a href=\"d/a%26b.png\">x</a>"
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
    assert plan.text == "![i](d/x.png) [l](d/x.png)"


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
    for inst in ("", "/", "https://h/"):
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
    ],
)
def test_malformed_attachment_paths_are_not_references(url):
    assert assets.find_refs(f"![i]({url})", INST) == []


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
    assert "![c](d/y.png)" in plan.text


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
    assert plan.text == "see [we\\[ird\\].png](d/we%5Bird%5D.png) now"


def test_angle_bracket_target_is_not_also_an_autolink():
    text = "[l](<https://h/bcs/attachment/wiki/P/x.png>)"
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.text == "[l](<d/x.png>)"


def test_percent_encoded_filename_is_fetched_decoded_and_relinked_encoded():
    text = (
        "![i](https://h/bcs/raw-attachment/wiki/P/my%20pic%C3%A9.png)"
    )
    refs = assets.find_refs(text, INST)
    assert refs == [AttachmentRef("wiki", "P", "my picé.png")]
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.files == {"my picé.png": b"bytes-of-my pic\xc3\xa9.png"}
    assert plan.text == "![i](d/my%20pic%C3%A9.png)"


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


# --- naming --------------------------------------------------------------


def test_name_collision_second_gets_the_parent_prefix_and_a_warning():
    text = (
        "![1](/bcs/raw-attachment/wiki/A/b/x.png) "
        "![2](/bcs/raw-attachment/wiki/C/x.png) "
        "![3](/bcs/raw-attachment/ticket/7/x.png)"
    )
    plan = _plan(text, output="o.md", assets_dir="d")
    assert list(plan.files) == ["x.png", "C--x.png", "7--x.png"]
    assert (
        plan.text == "![1](d/x.png) ![2](d/C--x.png) ![3](d/7--x.png)"
    )
    assert len(plan.warnings) == 2
    assert "'C--x.png'" in plan.warnings[0]
    assert "wiki page 'C'" in plan.warnings[0]
    assert "ticket #7" in plan.warnings[1]


def test_collision_prefix_is_filesystem_safe():
    text = (
        "![1](/bcs/raw-attachment/wiki/P/x.png) "
        "![2](/bcs/raw-attachment/wiki/A/B%20C:D/x.png)"
    )
    plan = _plan(text, output="o.md", assets_dir="d")
    assert list(plan.files) == ["x.png", "A-B-C-D--x.png"]


def test_collision_when_the_prefixed_name_is_taken_too():
    # "A/B" and "A-B" both sanitise to "A-B".
    text = (
        "![1](/bcs/raw-attachment/wiki/P/x.png) "
        "![2](/bcs/raw-attachment/wiki/A/B/x.png) "
        "![3](/bcs/raw-attachment/wiki/A-B/x.png)"
    )
    plan = _plan(text, output="o.md", assets_dir="d")
    assert list(plan.files) == ["x.png", "A-B--x.png", "A-B--2--x.png"]
    assert plan.text == (
        "![1](d/x.png) ![2](d/A-B--x.png) ![3](d/A-B--2--x.png)"
    )


def test_naming_is_deterministic():
    text = (
        "![1](/bcs/raw-attachment/wiki/P/x.png) "
        "![2](/bcs/raw-attachment/wiki/Q/x.png)"
    )
    assert (
        _plan(text, output="o.md").text
        == _plan(text, output="o.md").text
    )


# --- links ---------------------------------------------------------------


def test_relative_link_from_a_nested_output_directory():
    text = "![i](/bcs/raw-attachment/wiki/P/x.png)"
    plan = _plan(
        text, output="content/posts/x.md", assets_dir="static/images"
    )
    assert plan.text == "![i](../../static/images/x.png)"


def test_relative_link_when_the_output_is_beside_the_assets_dir():
    text = "![i](/bcs/raw-attachment/wiki/P/x.png)"
    plan = _plan(text, output="x.md", assets_dir="static/images")
    assert plan.text == "![i](static/images/x.png)"


def test_stdout_links_from_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    text = "![i](/bcs/raw-attachment/wiki/P/x.png)"
    plan = _plan(text, output=None, assets_dir="static/images")
    assert plan.text == "![i](static/images/x.png)"
    plan = _plan(text, output=None, assets_dir=tmp_path / "up" / "i")
    assert plan.text == "![i](up/i/x.png)"


def test_relative_link_uses_posix_separators_and_never_looks_like_a_scheme():
    text = "![i](/bcs/raw-attachment/wiki/P/a:b.png)"
    plan = _plan(text, output="o.md", assets_dir="d")
    assert plan.text == "![i](d/a%3Ab.png)"


def test_assets_url_replaces_the_relative_path():
    text = GOLDEN_MD
    plan = _plan(
        text,
        output="content/posts/x.md",
        assets_dir="static/images",
        url="/images",
    )
    assert plan.text.count("(/images/part1.png") == 2
    assert "static" not in plan.text and ".." not in plan.text


def test_assets_url_trailing_slash_and_encoding():
    text = "![i](/bcs/raw-attachment/wiki/P/my pic.png)".replace(
        "my pic", "my%20pic"
    )
    plan = _plan(text, output="o.md", url="https://cdn.example/a/")
    assert plan.text == "![i](https://cdn.example/a/my%20pic.png)"
    plan = _plan(text, output="o.md", url="/")
    assert plan.text == "![i](/my%20pic.png)"


def test_assets_url_in_html_is_attribute_escaped():
    text = '<img src="/bcs/raw-attachment/wiki/P/x.png">'
    plan = _plan(text, output="o.html", url="/i?a=1&b=2")
    assert plan.text == '<img src="/i?a=1&amp;b=2/x.png">'


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
        "![a](d/a.png) ![b](/bcs/raw-attachment/wiki/P/b.png) "
        "[b](/bcs/attachment/wiki/P/b.png)"
    )
    assert list(plan.files) == ["a.png"]
    assert plan.missing == [AttachmentRef("wiki", "P", "b.png")]
    assert len(plan.warnings) == 1
    assert "'b.png'" in plan.warnings[0]
    assert "wiki page 'P'" in plan.warnings[0]


def test_a_missing_attachment_does_not_take_a_name():
    text = (
        "![1](/bcs/raw-attachment/wiki/A/x.png) "
        "![2](/bcs/raw-attachment/wiki/B/x.png)"
    )
    plan = assets.plan_assets(
        text,
        INST,
        {
            AttachmentRef("wiki", "A", "x.png"): None,
            AttachmentRef("wiki", "B", "x.png"): b"b",
        },
        "d",
        "o.md",
    )
    assert plan.files == {"x.png": b"b"}
    assert "![2](d/x.png)" in plan.text


# --- writing -------------------------------------------------------------


def test_write_creates_the_directory_and_the_files(tmp_path):
    plan = _plan(
        GOLDEN_MD, output="o.md", assets_dir=tmp_path / "a" / "b"
    )
    assert assets.write_assets(plan, tmp_path / "a" / "b") == (1, 0)
    assert (tmp_path / "a" / "b" / "part1.png").read_bytes() == (
        b"bytes-of-part1.png"
    )


def test_identical_bytes_are_not_rewritten_and_changed_bytes_are(
    tmp_path,
):
    d = tmp_path / "d"
    d.mkdir()
    same = d / "same.png"
    same.write_bytes(b"same")
    changed = d / "changed.png"
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
    assert (d / "new.png").read_bytes() == b"n"


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


def test_as_bytes():
    import xmlrpc.client

    assert assets.as_bytes(xmlrpc.client.Binary(b"ab")) == b"ab"
    assert assets.as_bytes(bytearray(b"ab")) == b"ab"
    assert assets.as_bytes("é") == "é".encode()
    with pytest.raises(ValueError):
        assets.as_bytes(3)
