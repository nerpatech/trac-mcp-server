"""``trac-mcp wiki-pull --link-map`` and the local ``export.link_rewrites``
(Trac /trac_mcp_server #213).

Two layers: what the CLI puts in the ``wiki_file_pull`` call (a faked
``call_tool``, so the arguments can be read), and one pass through the
real server over real uvicorn (the ``TestRealSubstrate`` setup), so the
``link_rewrites`` argument is proven to be one the tool accepts.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_cli_client import FakeTrac, _cli, _served
from trac_mcp_server.cli import client as cli

_PREFIX = "http://fake/trac_test/wiki/"
_RULE = {"from": _PREFIX, "to": "https://blog.example/"}
_YAML = (
    "export:\n  link_rewrites:\n"
    f"    - from: {_PREFIX}\n      to: https://blog.example/\n"
)


def _write_config(root: Path, body: str) -> None:
    d = root / ".trac_mcp"
    d.mkdir(exist_ok=True)
    (d / "config.yaml").write_text(body)


@pytest.fixture
def reply():
    """Override the faked server's answer: set ``reply["result"]``."""
    return {}


@pytest.fixture
def sent(monkeypatch, reply):
    """Replace ``call_tool``; the list gets each (tool, arguments)."""
    calls: list[tuple[str, dict]] = []

    async def _call(url, token, name, arguments):
        calls.append((name, dict(arguments)))
        if "result" in reply:
            return reply["result"]
        structured = {
            "content": "# P\n",
            "version": 1,
            "format": "markdown",
        }
        if "link_rewrites" in arguments:
            structured["link_rewrites_source"] = "call"
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="Pulled ...")],
            structuredContent=structured,
        )

    monkeypatch.setattr(cli, "call_tool", _call)
    return calls


def _pull(tmp_path, *flags):
    return cli.main(
        [
            "--url",
            "http://x/mcp",
            "wiki-pull",
            "P",
            str(tmp_path / "p.md"),
        ]
        + list(flags),
        cwd=tmp_path,
        environ={"TRAC_MCP_AUTH_TOKEN": "t"},
    )


def _args(sent):
    assert len(sent) == 1
    name, arguments = sent[0]
    assert name == "wiki_file_pull"
    return arguments


def test_link_map_flag_is_sent_as_link_rewrites(
    isolated_config, sent, tmp_path
):
    rc = _pull(
        tmp_path,
        "--link-map",
        f"{_PREFIX}=https://blog.example/",
        "--link-map",
        "/a/=/b/",
    )
    assert rc == cli.EXIT_OK
    assert _args(sent)["link_rewrites"] == [
        _RULE,
        {"from": "/a/", "to": "/b/"},
    ]


def test_link_map_splits_at_the_first_equals(
    isolated_config, sent, tmp_path
):
    assert _pull(tmp_path, "--link-map", "/p/=/x?y=1") == cli.EXIT_OK
    assert _args(sent)["link_rewrites"] == [
        {"from": "/p/", "to": "/x?y=1"}
    ]


def test_local_yaml_map_is_sent_when_no_flag_is_given(
    isolated_config, sent, tmp_path
):
    _write_config(isolated_config, _YAML)
    assert _pull(tmp_path) == cli.EXIT_OK
    assert _args(sent)["link_rewrites"] == [_RULE]


def test_the_flag_replaces_the_local_yaml_map(
    isolated_config, sent, tmp_path
):
    _write_config(isolated_config, _YAML)
    assert _pull(tmp_path, "--link-map", "/a/=/b/") == cli.EXIT_OK
    assert _args(sent)["link_rewrites"] == [
        {"from": "/a/", "to": "/b/"}
    ]


def test_nothing_is_sent_when_the_local_config_has_no_such_key(
    isolated_config, sent, tmp_path
):
    _write_config(
        isolated_config, "trac:\n  max_parallel_requests: 5\n"
    )
    assert _pull(tmp_path) == cli.EXIT_OK
    assert "link_rewrites" not in _args(sent)


def test_nothing_is_sent_when_there_is_no_local_config(
    isolated_config, sent, tmp_path
):
    assert _pull(tmp_path) == cli.EXIT_OK
    assert "link_rewrites" not in _args(sent)


def test_an_empty_local_list_is_sent_as_no_rewriting(
    isolated_config, sent, tmp_path
):
    _write_config(isolated_config, "export:\n  link_rewrites: []\n")
    assert _pull(tmp_path) == cli.EXIT_OK
    assert _args(sent)["link_rewrites"] == []


