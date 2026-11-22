"""Offline tests for how scripts/agent_scenarios.py launches the server
(Trac /trac_mcp_server #288).

The script starts ``trac-mcp-server`` as a subprocess over stdio. The MCP
SDK gives that subprocess only a small default environment when ``env`` is
left unset, so an exported ``TRAC_URL``/``TRAC_PASSWORD`` never reached it
and the script's documented "the server also reads TRAC_* variables" was
untrue. These tests pin the launch parameters without starting a server:

* the connection variables of the caller's environment (``TRAC_URL``,
  ``TRAC_USERNAME``, ``TRAC_PASSWORD``, ``TRAC_INSECURE``,
  ``TRAC_RPC_TIMEOUT``), and nothing else of it beyond the SDK's own
  defaults, are passed to the subprocess. The server's own mode settings
  (``TRAC_MCP_TRANSPORT``, ``TRAC_MCP_READ_ONLY``, ...) are not: they would
  make the child serve HTTP, or list fewer tools than the scenario expects;
* no password value is ever put on the command line (visible in ``ps``);
  the script has no ``--password`` option, the password is
  ``TRAC_PASSWORD`` only.
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from mcp.client.stdio import get_default_environment

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "agent_scenarios.py"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "agent_scenarios_under_test", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scenarios = _load_script()

SECRET = "hunter2-not-for-argv"
ENVIRON = {
    "TRAC_URL": "http://trac.example.org/trac_test",
    "TRAC_PASSWORD": SECRET,
    "FOO": "unrelated",
}


def _parse(*argv):
    return scenarios.build_parser().parse_args(list(argv))


def test_connection_variables_reach_the_server_and_others_do_not():
    params = scenarios.build_server_params(_parse(), ENVIRON)

    assert params.env is not None
    assert params.env["TRAC_URL"] == ENVIRON["TRAC_URL"]
    assert params.env["TRAC_PASSWORD"] == SECRET
    assert "FOO" not in params.env


@pytest.mark.parametrize("name", sorted(scenarios.CONNECTION_ENV_VARS))
def test_every_connection_variable_is_forwarded(name):
    params = scenarios.build_server_params(
        _parse(), {name: "value-for-" + name}
    )

    assert params.env is not None
    assert params.env[name] == "value-for-" + name


def test_the_connection_variables_are_these():
    assert scenarios.CONNECTION_ENV_VARS == frozenset(
        {
            "TRAC_URL",
            "TRAC_USERNAME",
            "TRAC_PASSWORD",
            "TRAC_INSECURE",
            "TRAC_RPC_TIMEOUT",
        }
    )


@pytest.mark.parametrize(
    "name",
    [
        "TRAC_MCP_TRANSPORT",
        "TRAC_MCP_READ_ONLY",
        "TRAC_MCP_DISABLED_TOOLS",
        "TRAC_MCP_AUTH_TOKEN",
    ],
)
def test_server_mode_settings_are_not_forwarded(name):
    """A ``TRAC_MCP_*`` setting changes what the child serves or lists:
    TRANSPORT makes it serve HTTP and never answer on stdio, READ_ONLY and
    DISABLED_TOOLS shorten ``list_tools`` (which ``--update-refs`` would
    write into the reference files)."""
    params = scenarios.build_server_params(
        _parse(), {**ENVIRON, name: "http"}
    )

    assert params.env is not None
    assert name not in params.env
    assert params.env["TRAC_URL"] == ENVIRON["TRAC_URL"]


def test_the_sdk_default_environment_is_kept():
    params = scenarios.build_server_params(_parse(), ENVIRON)

    assert params.env is not None
    for key, value in get_default_environment().items():
        assert params.env[key] == value


def test_the_password_is_never_on_the_command_line():
    params = scenarios.build_server_params(
        _parse("--url", "http://t.example.org", "--username", "u"),
        ENVIRON,
    )

    assert SECRET not in params.args
    assert not any(SECRET in a for a in params.args)
    assert "--password" not in params.args


def test_url_username_and_insecure_are_still_forwarded():
    params = scenarios.build_server_params(
        _parse(
            "--url",
            "http://t.example.org",
            "--username",
            "u",
            "--insecure",
        ),
        {},
    )

    assert params.command == "trac-mcp-server"
    assert params.args == [
        "--url",
        "http://t.example.org",
        "--username",
        "u",
        "--insecure",
    ]


def test_password_is_no_longer_an_option(capsys):
    with pytest.raises(SystemExit) as excinfo:
        _parse("--password", "secret")

    assert excinfo.value.code == 2
    assert "--password" in capsys.readouterr().err
