"""Tests for trac_mcp_server.instances.load_identities() and caller_identity()
-- ticket #102's TRAC_IDENTITIES parsing, ``${VAR}`` interpolation, and the
per-request contextvar read.

InstanceRegistry's identity-aware resolve()/get_client() live in
test_instances.py, next to the rest of the registry's behaviour.
"""

import pytest

from trac_mcp_server.instances import (
    Identity,
    caller_identity,
    load_identities,
)

# ---------------------------------------------------------------------------
# load_identities()
# ---------------------------------------------------------------------------


class TestLoadIdentities:
    def test_unset_env_var_returns_empty(self, monkeypatch):
        monkeypatch.delenv("TRAC_IDENTITIES", raising=False)
        assert load_identities() == {}

    def test_instances_style_wrapped_shape(self, tmp_path, monkeypatch):
        identities_file = tmp_path / "identities.yml"
        identities_file.write_text(
            "identities:\n"
            "  alice:\n"
            "    token: tok-alice\n"
            "    username: alice-trac\n"
            "    password: alice-pw\n"
        )
        monkeypatch.setenv("TRAC_IDENTITIES", str(identities_file))

        identities = load_identities()

        assert identities == {
            "tok-alice": Identity(
                name="alice", username="alice-trac", password="alice-pw"
            )
        }

    def test_bare_mapping_shape_accepted(self, tmp_path, monkeypatch):
        identities_file = tmp_path / "identities.yml"
        identities_file.write_text(
            "alice:\n"
            "  token: tok-alice\n"
            "  username: alice-trac\n"
            "  password: alice-pw\n"
        )
        monkeypatch.setenv("TRAC_IDENTITIES", str(identities_file))

        identities = load_identities()

        assert identities["tok-alice"].name == "alice"

    def test_multiple_identities_keyed_by_token(
        self, tmp_path, monkeypatch
    ):
        identities_file = tmp_path / "identities.yml"
        identities_file.write_text(
            "identities:\n"
            "  alice:\n"
            "    token: tok-alice\n"
            "    username: alice-trac\n"
            "    password: alice-pw\n"
            "  bob:\n"
            "    token: tok-bob\n"
            "    username: bob-trac\n"
            "    password: bob-pw\n"
        )
        monkeypatch.setenv("TRAC_IDENTITIES", str(identities_file))

        identities = load_identities()

        assert set(identities) == {"tok-alice", "tok-bob"}
        assert identities["tok-bob"].name == "bob"

    def test_env_var_interpolation(self, tmp_path, monkeypatch):
        identities_file = tmp_path / "identities.yml"
        identities_file.write_text(
            "identities:\n"
            "  alice:\n"
            "    token: ${ALICE_TOKEN}\n"
            "    username: ${ALICE_USER}\n"
            "    password: ${ALICE_PASS}\n"
        )
        monkeypatch.setenv("TRAC_IDENTITIES", str(identities_file))
        monkeypatch.setenv("ALICE_TOKEN", "interpolated-token")
        monkeypatch.setenv("ALICE_USER", "interpolated-user")
        monkeypatch.setenv("ALICE_PASS", "interpolated-pass")

        identities = load_identities()

        assert identities == {
            "interpolated-token": Identity(
                name="alice",
                username="interpolated-user",
                password="interpolated-pass",
            )
        }

    def test_missing_token_raises(self, tmp_path, monkeypatch):
        identities_file = tmp_path / "identities.yml"
        identities_file.write_text(
            "identities:\n"
            "  alice:\n"
            "    username: alice-trac\n"
            "    password: alice-pw\n"
        )
        monkeypatch.setenv("TRAC_IDENTITIES", str(identities_file))

        with pytest.raises(ValueError, match="no token"):
            load_identities()

    def test_missing_username_raises(self, tmp_path, monkeypatch):
        identities_file = tmp_path / "identities.yml"
        identities_file.write_text(
            "identities:\n"
            "  alice:\n"
            "    token: tok-alice\n"
            "    password: alice-pw\n"
        )
        monkeypatch.setenv("TRAC_IDENTITIES", str(identities_file))

        with pytest.raises(ValueError, match="no username"):
            load_identities()

    def test_missing_password_raises(self, tmp_path, monkeypatch):
        identities_file = tmp_path / "identities.yml"
        identities_file.write_text(
            "identities:\n"
            "  alice:\n"
            "    token: tok-alice\n"
            "    username: alice-trac\n"
        )
        monkeypatch.setenv("TRAC_IDENTITIES", str(identities_file))

        with pytest.raises(ValueError, match="no password"):
            load_identities()

    def test_blank_token_raises(self, tmp_path, monkeypatch):
        """An unset interpolation var collapses to "" -- not just a
        missing key -- and must be caught the same way."""
        identities_file = tmp_path / "identities.yml"
        identities_file.write_text(
            "identities:\n"
            "  alice:\n"
            "    token: ${UNSET_VAR}\n"
            "    username: alice-trac\n"
            "    password: alice-pw\n"
        )
        monkeypatch.setenv("TRAC_IDENTITIES", str(identities_file))
        monkeypatch.delenv("UNSET_VAR", raising=False)

        with pytest.raises(ValueError, match="no token"):
            load_identities()

    def test_duplicate_token_raises(self, tmp_path, monkeypatch):
        identities_file = tmp_path / "identities.yml"
        identities_file.write_text(
            "identities:\n"
            "  alice:\n"
            "    token: tok-shared\n"
            "    username: alice-trac\n"
            "    password: alice-pw\n"
            "  bob:\n"
            "    token: tok-shared\n"
            "    username: bob-trac\n"
            "    password: bob-pw\n"
        )
        monkeypatch.setenv("TRAC_IDENTITIES", str(identities_file))

        with pytest.raises(ValueError, match="reuses a token"):
            load_identities()


# ---------------------------------------------------------------------------
# caller_identity()
# ---------------------------------------------------------------------------


class TestCallerIdentity:
    def test_outside_a_request_returns_none(self):
        """No request_ctx has ever been set in this process/thread --
        request_ctx.get() raises LookupError, which must be swallowed."""
        assert caller_identity() is None
