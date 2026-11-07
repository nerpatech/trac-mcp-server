"""The invariant of OIDC per-user auth: no outbound request of a tool call
carries the shared service-account credentials.

TRAC_MCP_OIDC_RPC_URL may be set alongside TRAC_USERNAME/TRAC_PASSWORD, which
keeps the startup self-test running, so the registry and the base config do
hold a shared identity here. Every registered tool runs through
handle_call_tool() with the HTTP transport replaced, because a tool that goes
around _resolve_client() would not show up in a test of _resolve_client().
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from mcp.server.lowlevel.server import request_ctx
from pydantic_core import Url
from starlette.requests import Request

from trac_mcp_server.config import Config
from trac_mcp_server.instances import InstanceRegistry
from trac_mcp_server.mcp.oidc import OidcClientCache
from trac_mcp_server.mcp.server import (
    PING_SPEC,
    handle_call_tool,
    handle_read_resource,
    set_instances,
    set_oidc_cache,
    set_registry,
)
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.instances import set_instance_registry
from trac_mcp_server.mcp.tools.registry import ToolRegistry

TRAC_URL = "https://trac.example.com/trac"
RPC_URL = "https://trac.example.com/trac-api/login/xmlrpc"
USER_TOKEN = "token-of-the-caller"

_FAULT = (
    b"<?xml version='1.0'?><methodResponse><fault><value><struct>"
    b"<member><name>faultCode</name><value><int>1</int></value></member>"
    b"<member><name>faultString</name><value><string>no</string></value>"
    b"</member></struct></value></fault></methodResponse>"
)
_EMPTY_ARRAY = (
    b"<?xml version='1.0'?><methodResponse><params><param><value><array>"
    b"<data/></array></value></param></params></methodResponse>"
)


class _Transport:
    """Stands in for HTTPAdapter.send and records what would go out."""

    def __init__(self, body: bytes):
        self.body = body
        self.sent: list[tuple[str, str | None]] = []

    def send(self, _adapter, request, **_kwargs):
        self.sent.append(
            (request.url, request.headers.get("Authorization"))
        )
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response._content = self.body
        return response


@pytest.fixture(autouse=True)
def _oidc_server_with_a_shared_identity():
    base_config = Config(
        trac_url=TRAC_URL,
        username="service-account",
        password="service-secret",
    )
    registry = InstanceRegistry(base_config, {})
    set_registry(ToolRegistry([PING_SPEC] + ALL_SPECS))
    set_instances(registry)
    set_instance_registry(registry)
    set_oidc_cache(OidcClientCache(base_config, RPC_URL))
    token = request_ctx.set(
        SimpleNamespace(
            request=Request(
                {
                    "type": "http",
                    "headers": [
                        (
                            b"authorization",
                            f"Bearer {USER_TOKEN}".encode(),
                        )
                    ],
                }
            )
        )
    )
    yield
    request_ctx.reset(token)
    set_oidc_cache(None)
    set_instance_registry(None)
    set_instances(None)
    set_registry(None)


def _value_for(schema: dict):
    """A value that passes a tool's schema, so its handler really runs."""
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next(k for k in kind if k != "null")
    if kind == "integer" or kind == "number":
        return max(1, schema.get("minimum", 1))
    if kind == "boolean":
        return True
    if kind == "array":
        return [_value_for(schema.get("items", {"type": "string"}))]
    if kind == "object":
        return _arguments_for(schema)
    return "x"


def _arguments_for(schema: dict) -> dict:
    properties = schema.get("properties", {})
    return {
        name: _value_for(properties.get(name, {"type": "string"}))
        for name in schema.get("required", [])
    }


# A schema cannot say that an object must not be empty, and the handler of this
# tool returns "attributes is required" on an empty one before any request.
# Nor can it say that a query needs a word of two or more characters:
# wiki_search refuses "x" ("no searchable words") before any request.
# ticket_check_list's handler refuses a non-number such as "x" in its default id
# mode before any request, so it needs a real ticket number.
_EXTRA_ARGUMENTS = {
    "milestone_update": {"attributes": {"description": "x"}},
    "ticket_check_list": {"values": "1"},
    "wiki_search": {"query": "xyz"},
}

# Everything else has to reach the transport, or the tests below prove nothing
# for it: list_instances by design, the others stop at reading a local file.
_SENDS_NOTHING = {
    "list_instances",
    "ticket_attachment_put",
    "wiki_attachment_put",
    "wiki_file_detect_format",
    "wiki_file_push",
}


