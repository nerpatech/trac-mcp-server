"""Where the file tools' bytes come from and go to (ticket #111).

The path-taking tools -- ``wiki_file_push``/``pull``/``detect_format``
and the ticket/wiki ``*_attachment_put``/``get`` pairs -- were written
when the server always ran on its caller's machine, so a ``file_path``
named a file both sides could see. Over the http transport it does not:
the path resolves on the SERVER, where the caller's file does not exist,
and where nothing confines it to anything but what this process's uid
can read or write. So each of those tools also takes its bytes inline
(``content`` / ``content_base64``) and, given no output path, returns
them inline; and the path forms are refused unless the operator set
``file_access: local``.

``file_access`` defaults by transport (see
``config_bootstrap.bootstrap_server_config``): ``local`` on stdio, ``off``
on http. The module-level default here is ``off`` so that a server wired
up without ``main()`` -- a test harness, a future entry point -- fails
closed rather than open.

The inline forms put the bytes into the tool call and its result, which
is what the path design avoided (see ``ticket_attachment``'s module
docstring). They are meant for a program, not a model: the ``trac-mcp``
CLI reads and writes the caller's local files and moves the bytes over
this transport, so they never enter a transcript either way.
"""

import base64
import binascii
from pathlib import Path
from typing import Any, Literal

import mcp.types as types

from ...core.async_utils import run_sync
from ...file_handler import (
    read_file_with_encoding,
    validate_file_path,
    validate_output_path,
)
from .errors import build_error_response

FileAccess = Literal["local", "off"]

DEFAULT_MAX_INLINE_BYTES = 5 * 1024 * 1024

_file_access: FileAccess = "off"
_max_inline_bytes: int = DEFAULT_MAX_INLINE_BYTES


def set_file_access(
    mode: FileAccess, max_inline_bytes: int = DEFAULT_MAX_INLINE_BYTES
) -> None:
    """Set the process-wide file access mode and inline cap (from main())."""
    global _file_access, _max_inline_bytes
    if mode not in ("local", "off"):
        raise ValueError(
            f"Invalid file_access '{mode}': must be 'local' or 'off'"
        )
    _file_access = mode
    _max_inline_bytes = max_inline_bytes


def get_file_access() -> FileAccess:
    """Return the current file access mode."""
    return _file_access


def get_max_inline_bytes() -> int:
    """Return the largest payload a tool returns inline."""
    return _max_inline_bytes


def _path_refused(key: str, cli_hint: str) -> types.CallToolResult:
    return build_error_response(
        "permission_denied",
        f"{key} is refused: file_access is 'off' on this server, so a "
        "path would name a file on the server's machine, not yours.",
        f"Use the trac-mcp CLI on your own machine ({cli_hint}), or pass "
        "the bytes inline instead of a path.",
    )


def _either_or(
    args: dict[str, Any], path_key: str, content_key: str
) -> types.CallToolResult | None:
    if (
        args.get(path_key) is not None
        and args.get(content_key) is not None
    ):
        return build_error_response(
            "validation_error",
            f"Pass {path_key} or {content_key}, not both",
            f"Drop one of {path_key} and {content_key}.",
        )
    if args.get(path_key) is None and args.get(content_key) is None:
        return build_error_response(
            "validation_error",
            f"{content_key} is required",
            f"Provide {content_key} (or {path_key}, only where the "
            "server's file_access is 'local').",
        )
    return None


async def read_text_input(
    args: dict[str, Any], cli_hint: str
) -> tuple[str, str, Path | None, str] | types.CallToolResult:
    """Resolve a text tool's input from ``file_path`` or ``content``.

    Returns ``(text, encoding, name, source)``, where ``name`` is a Path
    usable for extension-based format detection (the server file, or the
    caller's ``filename`` hint) or None, and ``source`` is what the result
    reports the bytes came from. Returns an error result instead when the
    arguments are unusable.
    """
    refusal = _either_or(args, "file_path", "content")
    if refusal is not None:
        return refusal
    file_path = args.get("file_path")
    if file_path is not None:
        if _file_access != "local":
            return _path_refused("file_path", cli_hint)
        resolved = await run_sync(validate_file_path, file_path)
        text, encoding = await run_sync(
            read_file_with_encoding, resolved
        )
        return text, encoding, resolved, str(file_path)
    filename = args.get("filename")
    name = Path(filename) if filename else None
    return args["content"], "utf-8", name, "inline content"


async def read_binary_input(
    args: dict[str, Any], cli_hint: str
) -> tuple[bytes, str | None, str] | types.CallToolResult:
    """Resolve an attachment upload's bytes from ``file_path`` or
    ``content_base64``.

    Returns ``(data, default_filename, source)``; ``default_filename`` is
    the server file's basename on the path form and None inline, where the
    caller must name the attachment.
    """
    refusal = _either_or(args, "file_path", "content_base64")
    if refusal is not None:
        return refusal
    file_path = args.get("file_path")
    if file_path is not None:
        if _file_access != "local":
            return _path_refused("file_path", cli_hint)
        resolved = await run_sync(validate_file_path, file_path)
        data = await run_sync(resolved.read_bytes)
        return data, resolved.name, str(file_path)
    try:
        data = base64.b64decode(args["content_base64"], validate=True)
    except (binascii.Error, ValueError):
        return build_error_response(
            "validation_error",
            "content_base64 is not valid base64",
            "Encode the attachment bytes with standard base64.",
        )
    if not args.get("filename"):
        return build_error_response(
            "validation_error",
            "filename is required with content_base64",
            "Provide the attachment filename to store.",
        )
    return data, None, "inline content"


async def check_output_path(
    args: dict[str, Any], path_key: str, cli_hint: str
) -> types.CallToolResult | None:
    """Check an output path before any Trac call is made: refuse it when
    file_access is off, and otherwise validate it (absolute, parent
    exists), raising ValueError, so a bad path never costs a fetch."""
    if args.get(path_key) is None:
        return None
    if _file_access != "local":
        return _path_refused(path_key, cli_hint)
    await run_sync(validate_output_path, args[path_key])
    return None


async def write_output(
    args: dict[str, Any], path_key: str, payload: bytes
) -> dict[str, Any]:
    """Write ``payload`` to the output path. Call only after
    :func:`check_output_path` passed and the path is present."""
    resolved = await run_sync(validate_output_path, args[path_key])
    await run_sync(resolved.write_bytes, payload)
    return {
        path_key: str(args[path_key]),
        "bytes_written": len(payload),
    }


def inline_too_large(
    size: int, cli_hint: str
) -> types.CallToolResult | None:
    """Refuse an inline result over the server's max_inline_bytes."""
    if size <= _max_inline_bytes:
        return None
    return build_error_response(
        "validation_error",
        f"Payload is {size} bytes, over this server's inline limit of "
        f"{_max_inline_bytes} bytes (max_inline_bytes)",
        f"Ask the operator to raise max_inline_bytes, or fetch it another "
        f"way; the trac-mcp CLI ({cli_hint}) is bound by the same limit.",
    )


def encode_base64(payload: bytes) -> str:
    """Standard base64 text for an inline binary result."""
    return base64.b64encode(payload).decode("ascii")


__all__ = [
    "DEFAULT_MAX_INLINE_BYTES",
    "check_output_path",
    "encode_base64",
    "get_file_access",
    "get_max_inline_bytes",
    "inline_too_large",
    "read_binary_input",
    "read_text_input",
    "set_file_access",
    "write_output",
]
