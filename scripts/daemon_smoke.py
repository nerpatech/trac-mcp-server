#!/usr/bin/env python3
"""Smoke-test a DEPLOYED trac-mcp-server daemon (Trac #161).

``ci-live.sh --daemon <url>`` runs this instead of the in-process live
suite. The in-process suite imports the checkout's own code and builds its
own app, so it cannot see what only the deployed daemon has: the FreeBSD
wheels it actually installed (``constraints/deploy.txt``), the config it
actually ships, its rc script and its environment. This script talks to
the daemon over HTTP, as a client would, and checks that what answers is
this checkout's release.

Usage:

    TRAC_MCP_AUTH_TOKEN_STAGE=... python scripts/daemon_smoke.py URL
    python scripts/daemon_smoke.py URL --token-env OTHER_VAR
    python scripts/daemon_smoke.py URL --instance /other --allow-instance-writes

``URL`` is the daemon's base (``http://host:8092``); a trailing ``/mcp`` or
``/`` is accepted and removed. Checks run in order and stop at the first
failure, one ``PASS <n> <name>`` / ``FAIL <n> <name>: <reason>`` line each:

  1 healthz        GET /healthz is 200 {"status": "ok"}
  2 auth_enforced  POST /mcp with no Authorization is 401
  3 tools          initialize + tools/list; every tool this checkout
                   defines is listed by the daemon
  4 read           wiki_get WikiStart on the test instance
  5 write_read     write Smoke/DaemonSmoke with a fresh nonce, read it
                   back, compare byte for byte
  6 comment_plugin post a comment on the smoke ticket, edit it, and see
                   the edit in ticket_comment_history (needs the
                   tracrpc_comment plugin on the daemon's Trac)

Checks 4-6 pass ``instance=<--instance>`` (default ``/trac_test``) on
EVERY tool call, never relying on the daemon's default, which on the
shared daemon is a production store. Each result's reported instance must
equal the requested one, or the check fails. Checks 5 and 6 WRITE there, so
an ``--instance`` other than ``/trac_test`` is refused (exit 2, NOT RUN)
unless ``--allow-instance-writes`` is also given.

Exit codes are the three-state contract of ``Rules/testing/
ExitCodeContractHasThreeStates``:

  0  every check passed
  1  a check failed -- including a daemon that is unreachable or refuses
     the connection: that is a finding about the release
  2  could not run: the token variable is unset or empty, the URL is
     malformed, or a write target other than /trac_test was not
     explicitly allowed. Decided before any network call, and says so
     ("NOT RUN").

The token is never printed. It is sent only as a bearer header; every
message built from an exception or a server reply passes through
``scrub`` first.
"""

import argparse
import asyncio
import logging
import os
import re
import sys
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from urllib.parse import urlsplit

