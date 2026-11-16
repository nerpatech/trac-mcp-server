"""``--flavor`` of ``trac-convert`` and ``trac-mcp wiki-push``
(Trac /trac_mcp_server #216).

trac-convert is offline Markdown -> TracWiki here, so it needs no Trac.
wiki-push is read in two layers, as in ``test_cli_client_force``: what it
puts in the ``wiki_file_push`` call (a faked ``call_tool``), and one pass
through the real server over real uvicorn, whose strict schema is the
thing that refuses an unknown argument.
"""

import io
from types import SimpleNamespace

import pytest

from tests.test_cli_client import FakeTrac, _cli, _served
from trac_mcp_server.cli import client as cli
from trac_mcp_server.cli.convert import (
    EXIT_OK,
    build_parser,
    convert_text,
    main,
)

MATH = "Energy \\\\(E\\_1\\\\) here\n"
MATH_CHATGPT = "Energy `E_1` here"
MATH_DEFAULT = "Energy \\(E_1\\) here"
GEMINI = "Run it:\nbash\nls -la\nUse code with caution.\nDone\n"
GEMINI_CONVERTED = "Run it:\n\n{{{#!sh\nls -la\n}}}\n\nDone"
REBUILT = "gemini: rebuilt 1 code block(s) from 'Use code with caution.' markers"


# -- trac-convert -------------------------------------------------------------


def _convert(tmp_path, text, *flags):
    path = tmp_path / "in.md"
    path.write_text(text, encoding="utf-8")
    return main([str(path), *flags])


def test_the_parser_defaults_to_commonmark():
    args = build_parser().parse_args(["--to", "tracwiki"])
    assert args.flavor == "commonmark"


def test_flavor_converts(tmp_path, capsys):
    rc = _convert(
        tmp_path,
        MATH,
        "--from",
        "md",
        "--to",
        "tracwiki",
        "--flavor",
        "chatgpt",
    )
    assert rc == EXIT_OK
    assert capsys.readouterr().out == MATH_CHATGPT


def test_the_default_flavor_is_as_before(tmp_path, capsys):
    rc = _convert(tmp_path, MATH, "--from", "md", "--to", "tracwiki")
    assert rc == EXIT_OK
    assert capsys.readouterr().out == MATH_DEFAULT
    rc = _convert(
        tmp_path,
        MATH,
        "--from",
        "md",
        "--to",
        "tracwiki",
        "--flavor",
        "commonmark",
    )
    assert capsys.readouterr().out == MATH_DEFAULT


def test_flavor_with_an_undecided_source_reads_markdown(
    tmp_path, capsys
):
    """A flavour implies Markdown, so --from auto does not guess."""
    rc = _convert(
        tmp_path, MATH, "--to", "tracwiki", "--flavor", "chatgpt"
    )
    assert rc == EXIT_OK
    assert capsys.readouterr().out == MATH_CHATGPT


def test_flavor_warnings_go_to_stderr(tmp_path, capsys):
    rc = _convert(
        tmp_path,
        GEMINI,
        "--from",
        "md",
        "--to",
        "tracwiki",
        "--flavor",
        "gemini",
    )
    assert rc == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == GEMINI_CONVERTED
    assert f"warning: {REBUILT}\n" in captured.err


def test_the_default_hints_at_gemini_on_stderr(tmp_path, capsys):
    rc = _convert(tmp_path, GEMINI, "--from", "md", "--to", "tracwiki")
    assert rc == EXIT_OK
    err = capsys.readouterr().err
    assert (
        'warning: Line "Use code with caution." looks like text' in err
    )
    assert "--flavor gemini" in err


@pytest.mark.parametrize(
    "flags",
    [
        ["--from", "tracwiki", "--to", "md"],
        ["--from", "md", "--to", "md"],
        ["--from", "tracwiki", "--to", "tracwiki"],
        ["--to", "md"],
        ["--from-wiki", "P", "--to", "tracwiki"],
        ["--from-wiki", "P", "--to", "md"],
    ],
    ids=[
        "tracwiki to md",
        "md to md",
        "tracwiki to tracwiki",
        "auto to md",
        "from-wiki to tracwiki",
        "from-wiki to md",
    ],
)
def test_a_flavor_in_any_other_direction_is_a_usage_error(
    tmp_path, capsys, flags
):
    with pytest.raises(SystemExit) as exit_info:
        _convert(tmp_path, MATH, *flags, "--flavor", "chatgpt")
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "--flavor" in err and "Markdown" in err


def test_the_default_flavor_is_fine_in_any_direction(
    tmp_path, capsys, isolated_config, fake_trac
):
    fake_trac.wiki_to_html.return_value = "<p>x</p>\n"
    rc = _convert(
        tmp_path,
        "= T =\n",
        "--from",
        "tracwiki",
        "--to",
        "md",
        "--flavor",
        "commonmark",
    )
    assert rc == EXIT_OK


def test_an_unknown_flavor_is_a_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        _convert(tmp_path, MATH, "--to", "tracwiki", "--flavor", "bard")
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "bard" in err and "gemini" in err


def test_flavor_reads_stdin_too(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(MATH))
    rc = main(
        ["--from", "md", "--to", "tracwiki", "--flavor", "chatgpt"]
    )
    assert rc == EXIT_OK
    assert capsys.readouterr().out == MATH_CHATGPT


def test_convert_text_takes_the_flavor():
    result = convert_text(MATH, "md", "tracwiki", flavor="chatgpt")
    assert result.text == "Energy `E_1` here"
    assert convert_text(MATH, "md", "tracwiki").text == (
        "Energy \\(E_1\\) here"
    )