def test_the_environment_is_not_read(
    isolated_config, sent, tmp_path, monkeypatch
):
    monkeypatch.setenv("TRAC_MCP_LINK_REWRITES", "/a/=/b/")
    assert _pull(tmp_path) == cli.EXIT_OK
    assert "link_rewrites" not in _args(sent)


def test_tracwiki_sends_none_and_ignores_a_broken_local_config(
    isolated_config, sent, tmp_path
):
    _write_config(isolated_config, "export: [oops]\n")
    rc = _pull(tmp_path, "--format", "tracwiki")
    assert rc == cli.EXIT_OK
    assert "link_rewrites" not in _args(sent)


def test_tracwiki_with_the_flag_warns_and_sends_none(
    isolated_config, sent, tmp_path, capsys
):
    rc = _pull(
        tmp_path, "--format", "tracwiki", "--link-map", "/a/=/b/"
    )
    assert rc == cli.EXIT_OK
    assert "link_rewrites" not in _args(sent)
    err = capsys.readouterr().err
    assert "--link-map has no effect" in err


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("export: [oops]\n", "must be a mapping"),
        (
            "export:\n  link_rewrites:\n    - {from: '', to: x}\n",
            "must not be empty",
        ),
        (
            "export:\n  link_rewrites:\n    - {from: a, to: b}\n"
            "    - {from: a, to: c}\n",
            "repeats 'from'",
        ),
        ("export: {link_rewrites: [\n", "cannot read the config file"),
    ],
)
def test_a_broken_local_config_is_a_clear_usage_error(
    isolated_config, sent, tmp_path, capsys, body, message
):
    _write_config(isolated_config, body)
    rc = _pull(tmp_path)
    assert rc == cli.EXIT_USAGE_ERROR
    err = capsys.readouterr().err
    assert err.startswith("trac-mcp: wiki-pull: ")
    assert err.count("\n") == 1  # one line, not pydantic's report
    assert message in err
    assert ".trac_mcp/config.yaml" in err
    assert "Traceback" not in err
    assert sent == []
    assert not (tmp_path / "p.md").exists()


def test_the_flag_overrides_a_broken_local_config(
    isolated_config, sent, tmp_path
):
    _write_config(isolated_config, "export: [oops]\n")
    assert _pull(tmp_path, "--link-map", "/a/=/b/") == cli.EXIT_OK
    assert _args(sent)["link_rewrites"] == [
        {"from": "/a/", "to": "/b/"}
    ]


def test_a_link_map_without_an_equals_is_a_usage_error(
    isolated_config, sent, tmp_path, capsys
):
    with pytest.raises(SystemExit) as exc:
        _pull(tmp_path, "--link-map", _PREFIX)
    assert exc.value.code == 2
    assert "not FROM=TO" in capsys.readouterr().err
    assert sent == []


def test_a_bad_local_rule_error_is_exactly_one_line(
    isolated_config, sent, tmp_path, capsys
):
    _write_config(
        isolated_config,
        "export:\n  link_rewrites:\n    - {from: a, to: b}\n"
        "    - {from: '', to: c}\n",
    )
    assert _pull(tmp_path) == cli.EXIT_USAGE_ERROR
    assert capsys.readouterr().err == (
        "trac-mcp: wiki-pull: invalid config file: "
        "export.link_rewrites[1] 'from' must not be empty (the "
        "export.link_rewrites key of .trac_mcp/config.yaml; pass "
        "--link-map to override it)\n"
    )


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--link-map", "=https://b/"], "must not be empty"),
        (
            ["--link-map", "/a/=/b/", "--link-map", "/a/=/c/"],
            "repeats 'from'",
        ),
    ],
)
def test_an_empty_or_repeated_from_is_a_usage_error(
    isolated_config, sent, tmp_path, capsys, flags, message
):
    rc = _pull(tmp_path, *flags)
    assert rc == cli.EXIT_USAGE_ERROR
    assert message in capsys.readouterr().err
    assert sent == []


# --- through the real server ---------------------------------------------


class _Linked(FakeTrac):
    def get_wiki_page_html(self, name, version=None):
        return (
            f'<p><a href="{_PREFIX}Other">Other</a> '
            '<a href="#Heading">Heading</a></p>\n'
        )


def _served_pull(tmp_path, *flags, configured=()):
    trac = _Linked()
    trac.config.link_rewrites = configured
    trac.pages["P"] = ["[wiki:Other Other]\n"]
    out = tmp_path / "out.md"
    with _served(trac) as url:
        rc = _cli(url, "wiki-pull", "P", str(out), *flags)
    assert rc == cli.EXIT_OK
    return out.read_text()


