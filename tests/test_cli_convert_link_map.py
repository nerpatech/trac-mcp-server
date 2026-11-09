"""``trac-convert --link-map`` and the configured link-rewrite map
(Trac /trac_mcp_server #213), through the ``fake_trac`` fixture.

The map applies to TracWiki -> Markdown, for ``--from-wiki`` and for
file / stdin input; any other direction says it has no effect.
"""

import io

import pytest

from trac_mcp_server.cli.convert import (
    EXIT_OK,
    EXIT_USAGE_ERROR,
    main,
)

_WIKI = "https://trac.example.com/wiki/"
_HTML = (
    f'<p><a href="{_WIKI}Other">Other</a> '
    '<a href="#Heading">Heading</a> '
    '<img alt="i" src="https://trac.example.com/raw/i.png"/></p>\n'
)
_RULE = f"{_WIKI}=https://blog.example/"
_REWRITTEN = "[Other](https://blog.example/Other)"
_UNTOUCHED = f"[Other]({_WIKI}Other)"


def _run_file(tmp_path, *flags):
    path = tmp_path / "in.wiki"
    path.write_text("[wiki:Other Other]\n", encoding="utf-8")
    return main([str(path), "--from", "tracwiki", "--to", "md", *flags])


@pytest.fixture
def trac(fake_trac, isolated_config):
    """Trac renders ``_HTML`` for any text and for page ``P``."""
    fake_trac.wiki_to_html.return_value = _HTML
    fake_trac.get_wiki_page.return_value = "[wiki:Other Other]\n"
    fake_trac.get_wiki_page_info.return_value = {"version": 3}
    fake_trac.get_wiki_page_html.return_value = _HTML
    return fake_trac


def test_link_map_rewrites_file_input(trac, tmp_path, capsys):
    assert _run_file(tmp_path, "--link-map", _RULE) == EXIT_OK
    out = capsys.readouterr().out
    assert _REWRITTEN in out
    assert "[Heading](#Heading)" in out
    # an image is never rewritten (#215)
    assert "![i](https://trac.example.com/raw/i.png)" in out


def test_link_map_rewrites_stdin_input(trac, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("= T =\n"))
    rc = main(["--from", "tracwiki", "--to", "md", "--link-map", _RULE])
    assert rc == EXIT_OK
    assert _REWRITTEN in capsys.readouterr().out


def test_link_map_rewrites_from_wiki(trac, capsys):
    rc = main(["--from-wiki", "P", "--to", "md", "--link-map", _RULE])
    assert rc == EXIT_OK
    trac.get_wiki_page_html.assert_called_once_with("P", 3)
    assert _REWRITTEN in capsys.readouterr().out


def test_link_map_is_repeatable_and_longest_prefix_wins(
    trac, tmp_path, capsys
):
    rc = _run_file(
        tmp_path,
        "--link-map",
        "https://trac.example.com/=https://short.example/",
        "--link-map",
        _RULE,
    )
    assert rc == EXIT_OK
    assert _REWRITTEN in capsys.readouterr().out


def test_link_map_splits_at_the_first_equals(trac, tmp_path, capsys):
    path = tmp_path / "in.wiki"
    path.write_text("x\n", encoding="utf-8")
    trac.wiki_to_html.return_value = '<p><a href="/p/z">q</a></p>'
    rc = main(
        [str(path), "--from", "tracwiki", "--to", "md"]
        + ["--link-map", "/p/=/x?y=1&"]
    )
    assert rc == EXIT_OK
    assert "[q](/x?y=1&z)" in capsys.readouterr().out


def test_no_map_leaves_links_alone_and_says_nothing(
    trac, tmp_path, capsys
):
    assert _run_file(tmp_path) == EXIT_OK
    captured = capsys.readouterr()
    assert _UNTOUCHED in captured.out
    assert captured.err == ""


# --- the configured map -------------------------------------------------


def _write_config(root, body):
    d = root / ".trac_mcp"
    d.mkdir(exist_ok=True)
    (d / "config.yaml").write_text(body)


