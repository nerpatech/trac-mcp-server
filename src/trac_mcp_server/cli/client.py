"""trac-mcp: move files between this machine and a trac-mcp-server (ticket #111).

The server's file tools cannot see the caller's filesystem once the server
runs somewhere else (the http transport), and with ``file_access: off``
they refuse paths outright. This CLI is the other half: it runs on the
caller's machine, reads and writes the local files itself, and calls the
same tools over streamable HTTP with their inline forms (``content`` /
``content_base64``). A program serializes the bytes, so nothing passes
through a model's transcript, and the call keeps everything the server
does on a write: the caller's identity, Markdown conversion, the
indentation guard and the link gate.

Where to connect, in order:

1. ``--url`` (token from ``--token-env``, default ``TRAC_MCP_AUTH_TOKEN``);
2. the ``trac-http`` entry (or ``--server NAME``) of the nearest
   ``.mcp.json`` from the working directory up -- its ``url`` and its
   ``Authorization`` header, with ``${VAR}`` / ``${VAR:-default}``
   expanded from the environment, exactly as Claude Code would;
3. ``TRAC_MCP_URL`` and ``TRAC_MCP_AUTH_TOKEN`` from the environment.

Using the project's own ``.mcp.json`` means a session's CLI calls run as
the same Trac identity as its MCP calls (e.g. ``auto_pm`` vs
``agent_rpc``), with no per-project setup.
"""

import argparse
import asyncio
import base64
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .. import __version__
from ..file_handler import detect_file_format, read_file_with_encoding

EXIT_OK = 0
EXIT_TOOL_ERROR = 1  # the server answered with an error result
EXIT_USAGE_ERROR = 2  # argparse, or a local file problem
EXIT_CONNECT_ERROR = (
    3  # no endpoint, missing token, or transport failure
)

DEFAULT_SERVER = "trac-http"
DEFAULT_TOKEN_ENV = "TRAC_MCP_AUTH_TOKEN"


class CliError(Exception):
    """A failure the CLI reports as one line and an exit code."""

    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Endpoint resolution
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_vars(value: str, environ: Mapping[str, str]) -> str:
    """Expand ``${VAR}`` and ``${VAR:-default}`` the way Claude Code
    expands an ``.mcp.json`` entry. An unset ``${VAR}`` with no default is
    an error naming the variable (never its value)."""

    def _sub(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        if name in environ and environ[name] != "":
            return environ[name]
        if default is not None:
            return default
        raise CliError(
            f"environment variable {name} is not set",
            EXIT_CONNECT_ERROR,
        )

    return _VAR_RE.sub(_sub, value)


def find_mcp_json(start: Path) -> Path | None:
    """The nearest ``.mcp.json`` from ``start`` up to the filesystem root."""
    for directory in [start, *start.parents]:
        candidate = directory / ".mcp.json"
        if candidate.is_file():
            return candidate
    return None


def _bearer(header: str) -> str:
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise CliError(
            "the Authorization header is not 'Bearer <token>'",
            EXIT_CONNECT_ERROR,
        )
    return token


def resolve_endpoint(
    args: argparse.Namespace, cwd: Path, environ: Mapping[str, str]
) -> tuple[str, str | None, str]:
    """Return ``(url, token, where)``; ``where`` says which source won,
    for error messages. The token is never part of ``where``."""
    if args.url:
        token_env = args.token_env or DEFAULT_TOKEN_ENV
        return args.url, environ.get(token_env) or None, "--url"

    mcp_json = find_mcp_json(cwd)
    if mcp_json is not None:
        try:
            servers = json.loads(mcp_json.read_text()).get(
                "mcpServers", {}
            )
        except (OSError, json.JSONDecodeError) as e:
            raise CliError(
                f"cannot read {mcp_json}: {e}", EXIT_CONNECT_ERROR
            ) from None
        entry = servers.get(args.server)
        if entry is not None:
            if "url" not in entry:
                raise CliError(
                    f"server '{args.server}' in {mcp_json} has no url "
                    "(is it a stdio server?)",
                    EXIT_CONNECT_ERROR,
                )
            url = expand_vars(entry["url"], environ)
            if args.token_env:
                token = environ.get(args.token_env) or None
            else:
                header = (entry.get("headers") or {}).get(
                    "Authorization"
                )
                token = (
                    _bearer(expand_vars(header, environ))
                    if header
                    else None
                )
            return url, token, f"{mcp_json} ({args.server})"
        if args.server != DEFAULT_SERVER:
            raise CliError(
                f"no server '{args.server}' in {mcp_json}",
                EXIT_CONNECT_ERROR,
            )

    url = environ.get("TRAC_MCP_URL")
    if url:
        token_env = args.token_env or DEFAULT_TOKEN_ENV
        return url, environ.get(token_env) or None, "TRAC_MCP_URL"

    raise CliError(
        "no server to connect to: pass --url, run from a project whose "
        f".mcp.json has a '{args.server}' entry, or set TRAC_MCP_URL",
        EXIT_CONNECT_ERROR,
    )


# ---------------------------------------------------------------------------
# One tool call over streamable HTTP
# ---------------------------------------------------------------------------


async def call_tool(
    url: str, token: str | None, name: str, arguments: dict[str, Any]
):
    """Open one MCP session, call one tool, return its CallToolResult."""
    import httpx
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(30.0, read=300.0),
        follow_redirects=True,
    ) as http:
        async with streamable_http_client(url, http_client=http) as (
            read,
            write,
            _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, arguments)