DEFAULT_TOKEN_ENV = "TRAC_MCP_AUTH_TOKEN_STAGE"
# Checks 4-6 read and write here, never at the daemon's default instance.
DEFAULT_INSTANCE = "/trac_test"
_INSTANCE_NAME = re.compile(r"/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_NOT_RUN = 2

WIKI_PAGE = "Smoke/DaemonSmoke"
TICKET_SUMMARY = "ci-live.sh --daemon smoke ticket"
TICKET_SEARCH = f"summary~={TICKET_SUMMARY}&status!=closed"

HTTP_TIMEOUT = 15.0
# One MCP call. A tool call that reaches Trac and back is seconds, so a
# call still pending after this is a finding, not a slow day.
CALL_TIMEOUT = 120.0
# initialize + tools/list: a daemon that cannot do these in this long is
# not serving MCP, whatever else it answers.
INIT_TIMEOUT = 30.0

_NOT_RUN_TAIL = "this is not a passing result"
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class CheckFailed(Exception):
    """A check's verdict: the reason is the message."""


# ---------------------------------------------------------------------------
# Pure helpers (unit tested)
# ---------------------------------------------------------------------------


def normalise_base(url: str) -> str:
    """The daemon base URL from what the operator typed.

    ``http://h:8092``, ``http://h:8092/``, ``http://h:8092/mcp`` and
    ``http://h:8092/mcp/`` all give ``http://h:8092``. Raises ``ValueError``
    for anything that is not an http(s) URL with a host, and never puts the
    rejected text in the message: a URL can carry a secret.
    """
    text = (url or "").strip()
    try:
        parts = urlsplit(text)
        hostname = parts.hostname
        parts.port  # noqa: B018 -- raises ValueError on a bad port
    except ValueError:
        raise ValueError("it does not parse as a URL") from None
    if parts.scheme not in ("http", "https"):
        raise ValueError("it must start with http:// or https://")
    if not hostname:
        raise ValueError("it has no host")
    if parts.username or parts.password:
        raise ValueError("it must not carry credentials")
    if parts.query or parts.fragment:
        raise ValueError("it must not have a query or fragment")
    path = parts.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path[: -len("/mcp")]
    return f"{parts.scheme}://{parts.netloc}{path.rstrip('/')}"


def instance_refusal(instance: str, allow_writes: bool) -> str | None:
    """Why ``instance`` may not be the smoke's write target, or None.

    The smoke writes a wiki page and a ticket there, so anything but the
    test instance has to be a deliberate act, and a value that is no
    instance path is refused outright.
    """
    if not _INSTANCE_NAME.match(instance or ""):
        return (
            "--instance must be an instance path such as /trac_test "
            "(one leading slash, no further slashes)"
        )
    if instance != DEFAULT_INSTANCE and not allow_writes:
        return (
            f"--instance {instance} is not {DEFAULT_INSTANCE}, and "
            "checks 5 and 6 write a wiki page and a ticket there; "
            "pass --allow-instance-writes to say that is intended"
        )
    return None


def compare_tools(
    checkout: Iterable[str], daemon: Iterable[str]
) -> tuple[list[str], list[str]]:
    """``(missing, extra)``: checkout tools the daemon does not list, and
    daemon tools the checkout does not define. Both sorted."""
    mine = set(checkout)
    theirs = set(daemon)
    return sorted(mine - theirs), sorted(theirs - mine)


def checkout_tool_names() -> set[str]:
    """Every tool name this checkout's server registers.

    Taken from the same specs ``server.main`` registers (``ping`` plus
    ``ALL_SPECS``), not from a list written here, so a tool added to the
    checkout is expected on the daemon without touching this script.
    """
    from trac_mcp_server.mcp.server import PING_SPEC
    from trac_mcp_server.mcp.tools import ALL_SPECS

    return {spec.tool.name for spec in [PING_SPEC, *ALL_SPECS]}


def scrub(text: str, token: str | None) -> str:
    """``text`` with the token, and any bearer value, blotted out."""
    if token:
        text = text.replace(token, "***")
    return re.sub(r"(?i)\b(bearer)\s+\S+", r"\1 ***", text)


def _flatten(exc: BaseException) -> list[BaseException]:
    inner = getattr(exc, "exceptions", None)
    if not inner:
        return [exc]
    leaves: list[BaseException] = []
    for item in inner:
        leaves.extend(_flatten(item))
    return leaves


def describe_exception(exc: BaseException, token: str | None) -> str:
    """One scrubbed line for an exception, task-group wrappers unwrapped.

    The type name leads, because ``str()`` of a bare ``ConnectError`` or
    ``TimeoutError`` can be empty.
    """
    seen: list[str] = []
    for leaf in _flatten(exc):
        text = f"{type(leaf).__name__}: {leaf}".rstrip(": ")
        if text not in seen:
            seen.append(text)
    line = " | ".join(seen).replace("\n", " ")
    line = scrub(line, token)
    return line if len(line) <= 300 else line[:297] + "..."


def make_nonce() -> str:
    """A value no other run has written: uuid4 plus a UTC stamp.

    The stamp has no colons, so the page body and ticket comment built
    from it carry nothing Trac could read as a link prefix.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{uuid.uuid4()}-{stamp}"


def smoke_page_body(nonce: str) -> str:
    """The TracWiki written to ``Smoke/DaemonSmoke``. No trailing newline
    and no link syntax: the comparison is exact and the write gate probes
    links."""
    return (
        "= Daemon smoke =\n"
        "\n"
        "Written by `scripts/daemon_smoke.py` (ci-live.sh --daemon). "
        "Safe to overwrite.\n"
        "\n"
        f"Nonce {nonce}"
    )


def first_difference(sent: str, stored: str) -> str:
    """Where two strings first differ, for a mismatch message."""
    offset = 0
    for a, b in zip(sent, stored, strict=False):
        if a != b:
            break
        offset += 1
    return (
        f"first difference at offset {offset} "
        f"(wrote {len(sent)} chars, read back {len(stored)})"
    )


def _result_text(result) -> str:
    return "\n".join(
        block.text
        for block in (result.content or [])
        if getattr(block, "type", None) == "text"
    )


def _error_line(result) -> str:
    """The first line of an error result's text."""
    text = _result_text(result).strip()
    return text.splitlines()[0] if text else "(no text)"


def _structured(result) -> dict:
    data = getattr(result, "structuredContent", None)
    return data if isinstance(data, dict) else {}


def _plugin_missing(result) -> bool:
    text = _result_text(result).lower()
    return any(
        marker in text
        for marker in (
            "method_not_available",
            "methodnotfound",
            "tracrpc_comment",
        )
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class Reporter:
    """One line per check on stdout; scrubs everything it prints."""

    def __init__(self, token: str, out=None):
        self._token = token
        self._out = out or sys.stdout

    def _line(self, text: str) -> None:
        print(scrub(text, self._token), file=self._out, flush=True)

    def passed(self, n: int, name: str, detail: str = "") -> None:
        self._line(
            f"PASS {n} {name}" + (f" ({detail})" if detail else "")
        )

    def failed(self, n: int, name: str, reason: str) -> None:
        self._line(f"FAIL {n} {name}: {reason}")

    def note(self, text: str) -> None:
        self._line(f"NOTE {text}")

    def final(self, text: str) -> None:
        self._line(text)


# ---------------------------------------------------------------------------
# Checks 1-2: raw HTTP
# ---------------------------------------------------------------------------


async def check_healthz(base: str) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
        response = await http.get(f"{base}/healthz")
    if response.status_code != 200:
        raise CheckFailed(
            f"GET /healthz answered {response.status_code}"
        )
    try:
        body = response.json()
    except ValueError:
        raise CheckFailed("GET /healthz did not return JSON") from None
    if body != {"status": "ok"}:
        raise CheckFailed(
            f"GET /healthz returned {str(body)[:80]}, "
            'expected {"status": "ok"}'
        )
    return ""


_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "daemon-smoke", "version": "0"},
    },
}