_CONFIGURED = (
    "export:\n  link_rewrites:\n"
    f"    - from: {_WIKI}\n      to: https://cfg.example/\n"
)


def test_the_configured_yaml_map_applies_without_the_flag(
    trac, isolated_config, tmp_path, capsys
):
    _write_config(isolated_config, _CONFIGURED)
    assert _run_file(tmp_path) == EXIT_OK
    assert (
        "[Other](https://cfg.example/Other)" in capsys.readouterr().out
    )


def test_the_configured_map_applies_to_from_wiki(
    trac, isolated_config, capsys
):
    _write_config(isolated_config, _CONFIGURED)
    assert main(["--from-wiki", "P", "--to", "md"]) == EXIT_OK
    assert (
        "[Other](https://cfg.example/Other)" in capsys.readouterr().out
    )


def test_the_environment_map_beats_the_yaml_map(
    trac, isolated_config, tmp_path, monkeypatch, capsys
):
    _write_config(isolated_config, _CONFIGURED)
    monkeypatch.setenv("TRAC_MCP_LINK_REWRITES", _RULE)
    assert _run_file(tmp_path) == EXIT_OK
    assert _REWRITTEN in capsys.readouterr().out


def test_the_flag_replaces_the_configured_map(
    trac, isolated_config, tmp_path, capsys
):
    # The configured rule would match too; the flag's rule is the only
    # one applied, not merged with it.
    _write_config(isolated_config, _CONFIGURED)
    rc = _run_file(
        tmp_path, "--link-map", "https://other.example/=https://x/"
    )
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert _UNTOUCHED in out
    assert "cfg.example" not in out


# --- other directions ---------------------------------------------------


def test_md_to_tracwiki_warns_the_flag_has_no_effect(
    trac, monkeypatch, capsys
):
    monkeypatch.setattr("sys.stdin", io.StringIO("# Hello\n"))
    rc = main(["--from", "md", "--to", "tracwiki", "--link-map", _RULE])
    assert rc == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out.startswith("= Hello")
    lines = [
        ln for ln in captured.err.splitlines() if "--link-map" in ln
    ]
    assert len(lines) == 1
    assert lines[0].startswith("warning: ")
    assert "no effect" in lines[0]


def test_pass_through_warns_the_flag_has_no_effect(
    trac, monkeypatch, capsys
):
    monkeypatch.setattr("sys.stdin", io.StringIO("= Hello =\n"))
    rc = main(
        ["--from", "tracwiki", "--to", "tracwiki", "--link-map", _RULE]
    )
    assert rc == EXIT_OK
    assert "no effect" in capsys.readouterr().err


def test_quiet_suppresses_the_no_effect_warning(
    trac, monkeypatch, capsys
):
    monkeypatch.setattr("sys.stdin", io.StringIO("# Hello\n"))
    rc = main(
        ["--from", "md", "--to", "tracwiki", "--link-map", _RULE, "-q"]
    )
    assert rc == EXIT_OK
    assert capsys.readouterr().err == ""


def test_the_flag_on_a_working_conversion_prints_no_warning(
    trac, tmp_path, capsys
):
    assert _run_file(tmp_path, "--link-map", _RULE) == EXIT_OK
    assert "no effect" not in capsys.readouterr().err


# --- validation -----------------------------------------------------------


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--link-map", f"{_WIKI}"], "not FROM=TO"),
        (["--link-map", "=https://b/"], "must not be empty"),
        (
            ["--link-map", "/a/=/b/", "--link-map", "/a/=/c/"],
            "repeats 'from'",
        ),
    ],
)
def test_a_bad_link_map_is_a_usage_error(
    trac, tmp_path, capsys, flags, message
):
    with pytest.raises(SystemExit) as exc:
        _run_file(tmp_path, *flags)
    assert exc.value.code == EXIT_USAGE_ERROR
    assert message in capsys.readouterr().err
    trac.wiki_to_html.assert_not_called()


def test_help_describes_link_map(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "--link-map FROM=TO" in out
    assert "TracWiki -> Markdown" in out
