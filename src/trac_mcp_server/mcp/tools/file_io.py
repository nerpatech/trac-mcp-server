"""Where the file tools' bytes come from and go to (ticket #111).

The path-taking tools -- ``wiki_file_push``/``pull``/``detect_format``
and the ticket/wiki ``*_attachment_put``/``get`` pairs -- were written
when the server always ran on its caller's machine, so a ``file_path``
named a file both sides could see. Over the http transport it does not:
the path resolves on the SERVER, where the caller's file does not exist,
and where nothing confines it to anything but what this process's uid
can read or write. So each of those tools also takes its bytes inline
(``content`` / ``content_base64``) and, with ``file_access: off``, returns
them inline; the path forms are refused unless the operator set
``file_access: local``, where the output paths stay required as before.

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
import re
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
    content = args["content"]
    too_large = _over_cap(len(content.encode("utf-8")), "content")
    if too_large is not None:
        return too_large
    filename = args.get("filename")
    name = Path(filename) if filename else None
    return content, "utf-8", name, "inline content"


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
    # Line-wrapped base64 (GNU `base64` wraps at 76 columns) is still
    # base64; strip whitespace, then decode strictly.
    encoded = re.sub(r"\s+", "", args["content_base64"])
    try:
        data = base64.b64decode(encoded, validate=True)
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
    too_large = _over_cap(len(data), "content_base64")
    if too_large is not None:
        return too_large
    return data, None, "inline content"


async def check_output_path(
    args: dict[str, Any], path_key: str, cli_hint: str
) -> types.CallToolResult | None:
    """Check an output path before any Trac call is made.

    With file_access off a path is refused, and its absence means "return
    the bytes inline". With file_access local the path is REQUIRED, as it
    was before ticket #111: that mode is the stdio one, where the caller
    is a model and the path form exists precisely to keep the bytes out of
    its transcript, so a forgotten path must not silently inline them. A
    given path is validated here (absolute, parent exists; ValueError
    otherwise) so a bad one never costs a fetch.
    """
    if _file_access != "local":
        if args.get(path_key) is not None:
            return _path_refused(path_key, cli_hint)
        return None
    if args.get(path_key) is None:
        return build_error_response(
            "validation_error",
            f"{path_key} is required",
            f"Provide {path_key} parameter.",
        )
    await run_sync(validate_output_path, args[path_key])
    return None


async def deliver_output(
    args: dict[str, Any],
    path_key: str,
    payload: bytes,
    *,
    text: str | None = None,
) -> dict[str, Any] | types.CallToolResult:
    """Deliver a file tool's result, after :func:`check_output_path` passed.

    With a path (file_access local): write ``payload`` there and return
    ``{path_key, bytes_written}``. Without (file_access off): return it
    inline as ``{content, bytes}`` when ``text`` is given, else as
    ``{content_base64, bytes}`` -- or a refusal over max_inline_bytes.
    """
    if args.get(path_key) is not None:
        resolved = await run_sync(validate_output_path, args[path_key])
        await run_sync(resolved.write_bytes, payload)
        return {
            path_key: str(args[path_key]),
            "bytes_written": len(payload),
        }
    too_large = _over_cap(len(payload), "result")
    if too_large is not None:
        return too_large
    if text is not None:
        return {"content": text, "bytes": len(payload)}
    return {
        "content_base64": base64.b64encode(payload).decode("ascii"),
        "bytes": len(payload),
    }


def _over_cap(size: int, what: str) -> types.CallToolResult | None:
    """Refuse an inline payload, either direction, over max_inline_bytes."""
    if size <= _max_inline_bytes:
        return None
    return build_error_response(
        "validation_error",
        f"Inline {what} is {size} bytes, over this server's limit of "
        f"{_max_inline_bytes} bytes (max_inline_bytes)",
        "Ask the operator to raise max_inline_bytes; the trac-mcp CLI "
        "moves its bytes inline too, so it is bound by the same limit.",
    )


__all__ = [
    "DEFAULT_MAX_INLINE_BYTES",
    "check_output_path",
    "deliver_output",
    "get_file_access",
    "get_max_inline_bytes",
    "read_binary_input",
    "read_text_input",
    "set_file_access",
]
