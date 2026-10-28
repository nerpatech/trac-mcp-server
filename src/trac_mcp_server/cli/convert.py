"""trac-convert CLI entry point.

This module is the Phase 11 scaffold for the ``trac-convert`` binary.
Phases 12-17 layer format flags, I/O modes, stdin/stdout wiring,
file I/O, clipboard I/O, converter options, error handling, and
verbosity flags (--quiet, --verbose) on top of this skeleton.
# Phase 20 adds Trac wiring: --trac-* flags, --check-trac, EXIT_TRAC.
# Phase 21 adds --from-wiki: fetch TracWiki pages via TracClient.
# Phase 22 adds --to-wiki + --wiki-comment: write TracWiki pages via TracClient.
# Phase 23 extracts a shared error-classification helper (Timeout +
#   permission-denied sub-classification) used uniformly by all three
#   Trac-facing functions.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pyperclip

from .. import __version__
from ..config_bootstrap import bootstrap_config
from ..converters import (
    detect_format_heuristic,
    tracwiki_to_markdown,
)
from ..converters.common import (
    ConversionResult,
    describe_indentation_loss,
    find_code_block_indentation_loss,
)
from ..converters.markdown_to_tracwiki import convert_with_warnings
from ..core.client import TracClient

# ---------------------------------------------------------------------------
# Exit-code constants
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1  # I/O, clipboard, mutual-exclusion
EXIT_USAGE_ERROR = 2  # argparse default (not raised by us directly)
EXIT_CONVERSION_ERROR = 3  # exception raised inside convert_text()
EXIT_TRAC = 4  # Trac client errors (auth, network, protocol) — used by --check-trac and Phases 21-23


# ---------------------------------------------------------------------------
# Clipboard helpers (subprocess fallback for headless terminals)
# ---------------------------------------------------------------------------
#
# pyperclip's built-in detection can fail on Linux terminals where no
# X11/Wayland clipboard tool is installed AND on remote/container shells
# where DISPLAY is set but the X socket is unreachable. To recover
# gracefully we try common command-line clipboard tools directly via
# subprocess before falling back to pyperclip. This way:
#
#   * The user can install wl-clipboard / xclip / xsel and things just
#     work, even if pyperclip's own detection is unhappy.
#   * On macOS/Windows (pbcopy/pbpaste, clip.exe), pyperclip still
#     handles the happy path.
#   * On total failure we raise a single custom exception whose message
#     is actionable in headless terminals (mentions --from-file and
#     stdin as clipboard-free alternatives).

_CLIPBOARD_INSTALL_HINT = (
    "install a clipboard tool (Linux: wl-clipboard, xclip, or xsel;"
    " macOS/Windows are handled automatically) or use --from-file PATH"
    " or pipe input on stdin instead"
)


class ClipboardUnavailableError(RuntimeError):
    """Raised when no clipboard read/write mechanism can be used.

    Distinct from pyperclip.PyperclipException so the CLI layer can
    format a single actionable error message regardless of which
    backend failed.
    """


def _clipboard_read_candidates() -> list[list[str]]:
    """Return an ordered list of subprocess argv candidates for reading.

    Order of preference:
      1. wl-paste (Wayland) if WAYLAND_DISPLAY is set
      2. xclip    (X11)    if DISPLAY is set
      3. xsel     (X11)    if DISPLAY is set
      4. pbpaste  (macOS)  unconditional; shutil.which filters
      5. powershell Get-Clipboard (Windows) unconditional; shutil.which filters

    Each candidate is only kept if its executable is on PATH.
    """
    candidates: list[list[str]] = []
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        candidates.append(["wl-paste", "--no-newline"])
    if os.environ.get("DISPLAY"):
        if shutil.which("xclip"):
            candidates.append(
                ["xclip", "-selection", "clipboard", "-o"]
            )
        if shutil.which("xsel"):
            candidates.append(["xsel", "--clipboard", "--output"])
    if shutil.which("pbpaste"):
        candidates.append(["pbpaste"])
    if shutil.which("powershell.exe"):
        candidates.append(
            ["powershell.exe", "-Command", "Get-Clipboard"]
        )
    return candidates


def _clipboard_write_candidates() -> list[list[str]]:
    """Return an ordered list of subprocess argv candidates for writing.

    Same platform ordering as reads. The command reads text from stdin.
    """
    candidates: list[list[str]] = []
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        candidates.append(["wl-copy"])
    if os.environ.get("DISPLAY"):
        if shutil.which("xclip"):
            candidates.append(["xclip", "-selection", "clipboard"])
        if shutil.which("xsel"):
            candidates.append(["xsel", "--clipboard", "--input"])
    if shutil.which("pbcopy"):
        candidates.append(["pbcopy"])
    if shutil.which("clip.exe"):
        candidates.append(["clip.exe"])
    return candidates


def read_clipboard() -> str:
    """Read text from the system clipboard.

    Tries subprocess-based tools first (wl-paste, xclip, xsel, pbpaste,
    powershell Get-Clipboard) whose availability is discovered via
    shutil.which and the WAYLAND_DISPLAY / DISPLAY env vars. Falls back
    to pyperclip.paste() if no subprocess tool is available or all of
    them fail. Raises ClipboardUnavailableError if everything fails.

    Rationale: pyperclip's own detection sometimes reports "no
    mechanism" on headless terminals even when a tool is installed;
    calling the tool directly bypasses that fragility.
    """
    errors: list[str] = []
    for argv in _clipboard_read_candidates():
        try:
            completed = subprocess.run(
                argv, capture_output=True, check=True
            )
            return completed.stdout.decode("utf-8", errors="replace")
        except (
            subprocess.CalledProcessError,
            OSError,
            UnicodeDecodeError,
        ) as e:
            errors.append(f"{argv[0]}: {e}")

    # Fallback: pyperclip (macOS/Windows happy path, or if user has a
    # backend pyperclip knows about that we don't).
    try:
        return pyperclip.paste()
    except pyperclip.PyperclipException as e:
        errors.append(f"pyperclip: {e}")

    raise ClipboardUnavailableError(
        "no working clipboard mechanism found; "
        + _CLIPBOARD_INSTALL_HINT
        + (f" (attempts: {'; '.join(errors)})" if errors else "")
    )


def write_clipboard(text: str) -> None:
    """Write text to the system clipboard.

    Symmetric to read_clipboard(). Tries subprocess tools first, then
    pyperclip.copy(). Raises ClipboardUnavailableError on total
    failure.
    """
    errors: list[str] = []
    payload = text.encode("utf-8")
    for argv in _clipboard_write_candidates():
        try:
            subprocess.run(argv, input=payload, check=True)
            return
        except (subprocess.CalledProcessError, OSError) as e:
            errors.append(f"{argv[0]}: {e}")

    try:
        pyperclip.copy(text)
        return
    except pyperclip.PyperclipException as e:
        errors.append(f"pyperclip: {e}")

    raise ClipboardUnavailableError(
        "no working clipboard mechanism found; "
        + _CLIPBOARD_INSTALL_HINT
        + (f" (attempts: {'; '.join(errors)})" if errors else "")
    )


# ---------------------------------------------------------------------------
# Trac helpers (Phase 20)
# ---------------------------------------------------------------------------


def _read_password_file(path: str) -> str | None:
    """Read password from a file (single line, trimmed).

    Returns the stripped contents on success, or None on failure (after
    writing a diagnostic message to stderr).
    """
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as e:
        sys.stderr.write(
            f"trac-convert: cannot read --trac-password-file:"
            f" {path}: {e.strerror or e}\n"
        )
        return None


def _build_trac_overrides(args) -> dict:
    """Build the cli_overrides dict for bootstrap_config() from parsed args.

    Only includes non-None values so bootstrap_config's 'if overrides:'
    check works correctly (an empty dict means no CLI overrides).
    Password-file takes precedence over --trac-password flag.
    Returns the filtered dict, or None if password-file read failed.
    """
    overrides: dict = {}
    if args.trac_url is not None:
        overrides["url"] = args.trac_url
    if args.trac_username is not None:
        overrides["username"] = args.trac_username

    if args.trac_password_file is not None:
        # File takes precedence; returns None on read failure (signal to caller)
        password = _read_password_file(args.trac_password_file)
        if password is None:
            return None  # type: ignore[return-value]  # caller checks for None
        overrides["password"] = password
    elif args.trac_password is not None:
        overrides["password"] = args.trac_password

    return overrides


def _classify_trac_error(
    exc: BaseException,
    url: str,
    page_name: str | None = None,
    action: str = "wiki operation",
) -> str:
    """Return a human-readable stderr message for a Trac-facing error.

    Sub-classification precedence:
      1. xmlrpc.client.Fault:
         - faultCode == 1 OR "not found"/"does not exist" in faultString
           -> "wiki page not found: <page_name>" (falls back to
              "resource not found (fault 1): <faultString>" when
              page_name is None)
         - "permission"/"denied" in faultString (case-insensitive)
           -> "Trac permission denied (<action>): <faultString>"
         - default -> "Trac fault (<action>): <faultString>"
      2. requests.exceptions.SSLError -> "SSL error: <exc>"
      3. requests.exceptions.Timeout  -> "Trac request timed out (host: <url>): <exc>"
         MUST be tested before ConnectionError because ConnectTimeout
         is a subclass of ConnectionError. See MRO:
         ConnectTimeout(ConnectionError, Timeout).
      4. requests.exceptions.ConnectionError -> "cannot reach Trac at <url>: <exc>"
      5. fallback -> "<action> failed: <exc>"

    Every returned message must be prefixed with "trac-convert: " by
    the caller (not by this helper) so the caller controls the
    subcommand-neutral prefix.

    The helper signature is password-free by design; no credential is
    ever passed in or could appear in the returned string.
    """
    import xmlrpc.client

    import requests.exceptions

    if isinstance(exc, xmlrpc.client.Fault):
        fault_str = exc.faultString
        fault_str_lower = fault_str.lower()
        if getattr(exc, "faultCode", None) == 1 or (
            "not found" in fault_str_lower
            or "does not exist" in fault_str_lower
        ):
            if page_name is not None:
                return f"wiki page not found: {page_name}"
            return f"resource not found (fault 1): {fault_str}"
        if (
            "permission" in fault_str_lower
            or "denied" in fault_str_lower
        ):
            return f"Trac permission denied ({action}): {fault_str}"
        return f"Trac fault ({action}): {fault_str}"

    if isinstance(exc, requests.exceptions.SSLError):
        return f"SSL error: {exc}"

    # Timeout MUST precede ConnectionError: ConnectTimeout inherits from both
    # requests.exceptions.ConnectionError and requests.exceptions.Timeout.
    if isinstance(exc, requests.exceptions.Timeout):
        return f"Trac request timed out (host: {url}): {exc}"

    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"cannot reach Trac at {url}: {exc}"

    return f"{action} failed: {str(exc) or type(exc).__name__}"


def _fetch_wiki_page(page_name: str, args) -> tuple[str | None, int]:
    """Fetch a TracWiki page's raw source via TracClient.

    Reuses _build_trac_overrides() and bootstrap_config() so the same
    layered config precedence (CLI > env > YAML > defaults) that
    --check-trac uses also applies here.

    Returns (text, EXIT_OK) on success or (None, EXIT_TRAC) on any
    failure. Never prints the password.
    """
    overrides = _build_trac_overrides(args)
    if overrides is None:
        return (
            None,
            EXIT_TRAC,
        )  # password-file diagnostic already written

    try:
        config, _ = bootstrap_config(overrides)
    except ValueError:
        sys.stderr.write(
            "trac-convert: no Trac credentials found.\n"
            "  Set env vars TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD, or\n"
            "  create .trac_mcp/config.yaml (see docs/reference/cli.md).\n"
        )
        return None, EXIT_TRAC

    client = TracClient(config)
    try:
        text = client.get_wiki_page(page_name)
    except Exception as e:
        msg = _classify_trac_error(
            e, config.trac_url, page_name=page_name, action="wiki fetch"
        )
        sys.stderr.write(f"trac-convert: {msg}\n")
        return None, EXIT_TRAC

    return text, EXIT_OK


def _put_wiki_page(
    page_name: str,
    content: str,
    comment: str,
    args,
) -> tuple[dict | None, int]:
    """Write a TracWiki page's raw source via TracClient.put_wiki_page.

    Reuses _build_trac_overrides() and bootstrap_config() so the same
    layered config precedence (CLI > env > YAML > defaults) that
    --check-trac and --from-wiki use also applies here.

    Passes version=None -> create-or-update semantics (no optimistic
    locking) per Phase 22 roadmap goal.

    Returns (info, EXIT_OK) on success or (None, EXIT_TRAC) on any
    failure. info is the dict returned by put_wiki_page:
    {name, version, author, lastModified, url}. Never prints the
    password.

    All wire errors (Fault, Timeout, SSL, ConnectionError, ValueError
    for write rejections) are routed through the shared error helper
    whose fallback branch preserves the exception string verbatim.
    """
    overrides = _build_trac_overrides(args)
    if overrides is None:
        return (
            None,
            EXIT_TRAC,
        )  # password-file diagnostic already written

    try:
        config, _ = bootstrap_config(overrides)
    except ValueError:
        sys.stderr.write(
            "trac-convert: no Trac credentials found.\n"
            "  Set env vars TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD, or\n"
            "  create .trac_mcp/config.yaml (see docs/reference/cli.md).\n"
        )
        return None, EXIT_TRAC

    client = TracClient(config)
    try:
        info = client.put_wiki_page(page_name, content, comment)
    except Exception as e:
        msg = _classify_trac_error(
            e, config.trac_url, page_name=page_name, action="wiki write"
        )
        sys.stderr.write(f"trac-convert: {msg}\n")
        return None, EXIT_TRAC

    return info, EXIT_OK


def _check_trac(args) -> int:
    """Resolve Trac config, ping server, report status.

    Uses module-level TracClient and bootstrap_config imports so that
    tests can monkeypatch trac_mcp_server.cli.convert.TracClient.

    Returns EXIT_OK (0) on successful ping, EXIT_TRAC (4) on any failure.
    Password is NEVER written to stdout or stderr.

    All wire errors are routed through the shared error helper with
    page_name=None and action="Trac ping". The permission-denied branch
    fires for PERMISSION_DENIED-style faultStrings; the generic fault
    branch fires for other auth/protocol errors (e.g. Forbidden).
    """
    overrides = _build_trac_overrides(args)
    if overrides is None:
        # Password-file read failure already reported to stderr
        return EXIT_TRAC

    try:
        config, sources = bootstrap_config(overrides)
    except ValueError:
        sys.stderr.write(
            "trac-convert: no Trac credentials found.\n"
            "  Set env vars TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD, or\n"
            "  create .trac_mcp/config.yaml (see docs/reference/cli.md).\n"
        )
        return EXIT_TRAC

    sys.stdout.write(f"URL:      {config.trac_url}\n")
    sys.stdout.write(f"Username: {config.username}\n")
    sys.stdout.write(f"Sources:  {', '.join(sources)}\n")
    if config.insecure:
        sys.stdout.write("SSL:      verification DISABLED\n")

    client = TracClient(config)
    try:
        version = client.validate_connection()
    except Exception as e:
        msg = _classify_trac_error(
            e, config.trac_url, page_name=None, action="Trac ping"
        )
        sys.stderr.write(f"trac-convert: {msg}\n")
        return EXIT_TRAC

    sys.stdout.write(f"OK (Trac API version {version})\n")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser.

    Kept pure (no side effects) so tests can inspect it directly.
    """
    parser = argparse.ArgumentParser(
        prog="trac-convert",
        description="Convert between Markdown and TracWiki formats.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"trac-convert {__version__}",
    )
    parser.add_argument(
        "--from",
        dest="source_format",
        choices=("md", "tracwiki", "auto"),
        default="auto",
        help=(
            "Source format. 'auto' (default) detects from content "
            "using heuristics."
        ),
    )
    parser.add_argument(
        "--to",
        dest="target_format",
        choices=("md", "tracwiki"),
        default=None,
        help="Target format. Required for conversion (not needed with --check-trac).",
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        default=None,
        metavar="FILE",
        help="Input file. Reads from stdin if omitted.",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_file",
        default=None,
        metavar="FILE",
        help="Output file. Writes to stdout if omitted.",
    )
    parser.add_argument(
        "--from-clipboard",
        dest="from_clipboard",
        action="store_true",
        default=False,
        help=(
            "Read input from the system clipboard instead of"
            " stdin or FILE."
        ),
    )
    parser.add_argument(
        "--to-clipboard",
        dest="to_clipboard",
        action="store_true",
        default=False,
        help=(
            "Write output to the system clipboard instead of"
            " stdout or --output FILE."
        ),
    )
    parser.add_argument(
        "--heading-anchors",
        dest="heading_anchors",
        choices=("on", "off"),
        default="off",
        help=(
            "Emit explicit #slug anchors on TracWiki headings"
            " (md → tracwiki only). Default: off."
        ),
    )
    parser.add_argument(
        "--unknown-macros",
        dest="unknown_macros",
        choices=("preserve", "drop"),
        default="preserve",
        help=(
            "How to render unknown TracWiki macros"
            " (tracwiki → md only). preserve = leave [[Name]] literal,"
            " drop = omit. Default: preserve."
            " (The 'bracket' mode, which emitted [MACRO: Name], was"
            " removed in ticket #63 -- 'preserve' round-trips back to"
            " TracWiki unchanged, which 'bracket' only simulated.)"
        ),
    )
    parser.add_argument(
        "--trac-url",
        dest="trac_url",
        default=None,
        help="Override Trac URL (default: TRAC_URL env or YAML).",
    )
    parser.add_argument(
        "--trac-username",
        dest="trac_username",
        default=None,
        help="Override Trac username (default: TRAC_USERNAME env or YAML).",
    )
    parser.add_argument(
        "--trac-password",
        dest="trac_password",
        default=None,
        help=(
            "Override Trac password (default: TRAC_PASSWORD env or YAML). "
            "Prefer --trac-password-file for secrets."
        ),
    )
    parser.add_argument(
        "--trac-password-file",
        dest="trac_password_file",
        default=None,
        metavar="PATH",
        help=(
            "Read Trac password from file (single line, trimmed). "
            "Takes precedence over --trac-password."
        ),
    )
    parser.add_argument(
        "--from-file",
        dest="from_file",
        default=None,
        metavar="PATH",
        help=(
            "Read input from a local file. "
            "Alternative to the positional FILE argument. "
            "Mutually exclusive with FILE and --from-clipboard."
        ),
    )
    parser.add_argument(
        "--from-wiki",
        dest="from_wiki",
        default=None,
        metavar="PAGE",
        help=(
            "Fetch input from a Trac wiki page (source format is TracWiki). "
            "Mutually exclusive with FILE, --from-file, and --from-clipboard."
        ),
    )
    parser.add_argument(
        "--to-wiki",
        dest="to_wiki",
        default=None,
        metavar="PAGE",
        help=(
            "Write output to a Trac wiki page (target format is TracWiki). "
            "Mutually exclusive with -o/--output and --to-clipboard."
        ),
    )
    parser.add_argument(
        "--wiki-comment",
        dest="wiki_comment",
        default="Updated via trac-convert",
        metavar="MSG",
        help=(
            "Change comment recorded on the wiki page when using --to-wiki. "
            "Default: 'Updated via trac-convert'. Ignored without --to-wiki."
        ),
    )
    parser.add_argument(
        "--check-trac",
        dest="check_trac",
        action="store_true",
        default=False,
        help=(
            "Print resolved Trac config source per field, ping the server, "
            "and exit (no conversion performed)."
        ),
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-q",
        "--quiet",
        dest="quiet",
        action="store_true",
        default=False,
        help="Suppress warning: lines on stderr. Errors still shown.",
    )
    verbosity.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        action="store_true",
        default=False,
        help="Emit info: diagnostic lines to stderr.",
    )
    return parser


