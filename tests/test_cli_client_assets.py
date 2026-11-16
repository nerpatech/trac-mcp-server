"""``trac-mcp wiki-pull --assets DIR [--assets-url PREFIX]`` (Trac
/trac_mcp_server #215), against the real substrate of
``tests/test_cli_client.py``: the production server over uvicorn, an
in-memory Trac behind it that renders a page with attachment URLs and
serves (or does not serve) the attachments.

The ticket's acceptance: a page with two attachments and one missing
exports to Markdown with the two saved and relinked and the missing one
kept as it was, exit 0.
"""

import asyncio
import base64
import os
import uuid
import xmlrpc.client
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_cli_client import (
    _TOKEN,
    FakeTrac,
    _cli,
    _no_network,
    _served,
    _served_instances,
)
from trac_mcp_server.cli import client as cli

BASE = "http://fake/trac_test"
PNG_A = b"\x89PNG\r\n\x1a\n" + b"A" * 40
PNG_B = b"\x89PNG\r\n\x1a\n" + b"B" * 40


def _image(page, name, realm="wiki"):
    return (
        f'<a href="{BASE}/attachment/{realm}/{page}/{name}" '
        f'style="padding:0; border:none"><img alt="{name}" '
        f'src="{BASE}/raw-attachment/{realm}/{page}/{name}" '
        f'title="{name}" /></a>'
    )


class AttachedTrac(FakeTrac):
    """Page ``P`` shows a.png and b.png (attached) and c.png (not), and
    ``extra_html`` after them."""

    def __init__(self, extra_html=""):
        super().__init__()
        self.pages["P"] = ["= T =\n"]
        self.extra_html = extra_html
        self.attachments[("wiki", "P", "a.png")] = PNG_A
        self.attachments[("wiki", "P", "b.png")] = PNG_B
        self.fail_on: str | None = None
        self.fetched: list[str] = []

    def get_wiki_page_html(self, name, version=None):
        return (
            '<h1 id="T">T</h1>\n<p>\n'
            + _image("P", "a.png")
            + "\n"
            + _image("P", "b.png")
            + "\n"
            + _image("P", "c.png")
            + "\n"
            + self.extra_html
            + "</p>\n"
        )

    def get_wiki_attachment(self, path):
        self.fetched.append(path)
        page, filename = path.rsplit("/", 1)
        if filename == self.fail_on:
            raise RuntimeError("the attachment store is down")
        if ("wiki", page, filename) not in self.attachments:
            raise xmlrpc.client.Fault(
                404, f"Attachment '{filename}' not found"
            )
        return xmlrpc.client.Binary(
            self.attachments[("wiki", page, filename)]
        )

    def get_ticket_attachment(self, ticket_id, filename):
        self.fetched.append(f"#{ticket_id}/{filename}")
        key = ("ticket", ticket_id, filename)
        if key not in self.attachments:
            raise xmlrpc.client.Fault(
                404, f"Attachment '{filename}' not found"
            )
        return xmlrpc.client.Binary(self.attachments[key])


def _links(text):
    import re

    return re.findall(r"\]\(([^\s)]+)", text)


def test_two_attachments_and_one_missing_markdown(tmp_path, capsys):
    out = tmp_path / "content" / "p.md"
    out.parent.mkdir()
    img = tmp_path / "static" / "images"
    trac = AttachedTrac()
    with _served(trac) as url:
        rc = _cli(url, "wiki-pull", "P", str(out), "--assets", str(img))
    assert rc == cli.EXIT_OK
    assert (img / "a.png").read_bytes() == PNG_A
    assert (img / "b.png").read_bytes() == PNG_B
    assert not (img / "c.png").exists()
    assert trac.fetched == ["P/a.png", "P/b.png", "P/c.png"]
    text = out.read_text()
    rel = os.path.relpath(img, out.parent)
    assert f'[![a.png]({rel}/a.png "a.png")]({rel}/a.png)' in text
    assert f'[![b.png]({rel}/b.png "b.png")]({rel}/b.png)' in text
    # the missing one is exactly as exported
    assert f"{BASE}/raw-attachment/wiki/P/c.png" in text
    assert f"{BASE}/attachment/wiki/P/c.png" in text
    err = capsys.readouterr().err
    assert (
        "trac-mcp: warning: attachment 'c.png' of wiki page 'P' was not "
        "found; its links are left as they are" in err
    )
    assert (
        f"Saved 2 attachments to {img} (0 unchanged, 1 missing)" in err
    )


