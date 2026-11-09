"""The link-rewrite map: validation, YAML schema, environment variable and
precedence (Trac /trac_mcp_server #213).

The translator's use of the map is in ``test_converter_html_to_markdown``,
the tool argument in ``test_mcp/tools/test_wiki_file.py``, the two CLIs in
``test_cli_convert_trac.py`` and ``test_cli_client.py``.
"""

import pytest
from pydantic import ValidationError

from trac_mcp_server.config import load_config
from trac_mcp_server.config_bootstrap import (
    bootstrap_config,
    load_local_link_rewrites,
)
from trac_mcp_server.config_schema import ExportConfig, build_config
from trac_mcp_server.link_rewrites import (
    parse_env,
    parse_pair,
    parse_pairs,
    validate_link_rewrites,
)

A = {"from": "https://t.example/wiki/", "to": "https://b.example/"}
B = {"from": "https://t.example/", "to": "https://c.example/"}


# --- validation ---------------------------------------------------------


def test_valid_rules_come_back_as_pairs_in_order():
    assert validate_link_rewrites([A, B]) == (
        ("https://t.example/wiki/", "https://b.example/"),
        ("https://t.example/", "https://c.example/"),
    )


def test_an_empty_list_is_a_valid_empty_map():
    assert validate_link_rewrites([]) == ()


def test_an_empty_to_is_valid():
    # Rewriting a prefix to nothing is a real rule (a relative link).
    assert validate_link_rewrites([{"from": "/w/", "to": ""}]) == (
        ("/w/", ""),
    )


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        (
            [{"from": "", "to": "x"}],
            r"link_rewrites\[0\] 'from' must not",
        ),
        (
            [A, {"from": "", "to": "x"}],
            r"link_rewrites\[1\] 'from' must",
        ),
        ([A, A], r"link_rewrites\[1\] repeats 'from'"),
        ([{"from": 1, "to": "x"}], r"'from' must be a string, not int"),
        (
            [{"from": "a", "to": None}],
            r"'to' must be a string, not NoneType",
        ),
        ([{"from": "a"}], r"link_rewrites\[0\] has no 'to'"),
        ([{"to": "a"}], r"link_rewrites\[0\] has no 'from'"),
        ([{"from": "a", "to": "b", "x": 1}], r"unknown key"),
        (["a=b"], r"must be an object"),
        ({"from": "a", "to": "b"}, r"must be a list"),
        ("a=b", r"must be a list"),
    ],
)
def test_a_bad_rule_is_refused_and_named(entries, message):
    with pytest.raises(ValueError, match=message):
        validate_link_rewrites(entries)


def test_the_where_label_names_the_source():
    with pytest.raises(
        ValueError, match=r"^export\.link_rewrites\[0\]"
    ):
        validate_link_rewrites(
            [{"from": "", "to": "x"}], "export.link_rewrites"
        )


def test_parse_pair_splits_at_the_first_equals():
    assert parse_pair("https://t/?a=1=https://b/?a=", "x") == {
        "from": "https://t/?a",
        "to": "1=https://b/?a=",
    }


def test_parse_pair_without_an_equals_is_refused():
    with pytest.raises(ValueError, match="not FROM=TO"):
        parse_pair("https://t/wiki/", "--link-map")


def test_parse_pairs_refuses_an_empty_from_and_a_duplicate():
    with pytest.raises(ValueError, match="must not be empty"):
        parse_pairs(["=x"])
    with pytest.raises(ValueError, match="repeats 'from'"):
        parse_pairs(["a=b", "a=c"])


def test_parse_env_is_whitespace_separated():
    raw = "  https://t/w/=https://b/\n\thttps://t/=/x/  "
    assert parse_env(raw) == (
        ("https://t/w/", "https://b/"),
        ("https://t/", "/x/"),
    )


def test_parse_env_empty_is_an_empty_map():
    assert parse_env("") == ()
    assert parse_env("  \n ") == ()


def test_parse_env_names_the_variable_on_a_bad_pair():
    with pytest.raises(ValueError, match="TRAC_MCP_LINK_REWRITES"):
        parse_env("https://t/w/=https://b/ oops")


# --- the YAML schema -----------------------------------------------------


def test_export_section_defaults_to_no_rules():
    assert build_config({}).export.link_rewrites == []
    assert ExportConfig().link_rewrites == []


def test_export_section_parses_the_rules():
    unified = build_config({"export": {"link_rewrites": [A, B]}})
    assert unified.export.link_rewrites == [A, B]


@pytest.mark.parametrize(
    "entries",
    [
        [{"from": "", "to": "x"}],
        [A, A],
        [{"from": 5, "to": "x"}],
        [{"from": "a", "to": 5}],
        "https://t/=https://b/",
    ],
)
def test_a_bad_export_rule_fails_config_loading(entries):
    with pytest.raises(ValidationError, match=r"export\.link_rewrites"):
        build_config({"export": {"link_rewrites": entries}})


# --- Config.link_rewrites: environment, YAML, precedence ----------------


@pytest.fixture
def trac_env(monkeypatch):
    monkeypatch.setenv("TRAC_URL", "https://trac.example.com/trac")
    monkeypatch.setenv("TRAC_USERNAME", "u")
    monkeypatch.setenv("TRAC_PASSWORD", "p")
    monkeypatch.delenv("TRAC_MCP_LINK_REWRITES", raising=False)


def test_the_default_is_no_rewriting(trac_env):
    assert load_config().link_rewrites == ()


