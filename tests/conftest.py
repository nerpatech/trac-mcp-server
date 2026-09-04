"""Shared pytest fixtures for trac-mcp-server tests."""

import os
from unittest.mock import MagicMock

import dotenv.main
import pytest
from dotenv import load_dotenv

from trac_mcp_server.config import Config


def _disable_dotenv():
    """Make an offline run behave as if no .env existed anywhere.

    Gating conftest's own ``load_dotenv()`` is not enough:
    ``bootstrap_config()`` calls ``load_dotenv()`` itself -- correctly, since
    the server reads .env at startup -- and that is the path #85's four live
    tests took. Seeding #85's defect back in showed ci.sh still green with
    only the conftest gate in place, which is why this exists.
    """
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    if not hasattr(dotenv.main, "_load_dotenv_disabled"):
        # A python-dotenv without the documented switch: cut the search off
        # instead. load_dotenv() resolves find_dotenv through its own module
        # globals at call time, so this reaches every call site too.
        dotenv.main.find_dotenv = lambda *_args, **_kwargs: ""


def pytest_addoption(parser):
    """Add custom CLI options for test filtering."""
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests that require a live Trac instance",
    )


def pytest_configure(config):
    """Register custom markers, and load .env only for a live run.

    A .env exists to supply live Trac credentials, so an offline run has no
    business reading it: reading it unconditionally is what let ticket #85
    hide, where @pytest.mark.live sat on a private helper instead of the
    class and four live tests ran on every offline run. They found TRAC_URL
    in .env and passed locally while master was red on GitHub Actions, the
    one environment with no credentials.

    Making an offline run credential-free makes it, on every machine, see
    what Actions sees -- so a misplaced live marker fails at the desk of
    whoever moved it. See ticket #81.
    """
    config.addinivalue_line(
        "markers", "live: mark test as requiring a live Trac instance"
    )
    if config.getoption("--run-live"):
        load_dotenv()
    else:
        _disable_dotenv()


def pytest_collection_modifyitems(config, items):
    """Skip live tests unless --run-live is passed."""
    if config.getoption("--run-live"):
        # --run-live given: do not skip live tests
        return
    skip_live = pytest.mark.skip(reason="need --run-live option to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def mock_config():
    """Create a mock Config instance for testing."""
    return Config(
        trac_url="https://trac.example.com/trac",
        username="testuser",
        password="testpass",
        insecure=False,
    )


@pytest.fixture
def mock_trac_client(mock_config):
    """Create a mock TracClient instance for testing."""
    from trac_mcp_server.core.client import TracClient

    client = MagicMock(spec=TracClient)
    client.config = mock_config
    return client


@pytest.fixture
def mock_xml_response():
    """Factory fixture for creating XML-RPC response mocks."""

    def _create_response(content):
        """Create a mock response with given XML content."""
        from unittest.mock import Mock

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = (
            content.encode() if isinstance(content, str) else content
        )
        return mock_response

    return _create_response
