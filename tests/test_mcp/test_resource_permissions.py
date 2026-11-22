"""Wiki resources follow the tool filters (Trac /trac_mcp_server #273).

The resources are served exactly when ``wiki_get`` is: the permissions
file (``wiki_get`` needs WIKI_VIEW), ``--read-only`` (``wiki_get`` is a
read and stays) and ``disabled_tools`` all act through the one
``ToolRegistry``. These rows drive the server's real list and read
handlers with registries built by the real ``build_registry`` over the
real spec list, from the shipped scenario permission files; only the
``TracClient`` is a mock.

Also here: the page template is listed through
``list_resource_templates`` and not as a concrete resource (L25), and
``convert_preview`` needs both view permissions, so the shipped
``ticket_manager`` and ``wiki_editor`` profiles no longer carry it.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic_core import Url

from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.mcp import server as server_module
from trac_mcp_server.mcp.server import (
    PING_SPEC,
    build_registry,
    handle_list_resource_templates,
    handle_list_resources,
    handle_read_resource,
    set_registry,
)
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.registry import (
    load_permissions_file,
    with_page_alias,
    with_strict_schema,
)

SCENARIOS = (
    Path(__file__).resolve().parents[2] / "scripts" / "scenarios"
)
SPECS = with_strict_schema(with_page_alias([PING_SPEC] + ALL_SPECS))


def _registry(profile=None, **config):
    allowed = (
        load_permissions_file(SCENARIOS / f"{profile}.permissions")
        if profile
        else None
    )
    return build_registry(SPECS, allowed, ServerConfig(**config))


# (id, registry kwargs, resources served)
FILTERS = [
    pytest.param(
        {"profile": "ticket_manager"}, False, id="ticket_manager"
    ),
    pytest.param({"profile": "readonly"}, True, id="readonly"),
    pytest.param({"profile": "wiki_editor"}, True, id="wiki_editor"),
    pytest.param({"profile": "full_access"}, True, id="full_access"),
    pytest.param({"read_only": True}, True, id="read_only"),
    pytest.param(
        {"disabled_tools": ["wiki_get"]}, False, id="disabled_wiki_get"
    ),
    pytest.param(
        {"disabled_tools": ["wiki_search"]}, True, id="disabled_other"
    ),
    pytest.param({}, True, id="no_filter"),
]


@pytest.fixture
def client():
    """The mock TracClient every read resolves to."""
    mock = MagicMock()
    mock.get_wiki_page.return_value = "= Start =\n"
    mock.get_wiki_page_info.return_value = {
        "author": "alice",
        "version": 3,
        "lastModified": None,
    }
    mock.list_wiki_pages.return_value = ["WikiStart", "Dev/Setup"]
    with patch("trac_mcp_server.mcp.server.get_instances") as instances:
        instances.return_value.get_client.return_value = mock
        yield mock


@pytest.fixture
def serve():
    """Install a registry as the global one; restore the previous one."""
    previous = server_module._registry

    def install(profile=None, **config):
        set_registry(_registry(profile, **config))

    try:
        yield install
    finally:
        set_registry(previous)


def _read(uri):
    return asyncio.run(handle_read_resource(Url(uri)))


@pytest.mark.parametrize(("filt", "served"), FILTERS)
class TestResourcesFollowWikiGet:
    def test_list_resources(self, serve, client, filt, served):
        serve(**filt)
        uris = [
            str(r.uri) for r in asyncio.run(handle_list_resources())
        ]
        assert uris == (["trac://wiki/_index"] if served else [])

    def test_list_resource_templates(self, serve, client, filt, served):
        serve(**filt)
        templates = asyncio.run(handle_list_resource_templates())
        assert [t.uriTemplate for t in templates] == (
            ["trac://wiki/{page_name}"] if served else []
        )

    @pytest.mark.parametrize(
        "uri",
        [
            "trac://wiki/WikiStart",
            "trac://wiki/WikiStart?version=2",
            "trac://wiki/_index",
        ],
    )
    def test_read(self, serve, client, filt, served, uri):
        serve(**filt)
        if served:
            text = _read(uri)
            assert not text.startswith("Error")
            return
        with pytest.raises(ValueError, match="Unknown resource type"):
            _read(uri)
        # Refused before any Trac call, and the same way an unknown
        # resource type is: hidden looks like missing.
        assert not client.method_calls
        with pytest.raises(ValueError) as unknown:
            _read("trac://nothing/WikiStart")
        assert (
            str(unknown.value).split(":")[0] == "Unknown resource type"
        )

    def test_the_tool_agrees(self, filt, served):
        """The rule is wiki_get's own: the resources go with the tool."""
        assert _registry(**filt).has_tool("wiki_get") is served


