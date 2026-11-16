"""``--version`` of trac-mcp, trac-convert and trac-mcp-server names the
build commit, or says it is unknown (Trac /trac_mcp_server #230).

Each parser builds its string through ``version.cli_version``, which
looks ``release_sha`` up in ``trac_mcp_server.version``, so that is the
one place the tests patch.
"""

import pytest

from trac_mcp_server import __version__, version
from trac_mcp_server.cli import client, convert
from trac_mcp_server.mcp import server

SHA = "0123456789abcdef" * 2 + "01234567"

# (module, parser prefix before the version)
CLIS = [
    pytest.param(client, "trac-mcp version", id="trac-mcp"),
    pytest.param(convert, "trac-convert", id="trac-convert"),
    pytest.param(
        server, "trac-mcp-server version", id="trac-mcp-server"
    ),
]


def version_output(module, monkeypatch, capsys, sha):
    monkeypatch.setattr(version, "release_sha", lambda: sha)
    with pytest.raises(SystemExit) as exc:
        module.build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    return capsys.readouterr().out.strip()


@pytest.mark.parametrize("module, prefix", CLIS)
def test_version_names_the_build_commit(
    module, prefix, monkeypatch, capsys
):
    out = version_output(module, monkeypatch, capsys, SHA)
    assert out == f"{prefix} {__version__}+g0123456789ab"


@pytest.mark.parametrize("module, prefix", CLIS)
def test_version_marks_a_dirty_build(
    module, prefix, monkeypatch, capsys
):
    out = version_output(module, monkeypatch, capsys, SHA + "-dirty")
    assert out == f"{prefix} {__version__}+g0123456789ab.dirty"


@pytest.mark.parametrize("module, prefix", CLIS)
def test_version_says_when_the_commit_is_unknown(
    module, prefix, monkeypatch, capsys
):
    out = version_output(module, monkeypatch, capsys, None)
    assert out == f"{prefix} {__version__} (commit unknown)"