async def check_auth_enforced(base: str) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
        response = await http.post(
            f"{base}/mcp",
            json=_INITIALIZE,
            headers={
                "Accept": "application/json, text/event-stream",
            },
        )
    if response.status_code != 401:
        raise CheckFailed(
            "POST /mcp with no Authorization answered "
            f"{response.status_code}, expected 401 -- the daemon is "
            "not enforcing its bearer token"
        )
    return ""


# ---------------------------------------------------------------------------
# Checks 3-6: over an MCP session. ``session`` needs only the
# ``call_tool(name, arguments)`` coroutine, so tests drive them with a
# stand-in.
# ---------------------------------------------------------------------------


async def check_tools(session, init, reporter: Reporter) -> str:
    server = init.serverInfo
    listed = await session.list_tools()
    daemon_names = [tool.name for tool in listed.tools]
    missing, extra = compare_tools(checkout_tool_names(), daemon_names)
    if missing:
        raise CheckFailed(
            f"the daemon (server {server.name} {server.version}) does "
            f"not list {len(missing)} tool(s) this checkout defines: "
            + ", ".join(missing)
        )
    if extra:
        reporter.note(
            f"{len(extra)} tool(s) on the daemon this checkout does "
            "not define: " + ", ".join(extra)
        )
    return (
        f"server {server.name} {server.version}, "
        f"{len(daemon_names)} tools"
    )


_INSTANCE_LINE = re.compile(r"^\[instance: (\S+) \((\w+)\)\]$")


def answered_instance(result) -> tuple[str | None, str | None]:
    """``(instance, source)`` a tool result says answered it.

    Every result carries them (``tools/instance_echo.py``): in
    ``structuredContent`` when the handler returned a dict, and always as a
    final ``[instance: /x (explicit)]`` text block. ``(None, None)`` when
    neither is there.
    """
    data = _structured(result)
    if isinstance(data.get("instance"), str):
        return data["instance"], data.get("instance_source")
    for line in reversed(_result_text(result).splitlines()):
        match = _INSTANCE_LINE.match(line.strip())
        if match:
            return match.group(1), match.group(2)
    return None, None


