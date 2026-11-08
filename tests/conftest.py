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


@pytest.fixture
def local_file_access():
    """Let the file tools use server-side paths for this test (ticket #111).

    The module default is ``off`` so a server wired up without ``main()``
    fails closed. Tests that exercise the ``file_path``/``output_path``
    forms opt in explicitly with this fixture rather than through an
    autouse one, so a test that silently depends on server paths cannot
    exist.
    """
    from trac_mcp_server.mcp.tools.file_io import set_file_access

    set_file_access("local")
    yield
    set_file_access("off")


@pytest.fixture(autouse=True)
def _live_write_guard(request):
    """Under --run-live, a live row may not write outside /trac_test.

    Trac /trac_mcp_server #173. Three live rows once wrote to the
    default `TRAC_URL` store, a real ticket tracker. A static check
    of the test source is fooled by any `"/trac_test"` string and
    cannot see a write made through `bootstrap_config()`, a helper
    or a CLI call; this watches what is actually sent instead. While
    a live-marked test runs, every TracClient XML-RPC request goes
    through `tests.live_helpers.live_write_guard`: a write (see
    `WRITE_PREFIXES`) from a client whose URL does not end with
    `/trac_test` is recorded and refused before anything is sent.
    Teardown fails the test listing every recorded violation, even
    when the body swallowed the exception. Reads are unrestricted.

    Inactive without --run-live (the live rows are skipped then) and
    for tests without the `live` marker: an offline test drives a
    real TracClient against a mocked transport on an example host,
    and that is not a write.
    """
    if not request.config.getoption("--run-live") or (
        request.node.get_closest_marker("live") is None
    ):
        yield
        return
    from tests.live_helpers import live_write_guard

    with live_write_guard() as violations:
        yield
    if violations:
        pytest.fail(
            "live write refused, not on /trac_test:\n  "
            + "\n  ".join(violations),
            pytrace=False,
        )


@pytest.fixture(scope="session", autouse=True)
def _sweep_stale_live_leftovers(request):
    """Once per live session, delete stale per-run leftovers.

    Trac /trac_mcp_server #173 gave every live object a per-run name,
    which removed the self-healing of the old fixed names (a re-run
    reset or deleted the object an interrupted run left). This
    restores the cleanup: before the first live row it deletes, on
    /trac_test only, objects with this branch's per-run prefixes
    whose last modification is over an hour old (a concurrent run's
    objects are younger and untouched). A sweep error never fails the
    session. See `tests.live_helpers.sweep_stale` for what it can and
    cannot see (milestones: not swept).
    """
    if not request.config.getoption("--run-live"):
        return
    if not (
        os.environ.get("TRAC_URL")
        and os.environ.get("TRAC_USERNAME")
        and os.environ.get("TRAC_PASSWORD")
    ):
        return
    reporter = request.config.pluginmanager.get_plugin(
        "terminalreporter"
    )

    def say(text):
        if reporter is not None:
            reporter.write_line(text)
        else:
            print(text)

    try:
        from tests.live_helpers import sweep_stale, trac_test_client

        deleted = sweep_stale(trac_test_client())
    except Exception as exc:
        say(f"live sweep skipped: {exc!r}")
        return
    for line in deleted:
        say(f"live sweep: deleted stale {line} on /trac_test")
