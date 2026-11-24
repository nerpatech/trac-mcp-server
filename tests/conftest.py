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
        # globals at call time, so this reaches a bare load_dotenv(); the
        # bounded lookup in config_bootstrap does not use find_dotenv and
        # reads PYTHON_DOTENV_DISABLED itself.
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


def pytest_collection_finish(session):
    """Probe /trac_test once collection is final; stop the run if it
    is wrong.

    Under --run-live, with at least one live item selected and Trac
    credentials in the environment, ``probe_trac_test`` asks /trac_test
    one XML-RPC question. A non-None answer ends the run here with that
    one line and exit code 2 ("not run", the third state beside pass
    and fail; ``ci-live.sh --daemon`` exits 2 for the same). Without
    this a wrong /trac_test origin (a 308 to another port, say) cost 47
    failures and 18 errors and no line naming the cause, because the
    session sweep fixture swallows the error (Trac /trac_mcp_server
    #224).

    This runs at collection FINISH, on ``session.items``, not in
    ``pytest_collection_modifyitems``: the mark plugin applies -k, -m
    and --deselect in its own modifyitems hook, which can run after
    conftest's, so a probe there would fire for a live row the command
    line has already excluded. No live item left, or no credentials,
    means no probe, and the rows' own handling of missing credentials
    stands.

    The probe stops the whole session, offline and default-instance
    rows included, by design: the live run is only a pass when all of
    it runs, and a run that silently dropped the rows it could not
    reach would read as one.
    """
    if not session.config.getoption("--run-live"):
        return
    if not any("live" in item.keywords for item in session.items):
        return
    from tests.live_helpers import live_credentials_set, probe_trac_test

    if not live_credentials_set():
        return
    problem = probe_trac_test()
    if problem is not None:
        pytest.exit(problem, returncode=2)


@pytest.fixture(autouse=True)
def _restore_async_semaphore():
    """Leave ``async_utils._semaphore`` as the test found it.

    ``init_semaphore`` sets a process-wide asyncio semaphore, and
    ``run_sync`` waits on it, so one test that initialises it and does not
    restore it bounds (and can bind to a dead event loop) every test that
    runs after it (Trac /trac_mcp_server #275).
    """
    from trac_mcp_server.core import async_utils

    saved = async_utils._semaphore
    saved_size = async_utils._max_parallel
    saved_executor = async_utils._rpc_executor
    yield
    # ``init_semaphore`` makes a thread pool too: stop any a test made
    # (the one it replaced was already shut down by the next init).
    made = async_utils._rpc_executor
    if made is not None and made is not saved_executor:
        made.shutdown(wait=False)
    async_utils._semaphore = saved
    async_utils._max_parallel = saved_size
    # A test that re-initialised made ``init_semaphore`` shut the saved
    # pool down: restore None (the uninitialised state), not a dead pool.
    if saved_executor is not None and getattr(
        saved_executor, "_shutdown", False
    ):
        saved_executor = None
    async_utils._rpc_executor = saved_executor


@pytest.fixture
def mock_config():
    """Create a mock Config instance for testing."""
    return Config(
        trac_url="https://trac.example.com/trac",
        username="testuser",
        password="testpass",
        insecure=False,
    )


def _isolate_config_sources(monkeypatch, tmp_path):
    """No config file, ``.env`` or link-rewrite setting reachable.

    Clears the variables that name a config file or a rewrite map, points
    HOME and the CWD at ``tmp_path`` (no ``.trac_mcp/config.yaml``,
    project or global) and disables ``.env``.

    An offline run disables ``.env`` for the whole session
    (``_disable_dotenv``), but under ``--run-live`` it is enabled, and
    ``bootstrap_config()``'s own ``.env`` lookup (the working directory
    upward, to the first ``.git`` or home) would find the repo's ``.env``
    and configure Trac again. So the switch is set here too; the lookup
    honours it before it reads the working directory. The ``find_dotenv``
    patch is kept for a python-dotenv that lacks the switch, and covers
    only calls that go through ``find_dotenv``.
    """
    for var in (
        "TRAC_MCP_CONFIG",
        "TRAC_ASSIST_CONFIG",
        "TRAC_MCP_LINK_REWRITES",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    if not hasattr(dotenv.main, "_load_dotenv_disabled"):
        monkeypatch.setattr(
            dotenv.main, "find_dotenv", lambda *_args, **_kwargs: ""
        )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """Config discovery sees only what the test writes (Trac #213).

    The CWD and HOME are an empty directory, returned: a test that wants
    a config file writes ``<returned>/.trac_mcp/config.yaml``. The Trac
    credentials in the environment are left as they are, so this
    composes with ``fake_trac``.
    """
    _isolate_config_sources(monkeypatch, tmp_path)
    return tmp_path


@pytest.fixture
def no_trac_configured(monkeypatch, tmp_path):
    """Run as if no Trac were configured anywhere (Trac #210).

    TracWiki -> Markdown renders through Trac, so what it does with no
    Trac configured is a behaviour of its own. Clears the Trac env vars
    and isolates config files and ``.env`` as ``isolated_config`` does.
    """
    for var in ("TRAC_URL", "TRAC_USERNAME", "TRAC_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    _isolate_config_sources(monkeypatch, tmp_path)


@pytest.fixture
def fake_trac(monkeypatch):
    """Trac is configured and ``trac-convert`` talks to a fake client.

    Returns the fake (a ``MagicMock(spec=TracClient)``): set
    ``fake.wiki_to_html.return_value`` or
    ``fake.get_wiki_page_html.return_value`` to the HTML Trac would
    render, and ``fake.get_wiki_page.return_value`` to a page's source.
    """
    from trac_mcp_server.core.client import TracClient

    monkeypatch.setenv("TRAC_URL", "https://trac.example.com")
    monkeypatch.setenv("TRAC_USERNAME", "testuser")
    monkeypatch.setenv("TRAC_PASSWORD", "testpass")
    fake = MagicMock(spec=TracClient)
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient", lambda _config: fake
    )
    return fake


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
    from tests.live_helpers import live_credentials_set

    if not live_credentials_set():
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
