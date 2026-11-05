"""Logging utilities for the trac-mcp-server package.

Provides a :class:`JsonFormatter` that emits one JSON object per log record
(JSON-lines output) and a :func:`setup_logging` helper that configures the
root logger for either MCP mode (a log file by default, never stdout, so
stdout remains clean for JSON-RPC traffic) or CLI/http mode (human-friendly
stderr logging, with an optional companion log file).
"""

import json
import logging
import os
import sys


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


DEFAULT_MCP_LOG_FILE = "/tmp/trac-mcp-server.log"

_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _resolve_log_file(mode: str, log_file: str | None) -> str | None:
    """Return the log file path to use, or None for "no log file".

    Order: the ``log_file`` argument if given, then the ``LOG_FILE``
    environment variable if it is set (even to empty), then the built-in
    default: ``/tmp/trac-mcp-server.log`` for "mcp" (stdio), none for
    "cli"/"http". An empty or whitespace-only value at either step means
    "no log file".
    """
    if log_file is None:
        log_file = os.environ.get("LOG_FILE")
    if log_file is None:
        log_file = DEFAULT_MCP_LOG_FILE if mode == "mcp" else None
    return log_file if log_file and log_file.strip() else None


def setup_logging(
    mode: str = "cli",
    debug: bool = False,
    log_file: str | None = None,
    debug_format: str = "text",
) -> None:
    """
    Configure logging based on execution mode.

    Args:
        mode: "mcp" for stdio transport: file logging by default, and
            never stdout; "cli" or "http" for stderr logging (with an
            optional companion log file).
        debug: If True, overrides LOG_LEVEL to DEBUG.
        log_file: Log file path (overrides LOG_FILE env var). An empty
            string means no log file.
        debug_format: "text" (default) or "json" for structured output.

    Environment variables:
        LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR).
                   Default: WARNING for MCP mode, INFO for CLI mode.
        LOG_FILE: Log file path, used when ``log_file`` is None; set to
                  an empty string for no log file. Default:
                  /tmp/trac-mcp-server.log for MCP (stdio) mode, no file
                  for CLI and http mode. In MCP mode with no log file,
                  logging goes to stderr (never stdout).
    """
    # Determine log level from environment or defaults
    default_level = "WARNING" if mode == "mcp" else "INFO"
    env_level = os.getenv("LOG_LEVEL", default_level).upper()

    # debug parameter overrides environment
    if debug:
        log_level = logging.DEBUG
    else:
        log_level = getattr(logging, env_level, logging.INFO)

    final_log_file = _resolve_log_file(mode, log_file)

    if mode == "mcp" and final_log_file:
        # MCP mode: Log to file to avoid stdout contamination
        # stdio transport uses stdout for JSON-RPC messages
        logging.basicConfig(
            level=log_level,
            format=_LOG_FORMAT,
            datefmt=_LOG_DATEFMT,
            filename=final_log_file,
            filemode="a",
        )
    elif mode == "mcp":
        # MCP mode with the log file disabled: stderr, never stdout
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
            file_handler = logging.FileHandler(final_log_file, mode="a")
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
