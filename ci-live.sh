#!/bin/bash
# Live CI -- the half of the test suite ci.sh deliberately cannot run.
#
# ci.sh is hermetic on purpose: no credentials, no daemon, no network, so an
# ordinary local run can only fail for reasons belonging to the change under
# test. That leaves every @pytest.mark.live test skipped, and the live suite
# is the only place this project exercises the real Trac substrate
# (Rules/testing/RealSubstrateNotMocks). Two defects landed green through
# ci.sh in one session because nothing ran the other half -- see ticket #81.
#
# Run this before deploying a change to the shared daemon, in addition to
# ci.sh, not instead of it.
#
# NOT RUN must not be able to look like PASSED: without credentials this
# script exits non-zero without running pytest at all, so there is no green
# summary line to misread. That is #80's target_check_skipped shape one
# layer out -- a skipped check reported as a clean one.
#
# ci-live.sh --daemon <url> [--token-env NAME] [--instance PATH
#                            [--allow-instance-writes]]
#
# The in-process suite above builds its own app from this checkout, so it
# cannot see what only a DEPLOYED daemon has: the FreeBSD wheels it really
# installed (constraints/deploy.txt), the config it really ships, its rc
# script and its environment. --daemon replaces the credential probe and the
# pytest run with scripts/daemon_smoke.py, which talks to the daemon over
# HTTP like a client: /healthz, bearer auth enforced, every tool of this
# checkout listed, a wiki read, a wiki write with a byte-exact read-back,
# and a ticket comment post/edit/history (the tracrpc_comment plugin) --
# see that script's docstring. <url> is the daemon base, e.g.
# http://192.168.10.4:8092 (a trailing /mcp or / is accepted). The bearer
# token comes from $TRAC_MCP_AUTH_TOKEN_STAGE, or the variable --token-env
# names, and is never printed. The exit code is the smoke script's: 0
# passed, 1 a check failed (an unreachable daemon included), 2 not run (no
# token, bad URL, a refused --instance). It needs no .env and no Trac
# credentials. It WRITES a wiki page and a ticket, always to the instance
# named by --instance (default /trac_test), passed on every call and never
# the daemon's own default, which on the shared daemon is a production
# store. Any other --instance is refused (exit 2) unless
# --allow-instance-writes is given too. Any other argument prints usage and
# exits 2.

set -e

# Run from the repo root whatever the caller's cwd, so the .env probe and
# pytest both see the same checkout.
cd "$(dirname "$0")"

usage() {
    echo "usage: ci-live.sh" >&2
    echo "       ci-live.sh --daemon URL [--token-env NAME]" >&2
    echo "                          [--instance PATH [--allow-instance-writes]]" >&2
}

if [ "$#" -gt 0 ]; then
    daemon_url=""
    # Everything but the URL goes to daemon_smoke.py as given; it does the
    # validating (and the exit 2 for a refused --instance).
    smoke_args=()
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --daemon)
                if [ "$#" -lt 2 ]; then
                    usage
                    exit 2
                fi
                daemon_url="$2"
                shift 2
                ;;
            --token-env | --instance)
                if [ "$#" -lt 2 ]; then
                    usage
                    exit 2
                fi
                smoke_args+=("$1" "$2")
                shift 2
                ;;
            --allow-instance-writes)
                smoke_args+=("$1")
                shift
                ;;
            *)
                usage
                exit 2
                ;;
        esac
    done
    if [ -z "$daemon_url" ]; then
        usage
        exit 2
    fi
    # exec: the smoke script's exit code is this script's, with no
    # pytest and no credential probe in between.
    exec python scripts/daemon_smoke.py "$daemon_url" ${smoke_args[@]+"${smoke_args[@]}"}
fi

echo "=== Live credentials ==="

# Resolve credentials exactly the way the tests resolve them -- .env first
# (conftest.py loads it under --run-live), then the environment. Testing
# shell variables alone would tell a developer with a working .env that they
# have no credentials.
# A non-zero exit here aborts the script under `set -e`, before pytest.
#
# Ticket #102 adds a second required pair, TRAC_USERNAME_2/TRAC_PASSWORD_2 --
# the second real Trac account (auto_pm) the multi-identity live test needs
# to prove two identities land as two different Trac usernames. Missing it
# must fail this same way, before pytest ever runs -- not skip silently the
# way a bare @pytest.mark.live would (that shape is exactly what #81 fixed).
python - <<'PY'
import os
import sys

from dotenv import find_dotenv, load_dotenv

# This heredoc has no file of its own -- it arrives on stdin -- so
# find_dotenv() has no calling file to start from. usecwd=True starts it at
# the repo root this script has already cd'd to and walks up from there. That
# is the same upward walk tests/conftest.py's bare load_dotenv() makes, except
# that conftest's walk starts one level down in tests/, which holds no .env.
# That walk is how a worktree nested in the main checkout (.claude/worktrees/*)
# reaches the main checkout's .env instead of failing the probe (Trac #128).
load_dotenv(find_dotenv(usecwd=True))

REQUIRED = (
    "TRAC_URL",
    "TRAC_USERNAME",
    "TRAC_PASSWORD",
    "TRAC_USERNAME_2",
    "TRAC_PASSWORD_2",
)
missing = [name for name in REQUIRED if not os.environ.get(name)]

if missing:
    print("")
    print("ERROR: live Trac credentials are not available.", file=sys.stderr)
    print("Missing: " + ", ".join(missing), file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "Set them in .env (see .env.example) or export them. "
        "TRAC_USERNAME_2/TRAC_PASSWORD_2 is a second real Trac account "
        "(ticket #102's multi-identity live test writes as it). The live "
        "suite was NOT run -- this is not a passing result.",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"TRAC_URL={os.environ['TRAC_URL']} as {os.environ['TRAC_USERNAME']}")
print(f"Second identity: {os.environ['TRAC_USERNAME_2']}")
PY

echo ""
echo "=== Full Suite (offline + live) ==="
pytest tests/ -q --run-live

echo ""
echo "=== Live checks passed ==="
