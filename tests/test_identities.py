"""Tests for trac_mcp_server.instances.load_identities() and caller_identity()
-- ticket #102's TRAC_IDENTITIES parsing, ``${VAR}`` interpolation, and the
per-request contextvar read.

InstanceRegistry's identity-aware resolve()/get_client() live in
test_instances.py, next to the rest of the registry's behaviour.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from mcp.server.lowlevel.server import request_ctx
from starlette.applications import Starlette
from starlette.requests import Request

from trac_mcp_server.instances import (
    IDENTITIES_CONFIGURED_STATE,
    Identity,
    IdentityRequiredError,
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


@contextmanager
def _in_request(*, identities_configured: bool | None, **scope_extra):
    """Run with ``request_ctx`` set the way the mcp SDK sets it over http:
    ``context.request`` is the Starlette ``Request`` for the in-flight
    message, whose scope carries the app and whatever the auth
    middleware stashed. ``identities_configured=None`` leaves the app's
    state untouched (an app that never declared the flag)."""
    app = Starlette()
    if identities_configured is not None:
        setattr(
            app.state,
            IDENTITIES_CONFIGURED_STATE,
            identities_configured,
        )
    scope = {"type": "http", "headers": [], "app": app, **scope_extra}
    token = request_ctx.set(SimpleNamespace(request=Request(scope)))  # type: ignore[arg-type]
    try:
        yield
    finally:
        request_ctx.reset(token)


_ALICE = Identity(name="alice", username="a", password="a-pw")


class TestCallerIdentityRequiresAuthMark:
    """Trac /trac_mcp_server #268 (L27): with identities configured, a
    request the auth middleware did not admit must never reach the
    default credentials."""

    def test_identities_configured_and_unmarked_raises(self):
        with _in_request(identities_configured=True):
            with pytest.raises(IdentityRequiredError):
                caller_identity()

    def test_unmarked_with_an_identity_in_scope_still_raises(self):
        """Only the middleware's mark counts, not a stray identity."""
        with _in_request(
            identities_configured=True, trac_identity=_ALICE
        ):
            with pytest.raises(IdentityRequiredError):
                caller_identity()

    def test_static_mark_returns_none_for_default_credentials(self):
        with _in_request(
            identities_configured=True, trac_auth="static"
        ):
            assert caller_identity() is None

    def test_identity_mark_returns_the_identity(self):
        with _in_request(
            identities_configured=True,
            trac_auth="identity",
            trac_identity=_ALICE,
        ):
            assert caller_identity() is _ALICE

    def test_no_identities_configured_unmarked_returns_none(self):
        with _in_request(identities_configured=False):
            assert caller_identity() is None

    def test_app_without_the_flag_unmarked_returns_none(self):
        with _in_request(identities_configured=None):
            assert caller_identity() is None

    def test_request_without_an_app_returns_none(self):
        with _in_request(identities_configured=True, app=None):
            assert caller_identity() is None

    def test_stdio_request_is_none_returns_none(self):
        token = request_ctx.set(SimpleNamespace(request=None))  # type: ignore[arg-type]
        try:
            assert caller_identity() is None
        finally:
            request_ctx.reset(token)

    def test_refusal_is_logged_without_any_token(self, caplog):
        with _in_request(
            identities_configured=True,
            headers=[(b"authorization", b"Bearer sentinel-token-xyz")],
        ):
            with caplog.at_level("WARNING"):
                with pytest.raises(IdentityRequiredError):
                    caller_identity()
        assert any(r.levelname == "WARNING" for r in caplog.records)
        assert "sentinel-token-xyz" not in caplog.text