def test_yaml_alone(trac_env):
    fb = {"link_rewrites": [A, B]}
    assert load_config(yaml_fallbacks=fb).link_rewrites == (
        (A["from"], A["to"]),
        (B["from"], B["to"]),
    )


def test_the_environment_replaces_the_yaml_list_whole(
    trac_env, monkeypatch
):
    monkeypatch.setenv("TRAC_MCP_LINK_REWRITES", "/only/=/one/")
    cfg = load_config(yaml_fallbacks={"link_rewrites": [A, B]})
    assert cfg.link_rewrites == (("/only/", "/one/"),)


def test_an_empty_environment_value_clears_the_yaml_list(
    trac_env, monkeypatch
):
    monkeypatch.setenv("TRAC_MCP_LINK_REWRITES", "")
    cfg = load_config(yaml_fallbacks={"link_rewrites": [A, B]})
    assert cfg.link_rewrites == ()


def test_a_bad_environment_value_fails_startup(trac_env, monkeypatch):
    monkeypatch.setenv("TRAC_MCP_LINK_REWRITES", "no-equals-here")
    with pytest.raises(ValueError, match="TRAC_MCP_LINK_REWRITES"):
        load_config()


def test_a_bad_yaml_fallback_fails_startup_naming_the_entry(trac_env):
    with pytest.raises(ValueError, match=r"export\.link_rewrites\[1\]"):
        load_config(
            yaml_fallbacks={
                "link_rewrites": [A, {"from": "", "to": "x"}]
            }
        )


def test_per_instance_configs_carry_the_map(trac_env, monkeypatch):
    import dataclasses

    cfg = load_config(yaml_fallbacks={"link_rewrites": [A]})
    other = dataclasses.replace(cfg, trac_url="https://x.example/trac2")
    assert other.link_rewrites == cfg.link_rewrites != ()


# --- through bootstrap_config and a real config file ---------------------


def _write_config(root, body):
    d = root / ".trac_mcp"
    d.mkdir(exist_ok=True)
    (d / "config.yaml").write_text(body)


_YAML = f"""\
export:
  link_rewrites:
    - from: {A["from"]}
      to: {A["to"]}
"""


def test_bootstrap_reads_export_link_rewrites_from_the_file(
    isolated_config, trac_env
):
    _write_config(isolated_config, _YAML)
    config, _ = bootstrap_config()
    assert config.link_rewrites == ((A["from"], A["to"]),)


def test_bootstrap_environment_beats_the_file(
    isolated_config, trac_env, monkeypatch
):
    _write_config(isolated_config, _YAML)
    monkeypatch.setenv("TRAC_MCP_LINK_REWRITES", "/a/=/b/")
    config, _ = bootstrap_config()
    assert config.link_rewrites == (("/a/", "/b/"),)


def test_bootstrap_empty_environment_clears_the_file(
    isolated_config, trac_env, monkeypatch
):
    _write_config(isolated_config, _YAML)
    monkeypatch.setenv("TRAC_MCP_LINK_REWRITES", "")
    config, _ = bootstrap_config()
    assert config.link_rewrites == ()


def test_bootstrap_without_the_key_has_no_rules(
    isolated_config, trac_env
):
    _write_config(
        isolated_config, "trac:\n  max_parallel_requests: 5\n"
    )
    config, _ = bootstrap_config()
    assert config.link_rewrites == ()


def test_bootstrap_with_a_bad_rule_in_the_file_fails(
    isolated_config, trac_env
):
    _write_config(
        isolated_config,
        "export:\n  link_rewrites:\n    - from: ''\n      to: x\n",
    )
    with pytest.raises(ValueError, match=r"export\.link_rewrites\[0\]"):
        bootstrap_config()


# --- trac-mcp's local read: only export.link_rewrites --------------------


def test_local_read_without_a_config_file_is_none(isolated_config):
    assert load_local_link_rewrites() is None


def test_local_read_without_the_key_is_none(isolated_config):
    _write_config(isolated_config, "trac:\n  url: https://x/\n")
    assert load_local_link_rewrites() is None


def test_local_read_returns_the_rules(isolated_config):
    _write_config(isolated_config, _YAML)
    assert load_local_link_rewrites() == ((A["from"], A["to"]),)


def test_local_read_of_an_empty_list_is_an_empty_map(isolated_config):
    _write_config(isolated_config, "export:\n  link_rewrites: []\n")
    assert load_local_link_rewrites() == ()


def test_local_read_ignores_the_environment(
    isolated_config, monkeypatch
):
    monkeypatch.setenv("TRAC_MCP_LINK_REWRITES", "/a/=/b/")
    assert load_local_link_rewrites() is None


def test_local_read_ignores_an_unrelated_broken_section(
    isolated_config,
):
    _write_config(
        isolated_config,
        _YAML + "server:\n  port: not-a-number\n",
    )
    assert load_local_link_rewrites() == ((A["from"], A["to"]),)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("export: [1, 2]\n", "must be a mapping"),
        (
            "export:\n  link_rewrites:\n    - {from: a, to: b}\n"
            "    - {from: a, to: c}\n",
            "repeats 'from'",
        ),
        ("export:\n  link_rewrites: [a=b]\n", "must be an object"),
        ("export: {link_rewrites: [\n", "cannot read the config file"),
    ],
)
def test_local_read_of_a_broken_file_is_a_value_error(
    isolated_config, body, message
):
    _write_config(isolated_config, body)
    with pytest.raises(ValueError, match=message):
        load_local_link_rewrites()