def _call_tool(spec, reply: bytes) -> _Transport:
    transport = _Transport(reply)
    with patch(
        "requests.adapters.HTTPAdapter.send",
        lambda adapter, request, **kwargs: transport.send(
            adapter, request, **kwargs
        ),
    ):
        asyncio.run(
            handle_call_tool(
                spec.tool.name,
                {
                    **_arguments_for(spec.tool.inputSchema),
                    **_EXTRA_ARGUMENTS.get(spec.tool.name, {}),
                },
            )
        )
    return transport


@pytest.mark.parametrize(
    "reply", [_FAULT, _EMPTY_ARRAY], ids=["fault", "empty"]
)
@pytest.mark.parametrize(
    "spec", [PING_SPEC] + ALL_SPECS, ids=lambda spec: spec.tool.name
)
def test_no_tool_sends_the_shared_credentials(spec, reply):
    transport = _call_tool(spec, reply)
    for url, authorization in transport.sent:
        assert authorization in (None, f"Bearer {USER_TOKEN}"), (
            f"{spec.tool.name} sent {authorization!r} to {url}"
        )


@pytest.mark.parametrize(
    "reply", [_FAULT, _EMPTY_ARRAY], ids=["fault", "empty"]
)
def test_only_the_exempt_tools_send_nothing(reply):
    silent = {
        spec.tool.name
        for spec in [PING_SPEC] + ALL_SPECS
        if not _call_tool(spec, reply).sent
    }
    assert silent == _SENDS_NOTHING


def test_the_tools_do_reach_the_transport_with_the_callers_token():
    """Guards the test above: it proves nothing if no request ever goes out."""
    transport = _call_tool(PING_SPEC, _EMPTY_ARRAY)
    assert transport.sent
    assert {authorization for _, authorization in transport.sent} == {
        f"Bearer {USER_TOKEN}"
    }


def test_wiki_resource_does_not_send_the_shared_credentials():
    transport = _Transport(_FAULT)
    with patch(
        "requests.adapters.HTTPAdapter.send",
        lambda adapter, request, **kwargs: transport.send(
            adapter, request, **kwargs
        ),
    ):
        asyncio.run(handle_read_resource(Url("trac://wiki/SomePage")))
    assert transport.sent
    assert {authorization for _, authorization in transport.sent} == {
        f"Bearer {USER_TOKEN}"
    }


@pytest.mark.parametrize("arguments", [{}, {"discover": True}])
def test_list_instances_reports_the_one_endpoint_and_stays_offline(
    arguments,
):
    transport = _Transport(_EMPTY_ARRAY)
    with (
        patch(
            "requests.adapters.HTTPAdapter.send",
            lambda adapter, request, **kwargs: transport.send(
                adapter, request, **kwargs
            ),
        ),
        patch(
            "trac_mcp_server.mcp.tools.instances.scrape_project_index"
        ) as scrape,
    ):
        result = asyncio.run(
            handle_call_tool("list_instances", arguments)
        )

    scrape.assert_not_called()
    assert transport.sent == []
    assert not result.isError
    assert result.structuredContent["default"] == TRAC_URL
    assert [
        e["url"] for e in result.structuredContent["configured"]
    ] == [TRAC_URL]
    assert "discovered" not in result.structuredContent
    assert result.structuredContent["write_gate"] is True


def test_list_instances_reports_a_disabled_write_gate():
    """Through the per-user client, so it also proves the setting survives
    OidcClientCache building that client's config."""
    set_oidc_cache(
        OidcClientCache(
            Config(
                trac_url=TRAC_URL,
                username="service-account",
                password="service-secret",
                write_gate=False,
            ),
            RPC_URL,
        )
    )
    transport = _Transport(_EMPTY_ARRAY)
    with (
        patch(
            "requests.adapters.HTTPAdapter.send",
            lambda adapter, request, **kwargs: transport.send(
                adapter, request, **kwargs
            ),
        ),
        patch(
            "trac_mcp_server.mcp.tools.instances.scrape_project_index"
        ) as scrape,
    ):
        result = asyncio.run(handle_call_tool("list_instances", {}))

    scrape.assert_not_called()
    assert transport.sent == []
    assert result.structuredContent["write_gate"] is False
    assert (
        "write_gate: false -- OFF on this daemon"
        in result.content[0].text
    )