class Scoped:
    """A session that sends ``instance=<instance>`` on EVERY tool call and
    checks, on every result, that the instance that answered is the one
    asked for.

    Checks 4-6 read and WRITE. Without ``instance`` a call goes to the
    daemon's default, which on the shared daemon is a production store
    (``/auto_pm``), so the argument is added here, at the one place every
    call passes through, and an answer from anywhere else fails the check.
    """

    def __init__(self, session, instance: str):
        self._session = session
        self.instance = instance

    async def call_tool(self, name, arguments):
        result = await self._session.call_tool(
            name, {**arguments, "instance": self.instance}
        )
        answered, source = answered_instance(result)
        if answered is None:
            raise CheckFailed(
                f"{name} did not report which instance answered, so "
                f"{self.instance} cannot be confirmed"
            )
        if (
            answered.rstrip("/") != self.instance
            or source != "explicit"
        ):
            raise CheckFailed(
                f"{name} was sent instance={self.instance} but the "
                f"answer came from {answered} ({source})"
            )
        return result


async def check_read(session) -> str:
    result = await session.call_tool(
        "wiki_get", {"page_name": "WikiStart"}
    )
    if result.isError:
        raise CheckFailed(
            f"wiki_get WikiStart returned an error: {_error_line(result)}"
        )
    data = _structured(result)
    content = data.get("content")
    if not isinstance(content, str):
        content = _result_text(result)
    if not content.strip():
        raise CheckFailed("wiki_get WikiStart returned empty content")
    return (
        f"WikiStart v{data.get('version', '?')}, {len(content)} chars, "
        f"instance {data.get('instance', 'not reported')}"
    )


async def _write_smoke_page(session, body: str) -> None:
    """wiki_update the smoke page, or wiki_create it when absent. One
    retry: a concurrent run can create it, or move its version, between
    our read and our write."""
    last = ""
    for _attempt in range(2):
        current = await session.call_tool(
            "wiki_get", {"page_name": WIKI_PAGE}
        )
        if current.isError:
            created = await session.call_tool(
                "wiki_create",
                {
                    "page_name": WIKI_PAGE,
                    "content": body,
                    "comment": "daemon smoke",
                },
            )
            if not created.isError:
                return
            last = f"wiki_create: {_error_line(created)}"
            continue
        version = _structured(current).get("version")
        if not isinstance(version, int) or version < 1:
            raise CheckFailed(
                f"wiki_get {WIKI_PAGE} reported no usable version "
                f"({version!r})"
            )
        updated = await session.call_tool(
            "wiki_update",
            {
                "page_name": WIKI_PAGE,
                "content": body,
                "version": version,
                "comment": "daemon smoke",
            },
        )
        if not updated.isError:
            return
        last = f"wiki_update: {_error_line(updated)}"
        if "version_conflict" not in last:
            break
    raise CheckFailed(f"could not write {WIKI_PAGE}: {last}")


async def check_write_read(session) -> str:
    body = smoke_page_body(make_nonce())
    await _write_smoke_page(session, body)
    back = await session.call_tool("wiki_get", {"page_name": WIKI_PAGE})
    if back.isError:
        raise CheckFailed(
            f"wiki_get {WIKI_PAGE} after the write returned an error: "
            f"{_error_line(back)}"
        )
    data = _structured(back)
    stored = data.get("content")
    if stored != body:
        if not isinstance(stored, str):
            raise CheckFailed(
                f"wiki_get {WIKI_PAGE} returned no content to compare"
            )
        raise CheckFailed(
            f"{WIKI_PAGE} reads back differently from what was "
            f"written: {first_difference(body, stored)} (another run "
            "writing the same page at the same moment would do this "
            "too)"
        )
    return f"{WIKI_PAGE} v{data.get('version', '?')}, {len(body)} chars"


async def _find_or_create_ticket(session) -> tuple[int, str]:
    found = await session.call_tool(
        "ticket_search", {"query": TICKET_SEARCH, "max_results": 100}
    )
    if found.isError:
        raise CheckFailed(f"ticket_search: {_error_line(found)}")
    tickets = _structured(found).get("tickets")
    if not isinstance(tickets, list):
        raise CheckFailed(
            "ticket_search returned no structured ticket list"
        )
    for ticket in tickets:
        summary = str(ticket.get("summary", ""))
        if summary.startswith(TICKET_SUMMARY):
            return int(ticket["id"]), "found"
    created = await session.call_tool(
        "ticket_create",
        {
            "summary": f"{TICKET_SUMMARY} (do not close)",
            "description": (
                "Written by `scripts/daemon_smoke.py` (run through "
                "ci-live.sh --daemon) to exercise comment post, edit "
                "and history on a deployed daemon. Each run adds a "
                "comment here. Do not close it: the next run reuses "
                "it."
            ),
            "ticket_type": "task",
            "owner": "agent_rpc",
        },
    )
    if created.isError:
        raise CheckFailed(f"ticket_create: {_error_line(created)}")
    match = re.search(r"Created ticket #(\d+)", _result_text(created))
    if match is None:
        raise CheckFailed(
            "ticket_create did not say which ticket it created: "
            + _error_line(created)
        )
    return int(match.group(1)), "created"


