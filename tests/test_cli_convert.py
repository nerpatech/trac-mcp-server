"""Smoke tests for trac-convert CLI (Phase 11–17)."""

import argparse
import io
from unittest.mock import Mock

import pyperclip
import pytest

from trac_mcp_server import __version__
from trac_mcp_server.cli.convert import (
    EXIT_CONVERSION_ERROR,
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    EXIT_USAGE_ERROR,
    ClipboardUnavailableError,
    build_parser,
    convert_text,
    main,
)
from trac_mcp_server.converters.common import ConversionResult


def test_build_parser_returns_parser():
    """build_parser() returns an ArgumentParser with prog=trac-convert."""
    parser = build_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    assert parser.prog == "trac-convert"


def test_version_flag_prints_package_version(capsys):
    """--version exits 0 and prints the package version."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out
    assert "trac-convert" in captured.out


def test_help_flag_exits_zero_with_usage(capsys):
    """--help exits 0 and includes usage and program name."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out
    assert "trac-convert" in captured.out


def test_no_args_missing_required_to_exits_2(capsys):
    """No args exits 2 with a --to-is-required message (manual check in main)."""
    # Since Phase 20 made --to optional in argparse (to allow --check-trac
    # without --to), the enforcement moved from argparse to main(). main()
    # returns EXIT_USAGE_ERROR (2), it does NOT raise SystemExit.
    exit_code = main([])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "--to" in captured.err
    assert "required" in captured.err.lower()


# ---------- Parser flag tests ----------


def test_from_flag_defaults_to_auto():
    args = build_parser().parse_args(["--to", "md"])
    assert args.source_format == "auto"
    assert args.target_format == "md"


def test_from_flag_accepts_md_tracwiki_auto():
    parser = build_parser()
    for choice in ("md", "tracwiki", "auto"):
        args = parser.parse_args(["--from", choice, "--to", "md"])
        assert args.source_format == choice


def test_to_flag_accepts_md_and_tracwiki():
    parser = build_parser()
    for choice in ("md", "tracwiki"):
        args = parser.parse_args(["--to", choice])
        assert args.target_format == choice


