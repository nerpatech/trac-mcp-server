"""``trac-convert --from-wiki PAGE --to md --assets DIR [--assets-url
PREFIX]`` (Trac /trac_mcp_server #215), through the ``fake_trac`` fixture:
the page's HTML names attachments, the fake client serves them.

The ticket's acceptance: two attachments and one missing -- the two are
saved and relinked, the missing one keeps its URLs, exit 0.
"""

import xmlrpc.client

import pytest
import requests.exceptions

from trac_mcp_server.cli.convert import (
    EXIT_OK,
    EXIT_TRAC,
    EXIT_USAGE_ERROR,
    main,
)

BASE = "https://trac.example.com/bcs"
PNG_A = b"\x89PNG\r\n\x1a\n" + b"A" * 40
PNG_B = b"\x89PNG\r\n\x1a\n" + b"B" * 40


def _image(page, name, realm="wiki"):
    return (
        f'<a href="{BASE}/attachment/{realm}/{page}/{name}" '
        f'style="padding:0; border:none"><img alt="{name}" '
        f'src="{BASE}/raw-attachment/{realm}/{page}/{name}" '
        f'title="{name}" /></a>'
    )


def _html(extra=""):
    return (
        '<h1 id="T">T</h1>\n<p>\n'
        + "\n".join(_image("P", n) for n in ("a.png", "b.png", "c.png"))
        + "\n"
        + extra
        + "</p>\n"
    )


def _not_found(name):
    return xmlrpc.client.Fault(404, f"Attachment '{name}' not found")


@pytest.fixture
def trac(fake_trac, isolated_config, monkeypatch):
    """Page ``P`` shows a.png and b.png (attached) and c.png (not), on
    the instance ``/bcs``."""
    monkeypatch.setenv("TRAC_URL", BASE)
    served = {("P/a.png"): PNG_A, ("P/b.png"): PNG_B}

    def get_wiki_attachment(path):
        if path not in served:
            raise _not_found(path)
        return xmlrpc.client.Binary(served[path])

    fake_trac.served = served
    fake_trac.get_wiki_page.return_value = "= T =\n"
    fake_trac.get_wiki_page_info.return_value = {"version": 3}
    fake_trac.get_wiki_page_html.return_value = _html()
    fake_trac.get_wiki_attachment.side_effect = get_wiki_attachment
    return fake_trac


def _run(*flags):
    return main(["--from-wiki", "P", "--to", "md", *flags])


def test_two_attachments_and_one_missing(trac, tmp_path, capsys):
    out = tmp_path / "content" / "p.md"
    out.parent.mkdir()
    img = tmp_path / "static" / "images"
    rc = _run("-o", str(out), "--assets", str(img))
    assert rc == EXIT_OK
    assert (img / "P" / "a.png").read_bytes() == PNG_A
    assert (img / "P" / "b.png").read_bytes() == PNG_B
    assert not (img / "P" / "c.png").exists()
    text = out.read_text()
    assert (
        '[![a.png](../static/images/P/a.png "a.png")]'
        "(../static/images/P/a.png)"
    ) in text
    assert "(../static/images/P/b.png)" in text
    assert f"{BASE}/raw-attachment/wiki/P/c.png" in text
    assert f"{BASE}/attachment/wiki/P/c.png" in text
    err = capsys.readouterr().err
    assert (
        "warning: attachment 'c.png' of wiki page 'P' was not found; "
        "its links are left as they are" in err
    )
    assert (
        f"Saved 2 attachments to {img} (0 unchanged, 1 missing)" in err
    )
    trac.get_wiki_attachment.assert_any_call("P/a.png")


def test_assets_url(trac, tmp_path):
    out = tmp_path / "content" / "posts" / "p.md"
    out.parent.mkdir(parents=True)
    img = tmp_path / "static" / "images"
    rc = _run(
        "-o", str(out), "--assets", str(img), "--assets-url", "/images"
    )
    assert rc == EXIT_OK
    assert (
        '[![a.png](/images/P/a.png "a.png")](/images/P/a.png)'
        in out.read_text()
    )


