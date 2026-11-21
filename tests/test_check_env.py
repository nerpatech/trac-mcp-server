"""scripts/check_env.py refuses a drifted environment (Trac #262).

GitHub's test job once installed unconstrained while ci.sh ran on the
constrained local venv, so a local pass said nothing about GitHub. The
script compares the installed requirement closure of trac-mcp-server[dev]
with constraints/deploy.txt and the interpreter with .python-version.
The root package's own requirements come from the checkout's
pyproject.toml, not installed metadata. Everything here is offline: the
distribution lookup and the Python version are injected, and the
declaration files are tmp files.
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


# The root's direct requirements as the checkout declares them; the
# installed metadata of ROOT in _clean_env() says the same, so a test
# that wants them to differ changes one side.
PYPROJECT = """\
[project]
name = "trac-mcp-server"
dependencies = [
    "requests>=2",
    "winonly>=1; sys_platform == 'win32'",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pyright[nodejs]==1.1.408",
]
docs = ["docsonly>=1"]
"""


def _files(tmp_path, pins, python="3.12.8", pyproject=PYPROJECT):
    constraints = tmp_path / "deploy.txt"
    constraints.write_text(
        "# header\n\n" + "".join(f"{n}=={v}\n" for n, v in pins.items())
    )
    pyver = tmp_path / ".python-version"
    pyver.write_text(f"{python}\n")
    (tmp_path / "pyproject.toml").write_text(pyproject)
    return constraints, pyver


def _args(tmp_path, constraints, pyver):
    return [
        "--constraints",
        str(constraints),
        "--python-version-file",
        str(pyver),
        "--pyproject",
        str(tmp_path / "pyproject.toml"),
    ]


def _run(
    tmp_path,
    env,
    pins,
    *,
    python="3.12.8",
    running=None,
    pyproject=PYPROJECT,
):
    constraints, pyver = _files(tmp_path, pins, python, pyproject)
    return check_env.main(
        _args(tmp_path, constraints, pyver),
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
        _args(tmp_path, constraints, pyver),
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
        _args(tmp_path, constraints, pyver),
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
        _args(tmp_path, constraints, pyver),
        lookup=env,
        python_version=lambda: "3.12.8",
    )
    assert code == 2
    assert "requests>=2" in capsys.readouterr().err


def _constraints_error(tmp_path, capsys, text):
    env, pins = _clean_env()
    constraints, pyver = _files(tmp_path, pins)
    constraints.write_text(text)
    code = check_env.main(
        _args(tmp_path, constraints, pyver),
        lookup=env,
        python_version=lambda: "3.12.8",
    )
    return code, capsys.readouterr().err


@pytest.mark.parametrize(
    "line, what",
    [
        ("requests==2.32.5 ; python_version >= '3.9'", "marker"),
        ("requests===2.32.5", "requests===2.32.5"),
        ("requests>=2", "requests>=2"),
        ("requests==2.*", "requests==2.*"),
        ("requests[security]==2.32.5", "requests[security]"),
        ("requests", "requests"),
        ("==2.32.5", "==2.32.5"),
    ],
)
def test_a_constraints_line_that_is_not_an_exact_pin_cannot_run(
    tmp_path, capsys, line, what
):
    # Line 3: the error names the line number and the line.
    code, err = _constraints_error(
        tmp_path, capsys, f"# header\n\n{line}\n"
    )
    assert code == 2
    assert "deploy.txt:3:" in err
    assert what in err


def test_inline_comments_and_whitespace_in_constraints_are_stripped(
    tmp_path,
):
    env, pins = _clean_env()
    constraints, pyver = _files(tmp_path, pins)
    constraints.write_text(
        "".join(
            f"  {n} == {v}   # why this pin\n" for n, v in pins.items()
        )
        + "   # a comment with == and ; in it\n"
    )
    code = check_env.main(
        _args(tmp_path, constraints, pyver),
        lookup=env,
        python_version=lambda: "3.12.8",
    )
    assert code == 0


def test_parse_constraints_returns_normalised_names_and_versions(
    tmp_path,
):
    f = tmp_path / "c.txt"
    f.write_text(
        "Foo_Bar==1.0  # c\n\n# only a comment\nbaz==2!3.4+x\n"
    )
    assert check_env.parse_constraints(f) == {
        "foo-bar": "1.0",
        "baz": "2!3.4+x",
    }


def _python_version_error(tmp_path, capsys, text):
    env, pins = _clean_env()
    constraints, pyver = _files(tmp_path, pins)
    pyver.write_text(text)
    code = check_env.main(
        _args(tmp_path, constraints, pyver),
        lookup=env,
        python_version=lambda: "3.12.8",
    )
    return code, capsys.readouterr().err


def test_python_version_ignores_blank_and_comment_lines(tmp_path):
    f = tmp_path / ".python-version"
    f.write_text("# the CI interpreter\n\n3.12.8\n\n")
    assert check_env.read_python_version(f) == "3.12.8"


def test_a_multi_version_python_file_cannot_run(tmp_path, capsys):
    code, err = _python_version_error(
        tmp_path, capsys, "3.12.8\n3.11.9\n"
    )
    assert code == 2
    assert "exactly one version is required" in err
    assert "3.12.8" in err and "3.11.9" in err


def test_a_python_file_with_only_comments_cannot_run(tmp_path, capsys):
    code, err = _python_version_error(tmp_path, capsys, "# nothing\n\n")
    assert code == 2
    assert "is empty" in err


def test_root_requirements_come_from_pyproject_not_installed_metadata(
    tmp_path, capsys
):
    # The checkout declares `extra-dep`; the root's INSTALLED metadata
    # (stale, or another checkout's) does not list it. The installed
    # metadata is not consulted for the root, so the unpinned dependency
    # is found.
    env, pins = _clean_env()
    env.dists["extra-dep"] = FakeDist("1.0")
    assert not any("extra-dep" in r for r in env.dists[ROOT].requires)
    changed = PYPROJECT.replace(
        '"requests>=2",', '"requests>=2",\n    "extra-dep>=1",'
    )
    assert _run(tmp_path, env, pins, pyproject=changed) == 1
    assert "extra-dep: installed 1.0, no pin" in capsys.readouterr().err


def test_a_requirement_only_in_installed_root_metadata_is_ignored(
    tmp_path,
):
    # The mirror: the installed root metadata names a package the
    # checkout no longer requires (and which is not installed).
    env, pins = _clean_env()
    env.dists[ROOT].requires.append("gone>=1")
    assert _run(tmp_path, env, pins) == 0


def test_a_pyproject_dependency_that_is_not_installed_fails(
    tmp_path, capsys
):
    env, pins = _clean_env()
    changed = PYPROJECT.replace(
        '"requests>=2",', '"requests>=2",\n    "absent>=1",'
    )
    assert _run(tmp_path, env, pins, pyproject=changed) == 1
    assert "absent: required but not installed" in (
        capsys.readouterr().err
    )


def test_a_missing_pyproject_cannot_run(tmp_path, capsys):
    env, pins = _clean_env()
    constraints, pyver = _files(tmp_path, pins)
    (tmp_path / "pyproject.toml").unlink()
    code = check_env.main(
        _args(tmp_path, constraints, pyver),
        lookup=env,
        python_version=lambda: "3.12.8",
    )
    assert code == 2
    assert "pyproject.toml" in capsys.readouterr().err


def test_an_unparseable_pyproject_cannot_run(tmp_path, capsys):
    env, pins = _clean_env()
    assert _run(tmp_path, env, pins, pyproject="[project\n") == 2
    assert "pyproject.toml" in capsys.readouterr().err


def test_an_invalid_requirement_cannot_run_and_prints_no_traceback(
    tmp_path, capsys
):
    env, pins = _clean_env()
    changed = PYPROJECT.replace('"requests>=2",', '"requests>=>2",')
    assert _run(tmp_path, env, pins, pyproject=changed) == 2
    err = capsys.readouterr().err
    assert "requests>=>2" in err
    assert "Traceback" not in err


def test_an_invalid_requirement_in_installed_metadata_cannot_run(
    tmp_path, capsys
):
    env, pins = _clean_env()
    env.dists["requests"].requires.append("not a requirement !!")
    assert _run(tmp_path, env, pins) == 2
    assert "not a requirement" in capsys.readouterr().err


def test_a_marker_that_cannot_be_evaluated_cannot_run(
    tmp_path, capsys, monkeypatch
):
    import packaging.markers

    def boom(self, environment=None):
        raise packaging.markers.UndefinedEnvironmentName("nope")

    monkeypatch.setattr(packaging.markers.Marker, "evaluate", boom)
    env, pins = _clean_env()
    assert _run(tmp_path, env, pins) == 2
    err = capsys.readouterr().err
    assert "marker" in err
    assert "Traceback" not in err


def test_a_missing_packaging_library_cannot_run(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setitem(sys.modules, "packaging.requirements", None)
    env, pins = _clean_env()
    assert _run(tmp_path, env, pins) == 2
    assert "packaging" in capsys.readouterr().err


def test_an_uninstalled_root_cannot_run(tmp_path, capsys):
    env, pins = _clean_env()
    del env.dists[ROOT]
    assert _run(tmp_path, env, pins) == 2
    assert ROOT in capsys.readouterr().err


def test_the_shipped_pyproject_names_the_root_requirements():
    reqs = check_env.root_requirements(REPO / "pyproject.toml")
    assert ("pytest>=8.0.0", "dev") in reqs
    assert any(r.startswith("mistune") and e == "" for r, e in reqs)


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
