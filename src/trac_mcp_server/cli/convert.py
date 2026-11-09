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
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pyperclip

from .. import __version__
from ..config_bootstrap import bootstrap_config
from ..converters import detect_format_heuristic
from ..converters.common import (
    ConversionResult,
    describe_indentation_loss,
    find_code_block_indentation_loss,
)
from ..converters.front_matter import (
    build_front_matter,
    exported_from,
    instance_path,
    split_front_matter,
)
from ..converters.html_to_markdown import trac_html_to_markdown
from ..converters.markdown_to_tracwiki import convert_with_warnings
from ..core.client import TracClient, WikiPageStoredReadbackFailed
from ..link_rewrites import (
    LinkRewrites,
    as_dict,
    link_map_arg,
    parse_pairs,
)

# ---------------------------------------------------------------------------
# Exit-code constants
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1  # I/O, clipboard, mutual-exclusion
EXIT_USAGE_ERROR = 2  # argparse default (not raised by us directly)
EXIT_CONVERSION_ERROR = 3  # exception raised inside convert_text()
EXIT_TRAC = 4  # Trac client errors (auth, network, protocol) — used by --check-trac and Phases 21-23
# --to-wiki stored the page, but reading back its new version failed
# (Trac /trac_mcp_server #140). Distinct from EXIT_TRAC, which means
# nothing was stored: a caller that retries on 4 must not retry on 5.
EXIT_TRAC_STORED = 5


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
         Classified by ``classify_fault`` (codes first: 404/403; then
         the fault text with TracXMLRPC's wrapper and every quoted name
         removed). Code 1 alone is no evidence: TracXMLRPC sends it for
         ANY TracError (Trac #189).
         - "not_found" -> "wiki page not found: <page_name>" (falls
           back to "resource not found (fault <code>): <faultString>"
           when page_name is None)
         - "permission_denied"
           -> "Trac permission denied (<action>): <faultString>"
         - anything else -> "Trac fault (<action>): <faultString>"
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

    from ..core.faults import classify_fault

    if isinstance(exc, xmlrpc.client.Fault):
        fault_str = exc.faultString
        kind = classify_fault(exc)
        if kind == "not_found":
            if page_name is not None:
                return f"wiki page not found: {page_name}"
            return (
                f"resource not found (fault {exc.faultCode}): "
                f"{fault_str}"
            )
        if kind == "permission_denied":
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


#: What TracWiki -> Markdown says when it has no Trac to render with.
_TRAC_REQUIRED_MESSAGE = (
    "TracWiki -> Markdown needs a Trac connection: Trac renders the text"
    " and trac-convert translates its HTML. Set TRAC_URL, TRAC_USERNAME,"
    " TRAC_PASSWORD (or .trac_mcp/config.yaml), or pass --trac-url."
    " Markdown -> TracWiki works offline."
)


class TracConnectionRequired(RuntimeError):
    """TracWiki -> Markdown was asked for with no Trac to render it.

    Trac's own HTML is what trac-convert translates (Trac
    /trac_mcp_server #210), so this direction cannot run offline.

    ``config_error`` is the text of the ``ValueError`` that
    ``bootstrap_config`` raised, when there was one: a configuration that
    exists but is invalid (a bad ``TRAC_URL``) is not the same as none,
    and ``str(exc)`` carries it on a ``  config error:`` line.
    """

    def __init__(
        self,
        message: str = _TRAC_REQUIRED_MESSAGE,
        config_error: str | None = None,
    ) -> None:
        self.config_error = config_error
        super().__init__(
            message
            if config_error is None
            else f"{message}\n  config error: {config_error}"
        )


class TracRenderError(RuntimeError):
    """Trac was reached but rendering failed.

    ``str(exc)`` is the ``_classify_trac_error`` message; it is empty
    when the cause was already reported on stderr (an unreadable
    ``--trac-password-file``).
    """


class _TracSession:
    """The TracClient for one trac-convert run, opened on first use.

    ``--from auto`` has to resolve before anyone knows whether Trac is
    needed, and Markdown -> TracWiki never needs it, so nothing here
    bootstraps config or builds a client until ``client()`` is called.
    """

    def __init__(self, args) -> None:
        self._args = args
        self._client: TracClient | None = None
        self.url: str = ""
        #: The page version ``--from-wiki`` pinned its reads to, or None
        #: when none was pinned (see ``_fetch_wiki_page``).
        self.page_version: int | None = None
        #: Warnings the session itself raised, for ``main`` to print with
        #: the converter's.
        self.warnings: list[str] = []
        #: ``--link-map`` (a replacement for the configured map) or, with
        #: none, the configured ``export.link_rewrites`` /
        #: ``TRAC_MCP_LINK_REWRITES``, which is known once ``client()``
        #: has bootstrapped the config (Trac /trac_mcp_server #213).
        self.explicit_link_map: LinkRewrites | None = None
        self._configured_link_map: LinkRewrites = ()

    def document_url(self, page_name: str | None = None) -> str:
        """The URL Trac gives the document being converted: the page's
        own URL for ``--from-wiki PAGE``, the instance root for text
        rendered with no page (``wikiToHtml``). A link into it with a
        ``#fragment`` is an in-page anchor (Trac /trac_mcp_server #213).
        """
        self.client()
        base = self.url.rstrip("/")
        return base if page_name is None else f"{base}/wiki/{page_name}"

    def link_map(self) -> dict[str, str]:
        """The link-rewrite map TracWiki -> Markdown applies.

        Opens the client when it is not open yet, which the HTML read
        that precedes the translation has always done.
        """
        self.client()
        if self.explicit_link_map is not None:
            return as_dict(self.explicit_link_map)
        return as_dict(self._configured_link_map)

    def client(self) -> TracClient:
        """Return the client, opening it on the first call.

        Raises ``TracConnectionRequired`` when no Trac is configured and
        ``TracRenderError("")`` when ``--trac-password-file`` could not
        be read (already reported on stderr).
        """
        if self._client is None:
            overrides = _build_trac_overrides(self._args)
            if overrides is None:
                raise TracRenderError("")
            try:
                config, _ = bootstrap_config(overrides)
            except ValueError as e:
                raise TracConnectionRequired(
                    config_error=str(e)
                ) from None
            self.url = config.trac_url
            self._configured_link_map = config.link_rewrites
            self._client = TracClient(config)
        return self._client

    def _render(
        self,
        call: Callable[[TracClient], str],
        page_name: str | None = None,
    ) -> str:
        client = self.client()
        try:
            return call(client)
        except Exception as e:
            raise TracRenderError(
                _classify_trac_error(
                    e,
                    self.url,
                    page_name=page_name,
                    action="wiki render",
                )
            ) from e

    def render_text(self, text: str) -> str:
        """Trac's HTML for ``text`` (file, stdin, clipboard input)."""
        return self._render(lambda client: client.wiki_to_html(text))

    def page_renderer(self, page_name: str) -> Callable[[str], str]:
        """A renderer returning the HTML of wiki page ``page_name``.

        ``--from-wiki``: the page renders with its own context, so
        relative links resolve; the text passed in is the page's source,
        which ``convert_text`` hands the translator for its code-block
        languages. The HTML is read at ``page_version`` (pinned by
        ``_fetch_wiki_page``), so it is the version that source is.
        """
        return lambda _text: self._render(
            lambda client: client.get_wiki_page_html(
                page_name, self.page_version
            ),
            page_name=page_name,
        )


def _usable_version(info) -> int | None:
    """The page version in a ``get_wiki_page_info`` result, or None."""
    version = info.get("version") if isinstance(info, dict) else None
    if isinstance(version, int) and not isinstance(version, bool):
        return version if version > 0 else None
    return None


def _fetch_wiki_page(
    page_name: str,
    args,
    session: _TracSession | None = None,
    *,
    pin_version: bool = False,
) -> tuple[str | None, int]:
    """Fetch a TracWiki page's raw source via TracClient.

    Reuses _build_trac_overrides() and bootstrap_config() so the same
    layered config precedence (CLI > env > YAML > defaults) that
    --check-trac uses also applies here. ``session`` is the run's
    shared Trac session, so the HTML read that follows (--from-wiki to
    Markdown) reuses this client; one is made when omitted.

    ``pin_version`` (--from-wiki to Markdown, which reads the page's HTML
    too): the page version is read first and the source is fetched at it,
    and ``session.page_version`` makes the HTML read use it, so a save
    between the two reads cannot pair one version's source with the next
    one's HTML. When the version cannot be read, both reads are the
    unpinned latest and ``session.warnings`` says so.

    Returns (text, EXIT_OK) on success or (None, EXIT_TRAC) on any
    failure. Never prints the password.
    """
    session = session or _TracSession(args)
    try:
        client = session.client()
    except TracConnectionRequired as e:
        sys.stderr.write(
            "trac-convert: no Trac credentials found.\n"
            "  Set env vars TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD, or\n"
            "  create .trac_mcp/config.yaml (see docs/reference/cli.md).\n"
        )
        if e.config_error is not None:
            sys.stderr.write(f"  config error: {e.config_error}\n")
        return None, EXIT_TRAC
    except TracRenderError:
        return (
            None,
            EXIT_TRAC,
        )  # password-file diagnostic already written

    try:
        if pin_version:
            session.page_version = _usable_version(
                client.get_wiki_page_info(page_name)
            )
            if session.page_version is None:
                session.warnings.append(
                    f"wiki page {page_name}: its version could not be"
                    " read, so the source and the HTML reads were not"
                    " pinned to one version; a save between them would"
                    " pair one version's source with another's HTML"
                )
            text = client.get_wiki_page(page_name, session.page_version)
        else:
            text = client.get_wiki_page(page_name)
    except Exception as e:
        msg = _classify_trac_error(
            e, session.url, page_name=page_name, action="wiki fetch"
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
    password. When the page was stored but its read-back failed
    (WikiPageStoredReadbackFailed), returns (None, EXIT_TRAC_STORED)
    after saying so on stderr: EXIT_TRAC always means nothing was
    stored.

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
    except WikiPageStoredReadbackFailed as e:
        sys.stderr.write(
            f"trac-convert: wiki page {page_name} WAS stored, but reading"
            f" back its new version failed -- do not retry the write"
            f" (exit {EXIT_TRAC_STORED}): {e}\n"
        )
        return None, EXIT_TRAC_STORED
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
    page_name=None and action="Trac ping". Faults are classified by
    ``classify_fault``: a 403 (TracXMLRPC's PermissionError), or a
    permission word outside any quoted name, gives the
    permission-denied message; any other fault gives the generic
    "Trac fault" message.
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


#: What ``--link-map`` says when the run is not a TracWiki -> Markdown
#: conversion (including ``--check-trac``, which converts nothing).
_LINK_MAP_NO_EFFECT = (
    "--link-map has no effect: it rewrites the links of a"
    " TracWiki -> Markdown conversion only"
)


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser.

    Kept pure (no side effects) so tests can inspect it directly.
    """
    parser = argparse.ArgumentParser(
        prog="trac-convert",
        description=(
            "Convert between Markdown and TracWiki formats. "
            "TracWiki -> Markdown needs a Trac connection (Trac "
            "renders the text; trac-convert translates its HTML); "
            "Markdown -> TracWiki runs offline."
        ),
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
        "--link-map",
        dest="link_map",
        action="append",
        type=link_map_arg,
        default=None,
        metavar="FROM=TO",
        help=(
            "Rewrite links of a TracWiki -> Markdown conversion: a link"
            " target starting with FROM has that prefix replaced by TO"
            " (longest FROM first; images and #anchors are never"
            " rewritten). Repeatable. Replaces the configured map"
            " (export.link_rewrites, TRAC_MCP_LINK_REWRITES). Has no"
            " effect in any other direction."
        ),
    )
    parser.add_argument(
        "--unknown-macros",
        dest="unknown_macros",
        choices=("preserve", "drop"),
        default=None,
        help=(
            "Deprecated, no effect (Trac renders macros itself);"
            " removed in #214."
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
        "--force",
        dest="force",
        action="store_true",
        help=(
            "Convert Markdown exported from Trac (front matter carrying "
            "trac_page) to TracWiki anyway, stripping that front matter"
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
    render_html: Callable[[str], str] | None = None,
    link_map: dict[str, str]
    | Callable[[], dict[str, str]]
    | None = None,
    document_url: str | Callable[[], str | None] | None = None,
) -> ConversionResult:
    """Convert text between Markdown and TracWiki.

    source_format: "md", "tracwiki", or "auto".
    target_format: "md" or "tracwiki".
    heading_anchors: forwarded to convert_with_warnings() for md→tracwiki.
        Ignored on the tracwiki→md direction.
    render_html: a callable from TracWiki text to Trac's HTML for it.
        TracWiki→Markdown translates that HTML
        (``trac_html_to_markdown(render_html(text), text)``), so it is
        required there: without one, ``TracConnectionRequired`` is
        raised. Markdown→TracWiki and pass-through never call it.
    link_map: URL prefix -> replacement for the links of TracWiki→Markdown
        (``trac_html_to_markdown(link_map=)``), or a callable returning
        it, called after ``render_html`` so a map that comes from the
        Trac session's config can be read once that session is open.
        Ignored by every other direction.
    document_url: the URL Trac gives the converted document
        (``trac_html_to_markdown(document_url=)``), or a callable
        returning it, called after ``render_html`` like ``link_map``.
        A link into it with a fragment is written ``#frag`` and never
        rewritten. Ignored by every other direction.

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
    if render_html is None:
        raise TracConnectionRequired(_TRAC_REQUIRED_MESSAGE)
    html = render_html(text)
    result = trac_html_to_markdown(
        html,
        text,
        link_map=link_map() if callable(link_map) else link_map,
        document_url=(
            document_url() if callable(document_url) else document_url
        ),
    )
    # The translator reports the format it read (HTML); the caller's
    # input, and what --verbose reports, is TracWiki.
    return replace(result, source_format=source)


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
      --check-trac, --from-wiki, --to-wiki, or the rendering step of
      TracWiki -> Markdown (Trac renders the text and trac-convert
      translates its HTML, Trac /trac_mcp_server #210). TracWiki ->
      Markdown with no Trac configured also exits 4, with a message
      saying what to set; nothing is written to stdout. Sub-classified into:
      wiki-page-not-found, permission-denied, timeout, SSL error,
      network unreachable, or generic Trac fault. Password is never
      written to stdout or stderr. With --to-wiki, nothing was stored.
    - 5 (EXIT_TRAC_STORED): --to-wiki stored the page, but reading back
      its new version failed; a ``trac-convert: wiki page <PAGE> WAS
      stored ...`` message is written to stderr. Do not retry the write
      (Trac /trac_mcp_server #140).

    Note on direction-scoped flags: --heading-anchors only affects the
    md→tracwiki direction and is silently ignored for tracwiki→md.  This
    is intentional — --from auto may resolve either way at runtime, so
    mutual-exclusion checks are not applied. --link-map is the same: it
    applies to tracwiki→md only, and any other direction prints one
    ``warning:`` line saying it has no effect (a ``--link-map`` value
    with no ``=``, an empty FROM or a repeated FROM is a usage error,
    exit 2). --unknown-macros is
    deprecated and has no effect: Trac renders macros itself; passing it
    prints one ``warning:`` line (removed in #214).

    Note on verbosity flags: --quiet suppresses warning: lines (but not
    trac-convert: errors). --verbose emits info: diagnostic lines (source
    format, byte counts) to stderr before warnings. The two flags are
    mutually exclusive via argparse add_mutually_exclusive_group().
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    link_map_flag: LinkRewrites | None = None
    if args.link_map is not None:
        # An empty FROM or a repeated FROM is a usage error too.
        try:
            link_map_flag = parse_pairs(args.link_map, "--link-map")
        except ValueError as e:
            parser.error(str(e))

    if args.unknown_macros is not None and not args.quiet:
        sys.stderr.write(
            "warning: --unknown-macros is deprecated and has no effect\n"
        )

    # --- --check-trac: early dispatch before conversion validation ---
    if args.check_trac:
        if args.from_wiki is not None or args.to_wiki is not None:
            sys.stderr.write(
                "trac-convert: --check-trac cannot be combined with"
                " --from-wiki or --to-wiki\n"
            )
            return EXIT_RUNTIME_ERROR
        if link_map_flag is not None and not args.quiet:
            sys.stderr.write(f"warning: {_LINK_MAP_NO_EFFECT}\n")
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
    # One lazily-opened Trac session per run: TracWiki -> Markdown
    # renders through it, --from-wiki reads through it, and nothing
    # else touches Trac.
    session = _TracSession(args)
    session.explicit_link_map = link_map_flag
    if args.from_wiki is not None:
        # To Markdown the page's HTML is read too, so both reads are
        # pinned to one version; --to tracwiki / --to-wiki read no HTML.
        text, fetch_rc = _fetch_wiki_page(
            args.from_wiki,
            args,
            session,
            pin_version=(
                args.to_wiki is None and args.target_format == "md"
            ),
        )
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

    # --- Ticket #212: the Markdown export is one-way. Converting text
    # whose front matter says it came from a Trac page back to TracWiki
    # (to stdout, a file, the clipboard or --to-wiki) would re-shape the
    # page, so it is refused before anything is converted or written,
    # unless forced, which drops that front matter and converts the rest.
    if args.from_wiki is None and args.target_format == "tracwiki":
        exported = exported_from(text)
        if exported:
            if not args.force:
                instance = exported.get("trac_instance") or "unknown"
                version = exported.get("trac_version")
                if version is None:
                    version = "unknown"
                page = exported["trac_page"]
                sys.stderr.write(
                    "trac-convert: refusing to convert: this Markdown was "
                    f"exported from Trac page '{page}' (instance "
                    f"{instance}, version {version}) and the export is "
                    "one-way; edit the page as TracWiki (trac-convert "
                    f"--from-wiki {page} --to tracwiki), or pass --force\n"
                )
                return EXIT_RUNTIME_ERROR
            text = split_front_matter(text)[1]

    # --- convert ---
    # Translate --heading-anchors on|off to bool before forwarding.
    heading_anchors_bool = args.heading_anchors == "on"
    try:
        result = convert_text(
            text,
            args.source_format,
            args.target_format,
            heading_anchors=heading_anchors_bool,
            render_html=(
                session.page_renderer(args.from_wiki)
                if args.from_wiki is not None
                else session.render_text
            ),
            link_map=session.link_map,
            document_url=lambda: session.document_url(args.from_wiki),
        )
    except TracConnectionRequired as e:
        sys.stderr.write(f"trac-convert: {e}\n")
        if args.source_format == "auto":
            # Only an undecided --from reaches here as "auto" (--from-wiki
            # sets tracwiki): the text may be Markdown that detection
            # could not tell from TracWiki, e.g. a bare line of prose.
            sys.stderr.write(
                "  the input was auto-detected as TracWiki; if it is"
                " Markdown, pass --from md\n"
            )
        return EXIT_TRAC
    except TracRenderError as e:
        if str(e):
            sys.stderr.write(f"trac-convert: {e}\n")
        return EXIT_TRAC
    except Exception as e:
        sys.stderr.write(f"trac-convert: conversion failed: {e}\n")
        return EXIT_CONVERSION_ERROR

    if link_map_flag is not None and not (
        result.converted
        and result.source_format == "tracwiki"
        and result.target_format == "markdown"
    ):
        session.warnings.append(_LINK_MAP_NO_EFFECT)

    if session.warnings:
        result = replace(
            result, warnings=[*session.warnings, *result.warnings]
        )

    # Ticket #212: a Markdown export of a page names the page it came
    # from, which is what a later push or conversion refuses it by.
    if (
        args.from_wiki is not None
        and result.converted
        and result.target_format == "markdown"
    ):
        result = replace(
            result,
            text=build_front_matter(
                title=result.title or args.from_wiki,
                source=session.document_url(args.from_wiki),
                trac_instance=instance_path(session.url),
                trac_page=args.from_wiki,
                trac_version=session.page_version,
            )
            + result.text,
        )

    # --- write output ---
    if args.to_wiki is not None:
        info, write_rc = _put_wiki_page(
            args.to_wiki, result.text, args.wiki_comment, args
        )
        if info is None:
            if write_rc == EXIT_TRAC_STORED and not args.quiet:
                # The page WAS stored (#140): say what the converter
                # altered, as a clean write does below.
                for warning in result.warnings:
                    sys.stderr.write(f"warning: {warning}\n")
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
