"""Tests for Phase 20-24 CLI Trac wiring:
- Phase 20: --trac-* flags, --check-trac diagnostic, EXIT_TRAC.
- Phase 21: --from-wiki fetch path + mutex validation.
- Phase 22: --to-wiki write path + --wiki-comment + mutex validation.
- Phase 23: refined error classification via _classify_trac_error().
- Phase 24: full integration coverage (mocked round-trip conversion +
  env-var-gated live integration test).
"""

import io
import sys
import xmlrpc.client
from unittest.mock import MagicMock

import pytest
import requests.exceptions  # noqa: F401

from trac_mcp_server.cli.convert import (
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    EXIT_TRAC,
    EXIT_USAGE_ERROR,
    main,
)
from trac_mcp_server.core.client import TracClient

# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------

_TEST_URL = "https://trac.example.com"
_TEST_USER = "testuser"
_TEST_PASS = "testpass"

# Default success return value for put_wiki_page mock
_PUT_OK = {
    "name": "MyPage",
    "version": 2,
    "author": _TEST_USER,
    "lastModified": "2026-07-26T12:00:00",
    "url": f"{_TEST_URL}/wiki/MyPage",
}


def _mock_valid_env(monkeypatch):
    """Set valid Trac env vars to safe test values."""
    monkeypatch.setenv("TRAC_URL", _TEST_URL)
    monkeypatch.setenv("TRAC_USERNAME", _TEST_USER)
    monkeypatch.setenv("TRAC_PASSWORD", _TEST_PASS)


def _mock_tracclient(
    validate_return="1.2.0",
    validate_side_effect=None,
    get_wiki_page_return=None,
    get_wiki_page_side_effect=None,
    put_wiki_page_return=None,
    put_wiki_page_side_effect=None,
):
    """Return a MagicMock(spec=TracClient) with configurable validate_connection.

    Args:
        validate_return: The value validate_connection() should return on success.
        validate_side_effect: If set, validate_connection() raises this instead.
        get_wiki_page_return: The value get_wiki_page() should return.
        get_wiki_page_side_effect: If set, get_wiki_page() raises this instead.
        put_wiki_page_return: The value put_wiki_page() should return on success.
        put_wiki_page_side_effect: If set, put_wiki_page() raises this instead.
    """
    mock_client_instance = MagicMock(spec=TracClient)
    if validate_side_effect is not None:
        mock_client_instance.validate_connection.side_effect = (
            validate_side_effect
        )
    else:
        mock_client_instance.validate_connection.return_value = (
            validate_return
        )
    if get_wiki_page_side_effect is not None:
        mock_client_instance.get_wiki_page.side_effect = (
            get_wiki_page_side_effect
        )
    elif get_wiki_page_return is not None:
        mock_client_instance.get_wiki_page.return_value = (
            get_wiki_page_return
        )
    if put_wiki_page_side_effect is not None:
        mock_client_instance.put_wiki_page.side_effect = (
            put_wiki_page_side_effect
        )
    elif put_wiki_page_return is not None:
        mock_client_instance.put_wiki_page.return_value = (
            put_wiki_page_return
        )
    return mock_client_instance


# ---------------------------------------------------------------------------
# 1. Help output & parser structure
# ---------------------------------------------------------------------------


def test_help_shows_all_five_trac_flags(capsys):
    """--help output includes all five --trac-* / --check-trac flags."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--trac-url" in out
    assert "--trac-username" in out
    assert "--trac-password" in out
    assert "--trac-password-file" in out
    assert "--check-trac" in out


def test_help_shows_from_wiki_and_to_wiki(capsys):
    """REGRESSION GUARD: both --from-wiki and --to-wiki must appear in --help.

    This test protects against regression that removes either wiki flag.
    Updated in Phase 22 to assert both flags are present (previously only
    guarded --from-wiki presence and --to-wiki absence).
    """
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "--from-wiki" in out
    assert "--to-wiki" in out


def test_exit_trac_constant_is_4():
    """EXIT_TRAC must equal 4."""
    assert EXIT_TRAC == 4


# ---------------------------------------------------------------------------
# 4-6. --check-trac happy path (mocked TracClient)
# ---------------------------------------------------------------------------


def test_check_trac_success_prints_url_user_sources_and_exits_ok(
    monkeypatch, capsys
):
    """Happy path: prints URL/username/sources/OK and returns EXIT_OK."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(validate_return="1.2.0")
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--check-trac"])

    assert result == EXIT_OK
    out = capsys.readouterr().out
    assert "URL:" in out
    assert _TEST_URL in out
    assert "Username:" in out
    assert _TEST_USER in out
    assert "Sources:" in out
    assert "environment variables" in out
    assert "OK (Trac API version 1.2.0)" in out


