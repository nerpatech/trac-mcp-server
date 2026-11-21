"""scripts/check_env.py refuses a drifted environment (Trac #262).

GitHub's test job once installed unconstrained while ci.sh ran on the
constrained local venv, so a local pass said nothing about GitHub. The
script compares the installed requirement closure of trac-mcp-server[dev]
with constraints/deploy.txt and the interpreter with .python-version.
Everything here is offline: the distribution lookup and the Python version
are injected, and the declaration files are tmp files.
"""

import importlib.metadata
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check_env.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_env_under_test", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_env = _load()


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class FakeDist:
    def __init__(self, version, requires=None):
        self.version = version
        self.requires = requires


class FakeEnv:
    """An installed set: name -> FakeDist, looked up PEP 503-normalised
    like importlib.metadata does."""

    def __init__(self, dists):
        self.dists = {_norm(n): d for n, d in dists.items()}

    def __call__(self, name):
        try:
            return self.dists[_norm(name)]
        except KeyError:
            raise importlib.metadata.PackageNotFoundError(
                name
            ) from None


def _files(tmp_path, pins, python="3.12.8"):
    constraints = tmp_path / "deploy.txt"
    constraints.write_text(
        "# header\n\n" + "".join(f"{n}=={v}\n" for n, v in pins.items())
    )
    pyver = tmp_path / ".python-version"
    pyver.write_text(f"{python}\n")
    return constraints, pyver


def _run(tmp_path, env, pins, *, python="3.12.8", running=None):
    constraints, pyver = _files(tmp_path, pins, python)
    return check_env.main(
        [
            "--constraints",
            str(constraints),
            "--python-version-file",
            str(pyver),
        ],
        lookup=env,
        python_version=lambda: running or python,
    )


ROOT = "trac-mcp-server"


def _clean_env():
    env = FakeEnv(
        {
            ROOT: FakeDist(
                "3.0.0",
                [
                    "requests>=2",
                    "pytest>=8; extra == 'dev'",
                    "pyright[nodejs]==1.1.408; extra == 'dev'",
                    "winonly>=1; sys_platform == 'win32'",
                    "docsonly>=1; extra == 'docs'",
                ],
            ),
            "requests": FakeDist("2.32.5", ["urllib3>=1.21", "idna<4"]),
            "urllib3": FakeDist("2.6.3"),
            "idna": FakeDist("3.11"),
            "pytest": FakeDist("9.0.2", ["pluggy>=1.5"]),
            "pluggy": FakeDist("1.6.0"),
            "pyright": FakeDist(
                "1.1.408",
                [
                    "nodeenv>=1.6.0",
                    "nodejs-wheel-binaries; extra == 'nodejs'",
                ],
            ),
            "nodeenv": FakeDist("1.12.0"),
            "nodejs-wheel-binaries": FakeDist("24.19.0"),
            # Installed, outside the closure: ignored.
            "stray": FakeDist("0.1"),
        }
    )
    pins = {
        "requests": "2.32.5",
        "urllib3": "2.6.3",
        "idna": "3.11",
        "pytest": "9.0.2",
        "pluggy": "1.6.0",
        "pyright": "1.1.408",
        "nodeenv": "1.12.0",
        "nodejs_wheel_binaries": "24.19.0",
    }
    return env, pins


def test_a_matching_environment_exits_0_and_says_ok(tmp_path, capsys):
    env, pins = _clean_env()
    assert _run(tmp_path, env, pins) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out.strip() == (
        "env: OK, 8 packages match constraints/deploy.txt, "
        "Python 3.12.8"
    )


def test_an_installed_version_that_differs_from_its_pin_fails(
    tmp_path, capsys
):
    env, pins = _clean_env()
    pins["urllib3"] = "2.6.2"
    assert _run(tmp_path, env, pins) == 1
    assert (
        "urllib3: installed 2.6.3, declared 2.6.2"
        in capsys.readouterr().err
    )


def test_a_closure_member_with_no_pin_fails(tmp_path, capsys):
    env, pins = _clean_env()
    del pins["nodeenv"]
    assert _run(tmp_path, env, pins) == 1
    assert (
        "nodeenv: installed 1.12.0, no pin in constraints/deploy.txt"
        in capsys.readouterr().err
    )


def test_a_required_package_that_is_not_installed_fails(
    tmp_path, capsys
):
    env, pins = _clean_env()
    del env.dists["idna"]
    assert _run(tmp_path, env, pins) == 1
    assert "idna: required but not installed" in (
        capsys.readouterr().err
    )


def test_the_wrong_python_fails_and_prints_both(tmp_path, capsys):
    env, pins = _clean_env()
    assert _run(tmp_path, env, pins, running="3.12.16") == 1
    err = capsys.readouterr().err
    assert "Python" in err
    assert "3.12.16" in err and "3.12.8" in err


