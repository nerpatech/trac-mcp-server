"""Tests for the list_instances MCP tool handler and tool definition."""

import asyncio
import dataclasses
import unittest
from unittest.mock import MagicMock, patch

from trac_mcp_server.config import Config
from trac_mcp_server.instances import Identity, InstanceRegistry
from trac_mcp_server.mcp.tools.instances import (
    INSTANCE_SPECS,
    INSTANCE_TOOLS,
    set_instance_registry,
)
from trac_mcp_server.mcp.tools.registry import ToolRegistry


def _default_config() -> Config:
    return Config(
        trac_url="http://192.168.10.4:8000/trac_mcp_server",
        username="agent_rpc",
        password="secret",
    )


class TestInstanceToolDefinition(unittest.TestCase):
    """Test INSTANCE_TOOLS definitions."""

    def test_one_tool_defined(self):
        self.assertEqual(len(INSTANCE_TOOLS), 1)

    def test_tool_name(self):
        self.assertEqual(INSTANCE_TOOLS[0].name, "list_instances")

    def test_discover_arg_schema(self):
        schema = INSTANCE_TOOLS[0].inputSchema
        self.assertIn("discover", schema["properties"])
        self.assertEqual(
            schema["properties"]["discover"]["type"], "boolean"
        )
        self.assertEqual(schema["required"], [])