def test_convert_text_checks_the_normalised_source_for_indent_loss():
    """The #68 check compares the Markdown's fenced code with the
    output; a rebuilt fence is Markdown the check must see."""
    result = convert_text(
        "bash\nif x; then\n    echo y\nfi\nUse code with caution.\n",
        "md",
        "tracwiki",
        flavor="gemini",
    )
    assert "    echo y" in result.text
    assert not [w for w in result.warnings if "indentation" in w]


# -- trac-mcp wiki-push -------------------------------------------------------


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


def _push(tmp_path, *flags, name="p.txt", text=MATH):
    src = tmp_path / name
    src.write_text(text, encoding="utf-8")
    return cli.main(
        ["--url", "http://x/mcp", "wiki-push", "P", str(src), *flags],
        cwd=tmp_path,
        environ={"TRAC_MCP_AUTH_TOKEN": "t"},
    )


def test_a_flavor_is_sent_and_implies_markdown(
    isolated_config, sent, tmp_path
):
    assert _push(tmp_path, "--flavor", "chatgpt") == cli.EXIT_OK
    assert sent[0][0] == "wiki_file_push"
    assert sent[0][1]["flavor"] == "chatgpt"
    assert sent[0][1]["format"] == "markdown"


def test_a_flavor_with_an_explicit_markdown_format(
    isolated_config, sent, tmp_path
):
    rc = _push(tmp_path, "--flavor", "gemini", "--format", "markdown")
    assert rc == cli.EXIT_OK
    assert sent[0][1]["flavor"] == "gemini"
    assert sent[0][1]["format"] == "markdown"


@pytest.mark.parametrize("flags", [[], ["--flavor", "commonmark"]])
def test_the_default_flavor_is_not_sent(
    isolated_config, sent, tmp_path, flags
):
    assert _push(tmp_path, *flags) == cli.EXIT_OK
    assert "flavor" not in sent[0][1]
    assert sent[0][1]["format"] == "auto"


def test_the_default_flavor_keeps_a_tracwiki_format(
    isolated_config, sent, tmp_path
):
    rc = _push(
        tmp_path, "--flavor", "commonmark", "--format", "tracwiki"
    )
    assert rc == cli.EXIT_OK
    assert sent[0][1]["format"] == "tracwiki"


def test_a_flavor_with_format_tracwiki_is_a_usage_error(
    isolated_config, sent, tmp_path, capsys
):
    rc = _push(tmp_path, "--flavor", "chatgpt", "--format", "tracwiki")
    assert rc == cli.EXIT_USAGE_ERROR == 2
    assert sent == []
    err = capsys.readouterr().err
    assert "--flavor" in err and "tracwiki" in err


def test_an_unknown_flavor_is_an_argparse_error(
    isolated_config, sent, tmp_path, capsys
):
    with pytest.raises(SystemExit) as exit_info:
        _push(tmp_path, "--flavor", "bard")
    assert exit_info.value.code == 2
    assert sent == []


def test_an_old_daemon_refusing_flavor_is_named(
    isolated_config, sent, reply, tmp_path, capsys
):
    reply["result"] = _error(
        "Input validation error: Additional properties are not allowed "
        "('flavor' was unexpected)"
    )
    rc = _push(tmp_path, "--flavor", "chatgpt")
    assert rc == cli.EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert "the server does not know --flavor" in err
    assert "predates Markdown flavour profiles" in err
    assert "#216" in err
    assert "nothing was written" in err


def test_an_error_that_merely_mentions_flavor_is_reported_as_it_is(
    isolated_config, sent, reply, tmp_path, capsys
):
    reply["result"] = _error(
        "Error (validation_error): flavor 'x' unknown"
    )
    assert _push(tmp_path, "--flavor", "chatgpt") == cli.EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert "predates" not in err
    assert "unknown" in err


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Additional properties are not allowed "
            "('flavor' was unexpected)",
            True,
        ),
        ("'flavor' is an unexpected key", True),
        ("this flavor was unexpected", False),
        ("Error (validation_error): flavor 'x' unknown", False),
    ],
)
def test_refuses_flavor(text, expected):
    assert cli._refuses_flavor(text) is expected


def test_wiki_push_flavor_through_the_real_server(tmp_path, capsys):
    src = tmp_path / "copy.txt"
    src.write_text(GEMINI, encoding="utf-8")
    trac = FakeTrac()
    with _served(trac) as url:
        rc = _cli(url, "wiki-push", "P", str(src), "--flavor", "gemini")
        assert rc == cli.EXIT_OK, capsys.readouterr().err
        assert trac.pages["P"] == [GEMINI_CONVERTED]


# -- review of #216 -----------------------------------------------------------


def test_a_flavor_with_check_trac_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--check-trac", "--flavor", "chatgpt"])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "--flavor" in err and "--check-trac" in err


def test_convert_text_normalises_once(monkeypatch):
    from trac_mcp_server.converters import flavors

    calls: list[str] = []

    def counted(normalise):
        def run(text):
            calls.append(normalise.__name__)
            return normalise(text)

        return run

    monkeypatch.setitem(
        flavors._NORMALISERS,
        "gemini",
        tuple(counted(n) for n in flavors._NORMALISERS["gemini"]),
    )
    convert_text(GEMINI, "md", "tracwiki", flavor="gemini")
    assert calls.count("_gemini_code_blocks") == 1
