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
    assert (img / "a.png").read_bytes() == PNG_A
    assert (img / "b.png").read_bytes() == PNG_B
    assert not (img / "c.png").exists()
    text = out.read_text()
    assert (
        '[![a.png](../static/images/a.png "a.png")]'
        "(../static/images/a.png)"
    ) in text
    assert "(../static/images/b.png)" in text
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
        '[![a.png](/images/a.png "a.png")](/images/a.png)'
        in out.read_text()
    )


def test_stdout_links_from_the_current_directory(
    trac, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    rc = _run("--assets", "static/images")
    assert rc == EXIT_OK
    captured = capsys.readouterr()
    assert "(static/images/a.png)" in captured.out
    assert "Saved" not in captured.out
    assert "Saved 2 attachments" in captured.err
    assert (
        tmp_path / "static" / "images" / "a.png"
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
    assert (img / "log.png").read_bytes() == b"ticket"
    assert (img / "deep.png").read_bytes() == b"deep"


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