def _result_text(result) -> str:
    return "\n".join(
        block.text for block in result.content if hasattr(block, "text")
    )


def _instance_line(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        if line.startswith("[instance:"):
            return line
    return None


# ---------------------------------------------------------------------------
# Local file I/O
# ---------------------------------------------------------------------------


def _read_text(path: str) -> tuple[str, str, str | None]:
    """Return ``(text, encoding, filename)``; ``-`` reads stdin."""
    if path == "-":
        return sys.stdin.read(), "utf-8", None
    p = Path(path)
    if not p.is_file():
        raise CliError(f"not a file: {path}", EXIT_USAGE_ERROR)
    text, encoding = read_file_with_encoding(p)
    return text, encoding, p.name


def _read_bytes(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read()
    p = Path(path)
    if not p.is_file():
        raise CliError(f"not a file: {path}", EXIT_USAGE_ERROR)
    return p.read_bytes()


def _write_bytes(path: str, data: bytes) -> None:
    if path == "-":
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return
    p = Path(path)
    if not p.parent.is_dir():
        raise CliError(
            f"output directory does not exist: {p.parent}",
            EXIT_USAGE_ERROR,
        )
    p.write_bytes(data)


def _say(path_is_stdout: bool, message: str) -> None:
    """Summaries go to stdout, unless stdout is carrying the data."""
    print(message, file=sys.stderr if path_is_stdout else sys.stdout)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _with_instance(args: argparse.Namespace, arguments: dict) -> dict:
    if args.instance:
        arguments["instance"] = args.instance
    return arguments


def _attach_target(args: argparse.Namespace) -> tuple[str, dict]:
    if args.ticket is not None:
        return "ticket", {"ticket_id": args.ticket}
    return "wiki", {"page_name": args.page}


async def _run(
    args: argparse.Namespace, url: str, token: str | None
) -> int:
    cmd = args.command

    if cmd == "wiki-push":
        text, encoding, filename = _read_text(args.file)
        if encoding != "utf-8":
            print(
                f"warning: {args.file} decoded as '{encoding}', not UTF-8 "
                "-- check the pushed page",
                file=sys.stderr,
            )
        arguments: dict[str, Any] = {
            "page_name": args.page,
            "content": text,
            "format": args.format,
            "strip_frontmatter": not args.keep_frontmatter,
        }
        if filename:
            arguments["filename"] = filename
        if args.comment:
            arguments["comment"] = args.comment
        if args.target_cap is not None:
            arguments["target_cap"] = args.target_cap
        result = await call_tool(
            url,
            token,
            "wiki_file_push",
            _with_instance(args, arguments),
        )
        return _report(result)

    if cmd == "wiki-pull":
        arguments = {"page_name": args.page, "format": args.format}
        if args.page_version is not None:
            arguments["version"] = args.page_version
        result = await call_tool(
            url,
            token,
            "wiki_file_pull",
            _with_instance(args, arguments),
        )
        if result.isError:
            return _report(result)
        sc = result.structuredContent or {}
        data = sc["content"].encode("utf-8")
        _write_bytes(args.file, data)
        _say(
            args.file == "-",
            f"Pulled wiki page '{args.page}' (version {sc.get('version')}, "
            f"format={sc.get('format')}) to {args.file} ({len(data)} bytes)",
        )
        _say_instance(args.file == "-", result)
        return EXIT_OK

    if cmd == "attach-put":
        realm, arguments = _attach_target(args)
        data = _read_bytes(args.file)
        filename = args.filename or (
            Path(args.file).name if args.file != "-" else None
        )
        if not filename:
            raise CliError(
                "--filename is required when reading stdin",
                EXIT_USAGE_ERROR,
            )
        arguments.update(
            {
                "filename": filename,
                "content_base64": base64.b64encode(data).decode(
                    "ascii"
                ),
                "replace": args.replace,
            }
        )
        if args.description:
            arguments["description"] = args.description
        result = await call_tool(
            url,
            token,
            f"{realm}_attachment_put",
            _with_instance(args, arguments),
        )
        return _report(result)

    if cmd == "attach-get":
        realm, arguments = _attach_target(args)
        arguments["filename"] = args.name
        result = await call_tool(
            url,
            token,
            f"{realm}_attachment_get",
            _with_instance(args, arguments),
        )
        if result.isError:
            return _report(result)
        sc = result.structuredContent or {}
        data = base64.b64decode(sc["content_base64"])
        _write_bytes(args.file, data)
        _say(
            args.file == "-",
            f"Downloaded attachment '{args.name}' to {args.file} "
            f"({len(data)} bytes)",
        )
        _say_instance(args.file == "-", result)
        return EXIT_OK

    raise CliError(f"unknown command {cmd}", EXIT_USAGE_ERROR)


def _report(result) -> int:
    text = _result_text(result)
    if result.isError:
        print(text, file=sys.stderr)
        return EXIT_TOOL_ERROR
    print(text)
    return EXIT_OK


def _say_instance(to_stderr: bool, result) -> None:
    line = _instance_line(_result_text(result))
    if line:
        _say(to_stderr, line)


def _detect_format(args: argparse.Namespace) -> int:
    """Runs locally: the same detection the server would apply."""
    text, encoding, filename = _read_text(args.file)
    fmt = detect_file_format(Path(filename or "stdin"), text)
    print(f"File: {args.file}\nFormat: {fmt}\nEncoding: {encoding}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Argument parsing and entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trac-mcp",
        description=(
            "Push and pull wiki pages and attachments between this machine "
            "and a trac-mcp-server over HTTP. Connects using --url, else "
            "the nearest .mcp.json's trac-http entry, else TRAC_MCP_URL."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"trac-mcp version {__version__}",
    )
    parser.add_argument(
        "--url", help="MCP endpoint URL, e.g. http://host:8091/mcp"
    )
    parser.add_argument(
        "--server",
        default=DEFAULT_SERVER,
        help=f".mcp.json server entry to use (default: {DEFAULT_SERVER})",
    )
    parser.add_argument(
        "--token-env",
        help="Environment variable holding the bearer token (default: the "
        f".mcp.json entry's own, else {DEFAULT_TOKEN_ENV})",
    )
    parser.add_argument(
        "--instance",
        help="Trac instance path, e.g. /auto_pm (default: the server's)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "wiki-push", help="Push a local file to a wiki page"
    )
    p.add_argument("page", help="Target wiki page name")
    p.add_argument("file", help="Local file to push ('-' for stdin)")
    p.add_argument(
        "--format",
        choices=["auto", "markdown", "tracwiki"],
        default="auto",
        help="Source format (default: auto, from extension then content)",
    )
    p.add_argument("--comment", help="Change comment")
    p.add_argument(
        "--keep-frontmatter",
        action="store_true",
        help="Do not strip YAML frontmatter",
    )
    p.add_argument(
        "--target-cap",
        type=int,
        help="Max cross-instance link targets the write gate probes",
    )

    p = sub.add_parser(
        "wiki-pull", help="Pull a wiki page to a local file"
    )
    p.add_argument("page", help="Wiki page name")
    p.add_argument("file", help="Local output file ('-' for stdout)")
    p.add_argument(
        "--format",
        choices=["markdown", "tracwiki"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    p.add_argument(
        "--page-version",
        type=int,
        help="Specific page version to pull",
    )

    for name, helptext in (
        ("attach-put", "Upload a local file as an attachment"),
        ("attach-get", "Download an attachment to a local file"),
    ):
        p = sub.add_parser(name, help=helptext)
        target = p.add_mutually_exclusive_group(required=True)
        target.add_argument("--ticket", type=int, help="Ticket number")
        target.add_argument("--page", help="Wiki page name")
        if name == "attach-put":
            p.add_argument(
                "file", help="Local file to upload ('-' for stdin)"
            )
            p.add_argument(
                "--filename",
                help="Attachment name (default: the file's basename)",
            )
            p.add_argument(
                "--description", help="Attachment description"
            )
            p.add_argument(
                "--replace",
                action="store_true",
                help="Overwrite an existing attachment of the same name",
            )
        else:
            p.add_argument("name", help="Attachment filename")
            p.add_argument(
                "file", help="Local output file ('-' for stdout)"
            )

    p = sub.add_parser(
        "detect-format",
        help="Detect Markdown vs TracWiki locally (no server call)",
    )
    p.add_argument("file", help="Local file ('-' for stdin)")
    return parser


def main(
    argv: list[str] | None = None,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "detect-format":
            return _detect_format(args)
        url, token, where = resolve_endpoint(
            args,
            cwd or Path.cwd(),
            os.environ if environ is None else environ,
        )
        try:
            return asyncio.run(_run(args, url, token))
        except CliError:
            raise
        except Exception as e:  # transport, HTTP status, protocol
            raise CliError(
                f"call to {url} (from {where}) failed: "
                f"{type(e).__name__}: {e}",
                EXIT_CONNECT_ERROR,
            ) from None
    except CliError as e:
        print(f"trac-mcp: {e}", file=sys.stderr)
        return e.code


def run() -> None:
    """Console-script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    run()