async def _ticket_ts(session, ticket_id: int) -> str:
    got = await session.call_tool(
        "ticket_get",
        {"ticket_id": ticket_id, "include_comments": False},
    )
    if got.isError:
        raise CheckFailed(
            f"ticket_get #{ticket_id}: {_error_line(got)}"
        )
    ts = _structured(got).get("_ts")
    if not ts:
        raise CheckFailed(f"ticket_get #{ticket_id} returned no _ts")
    return str(ts)


async def check_comment_plugin(session) -> str:
    plugin_message = (
        "tracrpc_comment plugin not loaded on the daemon's Trac"
    )
    ticket_id, how = await _find_or_create_ticket(session)

    first = f"daemon smoke comment, nonce {make_nonce()}"
    posted = await session.call_tool(
        "ticket_update",
        {
            "ticket_id": ticket_id,
            "comment": first,
            "base_ts": await _ticket_ts(session, ticket_id),
        },
    )
    if posted.isError:
        raise CheckFailed(
            f"ticket_update #{ticket_id}: {_error_line(posted)}"
        )
    data = _structured(posted)
    cnum = data.get("comment_number")
    if not isinstance(cnum, int) or cnum < 1:
        raise CheckFailed(
            f"ticket_update #{ticket_id} did not report the number of "
            f"the comment it posted (comment_number={cnum!r})"
        )
    if data.get("comment_stored_matches") is False:
        raise CheckFailed(
            f"comment {cnum} on #{ticket_id} was stored differently "
            "from the text sent"
        )

    second = f"daemon smoke comment, edited, nonce {make_nonce()}"
    edited = await session.call_tool(
        "ticket_comment_edit",
        {
            "ticket_id": ticket_id,
            "cnum": cnum,
            "comment": second,
            "base_ts": await _ticket_ts(session, ticket_id),
        },
    )
    if edited.isError:
        if _plugin_missing(edited):
            raise CheckFailed(plugin_message)
        raise CheckFailed(
            f"ticket_comment_edit #{ticket_id} comment {cnum}: "
            f"{_error_line(edited)}"
        )

    history = await session.call_tool(
        "ticket_comment_history", {"ticket_id": ticket_id, "cnum": cnum}
    )
    if history.isError:
        if _plugin_missing(history):
            raise CheckFailed(plugin_message)
        raise CheckFailed(
            f"ticket_comment_history #{ticket_id} comment {cnum}: "
            f"{_error_line(history)}"
        )
    text = _result_text(history)
    counted = re.search(r"(\d+) revision\(s\)", text)
    revisions = int(counted.group(1)) if counted else 0
    if revisions < 2:
        raise CheckFailed(
            f"comment {cnum} on #{ticket_id} was edited but "
            f"ticket_comment_history shows {revisions} revision(s), "
            "expected at least 2"
        )
    for label, expected in (("original", first), ("edited", second)):
        if expected not in text:
            raise CheckFailed(
                f"ticket_comment_history for comment {cnum} on "
                f"#{ticket_id} does not contain the {label} text"
            )
    return (
        f"ticket #{ticket_id} {how}, comment {cnum} posted, edited, "
        f"{revisions} revisions"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def _step(reporter, token, n, name, make_check) -> bool:
    """Run one check; print its line; True when it passed."""
    try:
        detail = await make_check()
    except CheckFailed as exc:
        reporter.failed(n, name, scrub(str(exc), token))
        return False
    except TimeoutError:
        reporter.failed(n, name, "the daemon did not answer in time")
        return False
    except Exception as exc:  # a check never crashes the run
        reporter.failed(n, name, describe_exception(exc, token))
        return False
    reporter.passed(n, name, detail)
    return True


class Progress:
    """Where the session checks got to, readable after an exception."""

    def __init__(self) -> None:
        self.current = 3  # the check running, or the next to run
        self.failed_at = 0
        self.completed = False
        self.version = "unknown"


_CHECK_NAMES = {
    1: "healthz",
    2: "auth_enforced",
    3: "tools",
    4: "read",
    5: "write_read",
    6: "comment_plugin",
}


async def _session_checks(
    base, token, reporter, progress, instance
) -> None:
    """Checks 3-6 on one MCP session, recording where they got to."""
    import anyio
    import httpx
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(30.0, read=CALL_TIMEOUT),
    ) as http:
        async with streamable_http_client(
            f"{base}/mcp", http_client=http
        ) as (read, write, _session_id):
            async with ClientSession(read, write) as session:

                async def tools():
                    with anyio.fail_after(INIT_TIMEOUT):
                        init = await session.initialize()
                        progress.version = init.serverInfo.version
                        return await check_tools(
                            session, init, reporter
                        )

                scoped = Scoped(session, instance)

                def bounded(check):
                    async def run():
                        with anyio.fail_after(CALL_TIMEOUT * 4):
                            return await check(scoped)

                    return run

                steps = (
                    (3, tools),
                    (4, bounded(check_read)),
                    (5, bounded(check_write_read)),
                    (6, bounded(check_comment_plugin)),
                )
                for n, make_check in steps:
                    progress.current = n
                    if not await _step(
                        reporter, token, n, _CHECK_NAMES[n], make_check
                    ):
                        progress.failed_at = n
                        return
                progress.completed = True