class TestPageTemplate:
    def test_listed_as_a_template_not_a_resource(self, serve, client):
        serve()
        resources = asyncio.run(handle_list_resources())
        templates = asyncio.run(handle_list_resource_templates())
        assert not any("{" in str(r.uri) for r in resources)
        assert len(templates) == 1
        template = templates[0]
        assert template.uriTemplate == "trac://wiki/{page_name}"
        assert template.name == "Wiki Page"
        assert template.mimeType == "text/plain"
        assert "TracWiki" in template.description
        assert "version=" in template.description


@pytest.mark.parametrize(
    "version",
    [
        "",
        "abc",
        "0",
        "-1",
        "1.5",
        "1e2",
        "+3",
        " 2",
        "\u0663",
        "2147483648",
        "9" * 20,
        "9" * 5000,
    ],
    ids=lambda v: v if len(v) < 25 else f"{len(v)}-digits",
)
def test_a_version_that_is_not_a_whole_number_is_a_validation_error(
    serve, client, version
):
    serve()
    text = _read(f"trac://wiki/WikiStart?version={version}")
    assert text.startswith("Error (validation_error): ")
    assert "version" in text.split("\n")[0]
    assert "\n\nHint: " in text
    assert not client.method_calls


def test_the_largest_xmlrpc_int_still_reads(serve, client):
    serve()
    text = _read("trac://wiki/WikiStart?version=2147483647")
    assert text.startswith("# WikiStart")
    client.get_wiki_page.assert_called_once_with(
        "WikiStart", 2147483647
    )


def test_a_whole_version_still_reads(serve, client):
    serve()
    text = _read("trac://wiki/WikiStart?version=2")
    assert text.startswith("# WikiStart")
    client.get_wiki_page.assert_called_once_with("WikiStart", 2)


# convert_preview needs both view permissions (comment 1 and the decision
# of comment 2): its render expands either realm.
CONVERT_PREVIEW = [
    ("full_access", True),
    ("readonly", True),
    ("ticket_manager", False),
    ("wiki_editor", False),
]


@pytest.mark.parametrize(("profile", "present"), CONVERT_PREVIEW)
def test_convert_preview_under_each_shipped_profile(profile, present):
    names = {t.name for t in _registry(profile).list_tools()}
    assert ("convert_preview" in names) is present


def test_convert_preview_without_a_filter_is_unaffected():
    assert _registry().has_tool("convert_preview")
    assert _registry(read_only=True).has_tool("convert_preview")


def test_every_shipped_profile_is_covered():
    shipped = {p.stem for p in SCENARIOS.glob("*.permissions")}
    assert shipped == {profile for profile, _ in CONVERT_PREVIEW}


def test_the_package_exports_the_template_names():
    from trac_mcp_server.mcp import resources
    from trac_mcp_server.mcp.resources import (
        WIKI_RESOURCE_TEMPLATES,
        handle_list_wiki_resource_templates,
    )

    assert "WIKI_RESOURCE_TEMPLATES" in resources.__all__
    assert "handle_list_wiki_resource_templates" in resources.__all__
    assert [t.uriTemplate for t in WIKI_RESOURCE_TEMPLATES] == [
        "trac://wiki/{page_name}"
    ]
    assert asyncio.run(handle_list_wiki_resource_templates()) == (
        WIKI_RESOURCE_TEMPLATES
    )


@pytest.mark.parametrize(
    "profile", sorted(p for p, _ in CONVERT_PREVIEW)
)
def test_expected_tools_reference_matches_the_registry(profile):
    """scripts/scenarios/<profile>.expected_tools.txt is what
    ``agent_scenarios.py --update-refs`` writes: the sorted names of the
    tools a server started with that permissions file lists."""
    expected = (SCENARIOS / f"{profile}.expected_tools.txt").read_text()
    names = sorted(t.name for t in _registry(profile).list_tools())
    assert expected == "\n".join(names) + "\n"
