"""Offline tests for how scripts/agent_scenarios.py launches the server
(Trac /trac_mcp_server #288).

The script starts ``trac-mcp-server`` as a subprocess over stdio. The MCP
SDK gives that subprocess only a small default environment when ``env`` is
left unset, so an exported ``TRAC_URL``/``TRAC_PASSWORD`` never reached it
and the script's documented "the server also reads TRAC_* variables" was
untrue. These tests pin the launch parameters without starting a server:

* the ``TRAC_*`` variables of the caller's environment, and nothing else of
  it beyond the SDK's own defaults, are passed to the subprocess;
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


def test_trac_variables_reach_the_server_and_others_do_not():
    params = scenarios.build_server_params(_parse(), ENVIRON)

    assert params.env is not None
    assert params.env["TRAC_URL"] == ENVIRON["TRAC_URL"]
    assert params.env["TRAC_PASSWORD"] == SECRET
    assert "FOO" not in params.env


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