def test_check_trac_success_never_prints_password(monkeypatch, capsys):
    """CRITICAL (Pitfall 3): the password must never appear in stdout or stderr."""
    secret = "s3cret_XY!zz"
    monkeypatch.setenv("TRAC_URL", _TEST_URL)
    monkeypatch.setenv("TRAC_USERNAME", _TEST_USER)
    monkeypatch.setenv("TRAC_PASSWORD", secret)
    mock_instance = _mock_tracclient(validate_return="1.2.0")
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    main(["--check-trac"])

    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


def test_check_trac_shows_insecure_warning_when_config_insecure(
    monkeypatch, capsys
):
    """When config.insecure is True, --check-trac prints 'verification DISABLED'."""
    _mock_valid_env(monkeypatch)
    monkeypatch.setenv("TRAC_INSECURE", "true")
    mock_instance = _mock_tracclient(validate_return="1.2.0")
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--check-trac"])

    assert result == EXIT_OK
    out = capsys.readouterr().out
    assert "verification DISABLED" in out


# ---------------------------------------------------------------------------
# 7-10. --check-trac failure paths
# ---------------------------------------------------------------------------


def test_check_trac_auth_missing_prints_friendly_error_and_exits_4(
    monkeypatch, tmp_path, capsys
):
    """No credentials: friendly two-line error to stderr, exit 4, no Traceback."""
    for var in (
        "TRAC_URL",
        "TRAC_USERNAME",
        "TRAC_PASSWORD",
        "TRAC_MCP_CONFIG",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # avoid picking up a real YAML

    # Suppress load_dotenv() so a real .env file in the repo root cannot
    # re-inject credentials that monkeypatch.delenv() just removed.
    monkeypatch.setattr(
        "trac_mcp_server.config_bootstrap.load_dotenv",
        lambda: None,
    )

    result = main(["--check-trac"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert err.startswith("trac-convert: no Trac credentials found.")
    assert "TRAC_URL" in err
    assert "TRAC_USERNAME" in err
    assert "TRAC_PASSWORD" in err
    assert ".trac_mcp/config.yaml" in err
    assert "Traceback" not in err


def test_check_trac_ping_auth_fault_classified(monkeypatch, capsys):
    """XML-RPC generic fault is classified as 'Trac fault' (Phase 23: refactored
    from 'authentication failed' to the shared helper's generic Fault branch)."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        validate_side_effect=xmlrpc.client.Fault(403, "Forbidden")
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--check-trac"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "Trac fault" in err
    assert "Forbidden" in err


def test_check_trac_ping_ssl_error_classified(monkeypatch, capsys):
    """SSL error is classified and mentions 'SSL'."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        validate_side_effect=requests.exceptions.SSLError("bad cert")
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--check-trac"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "SSL" in err


def test_check_trac_ping_connection_error_classified(
    monkeypatch, capsys
):
    """Connection error is classified and mentions 'cannot reach' and the URL."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        validate_side_effect=requests.exceptions.ConnectionError(
            "refused"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--check-trac"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "cannot reach" in err
    assert _TEST_URL in err


# ---------------------------------------------------------------------------
# 11-12. Password-file precedence
# ---------------------------------------------------------------------------


def test_password_file_precedes_password_flag(monkeypatch, tmp_path):
    """--trac-password-file takes precedence over --trac-password."""
    pw_file = tmp_path / "pw.txt"
    pw_file.write_text("filepass\n", encoding="utf-8")

    monkeypatch.setenv("TRAC_URL", _TEST_URL)
    monkeypatch.setenv("TRAC_USERNAME", _TEST_USER)
    # No TRAC_PASSWORD env - password comes from file or flag

    captured_config = []

    def mock_client_factory(cfg):
        captured_config.append(cfg)
        return _mock_tracclient(validate_return="1.2.0")

    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        mock_client_factory,
    )

    result = main(
        [
            "--check-trac",
            "--trac-password",
            "flagpass",
            "--trac-password-file",
            str(pw_file),
        ]
    )

    assert result == EXIT_OK
    assert len(captured_config) == 1
    assert captured_config[0].password == "filepass"


def test_password_file_read_failure_exits_4(monkeypatch, capsys):
    """Non-existent password file exits 4 with a diagnostic on stderr."""
    _mock_valid_env(monkeypatch)

    result = main(
        ["--check-trac", "--trac-password-file", "/nonexistent/path"]
    )

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "cannot read --trac-password-file" in err


# ---------------------------------------------------------------------------
# 13-14. Dispatch and regression guards
# ---------------------------------------------------------------------------


def test_check_trac_does_not_require_to_flag(monkeypatch):
    """--check-trac works without --to (dispatch happens before --to check)."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(validate_return="1.2.0")
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--check-trac"])  # no --to

    assert result == EXIT_OK


def test_check_trac_with_from_wiki_exits_error(capsys):
    """--check-trac + --from-wiki is rejected with EXIT_RUNTIME_ERROR."""
    result = main(["--check-trac", "--from-wiki", "WikiStart"])

    assert result == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert "--check-trac" in err
    assert "--from-wiki" in err


def test_check_trac_with_to_wiki_exits_error(capsys):
    """--check-trac + --to-wiki is rejected with EXIT_RUNTIME_ERROR."""
    result = main(["--check-trac", "--to-wiki", "WikiStart"])

    assert result == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert "--check-trac" in err
    assert "--to-wiki" in err


def test_normal_conversion_still_requires_to_flag(capsys):
    """REGRESSION GUARD: normal conversion without --to exits EXIT_USAGE_ERROR."""
    result = main(["--from", "md"])  # no --to, no --check-trac

    assert result == EXIT_USAGE_ERROR
    err = capsys.readouterr().err
    assert "--to" in err


# ---------------------------------------------------------------------------
# --- Phase 21: --from-wiki tests ---
# ---------------------------------------------------------------------------


def test_help_shows_from_wiki_page_metavar(capsys):
    """--from-wiki entry in --help shows PAGE as the metavar."""
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "PAGE" in out


def test_from_wiki_mutex_with_positional_file_exits_1_runtime_error(
    capsys,
):
    """--from-wiki and FILE positional are mutually exclusive (exit 1)."""
    from trac_mcp_server.cli.convert import EXIT_RUNTIME_ERROR

    result = main(["--from-wiki", "Foo", "somefile.md", "--to", "md"])

    assert result == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert "--from-wiki and FILE are mutually exclusive" in err


def test_from_wiki_mutex_with_from_clipboard_exits_1_runtime_error(
    capsys,
):
    """--from-wiki and --from-clipboard are mutually exclusive (exit 1)."""
    from trac_mcp_server.cli.convert import EXIT_RUNTIME_ERROR

    result = main(
        ["--from-wiki", "Foo", "--from-clipboard", "--to", "md"]
    )

    assert result == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert (
        "--from-wiki and --from-clipboard are mutually exclusive" in err
    )


def test_from_wiki_happy_path_prints_converted_markdown_to_stdout(
    monkeypatch, capsys
):
    """--from-wiki fetches TracWiki, converts to Markdown, prints to stdout."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_return="= Heading =\n\nsome tracwiki body"
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--from-wiki", "MyPage", "--to", "md"])

    assert result == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("# Heading")


def test_from_wiki_to_tracwiki_is_passthrough_no_conversion(
    monkeypatch, capsys
):
    """--from-wiki + --to tracwiki is a pass-through (source == target)."""
    raw_page = "= Heading =\n\nsome tracwiki body"
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(get_wiki_page_return=raw_page)
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--from-wiki", "MyPage", "--to", "tracwiki"])

    assert result == EXIT_OK
    out = capsys.readouterr().out
    assert out == raw_page


def test_from_wiki_silently_overrides_explicit_from_md(
    monkeypatch, capsys
):
    """--from-wiki forces source_format=tracwiki, silently ignoring --from md."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_return="= Heading =\n\nbody"
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    # --from md is explicitly set but should be silently overridden
    result = main(
        ["--from-wiki", "MyPage", "--from", "md", "--to", "md"]
    )

    assert result == EXIT_OK
    out = capsys.readouterr().out
    # If --from md were honoured, tracwiki "= Heading =" would be treated
    # as markdown and output "= Heading =" verbatim (passthrough or literal).
    # With override to tracwiki→md, it must become "# Heading".
    assert "# Heading" in out


def test_from_wiki_auth_missing_prints_friendly_error_and_exits_4(
    monkeypatch, tmp_path, capsys
):
    """No Trac credentials: friendly error, exit 4, no Traceback."""
    for var in (
        "TRAC_URL",
        "TRAC_USERNAME",
        "TRAC_PASSWORD",
        "TRAC_MCP_CONFIG",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "trac_mcp_server.config_bootstrap.load_dotenv",
        lambda: None,
    )

    result = main(["--from-wiki", "MyPage", "--to", "md"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "no Trac credentials found" in err
    assert "docs/reference/cli.md" in err
    assert "Traceback" not in err


def test_from_wiki_page_not_found_prints_friendly_error_and_exits_4(
    monkeypatch, capsys
):
    """Fault(1) -> 'wiki page not found: <page name>'."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_side_effect=xmlrpc.client.Fault(
            1, "page not found"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--from-wiki", "NoSuchPage", "--to", "md"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "wiki page not found: NoSuchPage" in err


def test_from_wiki_other_fault_reports_fault_string_and_exits_4(
    monkeypatch, capsys
):
    """Fault(403, 'permission denied') -> 'permission denied' message (Phase 23:
    faultString contains 'denied', so the permission-denied branch fires instead
    of the generic 'Trac fault' branch)."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_side_effect=xmlrpc.client.Fault(
            403, "permission denied"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--from-wiki", "SomePage", "--to", "md"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "permission denied" in err
    assert "wiki page not found" not in err


def test_from_wiki_connection_error_reports_reach_and_exits_4(
    monkeypatch, capsys
):
    """ConnectionError -> 'cannot reach Trac at <url>' message."""
    import requests.exceptions

    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_side_effect=requests.exceptions.ConnectionError(
            "connection refused"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--from-wiki", "SomePage", "--to", "md"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "cannot reach Trac at" in err
    assert _TEST_URL in err


def test_from_wiki_ssl_error_reports_ssl_and_exits_4(
    monkeypatch, capsys
):
    """SSLError -> 'SSL error' message."""
    import requests.exceptions

    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_side_effect=requests.exceptions.SSLError(
            "bad cert"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--from-wiki", "SomePage", "--to", "md"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "SSL error" in err


def test_from_wiki_password_never_printed(monkeypatch, capsys):
    """CRITICAL: password must never appear in stdout or stderr."""
    secret = "s3cret_XY!zz"
    monkeypatch.setenv("TRAC_URL", _TEST_URL)
    monkeypatch.setenv("TRAC_USERNAME", _TEST_USER)
    monkeypatch.setenv("TRAC_PASSWORD", secret)
    mock_instance = _mock_tracclient(
        get_wiki_page_return="= Heading =\n\nbody"
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    main(["--from-wiki", "MyPage", "--to", "md"])

    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


def test_from_wiki_output_to_clipboard_writes_converted_text(
    monkeypatch,
):
    """--from-wiki + --to-clipboard writes converted markdown to clipboard."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_return="= Heading =\n\nbody"
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    clipboard_calls = []
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.write_clipboard",
        lambda text: clipboard_calls.append(text),
    )

    result = main(
        ["--from-wiki", "MyPage", "--to", "md", "--to-clipboard"]
    )

    assert result == EXIT_OK
    assert len(clipboard_calls) == 1
    assert clipboard_calls[0].startswith("# Heading")


# ---------------------------------------------------------------------------
# --- Phase 22: --to-wiki tests ---
# ---------------------------------------------------------------------------


def test_help_shows_to_wiki_page_metavar(capsys):
    """--to-wiki entry in --help shows PAGE as the metavar."""
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "--to-wiki PAGE" in out


def test_help_shows_wiki_comment_flag_with_default(capsys):
    """--wiki-comment appears in --help with default value described."""
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "--wiki-comment" in out
    assert "Updated via trac-convert" in out


def test_to_wiki_mutex_with_output_file_exits_1_runtime_error(capsys):
    """--to-wiki and -o/--output are mutually exclusive (exit 1)."""
    result = main(["--to-wiki", "P", "-o", "/tmp/x.md"])

    assert result == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert "--to-wiki and --output are mutually exclusive" in err


def test_to_wiki_mutex_with_to_clipboard_exits_1_runtime_error(capsys):
    """--to-wiki and --to-clipboard are mutually exclusive (exit 1)."""
    result = main(["--to-wiki", "P", "--to-clipboard"])

    assert result == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert "--to-wiki and --to-clipboard are mutually exclusive" in err


def test_to_wiki_alone_does_not_require_to_flag(monkeypatch):
    """--to-wiki without --to does not fail with '--to is required'."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(put_wiki_page_return=_PUT_OK)
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "MyPage"])

    assert result == EXIT_OK
    assert mock_instance.put_wiki_page.called is True


def test_to_wiki_happy_path_writes_converted_tracwiki_via_put_wiki_page(
    monkeypatch, capsys
):
    """--to-wiki converts md input to TracWiki and writes via put_wiki_page."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(put_wiki_page_return=_PUT_OK)
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("# Heading\n\nBody"))

    result = main(["--to-wiki", "MyPage", "--from", "md"])

    assert result == EXIT_OK
    call_args = mock_instance.put_wiki_page.call_args
    assert call_args.args[0] == "MyPage"
    assert call_args.args[1].startswith("= Heading =")
    assert call_args.args[2] == "Updated via trac-convert"
    assert capsys.readouterr().out == ""


def test_to_wiki_from_tracwiki_is_passthrough(monkeypatch):
    """--to-wiki with tracwiki input passes content verbatim (source==target)."""
    raw = "= Existing =\n\nAs-is"
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(put_wiki_page_return=_PUT_OK)
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))

    result = main(["--to-wiki", "P", "--from", "tracwiki"])

    assert result == EXIT_OK
    assert mock_instance.put_wiki_page.call_args.args[1] == raw


def test_to_wiki_silently_overrides_explicit_to_md(monkeypatch):
    """--to-wiki silently overrides --to md: content is TracWiki, not Markdown."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(put_wiki_page_return=_PUT_OK)
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("# Heading\n\nBody"))

    result = main(["--to-wiki", "P", "--from", "md", "--to", "md"])

    assert result == EXIT_OK
    # Despite --to md, content must start with TracWiki heading syntax
    content = mock_instance.put_wiki_page.call_args.args[1]
    assert content.startswith("= ")


def test_to_wiki_default_comment_is_updated_via_trac_convert(
    monkeypatch,
):
    """Default --wiki-comment is 'Updated via trac-convert'."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(put_wiki_page_return=_PUT_OK)
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "MyPage"])

    assert result == EXIT_OK
    assert (
        mock_instance.put_wiki_page.call_args.args[2]
        == "Updated via trac-convert"
    )


def test_to_wiki_wiki_comment_flag_overrides_default_comment(
    monkeypatch,
):
    """--wiki-comment overrides the default comment string."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(put_wiki_page_return=_PUT_OK)
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "P", "--wiki-comment", "my custom msg"])

    assert result == EXIT_OK
    assert (
        mock_instance.put_wiki_page.call_args.args[2] == "my custom msg"
    )


def test_to_wiki_auth_missing_prints_friendly_error_and_exits_4(
    monkeypatch, tmp_path, capsys
):
    """No Trac credentials: friendly error to stderr, exit 4, no Traceback."""
    for var in (
        "TRAC_URL",
        "TRAC_USERNAME",
        "TRAC_PASSWORD",
        "TRAC_MCP_CONFIG",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "trac_mcp_server.config_bootstrap.load_dotenv",
        lambda: None,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "P"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "no Trac credentials found" in err
    assert "docs/reference/cli.md" in err
    assert "Traceback" not in err


def test_to_wiki_permission_denied_fault_reports_and_exits_4(
    monkeypatch, capsys
):
    """Fault(403, 'PERMISSION_DENIED') from put_wiki_page -> 'permission denied'
    message, exit 4. (Phase 23: faultString contains 'DENIED', so permission-
    denied branch fires instead of the old generic 'Trac fault' branch.)"""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        put_wiki_page_side_effect=xmlrpc.client.Fault(
            403, "PERMISSION_DENIED"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "P"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "permission denied" in err
    assert "PERMISSION_DENIED" in err


def test_to_wiki_not_modified_value_error_reports_and_exits_4(
    monkeypatch, capsys
):
    """ValueError('Page not modified') -> 'wiki write failed' with reason, exit 4.
    (Phase 23: ValueError goes through helper fallback which uses 'wiki write
    failed' prefix instead of the old 'wiki write rejected' prefix.)"""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        put_wiki_page_side_effect=ValueError(
            "Page not modified (content identical)"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "P"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "wiki write" in err
    assert "Page not modified" in err


def test_to_wiki_invalid_page_name_value_error_reports_and_exits_4(
    monkeypatch, capsys
):
    """ValueError('Invalid page name') -> 'wiki write failed' with reason, exit 4.
    (Phase 23: ValueError goes through helper fallback which uses 'wiki write
    failed' prefix instead of the old 'wiki write rejected' prefix.)"""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        put_wiki_page_side_effect=ValueError(
            "Invalid page name: contains illegal characters"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "P"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "wiki write" in err
    assert "Invalid page name" in err


def test_to_wiki_connection_error_reports_reach_and_exits_4(
    monkeypatch, capsys
):
    """ConnectionError -> 'cannot reach Trac at <url>', exit 4."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        put_wiki_page_side_effect=requests.exceptions.ConnectionError(
            "refused"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "P"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "cannot reach Trac at" in err
    assert _TEST_URL in err


def test_to_wiki_ssl_error_reports_ssl_and_exits_4(monkeypatch, capsys):
    """SSLError -> 'SSL error' message, exit 4."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        put_wiki_page_side_effect=requests.exceptions.SSLError(
            "bad cert"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "P"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "SSL error" in err


def test_to_wiki_password_never_printed(monkeypatch, capsys):
    """CRITICAL: password must never appear in stdout or stderr on --to-wiki."""
    secret = "s3cret_XY!zz"
    _mock_valid_env(monkeypatch)
    monkeypatch.setenv("TRAC_PASSWORD", secret)
    mock_instance = _mock_tracclient(put_wiki_page_return=_PUT_OK)
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    main(["--to-wiki", "P"])

    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


def test_to_wiki_verbose_prints_wrote_info_line_with_name_and_version(
    monkeypatch, capsys
):
    """--verbose emits 'info: wrote ... to wiki page NAME (vN)' on stderr."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(put_wiki_page_return=_PUT_OK)
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "MyPage", "-v"])

    assert result == EXIT_OK
    err = capsys.readouterr().err
    assert "info: wrote" in err
    assert "MyPage" in err
    assert "v2" in err


def test_to_wiki_stdout_is_empty_on_success(monkeypatch, capsys):
    """--to-wiki success produces no stdout output (mirrors --to-clipboard)."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(put_wiki_page_return=_PUT_OK)
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "MyPage"])

    assert result == EXIT_OK
    assert capsys.readouterr().out == ""


def test_from_wiki_to_to_wiki_end_to_end_copies_page_verbatim(
    monkeypatch,
):
    """Phase 21+22 round-trip: --from-wiki + --to-wiki copies page verbatim.

    source==target==tracwiki triggers the passthrough branch, so content
    is byte-for-byte identical.
    """
    source_content = "= Source =\n\nbody"
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_return=source_content,
        put_wiki_page_return=_PUT_OK,
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(
        ["--from-wiki", "SourcePage", "--to-wiki", "DestPage"]
    )

    assert result == EXIT_OK
    assert mock_instance.get_wiki_page.call_args.args[0] == "SourcePage"
    assert mock_instance.put_wiki_page.call_args.args[0] == "DestPage"
    assert (
        mock_instance.put_wiki_page.call_args.args[1] == source_content
    )


# ---------------------------------------------------------------------------
# Phase 23: refined error classification tests
# ---------------------------------------------------------------------------


def test_from_wiki_timeout_reports_timed_out_and_exits_4(
    monkeypatch, capsys
):
    """ReadTimeout from get_wiki_page -> 'timed out' message, exit 4."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_side_effect=requests.exceptions.ReadTimeout(
            "slow"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--from-wiki", "MyPage", "--to", "md"])

    assert result == EXIT_TRAC
    captured = capsys.readouterr()
    assert "trac-convert:" in captured.err
    assert "timed out" in captured.err
    assert _TEST_URL in captured.err


def test_from_wiki_connect_timeout_classified_as_timeout(
    monkeypatch, capsys
):
    """ConnectTimeout from get_wiki_page -> 'timed out' (NOT 'cannot reach').

    Pins MRO ordering: ConnectTimeout inherits from both ConnectionError and
    Timeout; the Timeout branch in _classify_trac_error must fire first.
    """
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_side_effect=requests.exceptions.ConnectTimeout(
            "slow-connect"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--from-wiki", "MyPage", "--to", "md"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "timed out" in err
    assert "cannot reach Trac" not in err


def test_from_wiki_permission_denied_fault_classified(
    monkeypatch, capsys
):
    """Fault(403, 'PERMISSION_DENIED: WIKI_VIEW required') -> 'permission denied'."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_side_effect=xmlrpc.client.Fault(
            403, "PERMISSION_DENIED: WIKI_VIEW required"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--from-wiki", "MyPage", "--to", "md"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "permission denied" in err
    assert "PERMISSION_DENIED: WIKI_VIEW required" in err


def test_to_wiki_fault_not_found_reports_page_not_found(
    monkeypatch, capsys
):
    """Fault(1, 'does not exist') from put_wiki_page -> 'wiki page not found: <page>'."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        put_wiki_page_side_effect=xmlrpc.client.Fault(
            1, "does not exist"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "TargetPage"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "wiki page not found:" in err
    assert "TargetPage" in err


def test_to_wiki_permission_denied_fault_classified(
    monkeypatch, capsys
):
    """Fault(403, 'PERMISSION_DENIED: WIKI_MODIFY required') -> 'permission denied'."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        put_wiki_page_side_effect=xmlrpc.client.Fault(
            403, "PERMISSION_DENIED: WIKI_MODIFY required"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "MyPage"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "permission denied" in err
    assert "PERMISSION_DENIED: WIKI_MODIFY required" in err


def test_to_wiki_timeout_reports_timed_out_and_exits_4(
    monkeypatch, capsys
):
    """ReadTimeout from put_wiki_page -> 'timed out' and URL host, exit 4."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        put_wiki_page_side_effect=requests.exceptions.ReadTimeout(
            "slow-write"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "MyPage"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "timed out" in err
    assert _TEST_URL in err


def test_to_wiki_value_error_still_reports_write_rejected(
    monkeypatch, capsys
):
    """ValueError('Page not modified ...') -> fallback preserves the exception string.

    Pins Phase 22's ValueError-write-rejected path after Phase 23 refactor:
    the fallback branch of _classify_trac_error preserves the full exception
    string so users still see the actual rejection reason.
    """
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        put_wiki_page_side_effect=ValueError(
            "Page not modified (content identical)"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("hello"))

    result = main(["--to-wiki", "MyPage"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "Page not modified" in err


def test_check_trac_permission_denied_ping_classified(
    monkeypatch, capsys
):
    """Fault(403, 'permission denied') from validate_connection -> 'permission denied'.

    Pins that _check_trac uses the same classifier as the wiki paths (not the
    old hard-coded 'authentication failed' message).
    """
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        validate_side_effect=xmlrpc.client.Fault(
            403, "permission denied"
        )
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--check-trac"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "permission denied" in err
    assert "authentication failed" not in err


def test_check_trac_timeout_ping_classified(monkeypatch, capsys):
    """Timeout from validate_connection -> 'timed out', exit 4."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        validate_side_effect=requests.exceptions.Timeout("ping timeout")
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--check-trac"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "timed out" in err


def test_check_trac_generic_fault_still_reports_fault_string(
    monkeypatch, capsys
):
    """Fault(500, 'Backend on fire') -> generic fault branch preserves faultString."""
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        validate_side_effect=xmlrpc.client.Fault(500, "Backend on fire")
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    result = main(["--check-trac"])

    assert result == EXIT_TRAC
    err = capsys.readouterr().err
    assert "Backend on fire" in err


def test_classify_trac_error_timeout_precedes_connection_error():
    """Direct unit test: ConnectTimeout classified as 'timed out', not 'cannot reach'.

    ConnectTimeout inherits from both ConnectionError and Timeout. The helper
    must check Timeout first (MRO ordering constraint).
    """
    from trac_mcp_server.cli.convert import _classify_trac_error

    exc = requests.exceptions.ConnectTimeout("x")
    msg = _classify_trac_error(exc, "https://trac.example")
    assert "timed out" in msg
    assert "cannot reach" not in msg


def test_classify_trac_error_password_never_included():
    """Direct unit test: helper signature is password-free.

    The helper receives no password argument. This test verifies that the
    faultString is included (expected) but no fabricated credential leak is
    possible — the helper simply has no parameter for it.
    """
    from trac_mcp_server.cli.convert import _classify_trac_error

    exc = xmlrpc.client.Fault(0, "secret-hunter-2000 rejected")
    msg = _classify_trac_error(
        exc, "https://trac.example", page_name="SomePage"
    )
    # faultString is preserved in the returned message
    assert "secret-hunter-2000" in msg
    # No password argument exists in the helper signature — this call
    # proves the contract: password cannot come out because it never goes in.


# ---------------------------------------------------------------------------
# Phase 24: full integration test coverage
# ---------------------------------------------------------------------------


def test_from_wiki_convert_edit_to_wiki_end_to_end_exercises_conversion(
    monkeypatch, capsys
):
    """Trac→MD→edit→Trac round-trip: fetch tracwiki, convert to MD,
    edit the MD text, convert back to tracwiki, put it — with a
    mocked TracClient. Pins the goal wording of Phase 24
    ('Trac→MD→edit→Trac roundtrip') by exercising the CONVERTER
    on both legs (not just the passthrough case Phase 22 covered).

    Strategy: run two main() calls sharing the same mocked TracClient.
      1. main(["--from-wiki", "SourcePage", "--to", "md"]) →
         captured stdout is TracWiki-of-SourcePage converted to MD.
      2. Locally 'edit' by appending a marker line.
      3. main(["--to-wiki", "DestPage", "--from", "md"]) with the
         edited MD on stdin → mocked put_wiki_page receives
         TracWiki-of-edited-MD.

    Assertions verify:
    - Leg 1 exit 0, stdout starts with "# " (proves tracwiki→md ran).
    - Leg 2 exit 0.
    - put_wiki_page was called with the DEST page name.
    - The content argument to put_wiki_page:
        * starts with "= " (proves md→tracwiki ran — not the raw MD).
        * contains the marker line from the edit step (proves the
          edited text, not the original, was written).
    - The comment argument is the default "Updated via trac-convert".
    """
    source_tracwiki = "= Source Heading =\n\nOriginal body.\n"
    marker = "Edited line added by test\n"

    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_return=source_tracwiki,
        put_wiki_page_return=_PUT_OK,
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )

    # Leg 1: fetch + convert tracwiki → md, capture on stdout.
    result1 = main(["--from-wiki", "SourcePage", "--to", "md"])
    assert result1 == EXIT_OK
    md_from_leg1 = capsys.readouterr().out
    # tracwiki_to_markdown turns "= X =" into "# X"
    assert md_from_leg1.lstrip().startswith("# ")
    assert "Source Heading" in md_from_leg1

    # Edit: append a marker line to the MD.
    edited_md = md_from_leg1 + marker

    # Leg 2: convert md → tracwiki + put via --to-wiki, edited MD on stdin.
    monkeypatch.setattr("sys.stdin", io.StringIO(edited_md))
    result2 = main(["--to-wiki", "DestPage", "--from", "md"])
    assert result2 == EXIT_OK

    put_args = mock_instance.put_wiki_page.call_args.args
    assert put_args[0] == "DestPage"
    # convert_with_warnings turns "# X" into "= X ="
    assert put_args[1].startswith("= ")
    # The edited marker survives the md → tracwiki conversion.
    assert marker.strip() in put_args[1]
    # Default comment is the Phase 22 constant.
    assert put_args[2] == "Updated via trac-convert"


# ---------------------------------------------------------------------------
# Phase 24: live integration test (gated by --run-live)
#
# Requires:
#   - `pytest --run-live` on the command line
#   - TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD env vars pointing at a
#     real Trac instance
#   - Optional: TRAC_TEST_WIKI_PAGE env var (default:
#     "TracConvertLiveTest") — the test OVERWRITES this page with a
#     fixed seed body on every run, creating it if absent, so point
#     it only at a scratch page the test user has WIKI_MODIFY on.
#     Never point it at a page whose content matters.
#   - Optional: TRAC_INSECURE=1 to disable SSL verification for
#     self-signed test servers
#
# Mirrors the convention used by
# tests/test_mcp/tools/test_wiki_attachment.py:578-644.
# ---------------------------------------------------------------------------


# Body the scratch page is reset to at the start of every live run.
# Authored as Markdown because it is written through --from md.
_LIVE_SEED_BODY = (
    "Scratch page for `trac-convert`'s live round-trip test in\n"
    "`tests/test_cli_convert_trac.py`.\n"
    "\n"
    "Everything below this line is machine-appended by that test\n"
    "and carries no meaning. The page is reset to this header at\n"
    "the start of every run.\n"
    "\n"
    "Created for auto_pm:#93.\n"
)


def _main_with_stdin(argv, text):
    """Run main(argv) with `text` on stdin, restoring stdin after."""
    original_stdin = sys.stdin
    sys.stdin = io.StringIO(text)
    try:
        return main(argv)
    finally:
        sys.stdin = original_stdin


@pytest.mark.live
def test_live_round_trip_check_from_edit_to(capsys):
    """Live round-trip: --check-trac → --from-wiki → edit → --to-wiki.

    Requires --run-live and TRAC_URL/USERNAME/PASSWORD env vars
    pointing at a real Trac instance. Uses TRAC_TEST_WIKI_PAGE
    (default 'TracConvertLiveTest') as the scratch target so
    WikiStart and production pages are never modified.

    Sequence:
      1. --check-trac → exit 0, stdout mentions "OK (Trac API version".
      2. --to-wiki <page> with a fixed seed body → exit 0. Creates
         the page when absent and resets it when present, so the
         test is self-contained and the page cannot grow run over
         run.
      3. --from-wiki <page> --to md → exit 0, MD content captured.
      4. Append a timestamped marker to the MD (proves edit fidelity).
      5. --to-wiki <page> --from md with edited MD on stdin →
         exit 0, verbose stderr line mentions the page name.
      6. --from-wiki <page> --to md (re-read) → exit 0, MD contains
         the marker written in step 5 (proves the round-trip actually
         persisted).

    Seeds the page rather than deleting it. Trac keeps every version,
    so the page's history still grows one version per run, but the
    rendered page stays at the seed body plus a single marker. This
    departs from test_wiki_attachment.py's "pages persist, ops can
    prune manually" convention, which nothing ever pruned — see #84.

    IMPORTANT: The test does NOT unset the real env vars. It reads
    them via os.environ.get and relies on the actual live Trac.
    """
    import os
    import time

    if not (
        os.environ.get("TRAC_URL")
        and os.environ.get("TRAC_USERNAME")
        and os.environ.get("TRAC_PASSWORD")
    ):
        pytest.skip(
            "TRAC_URL / TRAC_USERNAME / TRAC_PASSWORD must be set"
            " for --run-live tests"
        )

    page_name = os.environ.get(
        "TRAC_TEST_WIKI_PAGE", "TracConvertLiveTest"
    )

    # Step 1: --check-trac
    rc = main(["--check-trac"])
    assert rc == EXIT_OK, "live --check-trac should succeed"
    captured = capsys.readouterr()
    assert "OK (Trac API version" in captured.out

    # Step 2: seed the scratch page to a known body. Creates it if
    #   it does not exist, so the test needs no manual fixture, and
    #   resets it if it does, so markers cannot accumulate.
    rc = _main_with_stdin(
        ["--to-wiki", page_name, "--from", "md"], _LIVE_SEED_BODY
    )
    assert rc == EXIT_OK, (
        f"live seed of {page_name} should succeed"
        f" (does the test user have WIKI_MODIFY?)"
    )
    capsys.readouterr()

    # Step 3: --from-wiki → md on stdout
    rc = main(["--from-wiki", page_name, "--to", "md"])
    assert rc == EXIT_OK, f"live --from-wiki {page_name} should succeed"
    md_original = capsys.readouterr().out
    assert md_original, (
        "live --from-wiki should return non-empty content"
    )

    # Step 4: local edit — append a timestamped marker.
    marker = f"\n\nLive round-trip marker {time.time():.6f}\n"
    md_edited = md_original + marker

    # Step 5: --to-wiki with edited MD on stdin, verbose to check
    #   "info: wrote" line surfaces on stderr.
    rc = _main_with_stdin(
        ["--to-wiki", page_name, "--from", "md", "-v"], md_edited
    )
    assert rc == EXIT_OK, (
        f"live --to-wiki {page_name} should succeed"
        f" (does the test user have WIKI_MODIFY?)"
    )
    captured = capsys.readouterr()
    assert "info: wrote" in captured.err
    assert page_name in captured.err

    # Step 6: re-fetch and verify the marker landed.
    rc = main(["--from-wiki", page_name, "--to", "md"])
    assert rc == EXIT_OK
    md_reread = capsys.readouterr().out
    assert marker.strip() in md_reread, (
        "marker written in step 5 must appear in the re-read page"
    )