def test_from_flag_rejects_invalid_choice(capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--from", "bogus", "--to", "md"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err
    assert "bogus" in err


def test_to_flag_rejects_invalid_choice(capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--to", "bogus"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err


def test_to_flag_is_required(capsys):
    # Since Phase 20 made --to optional at the argparse level (to allow
    # --check-trac without --to), the requirement is enforced in main()
    # rather than by argparse.  Test through main() instead of build_parser().
    exit_code = main(["--from", "md"])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--to" in err


# ---------- convert_text dispatch tests ----------


def test_convert_text_auto_detects_markdown_source():
    result = convert_text("# Hello world", "auto", "tracwiki")
    assert result.source_format == "markdown"
    assert result.target_format == "tracwiki"
    assert result.converted is True


def test_convert_text_auto_detects_tracwiki_source():
    result = convert_text("= Hello world =", "auto", "md")
    assert result.source_format == "tracwiki"
    assert result.target_format == "markdown"
    assert result.converted is True


def test_convert_text_honors_explicit_md_source():
    # A doc whose content leans tracwiki but user says --from md:
    # explicit flag must win over the heuristic.
    result = convert_text("= Hello =", "md", "tracwiki")
    assert result.source_format == "markdown"
    assert result.target_format == "tracwiki"


def test_convert_text_honors_explicit_tracwiki_source():
    result = convert_text("# Hello", "tracwiki", "md")
    assert result.source_format == "tracwiki"
    assert result.target_format == "markdown"


def test_convert_text_same_format_is_passthrough():
    text = "# Hello\n\nParagraph text."
    result = convert_text(text, "md", "md")
    assert result.source_format == "markdown"
    assert result.target_format == "markdown"
    assert result.converted is False
    assert result.text == text
    assert result.warnings == []


def test_convert_text_same_format_tracwiki_passthrough():
    text = "= Hello =\n\nParagraph text."
    result = convert_text(text, "tracwiki", "tracwiki")
    assert result.converted is False
    assert result.text == text


def test_convert_text_md_to_tracwiki_produces_tracwiki_heading():
    result = convert_text("# Hello", "md", "tracwiki")
    # TracWiki H1 uses `= Hello =` style; we don't over-specify the
    # exact output (that's the converter's contract), just confirm
    # the tracwiki heading marker is present.
    assert "= Hello" in result.text


def test_convert_text_tracwiki_to_md_produces_md_heading():
    result = convert_text("= Hello =", "tracwiki", "md")
    # Markdown H1 uses `# Hello`.
    assert result.text.lstrip().startswith("# Hello")


# ---------- stdin/stdout integration tests ----------


def test_main_reads_stdin_and_writes_converted_output_to_stdout(
    monkeypatch, capsys
):
    monkeypatch.setattr("sys.stdin", io.StringIO("# Hello"))
    exit_code = main(["--from", "md", "--to", "tracwiki"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "= Hello" in captured.out
    assert captured.err == ""


def test_main_pass_through_when_source_equals_target(
    monkeypatch, capsys
):
    input_text = "# Hello\n\nBody.\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(input_text))
    exit_code = main(["--from", "md", "--to", "md"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == input_text


def test_main_writes_verbatim_no_trailing_newline_added(
    monkeypatch, capsys
):
    monkeypatch.setattr("sys.stdin", io.StringIO("# Hi"))
    exit_code = main(["--from", "md", "--to", "md"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == "# Hi"


def test_main_auto_detects_when_no_from_flag(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("= Hello ="))
    exit_code = main(["--to", "md"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("# Hello")


def test_main_empty_stdin_is_valid_no_op(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    exit_code = main(["--to", "md"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == ""


def test_main_writes_warnings_to_stderr_not_stdout(monkeypatch, capsys):
    fake_result = ConversionResult(
        text="output text",
        source_format="markdown",
        target_format="tracwiki",
        converted=True,
        warnings=["lossy: table dropped"],
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.convert_text",
        Mock(return_value=fake_result),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("anything"))
    exit_code = main(["--to", "tracwiki"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "warning: lossy: table dropped" in captured.err
    assert "warning:" not in captured.out


# ---------- file I/O integration tests ----------


def test_input_file_positional_defaults_to_none():
    """Positional FILE is optional — defaults to None when omitted."""
    assert build_parser().parse_args(["--to", "md"]).input_file is None


def test_input_file_positional_accepts_a_path():
    """Positional FILE captures the path string."""
    assert (
        build_parser()
        .parse_args(["some/file.md", "--to", "md"])
        .input_file
        == "some/file.md"
    )


def test_output_flag_short_and_long_forms_equivalent():
    """-o and --output set output_file to the same value."""
    short = (
        build_parser()
        .parse_args(["--to", "md", "-o", "out.md"])
        .output_file
    )
    long = (
        build_parser()
        .parse_args(["--to", "md", "--output", "out.md"])
        .output_file
    )
    assert short == "out.md"
    assert long == "out.md"


def test_main_reads_from_positional_file_and_writes_to_stdout(
    tmp_path, capsys
):
    """main() reads the positional FILE and prints conversion to stdout."""
    in_file = tmp_path / "in.md"
    in_file.write_text("# Hello", encoding="utf-8")
    exit_code = main([str(in_file), "--from", "md", "--to", "tracwiki"])
    assert exit_code == 0
    assert "= Hello" in capsys.readouterr().out


def test_main_file_input_takes_precedence_when_stdin_also_available(
    monkeypatch, tmp_path, capsys
):
    """When both a positional FILE and stdin are present, FILE wins."""
    in_file = tmp_path / "in.tracwiki"
    in_file.write_text("= FromFile =", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO("# FromStdin"))
    exit_code = main([str(in_file), "--to", "md"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("# FromFile")


def test_main_missing_input_file_returns_1_with_stderr_message(
    tmp_path, capsys
):
    """A non-existent positional FILE exits 1 with a clear stderr message."""
    missing = tmp_path / "does_not_exist.md"
    exit_code = main([str(missing), "--to", "md"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "trac-convert: cannot read input file" in err
    assert str(missing) in err


def test_from_file_flag_reads_file_and_writes_to_stdout(
    tmp_path, capsys
):
    """--from-file PATH reads the file and converts to stdout."""
    in_file = tmp_path / "in.md"
    in_file.write_text("# Hello", encoding="utf-8")
    exit_code = main(["--from-file", str(in_file), "--to", "tracwiki"])
    assert exit_code == 0
    assert "= Hello" in capsys.readouterr().out


def test_from_file_flag_missing_file_returns_1(tmp_path, capsys):
    """--from-file with a non-existent path exits 1 with a message."""
    missing = tmp_path / "no_such.md"
    exit_code = main(["--from-file", str(missing), "--to", "md"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "cannot read input file" in err
    assert str(missing) in err


def test_from_file_and_positional_file_are_mutually_exclusive(
    tmp_path, capsys
):
    """--from-file and positional FILE together exit 1."""
    f = tmp_path / "x.md"
    f.write_text("hi", encoding="utf-8")
    exit_code = main(["--from-file", str(f), str(f), "--to", "md"])
    assert exit_code == 1
    assert "--from-file" in capsys.readouterr().err


def test_from_file_and_from_clipboard_are_mutually_exclusive(
    tmp_path, capsys
):
    """--from-file and --from-clipboard together exit 1."""
    f = tmp_path / "x.md"
    f.write_text("hi", encoding="utf-8")
    exit_code = main(
        ["--from-file", str(f), "--from-clipboard", "--to", "md"]
    )
    assert exit_code == 1
    assert "--from-clipboard" in capsys.readouterr().err


def test_from_file_and_from_wiki_are_mutually_exclusive(
    tmp_path, capsys
):
    """--from-file and --from-wiki together exit 1."""
    f = tmp_path / "x.md"
    f.write_text("hi", encoding="utf-8")
    exit_code = main(
        ["--from-file", str(f), "--from-wiki", "MyPage", "--to", "md"]
    )
    assert exit_code == 1
    assert "--from-wiki" in capsys.readouterr().err


def test_main_writes_to_output_file_and_leaves_stdout_empty(
    monkeypatch, tmp_path, capsys
):
    """main() writes to -o FILE and emits nothing to stdout."""
    monkeypatch.setattr("sys.stdin", io.StringIO("# Hello"))
    out = tmp_path / "out.tracwiki"
    exit_code = main(
        ["--from", "md", "--to", "tracwiki", "-o", str(out)]
    )
    assert exit_code == 0
    assert out.exists()
    assert "= Hello" in out.read_text(encoding="utf-8")
    assert capsys.readouterr().out == ""


def test_main_output_write_error_returns_1_with_stderr_message(
    monkeypatch, tmp_path, capsys
):
    """An unwritable output path exits 1 with a clear stderr message."""
    monkeypatch.setattr("sys.stdin", io.StringIO("# Hello"))
    bad_out = str(tmp_path / "no_such_dir" / "out.md")
    exit_code = main(["--from", "md", "--to", "md", "-o", bad_out])
    assert exit_code == 1
    assert (
        "trac-convert: cannot write output file"
        in capsys.readouterr().err
    )


def test_main_file_to_file_roundtrip(tmp_path, capsys):
    """File-to-file pass-through produces a byte-identical copy on disk."""
    content = "# Hello\n\nBody.\n"
    in_file = tmp_path / "in.md"
    in_file.write_text(content, encoding="utf-8")
    out = tmp_path / "out.md"
    exit_code = main(
        [str(in_file), "--from", "md", "--to", "md", "-o", str(out)]
    )
    assert exit_code == 0
    assert out.read_text(encoding="utf-8") == content
    assert capsys.readouterr().out == ""


def test_main_output_file_written_as_utf8(monkeypatch, tmp_path):
    """Non-ASCII content roundtrips correctly through UTF-8 file write."""
    monkeypatch.setattr("sys.stdin", io.StringIO("# Café ☕"))
    out = tmp_path / "out.md"
    exit_code = main(["--from", "md", "--to", "md", "-o", str(out)])
    assert exit_code == 0
    raw = out.read_bytes()
    assert raw.decode("utf-8") == "# Café ☕"


# ---------- clipboard I/O integration tests ----------


def test_from_clipboard_flag_defaults_to_false():
    """--from-clipboard defaults to False when not provided."""
    args = build_parser().parse_args(["--to", "md"])
    assert args.from_clipboard is False


def test_to_clipboard_flag_defaults_to_false():
    """--to-clipboard defaults to False when not provided."""
    args = build_parser().parse_args(["--to", "md"])
    assert args.to_clipboard is False


def test_from_clipboard_flag_parsed_as_true():
    """--from-clipboard sets from_clipboard to True."""
    args = build_parser().parse_args(["--from-clipboard", "--to", "md"])
    assert args.from_clipboard is True


def test_to_clipboard_flag_parsed_as_true():
    """--to-clipboard sets to_clipboard to True."""
    args = build_parser().parse_args(["--to", "md", "--to-clipboard"])
    assert args.to_clipboard is True


def test_from_clipboard_and_file_mutually_exclusive(tmp_path, capsys):
    """--from-clipboard and positional FILE are mutually exclusive."""
    in_file = tmp_path / "in.md"
    in_file.write_text("# Hello", encoding="utf-8")
    exit_code = main(["--from-clipboard", str(in_file), "--to", "md"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "--from-clipboard" in err
    assert "FILE" in err
    assert "mutually exclusive" in err


def test_to_clipboard_and_output_mutually_exclusive(tmp_path, capsys):
    """--to-clipboard and --output are mutually exclusive."""
    exit_code = main(
        [
            "--to",
            "md",
            "--to-clipboard",
            "--output",
            str(tmp_path / "out.md"),
        ]
    )
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "--to-clipboard" in err
    assert "--output" in err
    assert "mutually exclusive" in err


def test_main_reads_from_clipboard(monkeypatch, capsys):
    """--from-clipboard reads via read_clipboard() and converts."""
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.read_clipboard",
        lambda: "= Hello =",
    )
    exit_code = main(["--from-clipboard", "--to", "md"])
    assert exit_code == 0
    assert capsys.readouterr().out.startswith("# Hello")


def test_clipboard_read_error_returns_1(monkeypatch, capsys):
    """ClipboardUnavailableError on read → exit 1 with 'clipboard read failed'."""

    def raise_paste():
        raise ClipboardUnavailableError("no mechanism")

    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.read_clipboard", raise_paste
    )
    exit_code = main(["--from-clipboard", "--to", "md"])
    assert exit_code == 1
    assert "clipboard read failed" in capsys.readouterr().err


def test_clipboard_read_pyperclip_error_returns_1(monkeypatch, capsys):
    """Legacy PyperclipException from read_clipboard() also returns exit 1."""

    def raise_paste():
        raise pyperclip.PyperclipException("no mechanism")

    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.read_clipboard", raise_paste
    )
    exit_code = main(["--from-clipboard", "--to", "md"])
    assert exit_code == 1
    assert "clipboard read failed" in capsys.readouterr().err


def test_main_writes_to_clipboard(monkeypatch, capsys):
    """--to-clipboard passes converted text to write_clipboard(); stdout empty."""
    copied = []
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.write_clipboard",
        lambda t: copied.append(t),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("# Hello"))
    exit_code = main(
        ["--from", "md", "--to", "tracwiki", "--to-clipboard"]
    )
    assert exit_code == 0
    assert len(copied) == 1
    assert "= Hello" in copied[0]
    assert capsys.readouterr().out == ""


def test_clipboard_write_error_returns_1(monkeypatch):
    """ClipboardUnavailableError on write → exit 1 with 'clipboard write failed'."""

    def raise_copy(_):
        raise ClipboardUnavailableError("no mechanism")

    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.write_clipboard", raise_copy
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("# Hello"))
    exit_code = main(
        ["--from", "md", "--to", "tracwiki", "--to-clipboard"]
    )
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Phase 16: --heading-anchors and --unknown-macros CLI flags
# ---------------------------------------------------------------------------


def test_heading_anchors_off_by_default(monkeypatch, capsys):
    """--heading-anchors defaults to off; stdout has no #slug suffix."""
    monkeypatch.setattr("sys.stdin", io.StringIO("# H"))
    exit_code = main(["--from", "md", "--to", "tracwiki"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("= H =")
    assert "#h" not in out


def test_heading_anchors_off_omits_slug(monkeypatch, capsys):
    """--heading-anchors off: heading emitted without #slug suffix."""
    monkeypatch.setattr("sys.stdin", io.StringIO("# H"))
    exit_code = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("= H =")
    assert "#h" not in out


def test_heading_anchors_ignored_on_tw_to_md(monkeypatch):
    """--heading-anchors off on the wrong direction exits 0 (silently ignored)."""
    monkeypatch.setattr("sys.stdin", io.StringIO("= H = #h"))
    exit_code = main(
        [
            "--from",
            "tracwiki",
            "--to",
            "md",
            "--heading-anchors",
            "off",
        ]
    )
    assert exit_code == 0


def test_unknown_macros_preserve_default(monkeypatch, capsys):
    """--unknown-macros defaults to preserve; [[PageOutline]] stays literal.

    The former default, "bracket", emitted [MACRO: PageOutline]; it was
    removed in ticket #63 along with the re-absorption on the other side.
    """
    monkeypatch.setattr("sys.stdin", io.StringIO("[[PageOutline]]"))
    exit_code = main(["--from", "tracwiki", "--to", "md"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[[PageOutline]]" in out
    assert "[MACRO:" not in out


def test_unknown_macros_preserve(monkeypatch, capsys):
    """--unknown-macros preserve: stdout contains [[PageOutline]] literal."""
    monkeypatch.setattr("sys.stdin", io.StringIO("[[PageOutline]]"))
    exit_code = main(
        [
            "--from",
            "tracwiki",
            "--to",
            "md",
            "--unknown-macros",
            "preserve",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[[PageOutline]]" in out


def test_unknown_macros_drop(monkeypatch, capsys):
    """--unknown-macros drop: stdout does NOT contain PageOutline."""
    monkeypatch.setattr("sys.stdin", io.StringIO("[[PageOutline]]"))
    exit_code = main(
        ["--from", "tracwiki", "--to", "md", "--unknown-macros", "drop"]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "PageOutline" not in out


def test_help_lists_new_flags(capsys):
    """--help output includes both new flags."""
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "--heading-anchors" in out
    assert "--unknown-macros" in out


# ---------------------------------------------------------------------------
# Phase 17: exit-code constants + conversion-error guard
# ---------------------------------------------------------------------------


def test_exit_code_constants_have_expected_values():
    """EXIT_* constants have values (0, 1, 2, 3) in order."""
    assert (
        EXIT_OK,
        EXIT_RUNTIME_ERROR,
        EXIT_USAGE_ERROR,
        EXIT_CONVERSION_ERROR,
    ) == (
        0,
        1,
        2,
        3,
    )


def test_convert_text_exception_returns_exit_3_with_stderr_message(
    monkeypatch, capsys
):
    """convert_text() raising an exception → EXIT_CONVERSION_ERROR with stderr message."""
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.convert_text",
        Mock(side_effect=ValueError("bad input")),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("anything"))
    exit_code = main(["--to", "tracwiki"])
    assert exit_code == EXIT_CONVERSION_ERROR
    err = capsys.readouterr().err
    assert "trac-convert: conversion failed: bad input" in err


def test_convert_text_exception_does_not_leak_traceback(
    monkeypatch, capsys
):
    """On converter exception, stderr must not contain traceback markers."""
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.convert_text",
        Mock(side_effect=ValueError("bad input")),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("anything"))
    main(["--to", "tracwiki"])
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert 'File "' not in err


def test_convert_text_exception_writes_no_stdout(monkeypatch, capsys):
    """On converter exception, stdout must be empty (no partial output)."""
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.convert_text",
        Mock(side_effect=ValueError("bad input")),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("anything"))
    main(["--to", "tracwiki"])
    assert capsys.readouterr().out == ""


def test_usage_error_still_exits_2_via_argparse():
    """Invalid --to choice → SystemExit with code EXIT_USAGE_ERROR (2)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--to", "bogus"])
    assert excinfo.value.code == EXIT_USAGE_ERROR


def test_runtime_error_missing_file_uses_exit_1_constant(tmp_path):
    """Missing positional FILE → EXIT_RUNTIME_ERROR (not literal 1)."""
    missing = tmp_path / "does_not_exist.md"
    exit_code = main([str(missing), "--to", "md"])
    assert exit_code == EXIT_RUNTIME_ERROR


# ---------------------------------------------------------------------------
# Phase 17: --quiet flag
# ---------------------------------------------------------------------------


def test_quiet_flag_defaults_to_false():
    """--quiet defaults to False when not provided."""
    args = build_parser().parse_args(["--to", "md"])
    assert args.quiet is False


def test_quiet_flag_short_and_long_forms_equivalent():
    """-q and --quiet both set args.quiet to True."""
    short = build_parser().parse_args(["--to", "md", "-q"]).quiet
    long = build_parser().parse_args(["--to", "md", "--quiet"]).quiet
    assert short is True
    assert long is True


def test_quiet_suppresses_warning_lines_on_stderr(monkeypatch, capsys):
    """--quiet suppresses warning: lines; stdout still has converted text."""
    fake_result = ConversionResult(
        text="output text",
        source_format="markdown",
        target_format="tracwiki",
        converted=True,
        warnings=["lossy: table dropped"],
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.convert_text",
        Mock(return_value=fake_result),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("anything"))
    exit_code = main(["--to", "tracwiki", "--quiet"])
    assert exit_code == EXIT_OK
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "output text" in captured.out


def test_quiet_does_not_suppress_trac_convert_errors(tmp_path, capsys):
    """--quiet does NOT suppress trac-convert: error lines on stderr."""
    missing = tmp_path / "does_not_exist.md"
    exit_code = main([str(missing), "--to", "md", "--quiet"])
    assert exit_code == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert "trac-convert: cannot read input file" in err


def test_quiet_preserves_exit_code_ok(monkeypatch, capsys):
    """Happy path with warnings + --quiet → exit 0, empty stderr, stdout has output."""
    fake_result = ConversionResult(
        text="converted output",
        source_format="markdown",
        target_format="tracwiki",
        converted=True,
        warnings=["lossy: table dropped"],
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.convert_text",
        Mock(return_value=fake_result),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("# Hello"))
    exit_code = main(["--to", "tracwiki", "--quiet"])
    assert exit_code == EXIT_OK
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "converted output" in captured.out


# ---------------------------------------------------------------------------
# Phase 17: --verbose flag and verbose/quiet mutex
# ---------------------------------------------------------------------------


def test_verbose_flag_defaults_to_false():
    """--verbose defaults to False when not provided."""
    args = build_parser().parse_args(["--to", "md"])
    assert args.verbose is False


def test_verbose_flag_short_and_long_forms_equivalent():
    """-v and --verbose both set args.verbose to True."""
    short = build_parser().parse_args(["--to", "md", "-v"]).verbose
    long = (
        build_parser().parse_args(["--to", "md", "--verbose"]).verbose
    )
    assert short is True
    assert long is True


def test_verbose_emits_source_format_info_line(monkeypatch, capsys):
    """--verbose: stderr contains 'info: source format: markdown'."""
    monkeypatch.setattr("sys.stdin", io.StringIO("# Hello"))
    exit_code = main(["--from", "md", "--to", "tracwiki", "--verbose"])
    assert exit_code == EXIT_OK
    err = capsys.readouterr().err
    assert "info: source format: markdown" in err


def test_verbose_emits_byte_count_info_lines(monkeypatch, capsys):
    """--verbose: stderr contains 'info: read N bytes' and 'info: wrote M bytes'."""
    input_text = "# Hello"
    monkeypatch.setattr("sys.stdin", io.StringIO(input_text))
    exit_code = main(["--from", "md", "--to", "tracwiki", "--verbose"])
    assert exit_code == EXIT_OK
    err = capsys.readouterr().err
    assert "info: read " in err
    assert "info: wrote " in err
    # Verify numeric counts are present
    import re

    assert re.search(r"info: read \d+ bytes", err)
    assert re.search(r"info: wrote \d+ bytes", err)


def test_verbose_diagnostics_go_to_stderr_not_stdout(
    monkeypatch, capsys
):
    """--verbose: 'info:' never appears on stdout."""
    monkeypatch.setattr("sys.stdin", io.StringIO("# Hello"))
    exit_code = main(["--from", "md", "--to", "tracwiki", "--verbose"])
    assert exit_code == EXIT_OK
    captured = capsys.readouterr()
    assert "info:" not in captured.out


def test_verbose_info_lines_precede_warning_lines(monkeypatch, capsys):
    """--verbose: info: lines appear before warning: lines in stderr."""
    fake_result = ConversionResult(
        text="output",
        source_format="markdown",
        target_format="tracwiki",
        converted=True,
        warnings=["lossy: table dropped"],
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.convert_text",
        Mock(return_value=fake_result),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("anything"))
    exit_code = main(["--to", "tracwiki", "--verbose"])
    assert exit_code == EXIT_OK
    err = capsys.readouterr().err
    info_pos = err.index("info: source format:")
    warning_pos = err.index("warning: lossy:")
    assert info_pos < warning_pos


def test_quiet_and_verbose_mutually_exclusive_exits_2(capsys):
    """--quiet and --verbose together → SystemExit code EXIT_USAGE_ERROR (2)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--to", "md", "--quiet", "--verbose"])
    assert excinfo.value.code == EXIT_USAGE_ERROR
    err = capsys.readouterr().err
    assert "not allowed with" in err


def test_no_flags_emits_no_info_lines(monkeypatch, capsys):
    """Default (no -v, no -q): stderr contains no info: lines."""
    monkeypatch.setattr("sys.stdin", io.StringIO("# Hello"))
    exit_code = main(["--from", "md", "--to", "tracwiki"])
    assert exit_code == EXIT_OK
    err = capsys.readouterr().err
    assert "info:" not in err


# ---------------------------------------------------------------------------
# Phase 18-01: Format roundtrip tests (md → tracwiki → md)
# ---------------------------------------------------------------------------

# Module-level fixtures shared by Task 1 (md→tw→md) and Task 2 (tw→md→tw).

MD_MULTI_CONSTRUCT = """\
# Project Overview

This module provides **core** functionality for *data processing*.

## Features

- First bullet item
- Second bullet item
- Third bullet item

## Installation

1. Install dependencies
2. Run setup
3. Configure options

Inline `code_example` works here.

```python
def hello():
    return 42
```

See [documentation](https://example.com) for more details.
"""

TW_MULTI_CONSTRUCT = """\
= Project Overview =
This module provides '''core''' functionality for ''data processing''.

== Features ==
 * First bullet item
 * Second bullet item
 * Third bullet item
== Installation ==
 1. Install dependencies
 2. Run setup
 3. Configure options
Inline `code_example` works here.

{{{#!python
def hello():
    return 42
}}}
See [https://example.com documentation] for more details.
"""


# ---------------------------------------------------------------------------
# Task 1: md → tracwiki → md roundtrip tests
# ---------------------------------------------------------------------------


def test_roundtrip_md_headings_preserve_text_and_levels(
    monkeypatch, capsys
):
    """H1/H2/H3 heading text and levels survive md → tw → md.

    Uses --heading-anchors off on the first hop so the intermediate
    TracWiki does not contain #slug anchors that would clutter assertions.
    """
    md_input = "# Project Overview\n\n## Features\n\n### Details\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(md_input))
    rc1 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(["--from", "tracwiki", "--to", "md"])
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    assert "# Project Overview" in result
    assert "## Features" in result
    assert "### Details" in result


def test_roundtrip_md_bullet_list_items_preserved(monkeypatch, capsys):
    """All bullet list items survive md → tw → md.

    Uses --heading-anchors off for a cleaner intermediate without #slug noise.
    """
    md_input = (
        "## Features\n\n"
        "- First bullet item\n"
        "- Second bullet item\n"
        "- Third bullet item\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(md_input))
    rc1 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(["--from", "tracwiki", "--to", "md"])
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    assert "First bullet item" in result
    assert "Second bullet item" in result
    assert "Third bullet item" in result


def test_roundtrip_md_numbered_list_items_preserved(
    monkeypatch, capsys
):
    """All numbered list items survive md → tw → md.

    Uses --heading-anchors off for a cleaner intermediate without #slug noise.
    """
    md_input = (
        "## Installation\n\n"
        "1. Install dependencies\n"
        "2. Run setup\n"
        "3. Configure options\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(md_input))
    rc1 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(["--from", "tracwiki", "--to", "md"])
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    assert "Install dependencies" in result
    assert "Run setup" in result
    assert "Configure options" in result


def test_roundtrip_md_inline_code_content_preserved(
    monkeypatch, capsys
):
    """Inline code payload text survives md → tw → md."""
    md_input = "Inline `code_example` works here.\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(md_input))
    rc1 = main(["--from", "md", "--to", "tracwiki"])
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(["--from", "tracwiki", "--to", "md"])
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    assert "code_example" in result
    assert "`code_example`" in result


def test_roundtrip_md_fenced_code_block_content_preserved(
    monkeypatch, capsys
):
    """Fenced code block body survives md → tw → md verbatim."""
    md_input = "```python\ndef hello():\n    return 42\n```\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(md_input))
    rc1 = main(["--from", "md", "--to", "tracwiki"])
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(["--from", "tracwiki", "--to", "md"])
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    assert "def hello():" in result
    assert "return 42" in result


def test_roundtrip_md_link_url_and_text_preserved(monkeypatch, capsys):
    """Both the URL and link text survive md → tw → md.

    Uses --heading-anchors off to keep the intermediate clean.
    """
    md_input = (
        "See [documentation](https://example.com) for more details.\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(md_input))
    rc1 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(["--from", "tracwiki", "--to", "md"])
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    assert "https://example.com" in result
    assert "documentation" in result


def test_roundtrip_md_full_document_preserves_all_construct_markers(
    monkeypatch, capsys
):
    """Full MD_MULTI_CONSTRUCT fixture: all construct types survive md → tw → md.

    Uses --heading-anchors off for a cleaner intermediate; exercises the
    real pipeline end-to-end (headings, lists, code, link, bold, italic).
    """
    monkeypatch.setattr("sys.stdin", io.StringIO(MD_MULTI_CONSTRUCT))
    rc1 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(["--from", "tracwiki", "--to", "md"])
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    # Headings
    assert "# Project Overview" in result
    assert "## Features" in result
    assert "## Installation" in result
    # Bullet list items
    assert "First bullet item" in result
    assert "Second bullet item" in result
    assert "Third bullet item" in result
    # Numbered list items
    assert "Install dependencies" in result
    # Inline code
    assert "code_example" in result
    # Code block content
    assert "def hello():" in result
    # Link URL and text
    assert "https://example.com" in result
    assert "documentation" in result


# ---------------------------------------------------------------------------
# Task 2: tracwiki → md → tracwiki roundtrip tests + expected-divergence
# ---------------------------------------------------------------------------


def test_roundtrip_tw_headings_preserve_text_and_levels(
    monkeypatch, capsys
):
    """H1/H2/H3 heading text and levels survive tw → md → tw."""
    tw_input = "= Project Overview =\n== Features ==\n=== Details ===\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(tw_input))
    rc1 = main(["--from", "tracwiki", "--to", "md"])
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    assert "= Project Overview =" in result
    assert "== Features ==" in result
    assert "=== Details ===" in result


def test_roundtrip_tw_bullet_list_items_preserved(monkeypatch, capsys):
    """All bullet list items survive tw → md → tw."""
    tw_input = (
        "== Features ==\n"
        " * First bullet item\n"
        " * Second bullet item\n"
        " * Third bullet item\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(tw_input))
    rc1 = main(["--from", "tracwiki", "--to", "md"])
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    assert "First bullet item" in result
    assert "Second bullet item" in result
    assert "Third bullet item" in result


def test_roundtrip_tw_numbered_list_items_preserved(
    monkeypatch, capsys
):
    """All numbered list items survive tw → md → tw."""
    tw_input = (
        "== Installation ==\n"
        " 1. Install dependencies\n"
        " 2. Run setup\n"
        " 3. Configure options\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(tw_input))
    rc1 = main(["--from", "tracwiki", "--to", "md"])
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    assert "Install dependencies" in result
    assert "Run setup" in result
    assert "Configure options" in result


def test_roundtrip_tw_code_block_content_preserved(monkeypatch, capsys):
    """{{{...}}} code block body survives tw → md → tw verbatim."""
    tw_input = "{{{#!python\ndef hello():\n    return 42\n}}}\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(tw_input))
    rc1 = main(["--from", "tracwiki", "--to", "md"])
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(["--from", "md", "--to", "tracwiki"])
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    assert "def hello():" in result
    assert "return 42" in result


def test_roundtrip_tw_link_url_and_text_preserved(monkeypatch, capsys):
    """TracWiki [url text] link: both URL and text survive tw → md → tw."""
    tw_input = (
        "See [https://example.com documentation] for more details.\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(tw_input))
    rc1 = main(["--from", "tracwiki", "--to", "md"])
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    assert "https://example.com" in result
    assert "documentation" in result


def test_roundtrip_tw_full_document_preserves_all_construct_markers(
    monkeypatch, capsys
):
    """Full TW_MULTI_CONSTRUCT fixture: all construct types survive tw → md → tw."""
    monkeypatch.setattr("sys.stdin", io.StringIO(TW_MULTI_CONSTRUCT))
    rc1 = main(["--from", "tracwiki", "--to", "md"])
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out

    # Headings
    assert "= Project Overview =" in result
    assert "== Features ==" in result
    assert "== Installation ==" in result
    # Bullet list items
    assert "First bullet item" in result
    assert "Second bullet item" in result
    assert "Third bullet item" in result
    # Numbered list items
    assert "Install dependencies" in result
    # Code block content
    assert "def hello():" in result
    # Link URL and text
    assert "https://example.com" in result
    assert "documentation" in result


# ---------------------------------------------------------------------------
# Expected-divergence tests: pin down known-lossy roundtrip paths
# ---------------------------------------------------------------------------


def test_roundtrip_tw_unknown_macro_survives_both_hops(
    monkeypatch, capsys
):
    """[[SomeMacro(arg)]] survives tw→md→tw unchanged.

    Only names carrying explicit "(args)" -- which a plain WikiLink never
    does -- or names on the known-macro allowlist are treated as macros
    (ticket #28: bare, unrecognized ``[[Word]]`` is now a WikiLink instead,
    see test_roundtrip_tw_bare_bracket_link_becomes_wikilink below).

    Regression test for ticket #19: the tw→md→tw path used to be lossy
    here, silently and permanently flattening the macro the first time a
    page carrying one was edited via the Markdown path. Ticket #19 fixed
    that by emitting "[MACRO: SomeMacro(arg)]" and re-absorbing it on the
    way back; ticket #63 removed that detour, and the macro is now simply
    left literal across both hops. The guarantee under test is unchanged.
    """
    tw_input = "[[SomeMacro(arg)]]\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(tw_input))
    rc1 = main(["--from", "tracwiki", "--to", "md"])
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out
    # First hop leaves the macro literal rather than flattening it.
    assert "[[SomeMacro(arg)]]" in intermediate
    assert "[MACRO:" not in intermediate

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out
    # Second hop carries it through unchanged.
    assert "[[SomeMacro(arg)]]" in result
    assert "[MACRO:" not in result


def test_roundtrip_tw_unknown_macro_preserve_mode_roundtrips(
    monkeypatch, capsys
):
    """[[SomeMacro(arg)]] with --unknown-macros preserve survives tw → md → tw intact.

    When the user opts in to preserve mode, the macro literal passes through
    unchanged across both hops — a case where the tw→md→tw round trip IS
    identity for macro syntax.
    """
    tw_input = "[[SomeMacro(arg)]]\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(tw_input))
    rc1 = main(
        [
            "--from",
            "tracwiki",
            "--to",
            "md",
            "--unknown-macros",
            "preserve",
        ]
    )
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out
    # Preserve mode: [[SomeMacro(arg)]] must survive to the md intermediate
    assert "[[SomeMacro(arg)]]" in intermediate

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out
    assert "[[SomeMacro(arg)]]" in result


def test_roundtrip_tw_bare_bracket_link_becomes_wikilink(
    monkeypatch, capsys
):
    """[[SomePage]] converts to a real Markdown link, not inert macro text.

    Regression test for ticket #28: bare, unrecognized ``[[Word]]`` (no
    "(args)", not a known Trac macro name) is a plain WikiLink in TracWiki,
    not a macro. Routing it through the macro-placeholder path used to
    destroy the link (and could corrupt neighboring links). It should
    survive as ``[SomePage](wiki:SomePage)`` in Markdown and round-trip to
    the equivalent single-bracket TracWiki link form.
    """
    tw_input = "[[SomePage]]\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(tw_input))
    rc1 = main(["--from", "tracwiki", "--to", "md"])
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out
    assert "[SomePage](wiki:SomePage)" in intermediate
    assert "MACRO" not in intermediate

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "off"]
    )
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out
    assert "[wiki:SomePage SomePage]" in result


def test_roundtrip_tw_adjacent_bracket_links_stay_separate(
    monkeypatch, capsys
):
    """Two [[Page]] links on one line convert independently, not merged.

    Regression test for ticket #28's core symptom: [[PageA]], [[PageB]] on
    the same line used to be merged into one corrupted, unterminated
    placeholder construct with an embedded raw control character.
    """
    tw_input = "See [[PageA]] and [[PageB]] for details.\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(tw_input))
    rc = main(["--from", "tracwiki", "--to", "md"])
    assert rc == EXIT_OK
    result = capsys.readouterr().out
    assert "[PageA](wiki:PageA)" in result
    assert "[PageB](wiki:PageB)" in result
    assert "\x00" not in result


def test_roundtrip_md_heading_anchor_survives_when_on(
    monkeypatch, capsys
):
    """With --heading-anchors on, slug appears in tw intermediate but NOT in final md.

    The md→tw→md path drops the anchor cleanly: the TracWiki #slug is not
    rendered back to Markdown, so the final md is semantically identical
    to the original (no spurious text).  This documents that the anchor
    is a tw-level concern, not a md-level one.
    """
    md_input = "# My Heading\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(md_input))
    # First hop: explicit --heading-anchors on (not the default)
    rc1 = main(
        ["--from", "md", "--to", "tracwiki", "--heading-anchors", "on"]
    )
    assert rc1 == EXIT_OK
    intermediate = capsys.readouterr().out
    # The #slug must appear in the tracwiki intermediate
    assert "#my-heading" in intermediate

    monkeypatch.setattr("sys.stdin", io.StringIO(intermediate))
    rc2 = main(["--from", "tracwiki", "--to", "md"])
    assert rc2 == EXIT_OK
    result = capsys.readouterr().out
    # Final md must contain the heading text without the anchor
    assert "# My Heading" in result
    assert "#my-heading" not in result


# ---------------------------------------------------------------------------
# Phase 18-02: Cross-mode I/O integration matrix
# ---------------------------------------------------------------------------

# Shared fixture: a short Markdown snippet unambiguously detected as markdown,
# with content that produces tracwiki output, short enough for concise assertions.
IO_MATRIX_INPUT = "# Title\n\nBody paragraph with **bold**.\n"

# Leading token of the TracWiki output every combination must produce.
IO_MATRIX_EXPECTED_TW_START = "= Title ="


# ---------------------------------------------------------------------------
# Task 1: 9-cell source × destination matrix (explicit named tests)
# ---------------------------------------------------------------------------


def test_io_matrix_stdin_to_stdout(monkeypatch, capsys):
    """Matrix[stdin → stdout]: converted TracWiki written to stdout; stderr empty."""
    monkeypatch.setattr("sys.stdin", io.StringIO(IO_MATRIX_INPUT))
    exit_code = main(["--from", "md", "--to", "tracwiki"])
    assert exit_code == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out.startswith(IO_MATRIX_EXPECTED_TW_START)
    assert captured.err == ""


def test_io_matrix_stdin_to_file(monkeypatch, tmp_path, capsys):
    """Matrix[stdin → file]: output written to file; stdout empty; stderr empty."""
    out_file = tmp_path / "out.tw"
    monkeypatch.setattr("sys.stdin", io.StringIO(IO_MATRIX_INPUT))
    exit_code = main(
        ["--from", "md", "--to", "tracwiki", "-o", str(out_file)]
    )
    assert exit_code == EXIT_OK
    assert capsys.readouterr().out == ""
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8").startswith(
        IO_MATRIX_EXPECTED_TW_START
    )


def test_io_matrix_stdin_to_clipboard(monkeypatch, capsys):
    """Matrix[stdin → clipboard]: output sent to write_clipboard(); stdout empty; stderr empty."""
    captured = {}

    def fake_copy(text):
        captured["text"] = text

    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.write_clipboard", fake_copy
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(IO_MATRIX_INPUT))
    exit_code = main(
        ["--from", "md", "--to", "tracwiki", "--to-clipboard"]
    )
    assert exit_code == EXIT_OK
    assert capsys.readouterr().out == ""
    assert captured["text"].startswith(IO_MATRIX_EXPECTED_TW_START)


def test_io_matrix_file_to_stdout(tmp_path, capsys):
    """Matrix[file → stdout]: positional FILE read; converted TracWiki written to stdout."""
    in_file = tmp_path / "in.md"
    in_file.write_text(IO_MATRIX_INPUT, encoding="utf-8")
    exit_code = main([str(in_file), "--from", "md", "--to", "tracwiki"])
    assert exit_code == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out.startswith(IO_MATRIX_EXPECTED_TW_START)
    assert captured.err == ""


def test_io_matrix_file_to_file(tmp_path, capsys):
    """Matrix[file → file]: positional FILE → -o FILE; stdout empty; stderr empty."""
    in_file = tmp_path / "in.md"
    in_file.write_text(IO_MATRIX_INPUT, encoding="utf-8")
    out_file = tmp_path / "out.tw"
    exit_code = main(
        [
            str(in_file),
            "--from",
            "md",
            "--to",
            "tracwiki",
            "-o",
            str(out_file),
        ]
    )
    assert exit_code == EXIT_OK
    assert capsys.readouterr().out == ""
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8").startswith(
        IO_MATRIX_EXPECTED_TW_START
    )


def test_io_matrix_file_to_clipboard(monkeypatch, tmp_path, capsys):
    """Matrix[file → clipboard]: positional FILE → --to-clipboard; stdout empty; stderr empty."""
    captured = {}

    def fake_copy(text):
        captured["text"] = text

    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.write_clipboard", fake_copy
    )
    in_file = tmp_path / "in.md"
    in_file.write_text(IO_MATRIX_INPUT, encoding="utf-8")
    exit_code = main(
        [
            str(in_file),
            "--from",
            "md",
            "--to",
            "tracwiki",
            "--to-clipboard",
        ]
    )
    assert exit_code == EXIT_OK
    assert capsys.readouterr().out == ""
    assert captured["text"].startswith(IO_MATRIX_EXPECTED_TW_START)


def test_io_matrix_clipboard_to_stdout(monkeypatch, capsys):
    """Matrix[clipboard → stdout]: --from-clipboard reads via read_clipboard(); stdout has output."""
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.read_clipboard",
        lambda: IO_MATRIX_INPUT,
    )
    exit_code = main(
        ["--from-clipboard", "--from", "md", "--to", "tracwiki"]
    )
    assert exit_code == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out.startswith(IO_MATRIX_EXPECTED_TW_START)
    assert captured.err == ""


def test_io_matrix_clipboard_to_file(monkeypatch, tmp_path, capsys):
    """Matrix[clipboard → file]: --from-clipboard → -o FILE; stdout empty; stderr empty."""
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.read_clipboard",
        lambda: IO_MATRIX_INPUT,
    )
    out_file = tmp_path / "out.tw"
    exit_code = main(
        [
            "--from-clipboard",
            "--from",
            "md",
            "--to",
            "tracwiki",
            "-o",
            str(out_file),
        ]
    )
    assert exit_code == EXIT_OK
    assert capsys.readouterr().out == ""
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8").startswith(
        IO_MATRIX_EXPECTED_TW_START
    )


def test_io_matrix_clipboard_to_clipboard(monkeypatch, capsys):
    """Matrix[clipboard → clipboard]: --from-clipboard → --to-clipboard; stdout empty; stderr empty."""
    captured = {}

    def fake_copy(text):
        captured["text"] = text

    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.read_clipboard",
        lambda: IO_MATRIX_INPUT,
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.write_clipboard", fake_copy
    )
    exit_code = main(
        [
            "--from-clipboard",
            "--from",
            "md",
            "--to",
            "tracwiki",
            "--to-clipboard",
        ]
    )
    assert exit_code == EXIT_OK
    assert capsys.readouterr().out == ""
    assert captured["text"].startswith(IO_MATRIX_EXPECTED_TW_START)


# ---------------------------------------------------------------------------
# Task 2: Cross-mode warning propagation + encoding + empty-input edge cases
# ---------------------------------------------------------------------------


def test_warnings_reach_stderr_when_output_is_file(
    monkeypatch, tmp_path, capsys
):
    """Warnings still reach stderr when destination is --output FILE.

    Uses monkeypatch to inject a synthetic warning (same pattern as
    test_main_writes_warnings_to_stderr_not_stdout).
    """
    fake_result = ConversionResult(
        text="= Title = #title\nBody paragraph.",
        source_format="markdown",
        target_format="tracwiki",
        converted=True,
        warnings=["lossy: unknown construct"],
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.convert_text",
        Mock(return_value=fake_result),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(IO_MATRIX_INPUT))
    out_file = tmp_path / "out.tw"
    exit_code = main(["--to", "tracwiki", "-o", str(out_file)])
    assert exit_code == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == ""
    assert out_file.exists()
    assert "warning: lossy: unknown construct" in captured.err


def test_warnings_reach_stderr_when_output_is_clipboard(
    monkeypatch, capsys
):
    """Warnings still reach stderr when destination is --to-clipboard.

    Uses monkeypatch to inject a synthetic warning so the test is
    independent of which converter constructs trigger real warnings.
    """
    fake_result = ConversionResult(
        text="= Title = #title\nBody paragraph.",
        source_format="markdown",
        target_format="tracwiki",
        converted=True,
        warnings=["lossy: unknown construct"],
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.convert_text",
        Mock(return_value=fake_result),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(IO_MATRIX_INPUT))
    copied = []
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.write_clipboard",
        lambda t: copied.append(t),
    )
    exit_code = main(["--to", "tracwiki", "--to-clipboard"])
    assert exit_code == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(copied) == 1
    assert "warning: lossy: unknown construct" in captured.err


def test_non_ascii_content_preserved_through_stdin_stdout(
    monkeypatch, capsys
):
    """Non-ASCII UTF-8 characters survive a stdin → stdout pass-through (md→md)."""
    non_ascii = "# Café ☕ — naïve\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(non_ascii))
    exit_code = main(["--from", "md", "--to", "md"])
    assert exit_code == EXIT_OK
    assert capsys.readouterr().out == non_ascii


def test_non_ascii_content_preserved_through_file_to_file(tmp_path):
    """Non-ASCII UTF-8 survives a file → file pass-through; raw bytes confirm UTF-8 write."""
    non_ascii = "# Café ☕ — naïve\n"
    in_file = tmp_path / "in.md"
    in_file.write_text(non_ascii, encoding="utf-8")
    out_file = tmp_path / "out.md"
    exit_code = main(
        [
            str(in_file),
            "--from",
            "md",
            "--to",
            "md",
            "-o",
            str(out_file),
        ]
    )
    assert exit_code == EXIT_OK
    raw = out_file.read_bytes()
    # Confirm the file was written as UTF-8, not latin-1 or ascii
    assert raw.decode("utf-8") == non_ascii
    # Spot-check: é is encoded as \xc3\xa9 in UTF-8
    assert b"\xc3\xa9" in raw


def test_crlf_input_from_stdin_does_not_crash(monkeypatch, capsys):
    """CRLF line endings in stdin input do not crash the CLI (exit 0, non-empty stdout)."""
    crlf_input = "# H\r\n\r\nBody\r\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(crlf_input))
    exit_code = main(["--from", "md", "--to", "tracwiki"])
    assert exit_code == EXIT_OK
    out = capsys.readouterr().out
    assert out  # non-empty: converter produced some output


def test_empty_input_returns_exit_ok_from_file(tmp_path, capsys):
    """An empty input file returns EXIT_OK with empty stdout (file source)."""
    empty_file = tmp_path / "empty.md"
    empty_file.write_text("", encoding="utf-8")
    exit_code = main([str(empty_file), "--to", "md"])
    assert exit_code == EXIT_OK
    assert capsys.readouterr().out == ""


def test_empty_input_returns_exit_ok_from_clipboard(
    monkeypatch, capsys
):
    """An empty clipboard string returns EXIT_OK with empty stdout (clipboard source)."""
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.read_clipboard", lambda: ""
    )
    exit_code = main(["--from-clipboard", "--to", "md"])
    assert exit_code == EXIT_OK
    assert capsys.readouterr().out == ""