def test_assets_url_links_the_served_prefix(tmp_path):
    out = tmp_path / "content" / "posts" / "p.md"
    out.parent.mkdir(parents=True)
    img = tmp_path / "static" / "images"
    with _served(AttachedTrac()) as url:
        rc = _cli(
            url,
            "wiki-pull",
            "P",
            str(out),
            "--assets",
            str(img),
            "--assets-url",
            "/images",
        )
    assert rc == cli.EXIT_OK
    text = out.read_text()
    assert '[![a.png](/images/a.png "a.png")](/images/a.png)' in text
    assert "static" not in text.split("---", 2)[2]
    assert (img / "a.png").read_bytes() == PNG_A


def test_a_second_run_leaves_unchanged_files_alone(tmp_path, capsys):
    out = tmp_path / "p.md"
    img = tmp_path / "img"
    with _served(AttachedTrac()) as url:
        assert (
            _cli(url, "wiki-pull", "P", str(out), "--assets", str(img))
            == cli.EXIT_OK
        )
        capsys.readouterr()
        (img / "b.png").write_bytes(b"locally changed")
        os.utime(img / "a.png", (1_000_000_000, 1_000_000_000))
        assert (
            _cli(url, "wiki-pull", "P", str(out), "--assets", str(img))
            == cli.EXIT_OK
        )
    assert (img / "a.png").stat().st_mtime == 1_000_000_000
    assert (img / "b.png").read_bytes() == PNG_B
    assert (
        f"Saved 2 attachments to {img} (1 unchanged, 1 missing)"
        in capsys.readouterr().err
    )


def test_format_html(tmp_path):
    out = tmp_path / "p.html"
    img = tmp_path / "img"
    with _served(AttachedTrac()) as url:
        rc = _cli(
            url,
            "wiki-pull",
            "P",
            str(out),
            "--format",
            "html",
            "--assets",
            str(img),
        )
    assert rc == cli.EXIT_OK
    text = out.read_text()
    assert (
        '<a href="img/a.png" style="padding:0; border:none">'
        '<img alt="a.png" src="img/a.png" title="a.png" /></a>'
    ) in text
    assert f'src="{BASE}/raw-attachment/wiki/P/c.png"' in text
    assert (img / "a.png").read_bytes() == PNG_A


def test_ticket_attachment_and_nested_page(tmp_path):
    trac = AttachedTrac(
        extra_html=(
            _image("42", "log.png", realm="ticket")
            + _image("A/B", "deep.png")
        )
    )
    trac.attachments[("ticket", 42, "log.png")] = b"ticket-bytes"
    trac.attachments[("wiki", "A/B", "deep.png")] = b"deep-bytes"
    out = tmp_path / "p.md"
    img = tmp_path / "img"
    with _served(trac) as url:
        rc = _cli(url, "wiki-pull", "P", str(out), "--assets", str(img))
    assert rc == cli.EXIT_OK
    assert (img / "log.png").read_bytes() == b"ticket-bytes"
    assert (img / "deep.png").read_bytes() == b"deep-bytes"
    assert (
        "#42/log.png" in trac.fetched and "A/B/deep.png" in trac.fetched
    )
    assert "![log.png](img/log.png" in out.read_text()


def test_a_name_collision_is_warned_about(tmp_path, capsys):
    trac = AttachedTrac(extra_html=_image("Q", "a.png"))
    trac.attachments[("wiki", "Q", "a.png")] = b"q-bytes"
    out = tmp_path / "p.md"
    img = tmp_path / "img"
    with _served(trac) as url:
        rc = _cli(url, "wiki-pull", "P", str(out), "--assets", str(img))
    assert rc == cli.EXIT_OK
    assert (img / "a.png").read_bytes() == PNG_A
    assert (img / "Q--a.png").read_bytes() == b"q-bytes"
    assert (
        "trac-mcp: warning: attachment 'a.png' of wiki page 'Q' is saved "
        "as 'Q--a.png'" in capsys.readouterr().err
    )


def test_a_server_error_on_the_second_fetch_writes_nothing(
    tmp_path, capsys
):
    trac = AttachedTrac()
    trac.fail_on = "b.png"
    out = tmp_path / "p.md"
    img = tmp_path / "img"
    with _served(trac) as url:
        rc = _cli(url, "wiki-pull", "P", str(out), "--assets", str(img))
    assert rc == cli.EXIT_TOOL_ERROR
    assert not out.exists()
    assert not img.exists()
    assert "b.png" in capsys.readouterr().err


