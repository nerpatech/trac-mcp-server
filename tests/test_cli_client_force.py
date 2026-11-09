"""``trac-mcp wiki-push --force`` and the exported-Markdown guard
(Trac /trac_mcp_server #212).

Two layers, as in ``test_cli_client_link_map``: what the CLI puts in the
``wiki_file_push`` call (a faked ``call_tool``), and one pass through the
real server over real uvicorn, so the refusal and ``force`` are proven
to be the tool's own.
"""

from types import SimpleNamespace

import pytest

from tests.test_cli_client import FakeTrac, _cli, _served
from trac_mcp_server.cli import client as cli

_EXPORT = (
    "---\n"
    'title: "P"\n'
    'source: "http://fake/trac_test/wiki/P"\n'
    'trac_instance: "/trac_test"\n'
    'trac_page: "P"\n'
    "trac_version: 3\n"
    'exported: "2026-11-09T12:00:00Z"\n'
    "---\n\n# P\n\nBody text.\n"
)


@pytest.fixture
def reply():
    return {}


@pytest.fixture
def sent(monkeypatch, reply):
    calls: list[tuple[str, dict]] = []

    async def _call(url, token, name, arguments):
        calls.append((name, dict(arguments)))
        if "result" in reply:
            return reply["result"]
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="Updated wiki page 'P'")],
            structuredContent={"version": 4},
        )

    monkeypatch.setattr(cli, "call_tool", _call)
    return calls


def _error(text):
    return SimpleNamespace(
        isError=True,
        content=[SimpleNamespace(text=text)],
        structuredContent=None,
    )


def _push(tmp_path, *flags):
    src = tmp_path / "p.md"
    src.write_text("# P\n\nBody.\n", encoding="utf-8")
    return cli.main(
        ["--url", "http://x/mcp", "wiki-push", "P", str(src), *flags],
        cwd=tmp_path,
        environ={"TRAC_MCP_AUTH_TOKEN": "t"},
    )


def test_force_flag_sends_force_true(isolated_config, sent, tmp_path):
    assert _push(tmp_path, "--force") == cli.EXIT_OK
    assert sent[0][0] == "wiki_file_push"
    assert sent[0][1]["force"] is True


def test_without_the_flag_no_force_key_is_sent(
    isolated_config, sent, tmp_path
):
    assert _push(tmp_path) == cli.EXIT_OK
    assert "force" not in sent[0][1]


def test_an_old_daemon_refusing_force_is_named(
    isolated_config, sent, reply, tmp_path, capsys
):
    reply["result"] = _error(
        "Input validation error: Additional properties are not allowed "
        "('force' was unexpected)"
    )
    rc = _push(tmp_path, "--force")
    assert rc == cli.EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert "the server does not know --force" in err
    assert "predates the exported-Markdown guard" in err
    assert "#212" in err
    assert "push without --force" in err


def test_an_error_mentioning_enforced_is_reported_as_it_is(
    isolated_config, sent, reply, tmp_path, capsys
):
    reply["result"] = _error(
        "Error (validation_error): a policy is enforced here; "
        "this was unexpected"
    )
    assert _push(tmp_path, "--force") == cli.EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert "predates" not in err
    assert "enforced here" in err


def test_a_forced_push_the_server_refuses_otherwise_is_plain(
    isolated_config, sent, reply, tmp_path, capsys
):
    reply["result"] = _error("Error (version_conflict): stale")
    assert _push(tmp_path, "--force") == cli.EXIT_TOOL_ERROR
    assert "predates" not in capsys.readouterr().err


def test_a_force_error_without_the_flag_is_plain(
    isolated_config, sent, reply, tmp_path, capsys
):
    reply["result"] = _error(
        "Input validation error: Additional properties are not allowed "
        "('force' was unexpected)"
    )
    assert _push(tmp_path) == cli.EXIT_TOOL_ERROR  # nothing was sent
    assert "predates" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Additional properties are not allowed "
            "('force' was unexpected)",
            True,
        ),
        ("'force' is an unexpected key", True),
        ("this was enforced, unexpected", False),
        ("force was unexpected", False),
        (
            "Additional properties are not allowed ('x' was unexpected)",
            False,
        ),
        ("'force' is required", False),
    ],
)
def test_refuses_force(text, expected):
    assert cli._refuses_force(text) is expected


class TestRealServer:
    def _file(self, tmp_path, text=_EXPORT):
        src = tmp_path / "pulled.md"
        src.write_text(text, encoding="utf-8")
        return str(src)

    def test_an_exported_file_is_refused_and_nothing_is_stored(
        self, tmp_path, capsys
    ):
        trac = FakeTrac()
        with _served(trac) as url:
            rc = _cli(url, "wiki-push", "P", self._file(tmp_path))

        assert rc == cli.EXIT_TOOL_ERROR
        assert trac.pages == {}
        err = capsys.readouterr().err
        assert "Refusing to push" in err
        assert "'P'" in err
        assert "instance /trac_test" in err
        assert "version 3" in err
        assert "--force" in err

    def test_force_stores_it_without_the_front_matter(self, tmp_path):
        trac = FakeTrac()
        with _served(trac) as url:
            rc = _cli(
                url, "wiki-push", "P", self._file(tmp_path), "--force"
            )

        assert rc == cli.EXIT_OK
        (stored,) = trac.pages["P"]
        assert "trac_page" not in stored
        assert "Body text." in stored

    def test_plain_markdown_is_not_refused(self, tmp_path):
        trac = FakeTrac()
        with _served(trac) as url:
            rc = _cli(
                url,
                "wiki-push",
                "P",
                self._file(tmp_path, "# P\n\nBody.\n"),
            )

        assert rc == cli.EXIT_OK
        assert len(trac.pages["P"]) == 1