class TestListInstancesHandler(unittest.TestCase):
    """Test list_instances handler behavior."""

    def setUp(self):
        self.config = _default_config()
        self.client = MagicMock()
        self.client.config = self.config
        self._no_config_files = patch(
            "trac_mcp_server.instances.discover_config_files",
            return_value=[],
        )
        self._no_config_files.start()

    def tearDown(self):
        set_instance_registry(None)
        self._no_config_files.stop()

    def test_registry_not_initialized_returns_error(self):
        set_instance_registry(None)

        result = asyncio.run(
            ToolRegistry(INSTANCE_SPECS).call_tool(
                "list_instances", {}, self.client
            )
        )

        self.assertTrue(result.isError)
        self.assertIn("not initialized", result.content[0].text)

    @patch("trac_mcp_server.mcp.tools.instances.scrape_project_index")
    def test_configured_and_discovered_shape(self, mock_scrape):
        registry = InstanceRegistry(self.config, {})
        set_instance_registry(registry)
        mock_scrape.return_value = [
            {
                "path": "/trac_mcp_server",
                "title": "Trac MCP Server",
                "description": "",
                "url": "http://192.168.10.4:8000/trac_mcp_server",
            },
            {
                "path": "/bcs",
                "title": "BCS",
                "description": "",
                "url": "http://192.168.10.4:8000/bcs",
            },
        ]

        result = asyncio.run(
            ToolRegistry(INSTANCE_SPECS).call_tool(
                "list_instances", {}, self.client
            )
        )

        structured = result.structuredContent
        self.assertIn("configured", structured)
        self.assertIn("default", structured)
        self.assertIn("discovered", structured)

        discovered = {e["path"]: e for e in structured["discovered"]}
        self.assertTrue(discovered["/trac_mcp_server"]["configured"])
        self.assertFalse(discovered["/bcs"]["configured"])

    @patch("trac_mcp_server.mcp.tools.instances.scrape_project_index")
    def test_discover_false_skips_scrape(self, mock_scrape):
        registry = InstanceRegistry(self.config, {})
        set_instance_registry(registry)

        result = asyncio.run(
            ToolRegistry(INSTANCE_SPECS).call_tool(
                "list_instances", {"discover": False}, self.client
            )
        )

        mock_scrape.assert_not_called()
        self.assertNotIn("discovered", result.structuredContent)

    @patch("trac_mcp_server.mcp.tools.instances.scrape_project_index")
    def test_scrape_failure_omits_discovered_key(self, mock_scrape):
        registry = InstanceRegistry(self.config, {})
        set_instance_registry(registry)
        mock_scrape.return_value = []

        result = asyncio.run(
            ToolRegistry(INSTANCE_SPECS).call_tool(
                "list_instances", {}, self.client
            )
        )

        self.assertNotIn("discovered", result.structuredContent)

    @patch("trac_mcp_server.mcp.tools.instances.scrape_project_index")
    def test_default_url_matches_default_instance(self, mock_scrape):
        registry = InstanceRegistry(self.config, {})
        set_instance_registry(registry)
        mock_scrape.return_value = []

        result = asyncio.run(
            ToolRegistry(INSTANCE_SPECS).call_tool(
                "list_instances", {}, self.client
            )
        )

        self.assertEqual(
            result.structuredContent["default"], self.config.trac_url
        )

    @patch("trac_mcp_server.mcp.tools.instances.caller_identity")
    @patch("trac_mcp_server.mcp.tools.instances.scrape_project_index")
    def test_discover_scrapes_with_caller_identity(
        self, mock_scrape, mock_caller_identity
    ):
        """Ticket #102 gap 5: discovery authenticates as the caller's own
        identity, not whichever identity started this process."""
        registry = InstanceRegistry(self.config, {})
        set_instance_registry(registry)
        mock_caller_identity.return_value = Identity(
            name="alice", username="alice-trac", password="alice-pw"
        )
        mock_scrape.return_value = []

        asyncio.run(
            ToolRegistry(INSTANCE_SPECS).call_tool(
                "list_instances", {}, self.client
            )
        )

        mock_scrape.assert_called_once()
        _host_root, credentials = mock_scrape.call_args[0]
        self.assertEqual(credentials, ("alice-trac", "alice-pw"))

    @patch("trac_mcp_server.mcp.tools.instances.caller_identity")
    @patch("trac_mcp_server.mcp.tools.instances.scrape_project_index")
    def test_discover_scrapes_with_default_credentials_when_no_identity(
        self, mock_scrape, mock_caller_identity
    ):
        registry = InstanceRegistry(self.config, {})
        set_instance_registry(registry)
        mock_caller_identity.return_value = None
        mock_scrape.return_value = []

        asyncio.run(
            ToolRegistry(INSTANCE_SPECS).call_tool(
                "list_instances", {}, self.client
            )
        )

        _host_root, credentials = mock_scrape.call_args[0]
        self.assertEqual(
            credentials, (self.config.username, self.config.password)
        )

    # Ticket #153: the write gate's switch, which nothing a session
    # could reach used to report.

    def _list(self, config):
        set_instance_registry(InstanceRegistry(config, {}))
        return asyncio.run(
            ToolRegistry(INSTANCE_SPECS).call_tool(
                "list_instances", {"discover": False}, self.client
            )
        )

    def test_153_reports_the_write_gate_on(self):
        result = self._list(self.config)
        self.assertIs(result.structuredContent["write_gate"], True)
        text = result.content[0].text
        self.assertIn("write_gate: true", text.splitlines())

    def test_153_reports_the_write_gate_off(self):
        config = dataclasses.replace(self.config, write_gate=False)
        result = self._list(config)
        self.assertIs(result.structuredContent["write_gate"], False)
        text = result.content[0].text
        self.assertIn("write_gate: false", text)
        self.assertIn("NOT link-checked", text)

    def test_153_reads_the_daemon_setting_not_the_resolved_client(self):
        """A caller scoped away from the default instance reaches this
        handler with no client at all (ticket #110), and must still be
        told -- the setting is the daemon's, inherited by every routed
        instance (ticket #117)."""
        config = dataclasses.replace(self.config, write_gate=False)
        set_instance_registry(InstanceRegistry(config, {}))
        result = asyncio.run(
            ToolRegistry(INSTANCE_SPECS).call_tool(
                "list_instances", {"discover": False}, None
            )
        )
        self.assertFalse(result.isError, result.content[0].text)
        self.assertIs(result.structuredContent["write_gate"], False)


if __name__ == "__main__":
    unittest.main()