def test_every_failure_is_reported_in_one_run(tmp_path, capsys):
    env, pins = _clean_env()
    pins["urllib3"] = "0"
    del pins["pluggy"]
    del env.dists["idna"]
    assert _run(tmp_path, env, pins, running="3.13.0") == 1
    err = capsys.readouterr().err
    assert "urllib3: installed 2.6.3, declared 0" in err
    assert "pluggy: installed 1.6.0, no pin" in err
    assert "idna: required but not installed" in err
    assert "3.13.0" in err


def test_the_root_is_exempt_from_pinning(tmp_path):
    env, pins = _clean_env()
    assert ROOT not in pins
    assert _run(tmp_path, env, pins) == 0


def test_a_package_outside_the_closure_is_ignored(tmp_path, capsys):
    env, pins = _clean_env()
    env.dists["stray"] = FakeDist("9.9")
    assert _run(tmp_path, env, pins) == 0
    assert "stray" not in capsys.readouterr().err


def test_an_extra_named_on_a_requirement_pulls_that_extras_deps(
    tmp_path, capsys
):
    # `pyright[nodejs]` makes nodejs-wheel-binaries part of the closure,
    # so an unpinned one fails.
    env, pins = _clean_env()
    del pins["nodejs_wheel_binaries"]
    assert _run(tmp_path, env, pins) == 1
    assert "nodejs-wheel-binaries: installed 24.19.0, no pin" in (
        capsys.readouterr().err
    )


def test_an_extra_nobody_asked_for_is_not_in_the_closure(tmp_path):
    env, pins = _clean_env()
    # `docsonly` needs extra == 'docs'; nothing requests it, and it is
    # not installed here, which would fail were it in the closure.
    assert "docsonly" not in env.dists
    assert _run(tmp_path, env, pins) == 0


def test_a_marker_false_on_this_platform_is_not_required(tmp_path):
    env, pins = _clean_env()
    assert "winonly" not in env.dists
    assert _run(tmp_path, env, pins) == 0


def test_a_diamond_is_walked_once_and_a_cycle_terminates(tmp_path):
    env, pins = _clean_env()
    env.dists["pluggy"] = FakeDist("1.6.0", ["pytest>=8"])
    assert _run(tmp_path, env, pins) == 0


def test_names_are_compared_pep_503_normalised(tmp_path):
    env, pins = _clean_env()
    pins["Nodejs.Wheel_Binaries"] = pins.pop("nodejs_wheel_binaries")
    assert _run(tmp_path, env, pins) == 0


def test_equal_versions_spelled_differently_match(tmp_path):
    env, pins = _clean_env()
    pins["idna"] = "3.11.0"
    assert _run(tmp_path, env, pins) == 0


def test_the_dev_extra_is_the_requested_one(tmp_path, capsys):
    # pytest is required only by extra == 'dev'; an unpinned pytest
    # failing proves the walk evaluated markers with extra=dev.
    env, pins = _clean_env()
    del pins["pytest"]
    assert _run(tmp_path, env, pins) == 1
    assert "pytest: installed 9.0.2, no pin" in capsys.readouterr().err


def test_a_missing_constraints_file_cannot_run(tmp_path, capsys):
    env, pins = _clean_env()
    constraints, pyver = _files(tmp_path, pins)
    constraints.unlink()
    code = check_env.main(
        [
            "--constraints",
            str(constraints),
            "--python-version-file",
            str(pyver),
        ],
        lookup=env,
        python_version=lambda: "3.12.8",
    )
    assert code == 2
    assert "deploy.txt" in capsys.readouterr().err


def test_a_missing_python_version_file_cannot_run(tmp_path, capsys):
    env, pins = _clean_env()
    constraints, pyver = _files(tmp_path, pins)
    pyver.unlink()
    code = check_env.main(
        [
            "--constraints",
            str(constraints),
            "--python-version-file",
            str(pyver),
        ],
        lookup=env,
        python_version=lambda: "3.12.8",
    )
    assert code == 2
    assert ".python-version" in capsys.readouterr().err


def test_a_malformed_constraints_line_cannot_run(tmp_path, capsys):
    env, pins = _clean_env()
    constraints, pyver = _files(tmp_path, pins)
    constraints.write_text("requests>=2\n")
    code = check_env.main(
        [
            "--constraints",
            str(constraints),
            "--python-version-file",
            str(pyver),
        ],
        lookup=env,
        python_version=lambda: "3.12.8",
    )
    assert code == 2
    assert "requests>=2" in capsys.readouterr().err


def test_an_uninstalled_root_cannot_run(tmp_path, capsys):
    env, pins = _clean_env()
    del env.dists[ROOT]
    assert _run(tmp_path, env, pins) == 2
    assert ROOT in capsys.readouterr().err


def test_the_default_files_are_the_repos_and_agree_with_each_other():
    # The shipped declaration parses: every line an exact pin, and the
    # Python file one version.
    pins = check_env.parse_constraints(REPO / "constraints/deploy.txt")
    assert pins["mistune"]
    version = (REPO / ".python-version").read_text().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)


@pytest.mark.parametrize("name", ["nodeenv", "pyright"])
def test_the_dev_closure_is_pinned_in_the_shipped_file(name):
    pins = check_env.parse_constraints(REPO / "constraints/deploy.txt")
    assert name in pins