def convert_text(
    text: str,
    source_format: str,
    target_format: str,
    *,
    heading_anchors: bool = False,
    unknown_macros: str = "preserve",
) -> ConversionResult:
    """Convert text between Markdown and TracWiki.

    source_format: "md", "tracwiki", or "auto".
    target_format: "md" or "tracwiki".
    heading_anchors: forwarded to convert_with_warnings() for md→tracwiki.
        Ignored on the tracwiki→md direction.
    unknown_macros: forwarded to tracwiki_to_markdown() for tracwiki→md.
        Ignored on the md→tracwiki direction.

    Returns a ConversionResult (may be a pass-through with
    converted=False when source and target resolve to the same
    format).
    """
    _ALIAS = {"md": "markdown", "tracwiki": "tracwiki"}

    if source_format == "auto":
        source = detect_format_heuristic(text)
    else:
        source = _ALIAS[source_format]

    target = _ALIAS[target_format]

    if source == target:
        return ConversionResult(
            text=text,
            source_format=source,
            target_format=target,
            converted=False,
            warnings=[],
        )
    if source == "markdown" and target == "tracwiki":
        result = convert_with_warnings(
            text, heading_anchors=heading_anchors
        )
        # Ticket #68: a TracWiki `{{{ }}}` block in Markdown input has
        # its body indentation eaten, and nothing else in this pipeline
        # says so -- the conversion succeeds and the output looks
        # plausible. Reported here rather than raised: unlike the MCP
        # write tools, this binary stores nothing, so the caller still
        # holds the input and a stderr line is enough to act on.
        result.warnings.extend(
            describe_indentation_loss(loss)
            for loss in find_code_block_indentation_loss(
                text, result.text
            )
        )
        return result
    return tracwiki_to_markdown(text, unknown_macros=unknown_macros)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the CLI.

    Reads from --from-wiki PAGE, --from-clipboard, positional FILE, or
    stdin (in that order of precedence), dispatches via convert_text(),
    writes result.text verbatim to --to-wiki PAGE, --to-clipboard,
    --output FILE, or stdout (in that order of precedence), emits
    result.warnings to stderr one line each prefixed with ``warning: ``,
    and returns an integer exit code for ``sys.exit``.

    Exit codes:
    - 0 (EXIT_OK): success (including pass-through with no conversion).
    - 1 (EXIT_RUNTIME_ERROR): I/O error, clipboard failure, or
      mutual-exclusion violation; a ``trac-convert: <what>: <reason>``
      message is written to stderr.
    - 2 (EXIT_USAGE_ERROR): invalid flags or missing required argument;
      raised internally by argparse (not by this function directly).
    - 3 (EXIT_CONVERSION_ERROR): the converter raised an unexpected
      exception; a ``trac-convert: conversion failed: <reason>`` message
      is written to stderr (no traceback).
    - 4 (EXIT_TRAC): Trac connectivity, auth, or protocol error from
      --check-trac, --from-wiki, or --to-wiki. Sub-classified into:
      wiki-page-not-found, permission-denied, timeout, SSL error,
      network unreachable, or generic Trac fault. Password is never
      written to stdout or stderr.

    Note on direction-scoped flags: --heading-anchors only affects the
    md→tracwiki direction and is silently ignored for tracwiki→md (and
    vice-versa for --unknown-macros).  This is intentional — --from auto
    may resolve either way at runtime, so mutual-exclusion checks are not
    applied.

    Note on verbosity flags: --quiet suppresses warning: lines (but not
    trac-convert: errors). --verbose emits info: diagnostic lines (source
    format, byte counts) to stderr before warnings. The two flags are
    mutually exclusive via argparse add_mutually_exclusive_group().
    """
    args = build_parser().parse_args(argv)

    # --- --check-trac: early dispatch before conversion validation ---
    if args.check_trac:
        if args.from_wiki is not None or args.to_wiki is not None:
            sys.stderr.write(
                "trac-convert: --check-trac cannot be combined with"
                " --from-wiki or --to-wiki\n"
            )
            return EXIT_RUNTIME_ERROR
        return _check_trac(args)

    # --- --to is required for conversion (not for --check-trac or --to-wiki) ---
    if args.target_format is None and args.to_wiki is None:
        sys.stderr.write("trac-convert: --to is required\n")
        return EXIT_USAGE_ERROR

    # --- --from-file mutex validation ---
    if args.from_file is not None:
        if args.input_file is not None:
            sys.stderr.write(
                "trac-convert: --from-file and FILE are"
                " mutually exclusive\n"
            )
            return EXIT_RUNTIME_ERROR
        if args.from_clipboard:
            sys.stderr.write(
                "trac-convert: --from-file and --from-clipboard are"
                " mutually exclusive\n"
            )
            return EXIT_RUNTIME_ERROR
        if args.from_wiki is not None:
            sys.stderr.write(
                "trac-convert: --from-file and --from-wiki are"
                " mutually exclusive\n"
            )
            return EXIT_RUNTIME_ERROR

    # --- --from-wiki mutex validation ---
    if args.from_wiki is not None:
        if args.input_file is not None:
            sys.stderr.write(
                "trac-convert: --from-wiki and FILE are"
                " mutually exclusive\n"
            )
            return EXIT_RUNTIME_ERROR
        if args.from_clipboard:
            sys.stderr.write(
                "trac-convert: --from-wiki and --from-clipboard are"
                " mutually exclusive\n"
            )
            return EXIT_RUNTIME_ERROR

    # --- --to-wiki mutex validation ---
    if args.to_wiki is not None:
        if args.output_file is not None:
            sys.stderr.write(
                "trac-convert: --to-wiki and --output are"
                " mutually exclusive\n"
            )
            return EXIT_RUNTIME_ERROR
        if args.to_clipboard:
            sys.stderr.write(
                "trac-convert: --to-wiki and --to-clipboard are"
                " mutually exclusive\n"
            )
            return EXIT_RUNTIME_ERROR

    # --- mutual exclusion validation ---
    if args.from_clipboard and args.input_file is not None:
        sys.stderr.write(
            "trac-convert: --from-clipboard and FILE are"
            " mutually exclusive\n"
        )
        return EXIT_RUNTIME_ERROR
    if args.from_clipboard and args.from_file is not None:
        sys.stderr.write(
            "trac-convert: --from-clipboard and --from-file are"
            " mutually exclusive\n"
        )
        return EXIT_RUNTIME_ERROR
    if args.to_clipboard and args.output_file is not None:
        sys.stderr.write(
            "trac-convert: --to-clipboard and --output are"
            " mutually exclusive\n"
        )
        return EXIT_RUNTIME_ERROR

    # --- read input ---
    if args.from_wiki is not None:
        text, fetch_rc = _fetch_wiki_page(args.from_wiki, args)
        if text is None:
            return fetch_rc
        # --from-wiki authoritatively knows the source is TracWiki;
        # silently override --from (which may still be 'auto' or 'md').
        args.source_format = "tracwiki"
    elif args.from_file is not None:
        try:
            text = Path(args.from_file).read_text(encoding="utf-8")
        except OSError as e:
            sys.stderr.write(
                f"trac-convert: cannot read input file:"
                f" {args.from_file}: {e.strerror or e}\n"
            )
            return EXIT_RUNTIME_ERROR
    elif args.from_clipboard:
        try:
            text = read_clipboard()
        except (
            ClipboardUnavailableError,
            pyperclip.PyperclipException,
        ) as e:
            sys.stderr.write(
                f"trac-convert: clipboard read failed: {e}\n"
            )
            return EXIT_RUNTIME_ERROR
    elif args.input_file is not None:
        try:
            text = Path(args.input_file).read_text(encoding="utf-8")
        except OSError as e:
            sys.stderr.write(
                f"trac-convert: cannot read input file:"
                f" {args.input_file}: {e.strerror or e}\n"
            )
            return EXIT_RUNTIME_ERROR
    else:
        text = sys.stdin.read()

    # --- --to-wiki authoritatively knows the target is TracWiki;
    # silently override --to (which may be None or 'md'). ---
    if args.to_wiki is not None:
        args.target_format = "tracwiki"

    # --- convert ---
    # Translate --heading-anchors on|off to bool before forwarding.
    heading_anchors_bool = args.heading_anchors == "on"
    try:
        result = convert_text(
            text,
            args.source_format,
            args.target_format,
            heading_anchors=heading_anchors_bool,
            unknown_macros=args.unknown_macros,
        )
    except Exception as e:
        sys.stderr.write(f"trac-convert: conversion failed: {e}\n")
        return EXIT_CONVERSION_ERROR

    # --- write output ---
    if args.to_wiki is not None:
        info, write_rc = _put_wiki_page(
            args.to_wiki, result.text, args.wiki_comment, args
        )
        if info is None:
            return write_rc
        if args.verbose:
            sys.stderr.write(
                f"info: wrote {len(result.text.encode('utf-8'))} bytes"
                f" to wiki page {info['name']} (v{info['version']})\n"
            )
    elif args.to_clipboard:
        try:
            write_clipboard(result.text)
        except (
            ClipboardUnavailableError,
            pyperclip.PyperclipException,
        ) as e:
            sys.stderr.write(
                f"trac-convert: clipboard write failed: {e}\n"
            )
            return EXIT_RUNTIME_ERROR
    elif args.output_file is not None:
        try:
            Path(args.output_file).write_text(
                result.text, encoding="utf-8"
            )
        except OSError as e:
            sys.stderr.write(
                f"trac-convert: cannot write output file:"
                f" {args.output_file}: {e.strerror or e}\n"
            )
            return EXIT_RUNTIME_ERROR
    else:
        sys.stdout.write(result.text)

    # --- verbose diagnostics (emitted before warnings) ---
    if args.verbose:
        sys.stderr.write(
            f"info: source format: {result.source_format}\n"
        )
        sys.stderr.write(
            f"info: read {len(text.encode('utf-8'))} bytes\n"
        )
        if args.to_wiki is None:
            sys.stderr.write(
                f"info: wrote {len(result.text.encode('utf-8'))} bytes\n"
            )

    # --- warnings (emitted after write, preserving Phase 13 ordering) ---
    if not args.quiet:
        for warning in result.warnings:
            sys.stderr.write(f"warning: {warning}\n")

    return EXIT_OK


def run() -> None:
    """Console scripts entry point — delegates to main()."""
    sys.exit(main())