def test_a_transport_failure_on_the_second_fetch_writes_nothing(
    tmp_path, monkeypatch, capsys
):
    import httpx

    real = cli.call_tool
    fetches = []

    async def flaky(url, token, name, arguments):
        if name == "wiki_attachment_get":
            fetches.append(arguments["filename"])
            if len(fetches) == 2:
                raise httpx.ConnectError("connection reset")
        return await real(url, token, name, arguments)

    monkeypatch.setattr(cli, "call_tool", flaky)
    out = tmp_path / "p.md"
    img = tmp_path / "img"
    with _served(AttachedTrac()) as url:
        rc = _cli(url, "wiki-pull", "P", str(out), "--assets", str(img))
    assert rc == cli.EXIT_CONNECT_ERROR
    assert fetches == ["a.png", "b.png"]
    assert not out.exists()
    assert not img.exists()
    assert "connection reset" in capsys.readouterr().err


def test_a_page_without_attachments_makes_no_directory(
    tmp_path, capsys
):
    class Plain(AttachedTrac):
        def get_wiki_page_html(self, name, version=None):
            return '<h1 id="T">T</h1>\n'

    out = tmp_path / "p.md"
    img = tmp_path / "img"
    with _served(Plain()) as url:
        rc = _cli(url, "wiki-pull", "P", str(out), "--assets", str(img))
    assert rc == cli.EXIT_OK
    assert out.exists() and not img.exists()
    assert (
        f"Saved 0 attachments to {img} (0 unchanged, 0 missing)"
        in capsys.readouterr().err
    )


# --- usage errors, before any network call ----------------------------------


def _main(*argv):
    return cli.main(
        ["--url", "http://x/mcp", *argv],
        cwd=Path("/"),
        environ={"TRAC_MCP_AUTH_TOKEN": "t"},
    )


def test_assets_with_tracwiki_is_a_usage_error(
    tmp_path, monkeypatch, capsys
):
    _no_network(monkeypatch)
    out = tmp_path / "p.wiki"
    rc = _main(
        "wiki-pull",
        "P",
        str(out),
        "--format",
        "tracwiki",
        "--assets",
        str(tmp_path / "img"),
    )
    assert rc == cli.EXIT_USAGE_ERROR
    err = capsys.readouterr().err
    assert "--assets" in err and "tracwiki" in err
    assert not out.exists() and not (tmp_path / "img").exists()


def test_assets_url_without_assets_is_a_usage_error(
    tmp_path, monkeypatch, capsys
):
    _no_network(monkeypatch)
    rc = _main(
        "wiki-pull", "P", str(tmp_path / "p.md"), "--assets-url", "/i"
    )
    assert rc == cli.EXIT_USAGE_ERROR
    assert "--assets-url needs --assets" in capsys.readouterr().err


def test_assets_dir_that_is_a_file_is_a_usage_error(
    tmp_path, monkeypatch, capsys
):
    _no_network(monkeypatch)
    f = tmp_path / "img"
    f.write_text("not a directory")
    rc = _main(
        "wiki-pull", "P", str(tmp_path / "p.md"), "--assets", str(f)
    )
    assert rc == cli.EXIT_USAGE_ERROR
    assert "not a directory" in capsys.readouterr().err


def test_assets_exists_only_on_wiki_pull():
    with pytest.raises(SystemExit) as exc:
        _main("attach-get", "--page", "P", "a", "-", "--assets", "d")
    assert exc.value.code == 2


# --- which instance's attachment URLs these are -----------------------------


def _fake_call_tool(content, structured, attachments=None):
    """A ``call_tool`` answering the pull with ``content`` and the
    attachment tools from ``attachments`` (filename -> bytes)."""

    async def call(url, token, name, arguments):
        if name == "wiki_file_pull":
            return SimpleNamespace(
                isError=False,
                content=[SimpleNamespace(text="Pulled")],
                structuredContent={
                    "content": content,
                    "version": 1,
                    "format": "markdown",
                    **structured,
                },
            )
        data = (attachments or {}).get(arguments["filename"])
        if data is None:
            return SimpleNamespace(
                isError=True,
                content=[
                    SimpleNamespace(text="Error (not_found): gone\n")
                ],
                structuredContent=None,
            )
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="Fetched")],
            structuredContent={
                "content_base64": base64.b64encode(data).decode()
            },
        )

    return call


_FRONT = (
    '---\ntitle: "T"\nsource: "http://h/bcs/wiki/P"\n'
    'trac_instance: "/bcs"\ntrac_page: "P"\ntrac_version: 1\n---\n\n'
)
_BODY = "![i](http://h/bcs/raw-attachment/wiki/P/a.png)\n"