def test_through_the_server_the_flag_rewrites_the_page_link_only(
    isolated_config, tmp_path
):
    text = _served_pull(
        tmp_path, "--link-map", f"{_PREFIX}=https://blog.example/"
    )
    assert "[Other](https://blog.example/Other)" in text
    assert "[Heading](#Heading)" in text


def test_through_the_server_the_local_yaml_map_is_used(
    isolated_config, tmp_path
):
    _write_config(isolated_config, _YAML)
    text = _served_pull(tmp_path)
    assert "[Other](https://blog.example/Other)" in text


def test_through_the_server_with_no_local_map_the_daemons_applies(
    isolated_config, tmp_path
):
    text = _served_pull(
        tmp_path, configured=((_PREFIX, "https://daemon.example/"),)
    )
    assert "[Other](https://daemon.example/Other)" in text


def test_through_the_server_a_local_map_beats_the_daemons(
    isolated_config, tmp_path
):
    _write_config(isolated_config, _YAML)
    text = _served_pull(
        tmp_path, configured=((_PREFIX, "https://daemon.example/"),)
    )
    assert "[Other](https://blog.example/Other)" in text
    assert "daemon.example" not in text


# --- an old daemon, and a server that does not confirm ---------------------


def _error(text):
    return SimpleNamespace(
        isError=True,
        content=[SimpleNamespace(text=text)],
        structuredContent=None,
    )


def test_an_old_daemon_refusing_link_rewrites_is_named(
    isolated_config, sent, reply, tmp_path, capsys
):
    reply["result"] = _error(
        "Input validation error: Additional properties are not allowed "
        "('link_rewrites' was unexpected)"
    )
    rc = _pull(tmp_path, "--link-map", "/a/=/b/")
    assert rc == cli.EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert "predates link rewriting (Trac /trac_mcp_server #213)" in err
    assert "remove export.link_rewrites from the local config" in err
    assert "drop --link-map" in err
    assert not (tmp_path / "p.md").exists()


def test_an_old_daemon_is_named_for_the_local_configs_map_too(
    isolated_config, sent, reply, tmp_path, capsys
):
    _write_config(isolated_config, _YAML)
    reply["result"] = _error(
        "Input validation error: Additional properties are not allowed "
        "('link_rewrites' was unexpected)"
    )
    assert _pull(tmp_path) == cli.EXIT_TOOL_ERROR
    assert "predates link rewriting" in capsys.readouterr().err


def test_a_current_daemons_error_about_a_rule_is_reported_as_it_is(
    isolated_config, sent, reply, tmp_path, capsys
):
    reply["result"] = _error(
        "Error (validation_error): link_rewrites[1] repeats 'from'"
    )
    assert (
        _pull(tmp_path, "--link-map", "/a/=/b/") == cli.EXIT_TOOL_ERROR
    )
    err = capsys.readouterr().err
    assert "repeats 'from'" in err
    assert "predates" not in err


def test_an_unexpected_property_error_without_a_sent_map_is_plain(
    isolated_config, sent, reply, tmp_path, capsys
):
    reply["result"] = _error(
        "Input validation error: Additional properties are not allowed "
        "('link_rewrites' was unexpected)"
    )
    assert _pull(tmp_path) == cli.EXIT_TOOL_ERROR  # nothing was sent
    assert "predates" not in capsys.readouterr().err


def test_a_reply_that_does_not_confirm_the_map_warns(
    isolated_config, sent, reply, tmp_path, capsys
):
    reply["result"] = SimpleNamespace(
        isError=False,
        content=[SimpleNamespace(text="Pulled ...")],
        structuredContent={"content": "# P\n", "version": 1},
    )
    rc = _pull(tmp_path, "--link-map", "/a/=/b/")
    assert rc == cli.EXIT_OK
    assert (tmp_path / "p.md").read_text() == "# P\n"
    assert (
        "trac-mcp: warning: the server did not confirm the link map "
        "was applied"
    ) in capsys.readouterr().err


def test_a_confirming_reply_and_an_unsent_map_print_no_such_warning(
    isolated_config, sent, tmp_path, capsys
):
    assert _pull(tmp_path, "--link-map", "/a/=/b/") == cli.EXIT_OK
    assert "did not confirm" not in capsys.readouterr().err
    assert (
        _pull(tmp_path) == cli.EXIT_OK
    )  # nothing sent, nothing to confirm
    assert "did not confirm" not in capsys.readouterr().err