def test_stdout_links_from_the_current_directory(
    trac, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    rc = _run("--assets", "static/images")
    assert rc == EXIT_OK
    captured = capsys.readouterr()
    assert "(static/images/P/a.png)" in captured.out
    assert "Saved" not in captured.out
    assert "Saved 2 attachments" in captured.err
    assert (
        tmp_path / "static" / "images" / "P" / "a.png"
    ).read_bytes() == PNG_A


def test_quiet_suppresses_the_warnings_and_the_report(
    trac, tmp_path, capsys
):
    rc = _run(
        "-q",
        "--assets",
        str(tmp_path / "img"),
        "-o",
        str(tmp_path / "p.md"),
    )
    assert rc == EXIT_OK
    assert capsys.readouterr().err == ""


def test_ticket_attachment_and_nested_page(trac, tmp_path):
    trac.get_wiki_page_html.return_value = _html(
        _image("42", "log.png", realm="ticket")
        + _image("A/B", "deep.png")
    )
    trac.served["A/B/deep.png"] = b"deep"
    trac.get_ticket_attachment.return_value = xmlrpc.client.Binary(
        b"ticket"
    )
    out = tmp_path / "p.md"
    img = tmp_path / "img"
    assert _run("-o", str(out), "--assets", str(img)) == EXIT_OK
    trac.get_ticket_attachment.assert_called_once_with(42, "log.png")
    trac.get_wiki_attachment.assert_any_call("A/B/deep.png")
    assert (img / "ticket-42" / "log.png").read_bytes() == b"ticket"
    assert (img / "A" / "B" / "deep.png").read_bytes() == b"deep"
    text = out.read_text()
    assert "![log.png](img/ticket-42/log.png" in text
    assert "![deep.png](img/A/B/deep.png" in text


def test_a_failure_on_the_second_fetch_writes_nothing(
    trac, tmp_path, capsys
):
    calls = []

    def flaky(path):
        calls.append(path)
        if len(calls) == 2:
            raise requests.exceptions.ConnectionError("reset by peer")
        return xmlrpc.client.Binary(PNG_A)

    trac.get_wiki_attachment.side_effect = flaky
    out = tmp_path / "p.md"
    img = tmp_path / "img"
    rc = _run("-o", str(out), "--assets", str(img))
    assert rc == EXIT_TRAC
    assert calls == ["P/a.png", "P/b.png"]
    assert not out.exists()
    assert not img.exists()
    assert "reset by peer" in capsys.readouterr().err


def test_a_permission_fault_is_a_failure_not_a_missing_attachment(
    trac, tmp_path, capsys
):
    trac.get_wiki_attachment.side_effect = xmlrpc.client.Fault(
        403, "WIKI_VIEW privileges are required"
    )
    out = tmp_path / "p.md"
    rc = _run("-o", str(out), "--assets", str(tmp_path / "img"))
    assert rc == EXIT_TRAC
    assert not out.exists() and not (tmp_path / "img").exists()
    assert "permission denied" in capsys.readouterr().err


def test_one_name_on_two_pages_gets_two_directories(trac, tmp_path):
    trac.get_wiki_page_html.return_value = _html(_image("Q", "a.png"))
    trac.served["Q/a.png"] = b"q"
    out = tmp_path / "p.md"
    img = tmp_path / "img"
    assert _run("-o", str(out), "--assets", str(img)) == EXIT_OK
    assert (img / "P" / "a.png").read_bytes() == PNG_A
    assert (img / "Q" / "a.png").read_bytes() == b"q"
    text = out.read_text()
    assert "img/P/a.png" in text and "img/Q/a.png" in text


def test_root_instance_leaves_another_host_alone(
    trac, tmp_path, monkeypatch
):
    monkeypatch.setenv("TRAC_URL", "https://trac.example.com")
    own = "https://trac.example.com/raw-attachment/wiki/P/a.png"
    other = "https://trac.edgewall.org/raw-attachment/wiki/P/a.png"
    trac.get_wiki_page_html.return_value = (
        f'<p><img alt="o" src="{other}"><img alt="a" src="{own}"></p>\n'
    )
    out = tmp_path / "p.md"
    img = tmp_path / "img"
    assert _run("-o", str(out), "--assets", str(img)) == EXIT_OK
    trac.get_wiki_attachment.assert_called_once_with("P/a.png")
    text = out.read_text()
    assert f"![o]({other})" in text
    assert "![a](img/P/a.png)" in text


def test_a_missing_output_directory_stops_before_any_fetch(
    trac, tmp_path, capsys
):
    img = tmp_path / "img"
    rc = _main_usage(
        [
            "--from-wiki",
            "P",
            "--to",
            "md",
            "-o",
            str(tmp_path / "missing_dir" / "p.md"),
            "--assets",
            str(img),
        ]
    )
    assert rc == EXIT_USAGE_ERROR
    assert "output directory does not exist" in capsys.readouterr().err
    assert not img.exists()
    trac.get_wiki_attachment.assert_not_called()
    trac.get_wiki_page.assert_not_called()
    trac.get_wiki_page_html.assert_not_called()


def test_an_output_parent_that_is_a_file_stops_before_any_fetch(
    trac, tmp_path, capsys
):
    parent = tmp_path / "afile"
    parent.write_text("x")
    rc = _main_usage(
        [
            "--from-wiki",
            "P",
            "--to",
            "md",
            "-o",
            str(parent / "p.md"),
            "--assets",
            str(tmp_path / "img"),
        ]
    )
    assert rc == EXIT_USAGE_ERROR
    assert not (tmp_path / "img").exists()
    trac.get_wiki_attachment.assert_not_called()


# --- second review round (Trac /trac_mcp_server #215) ----------------------------


def test_an_output_that_is_a_directory_is_a_usage_error_before_any_fetch(
    trac, tmp_path, capsys
):
    out = tmp_path / "p.md"
    out.mkdir()
    rc = _main_usage(
        [
            "--from-wiki",
            "P",
            "--to",
            "md",
            "-o",
            str(out),
            "--assets",
            str(tmp_path / "img"),
        ]
    )
    assert rc == EXIT_USAGE_ERROR
    assert "is a directory" in capsys.readouterr().err
    assert not (tmp_path / "img").exists()
    trac.get_wiki_attachment.assert_not_called()
    trac.get_wiki_page.assert_not_called()


def test_a_file_where_a_directory_is_needed_is_a_usage_error_and_writes_nothing(
    trac, tmp_path, capsys
):
    img = tmp_path / "img"
    img.mkdir()
    (img / "P").write_bytes(b"an earlier attachment named P")
    out = tmp_path / "p.md"
    rc = _main_usage(
        [
            "--from-wiki",
            "P",
            "--to",
            "md",
            "-o",
            str(out),
            "--assets",
            str(img),
        ]
    )
    assert rc == EXIT_USAGE_ERROR
    err = capsys.readouterr().err
    assert "is a file, not a directory" in err
    assert "nothing was written" in err
    assert not out.exists()
    assert (img / "P").read_bytes() == b"an earlier attachment named P"
    assert [p.name for p in tmp_path.rglob("*")] == ["img", "P"]


def test_a_write_failure_while_staging_leaves_nothing_and_keeps_the_old_output(
    trac, tmp_path, monkeypatch, capsys
):
    from trac_mcp_server.cli import assets as export
    from trac_mcp_server.cli.convert import EXIT_RUNTIME_ERROR

    trac.served["P/c.png"] = b"C"
    trac.served["P/d.png"] = b"D"
    real = export._write_temp
    calls = []

    def flaky(path, data):
        calls.append(path)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        real(path, data)

    monkeypatch.setattr(export, "_write_temp", flaky, raising=False)
    out = tmp_path / "p.md"
    out.write_text("the old page")
    rc = _run("-o", str(out), "--assets", str(tmp_path / "img"))
    assert rc == EXIT_RUNTIME_ERROR
    assert "No space left on device" in capsys.readouterr().err
    assert out.read_text() == "the old page"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["p.md"]


def test_the_output_and_the_assets_arrive_together_without_temp_files(
    trac, tmp_path
):
    out = tmp_path / "p.md"
    img = tmp_path / "img"
    assert _run("-o", str(out), "--assets", str(img)) == EXIT_OK
    assert out.exists() and (img / "P" / "a.png").exists()
    assert [p for p in tmp_path.rglob("*") if ".tmp-" in p.name] == []


# --- usage errors -----------------------------------------------------------
# Like --link-map's, a usage error is argparse's: SystemExit(2).


def _main_usage(argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    return exc.value.code


def test_assets_with_tracwiki_is_a_usage_error(trac, tmp_path, capsys):
    out = tmp_path / "p.tw"
    rc = _main_usage(
        [
            "--from-wiki",
            "P",
            "--to",
            "tracwiki",
            "-o",
            str(out),
            "--assets",
            str(tmp_path / "img"),
        ]
    )
    assert rc == EXIT_USAGE_ERROR
    err = capsys.readouterr().err
    assert "--assets" in err and "tracwiki" in err
    assert not out.exists() and not (tmp_path / "img").exists()
    trac.get_wiki_attachment.assert_not_called()


def test_assets_without_from_wiki_is_a_usage_error(
    trac, tmp_path, capsys
):
    src = tmp_path / "in.wiki"
    src.write_text("= T =\n")
    rc = _main_usage(
        [
            str(src),
            "--from",
            "tracwiki",
            "--to",
            "md",
            "--assets",
            str(tmp_path / "img"),
        ]
    )
    assert rc == EXIT_USAGE_ERROR
    assert "--assets needs --from-wiki" in capsys.readouterr().err


def test_assets_with_to_wiki_is_a_usage_error(trac, tmp_path, capsys):
    rc = _main_usage(
        [
            "--from-wiki",
            "P",
            "--to-wiki",
            "Q",
            "--assets",
            str(tmp_path / "img"),
        ]
    )
    assert rc == EXIT_USAGE_ERROR
    assert "--assets" in capsys.readouterr().err
    trac.put_wiki_page.assert_not_called()


def test_to_wiki_direction_without_from_wiki_is_a_usage_error(
    trac, tmp_path, capsys
):
    src = tmp_path / "in.md"
    src.write_text("# T\n")
    rc = _main_usage(
        [
            "--from-file",
            str(src),
            "--to-wiki",
            "Q",
            "--assets",
            str(tmp_path / "img"),
        ]
    )
    assert rc == EXIT_USAGE_ERROR
    trac.put_wiki_page.assert_not_called()


def test_assets_url_without_assets_is_a_usage_error(trac, capsys):
    rc = _main_usage(
        ["--from-wiki", "P", "--to", "md", "--assets-url", "/images"]
    )
    assert rc == EXIT_USAGE_ERROR
    assert "--assets-url needs --assets" in capsys.readouterr().err


def test_assets_with_check_trac_is_a_usage_error(
    trac, tmp_path, capsys
):
    rc = _main_usage(
        ["--check-trac", "--assets", str(tmp_path / "img")]
    )
    assert rc == EXIT_USAGE_ERROR


# --- third review round (Trac /trac_mcp_server #215) ----------------------------


def test_a_conflict_at_a_missing_attachments_path_only_warns(
    trac, tmp_path, capsys
):
    # c.png is not on the server; a directory of that name where it
    # would go is no obstacle.
    img = tmp_path / "img"
    (img / "P" / "c.png").mkdir(parents=True)
    out = tmp_path / "p.md"
    assert _run("-o", str(out), "--assets", str(img)) == EXIT_OK
    err = capsys.readouterr().err
    assert "attachment 'c.png' of wiki page 'P' was not found" in err
    assert (img / "P" / "a.png").read_bytes() == PNG_A
    assert (img / "P" / "c.png").is_dir()


def test_a_conflict_at_a_found_attachments_path_writes_nothing(
    trac, tmp_path, capsys
):
    img = tmp_path / "img"
    (img / "P" / "b.png").mkdir(parents=True)
    out = tmp_path / "p.md"
    rc = _main_usage(
        ["--from-wiki", "P", "--to", "md", "-o", str(out)]
        + ["--assets", str(img)]
    )
    assert rc == EXIT_USAGE_ERROR
    assert "b.png is a directory" in capsys.readouterr().err
    assert not out.exists()
    assert sorted(
        p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")
    ) == ["img", "img/P", "img/P/b.png"]


def test_a_dangling_symlinked_output_is_a_usage_error_before_any_fetch(
    trac, tmp_path, capsys
):
    work = tmp_path / "work"
    work.mkdir()
    (work / "out.md").symlink_to("../nosite/page.md")
    img = tmp_path / "img"
    rc = _main_usage(
        ["--from-wiki", "P", "--to", "md", "-o", str(work / "out.md")]
        + ["--assets", str(img)]
    )
    assert rc == EXIT_USAGE_ERROR
    assert "output directory does not exist" in capsys.readouterr().err
    trac.get_wiki_attachment.assert_not_called()
    assert not (tmp_path / "nosite").exists() and not img.exists()


def test_a_symlinked_output_links_from_where_it_is_written(
    trac, tmp_path
):
    (tmp_path / "site").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / "out.md").symlink_to("../site/page.md")
    img = work / "img"
    assert (
        _run("-o", str(work / "out.md"), "--assets", str(img))
        == EXIT_OK
    )
    assert (
        "(../work/img/P/a.png"
        in (tmp_path / "site" / "page.md").read_text()
    )


def test_an_output_that_is_a_planned_attachment_is_a_usage_error(
    trac, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "img" / "P").mkdir(parents=True)
    rc = _main_usage(
        ["--from-wiki", "P", "--to", "md", "-o", "img/P/a.png"]
        + ["--assets", "img"]
    )
    assert rc == EXIT_USAGE_ERROR
    assert "nothing was written" in capsys.readouterr().err
    assert [p.name for p in (tmp_path / "img" / "P").iterdir()] == []
