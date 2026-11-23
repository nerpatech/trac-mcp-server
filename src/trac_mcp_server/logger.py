"""Logging utilities for the trac-mcp-server package.

Provides a :class:`JsonFormatter` that emits one JSON object per log record
(JSON-lines output) and a :func:`setup_logging` helper that configures the
root logger for either MCP mode (a log file by default, never stdout, so
stdout remains clean for JSON-RPC traffic) or CLI/http mode (human-friendly
stderr logging, with an optional companion log file).
"""

import json
import logging
import logging.handlers
import os
import sys
from collections.abc import Callable
from io import TextIOWrapper
from typing import NamedTuple, cast

try:
    import fcntl
except ImportError:  # not POSIX
    fcntl = None


class JsonFormatter(logging.Formatter):
    """Single-line JSON formatter for structured debug output.

    Produces one JSON object per log record with fields: ts, level, logger, msg.
    Exception info is included as an "exc" field when present.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


# The default stdio log file rotates; an explicit path is left to its owner
# (logrotate, say) and stays a plain append file.
_DEFAULT_LOG_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_LOG_BACKUP_COUNT = 3

# The level names LOG_LEVEL and YAML ``logging.level`` accept: the
# ``logging`` module's names, WARN and FATAL being its aliases, which the
# old ``getattr(logging, name)`` lookup took too.
LOG_LEVEL_NAMES = (
    "DEBUG",
    "INFO",
    "WARNING",
    "WARN",
    "ERROR",
    "CRITICAL",
    "FATAL",
)
LOG_LEVEL_ALIASES = {"WARN": "WARNING", "FATAL": "CRITICAL"}


def default_mcp_log_file() -> str:
    """The stdio default log file: ``$XDG_STATE_HOME/trac-mcp-server/
    server.log`` when ``XDG_STATE_HOME`` is set to an absolute path (the
    XDG spec says to ignore a relative one), else
    ``~/.local/state/trac-mcp-server/server.log``.

    A function, so HOME and XDG_STATE_HOME are read at call time. It
    replaced a fixed ``/tmp`` path, where any local user could read the
    log (it carries Trac page text at DEBUG) or plant a symlink for the
    server to append through (Trac /trac_mcp_server #277).
    """
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg and xdg.strip() and os.path.isabs(xdg):
        base = xdg
    else:
        base = os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "trac-mcp-server", "server.log")


def _private_opener(path: str, flags: int) -> int:
    """``open()``'s opener: ``os.open`` with mode 0600, so the mode of a
    file this code creates never depends on the umask. An existing file
    keeps its mode."""
    return os.open(path, flags, 0o600)


class _PrivateFileHandler(logging.FileHandler):
    """A ``FileHandler`` that creates its file with mode 0600."""

    def _open(self) -> TextIOWrapper:
        return cast(
            TextIOWrapper,
            open(
                self.baseFilename,
                self.mode,
                encoding=self.encoding,
                errors=self.errors,
                opener=_private_opener,
            ),
        )


class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A ``RotatingFileHandler`` whose file, and every file it rotates
    in, is created with mode 0600 (``doRollover`` reopens the base path
    through ``_open``), and that is safe for several processes sharing
    one file, as the per-user default log is.

    The stdlib handler is not: it decides from ``stream.tell()``, the
    size of what *this* handler wrote. After another process rotates,
    this one keeps writing through its descriptor to the renamed file,
    its count passes ``maxBytes``, and it rotates the other process's
    fresh file away; the backups fill with fragments. Here the decision
    is made from the base path's size, after reopening if the path no
    longer names the open file, and a rollover runs under an exclusive
    ``flock`` on ``<base>.lock`` (POSIX; elsewhere the stdlib behaviour)
    that re-checks the size once held, since another process may just
    have rolled.
    """

    _pending_len = 0

    def _open(self) -> TextIOWrapper:
        return cast(
            TextIOWrapper,
            open(
                self.baseFilename,
                self.mode,
                encoding=self.encoding,
                errors=self.errors,
                opener=_private_opener,
            ),
        )

    def _reopen(self) -> None:
        if self.stream is not None:
            self.stream.close()
        self.stream = self._open()

    def _stream_is_current(self) -> bool:
        """Whether the base path still names the file this handler has
        open (another process rotated or removed it if not)."""
        try:
            on_disk = os.stat(self.baseFilename)
            held = os.fstat(self.stream.fileno())
        except OSError:
            return False
        return (on_disk.st_dev, on_disk.st_ino) == (
            held.st_dev,
            held.st_ino,
        )

    def _base_size(self) -> int:
        try:
            return os.stat(self.baseFilename).st_size
        except OSError:
            return 0

    def _needs_rollover(self, pending_len: int) -> bool:
        size = self._base_size()
        if not size:
            # gh-116263, as the stdlib: never roll an empty file.
            return False
        if size + pending_len < self.maxBytes:
            return False
        # bpo-45401, as the stdlib: only ever roll a regular file.
        if os.path.exists(self.baseFilename) and not os.path.isfile(
            self.baseFilename
        ):
            return False
        return True

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self.stream is None:  # delay was set
            self.stream = self._open()
        if self.maxBytes <= 0:
            return False
        if not self._stream_is_current():
            self._reopen()
        self._pending_len = len("%s\n" % self.format(record))
        return self._needs_rollover(self._pending_len)

    def doRollover(self) -> None:
        if fcntl is None:
            super().doRollover()
            return
        lock_fd = os.open(
            self.baseFilename + ".lock", os.O_WRONLY | os.O_CREAT, 0o600
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if self._needs_rollover(self._pending_len):
                super().doRollover()
            else:
                # Another process rolled while this one waited.
                self._reopen()
        finally:
            os.close(lock_fd)


_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def logging_reaches_stderr(logger: logging.Logger, level: int) -> bool:
    """Whether a record from ``logger`` at ``level`` is written to stderr.

    True when the logger is enabled for ``level`` and some handler it
    propagates to emits the record to the current ``sys.stderr``. A
    ``FileHandler`` is a ``StreamHandler`` too, but its stream is the file.
    ``sys.stderr`` is read at call time, so a handler built on a replaced
    stream (a test capture, say) counts only while that stream is current.
    With no handler anywhere on the chain, logging's ``lastResort`` handler
    writes WARNING and above to the current stderr.
    """
    if not logger.isEnabledFor(level):
        return False
    seen_handler = False
    current: logging.Logger | None = logger
    while current is not None:
        for handler in current.handlers:
            seen_handler = True
            if (
                isinstance(handler, logging.StreamHandler)
                and not isinstance(handler, logging.FileHandler)
                and handler.stream is sys.stderr
                and level >= handler.level
            ):
                return True
        current = current.parent if current.propagate else None
    last_resort = logging.lastResort
    return (
        not seen_handler
        and last_resort is not None
        and level >= last_resort.level
    )


def _print_stderr(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def startup_note(
    logger: logging.Logger,
    message: str,
    *args: object,
    level: int = logging.INFO,
    printed: str | list[str] | None = None,
    print_fn: Callable[[str], None] | None = None,
) -> None:
    """Log a startup line, and print it on stderr unless the logger
    already does.

    Under stdio with a log file the logger writes only that file, so stderr
    is the only place an operator sees the line. Under http/cli, or stdio
    without a log file, the logger has its own stderr handler and shows the
    line itself: printing it as well showed it twice.

    ``printed`` is the fallback text, a string or a list of strings printed
    one per line; it lets stdio keep its own wording and indentation. By
    default the fallback is ``message % args``, with a ``Warning: `` prefix
    at WARNING and above. ``print_fn`` receives each line (default: print
    to stderr).
    """
    logger.log(level, message, *args)
    if logging_reaches_stderr(logger, level):
        return
    if printed is None:
        text = message % args if args else message
        if level >= logging.WARNING:
            text = f"Warning: {text}"
        lines = [text]
    elif isinstance(printed, str):
        lines = [printed]
    else:
        lines = list(printed)
    emit = print_fn if print_fn is not None else _print_stderr
    for line in lines:
        emit(line)


class _ResolvedLogFile(NamedTuple):
    path: str | None
    is_default: bool


def _resolve_log_file(
    mode: str, log_file: str | None
) -> _ResolvedLogFile:
    """Return the log file path to use (None for "no log file") and
    whether it is the built-in default.

    Order: the ``log_file`` argument if given, then the ``LOG_FILE``
    environment variable if it is set (even to empty), then the built-in
    default: ``default_mcp_log_file()`` for "mcp" (stdio), none for
    "cli"/"http". An empty or whitespace-only value at either step means
    "no log file". ``main()`` passes ``--log-file``, else ``LOG_FILE``,
    else YAML ``logging.file`` as ``log_file``.
    """
    is_default = False
    if log_file is None:
        log_file = os.environ.get("LOG_FILE")
    if log_file is None and mode == "mcp":
        log_file = default_mcp_log_file()
        is_default = True
    if not log_file or not log_file.strip():
        return _ResolvedLogFile(None, False)
    # ``~`` and ``~user`` expand whatever the source; a relative path
    # keeps resolving against the working directory.
    return _ResolvedLogFile(os.path.expanduser(log_file), is_default)


def _open_mcp_file_handler(
    resolved: _ResolvedLogFile,
) -> logging.Handler | None:
    """The handler for stdio's log file, or None to log to stderr.

    The default path gets its directory created (mode 0700), rotation,
    and a stderr fallback if it cannot be opened (a read-only home, say):
    the operator did not choose it, so failing to start over it would be
    wrong. An explicit path is opened as given, and an ``OSError`` from
    it propagates: ``main()`` turns it into a startup failure.
    """
    assert resolved.path is not None
    path = resolved.path
    if not resolved.is_default:
        return _PrivateFileHandler(path, mode="a")
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        return _PrivateRotatingFileHandler(
            path,
            mode="a",
            maxBytes=_DEFAULT_LOG_MAX_BYTES,
            backupCount=_DEFAULT_LOG_BACKUP_COUNT,
        )
    except OSError as e:
        _print_stderr(
            f"WARNING: cannot open default log file {path}: "
            f"{e.strerror or e}; logging to stderr"
        )
        return None


def setup_logging(
    mode: str = "cli",
    debug: bool = False,
    log_file: str | None = None,
    debug_format: str = "text",
    level: str | None = None,
) -> None:
    """
    Configure logging based on execution mode.

    Args:
        mode: "mcp" for stdio transport: file logging by default, and
            never stdout; "cli" or "http" for stderr logging (with an
            optional companion log file).
        debug: If True, the level is DEBUG whatever ``level`` and
            LOG_LEVEL say.
        log_file: Log file path (overrides LOG_FILE env var). An empty
            string means no log file. ``main()`` resolves ``--log-file``,
            LOG_FILE and YAML ``logging.file`` into this argument.
        debug_format: "text" (default) or "json" for structured output.
        level: Level name (DEBUG, INFO, WARNING, ERROR, CRITICAL), any
            case. ``main()`` resolves LOG_LEVEL and YAML ``logging.level``
            into this argument. None, or a blank string, falls through to
            the LOG_LEVEL environment variable, then the mode default.

    Level precedence: ``debug``, then ``level``, then the LOG_LEVEL
    environment variable (so a direct caller still gets it), then the
    mode default.

    Environment variables:
        LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR), used when
                   ``level`` is None. Default: WARNING for MCP mode,
                   INFO for CLI mode.
        LOG_FILE: Log file path, used when ``log_file`` is None; set to
                  an empty string for no log file. Default: the
                  per-user state file (``default_mcp_log_file()``,
                  rotating, private) for MCP (stdio) mode, no file for
                  CLI and http mode. In MCP mode with no log file,
                  logging goes to stderr (never stdout).

    Raises:
        OSError: an explicitly chosen log file cannot be opened. An
            unopenable default file does not raise: it logs to stderr
            after one warning line.
    """
    default_level = "WARNING" if mode == "mcp" else "INFO"
    if debug:
        log_level = logging.DEBUG
    else:
        name = level
        if name is None or not name.strip():
            name = os.environ.get("LOG_LEVEL")
        if name is None or not name.strip():
            name = default_level
        log_level = getattr(logging, name.strip().upper(), logging.INFO)

    resolved = _resolve_log_file(mode, log_file)
    final_log_file = resolved.path

    file_handler_for_mcp = (
        _open_mcp_file_handler(resolved)
        if mode == "mcp" and final_log_file
        else None
    )

    if file_handler_for_mcp is not None:
        # MCP mode: Log to file to avoid stdout contamination
        # stdio transport uses stdout for JSON-RPC messages
        file_handler_for_mcp.setFormatter(
            logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
        )
        logging.basicConfig(
            level=log_level, handlers=[file_handler_for_mcp]
        )
    elif mode == "mcp":
        # MCP mode with no log file (disabled, or the default one could
        # not be opened): stderr, never stdout
        # (stdout carries JSON-RPC; the stdio spec allows stderr logs)
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(
            logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
        )
        logging.basicConfig(level=log_level, handlers=[stderr_handler])
    else:
        # CLI mode: Log to stderr (safe, doesn't interfere with output)
        # When a log file resolves (--log-file or LOG_FILE), also write to file
        handlers: list[logging.Handler] = []
        stderr_handler = logging.StreamHandler(sys.stderr)

        if debug_format == "json":
            stderr_handler.setFormatter(
                JsonFormatter(datefmt="%Y-%m-%d %H:%M:%S")
            )
        else:
            stderr_handler.setFormatter(
                logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
            )
        handlers.append(stderr_handler)

        if final_log_file:
            file_handler = _PrivateFileHandler(final_log_file, mode="a")
            if debug_format == "json":
                file_handler.setFormatter(
                    JsonFormatter(datefmt="%Y-%m-%d %H:%M:%S")
                )
            else:
                file_handler.setFormatter(
                    logging.Formatter(
                        "[%(asctime)s] [%(levelname)s] %(name)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S",
                    )
                )
            handlers.append(file_handler)

        logging.basicConfig(
            level=log_level,
            handlers=handlers,
        )

    # Silence third-party libs unless DEBUG
    if log_level != logging.DEBUG:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("uvicorn").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