async def run_smoke(
    base: str, token: str, out=None, instance: str = DEFAULT_INSTANCE
) -> int:
    """Run the checks against ``base``; the process exit code."""
    reporter = Reporter(token, out)
    # The SDK logs transport failures, with tracebacks, straight to
    # stderr, bypassing scrub(). The verdict lines say what failed.
    for noisy in ("mcp", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)

    async def healthz():
        return await check_healthz(base)

    async def auth():
        return await check_auth_enforced(base)

    for n, make_check in ((1, healthz), (2, auth)):
        if not await _step(
            reporter, token, n, _CHECK_NAMES[n], make_check
        ):
            reporter.final(f"DAEMON SMOKE FAILED at check {n}")
            return EXIT_FAILED

    progress = Progress()
    try:
        await _session_checks(base, token, reporter, progress, instance)
    except Exception as exc:
        # The session machinery failed outside any one check: opening
        # it, or closing it afterwards. Closing noise after a verdict is
        # not a second finding; failing to open is the running check's.
        text = describe_exception(exc, token)
        if progress.failed_at or progress.completed:
            reporter.note(f"closing the session also raised: {text}")
        else:
            progress.failed_at = progress.current
            reporter.failed(
                progress.current, _CHECK_NAMES[progress.current], text
            )
    if progress.failed_at:
        reporter.final(
            f"DAEMON SMOKE FAILED at check {progress.failed_at}"
        )
        return EXIT_FAILED
    reporter.final(
        f"DAEMON SMOKE PASSED {base} (server {progress.version})"
    )
    return EXIT_PASSED


def _not_run(message: str) -> int:
    print(
        f"NOT RUN: {message} -- {_NOT_RUN_TAIL}.",
        file=sys.stderr,
    )
    return EXIT_NOT_RUN


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="daemon_smoke.py",
        description="Smoke-test a deployed trac-mcp-server daemon.",
    )
    parser.add_argument(
        "url", help="daemon base URL, e.g. http://h:8092"
    )
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help="environment variable holding the bearer token "
        f"(default {DEFAULT_TOKEN_ENV})",
    )
    parser.add_argument(
        "--instance",
        default=DEFAULT_INSTANCE,
        help="Trac instance path checks 4-6 read and write "
        f"(default {DEFAULT_INSTANCE})",
    )
    parser.add_argument(
        "--allow-instance-writes",
        action="store_true",
        help=f"permit an --instance other than {DEFAULT_INSTANCE}",
    )
    args = parser.parse_args(argv)

    try:
        base = normalise_base(args.url)
    except ValueError as exc:
        return _not_run(f"the daemon URL is malformed: {exc}")
    refusal = instance_refusal(
        args.instance, args.allow_instance_writes
    )
    if refusal:
        return _not_run(refusal)
    if not _ENV_NAME.match(args.token_env):
        return _not_run(
            "--token-env must name an environment variable "
            "(letters, digits, underscore)"
        )
    token = (os.environ.get(args.token_env) or "").strip()
    if not token:
        return _not_run(
            f"environment variable {args.token_env} is unset or empty, "
            "so the daemon smoke has no bearer token and made no "
            "network call"
        )
    if not (token.isascii() and token.isprintable()) or " " in token:
        return _not_run(
            f"the value of {args.token_env} is not a usable bearer "
            "token (printable ASCII, no spaces)"
        )

    return asyncio.run(run_smoke(base, token, instance=args.instance))


if __name__ == "__main__":
    sys.exit(main())
