#!/usr/bin/env python3
"""Checks a running trac-mcp-server against the setup in this directory.

Two users act through the same server, and Trac must see each of them under
their own name. Standard library only.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL = "2025-06-18"


def token_for(keycloak_url: str, user: str) -> str:
    data = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "trac-mcp",
            "username": user,
            "password": user,
        }
    ).encode()
    url = (
        f"{keycloak_url}/realms/trac-demo/protocol/openid-connect/token"
    )
    with urllib.request.urlopen(url, data=data, timeout=15) as response:
        return json.load(response)["access_token"]


def post(url: str, payload: dict, headers: dict):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **headers,
        },
    )
    return urllib.request.urlopen(request, timeout=60)


class McpSession:
    """Just enough of the MCP streamable HTTP protocol to call tools."""

    def __init__(self, url: str, token: str):
        self.url = url
        self.headers = {"Authorization": f"Bearer {token}"}
        self.next_id = 1
        with post(
            url,
            {
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "verify", "version": "1"},
                },
            },
            self.headers,
        ) as response:
            session_id = response.headers["Mcp-Session-Id"]
            response.read()
        self.headers = {
            **self.headers,
            "Mcp-Session-Id": session_id,
            "Mcp-Protocol-Version": PROTOCOL,
        }
        post(
            url,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            self.headers,
        ).close()

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    def call(self, tool: str, **arguments) -> dict:
        with post(
            self.url,
            {
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            self.headers,
        ) as response:
            body = response.read().decode()
        for line in body.splitlines():
            if line.startswith("data:"):
                body = line[len("data:") :]
        result = json.loads(body)["result"]
        if result.get("isError"):
            raise SystemExit(
                f"{tool} failed: {result['content'][0]['text']}"
            )
        return result


def check(description: str, passed: bool, detail: object = "") -> None:
    print(f"{'ok  ' if passed else 'FAIL'} {description}")
    if not passed:
        raise SystemExit(f"     got: {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keycloak", default="http://localhost:8180")
    parser.add_argument("--mcp", default="http://127.0.0.1:8080/mcp")
    args = parser.parse_args()

    try:
        post(args.mcp, {}, {}).close()
        status = 200
    except urllib.error.HTTPError as error:
        status = error.code
    check("a request without a token is refused", status == 401, status)

    alice = McpSession(args.mcp, token_for(args.keycloak, "alice"))
    bob = McpSession(args.mcp, token_for(args.keycloak, "bob"))

    created = alice.call(
        "ticket_create",
        summary="OIDC check",
        description="Written by alice.",
    )
    ticket = int(
        re.search(r"#(\d+)", created["content"][0]["text"]).group(1)
    )
    updated = bob.call(
        "ticket_update", ticket_id=ticket, comment="Written by bob."
    )
    number = updated["structuredContent"].get("comment_number")
    check(
        "bob gets the number of his comment back", number == 1, updated
    )

    fetched = alice.call("ticket_get", ticket_id=ticket)[
        "structuredContent"
    ]
    reporter = fetched["reporter"]
    check(
        "alice's ticket has alice as its reporter",
        reporter == "alice",
        reporter,
    )

    authors = {comment["author"] for comment in fetched["comments"]}
    check(
        "bob's comment has bob as its author",
        authors == {"bob"},
        authors,
    )

    listed = alice.call("list_instances")["structuredContent"]
    check(
        "list_instances reports one endpoint and no discovery",
        len(listed["configured"]) == 1 and "discovered" not in listed,
        listed,
    )
    print(
        "All checks passed. `docker compose logs trac` shows who made each request."
    )


if __name__ == "__main__":
    sys.exit(main())