def test_instance_from_the_front_matter_when_the_result_has_none(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        cli,
        "call_tool",
        _fake_call_tool(_FRONT + _BODY, {}, {"a.png": b"x"}),
    )
    out = tmp_path / "p.md"
    rc = _main(
        "wiki-pull", "P", str(out), "--assets", str(tmp_path / "img")
    )
    assert rc == cli.EXIT_OK
    assert "![i](img/a.png)" in out.read_text()


def test_instance_from_the_result_wins_over_the_front_matter(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        cli,
        "call_tool",
        _fake_call_tool(
            _FRONT + _BODY, {"instance": "/other"}, {"a.png": b"x"}
        ),
    )
    out = tmp_path / "p.md"
    rc = _main(
        "wiki-pull", "P", str(out), "--assets", str(tmp_path / "img")
    )
    assert rc == cli.EXIT_OK
    assert "http://h/bcs/raw-attachment/wiki/P/a.png" in out.read_text()
    assert not (tmp_path / "img").exists()


def test_no_instance_anywhere_is_a_tool_error_and_writes_nothing(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        cli, "call_tool", _fake_call_tool(_BODY, {}, {"a.png": b"x"})
    )
    out = tmp_path / "p.md"
    rc = _main(
        "wiki-pull", "P", str(out), "--assets", str(tmp_path / "img")
    )
    assert rc == cli.EXIT_TOOL_ERROR
    assert "instance" in capsys.readouterr().err
    assert not out.exists() and not (tmp_path / "img").exists()


# --- live -------------------------------------------------------------------


def _png(width=2, height=2, rgb=(200, 30, 30)) -> bytes:
    """A real, tiny PNG, generated here."""
    import struct
    import zlib

    def chunk(kind, data):
        body = kind + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.mark.live
def test_live_wiki_pull_assets_saves_the_attachment_and_relinks(
    tmp_path,
):
    """Trac /trac_mcp_server #215 against real /trac_test: a scratch page
    with a small PNG attached and ``[[Image(...)]]`` is pulled with
    ``--assets`` (relative links) and with ``--assets-url /images``. The
    saved file is the attachment's bytes, and both the image and the link
    wrapping it point at the local file."""
    from tests.live_helpers import trac_test_registry
    from trac_mcp_server.config import Config

    default_config = Config(
        trac_url=os.environ["TRAC_URL"],
        username=os.environ["TRAC_USERNAME"],
        password=os.environ["TRAC_PASSWORD"],
        insecure=os.environ.get("TRAC_INSECURE", "").lower()
        in ("1", "true", "yes"),
    )
    instance = ["--instance", "/trac_test"]
    page = f"Sandbox/Ticket215Assets{uuid.uuid4().hex[:8]}"
    filename = f"dot{uuid.uuid4().hex[:6]}.png"
    png = _png()
    src = tmp_path / "page.wiki"
    src.write_bytes(
        f"= Ticket 215 live scratch =\n\n[[Image({filename})]]\n".encode()
    )
    attachment = tmp_path / filename
    attachment.write_bytes(png)

    with _served_instances(trac_test_registry(default_config)) as url:
        try:
            assert (
                _cli(url, *instance, "wiki-push", page, str(src))
                == cli.EXIT_OK
            )
            assert (
                _cli(
                    url,
                    *instance,
                    "attach-put",
                    "--page",
                    page,
                    str(attachment),
                )
                == cli.EXIT_OK
            )

            posts = tmp_path / "content" / "posts"
            posts.mkdir(parents=True)
            out = posts / "page.md"
            img = tmp_path / "static" / "images"
            assert (
                _cli(
                    url,
                    *instance,
                    "wiki-pull",
                    page,
                    str(out),
                    "--assets",
                    str(img),
                )
                == cli.EXIT_OK
            )
            assert (img / filename).read_bytes() == png
            rel = os.path.relpath(img / filename, posts)
            links = _links(out.read_text())
            assert links.count(rel) == 2, (links, rel)
            assert not any("attachment" in link for link in links)

            out2 = posts / "page2.md"
            img2 = tmp_path / "static" / "images2"
            assert (
                _cli(
                    url,
                    *instance,
                    "wiki-pull",
                    page,
                    str(out2),
                    "--assets",
                    str(img2),
                    "--assets-url",
                    "/images",
                )
                == cli.EXIT_OK
            )
            assert (img2 / filename).read_bytes() == png
            links = _links(out2.read_text())
            assert links.count(f"/images/{filename}") == 2, links
        finally:
            asyncio.run(
                cli.call_tool(
                    url,
                    _TOKEN,
                    "wiki_delete",
                    {"page_name": page, "instance": "/trac_test"},
                )
            )
