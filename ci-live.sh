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
# script exits 2 without running pytest at all, so there is no green
# summary line to misread. That is #80's target_check_skipped shape one
# layer out -- a skipped check reported as a clean one.
#
# Exit codes, the same three states in both modes:
#   0  the live suite ran and passed.
#   1  the live suite ran and FAILED: pytest's own exit 1, or its exit 2
#      when the output reports errors during collection (a test module
#      that does not import is a broken change).
#   2  the live suite could NOT run, so there is no verdict: a tool this
#      script calls is not on PATH, the credential probe exited non-zero
#      for any reason (missing credentials or python-dotenv, an unreadable
#      .env), pytest stopped before giving a verdict (any other exit 2, or
#      3 to 5: interrupted, internal error, usage error, no tests
#      collected), or the command line was bad. A caller that treats 1 as
#      "fix the change" and 2 as "fix the environment" can rely on this.
#
# ci-live.sh --daemon <url> [--token-env NAME] [--instance PATH
#                            [--allow-instance-writes]] [--expect-sha SHA]
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
# http://mcp.example.org:8092 (a trailing /mcp or / is accepted). The bearer
# token comes from $TRAC_MCP_AUTH_TOKEN_STAGE, or the variable --token-env
# names, and is never printed. The exit code is the smoke script's: 0
# passed, 1 a check failed, 2 not run (no token, bad URL, a refused
# --instance, or a daemon that cannot be reached at all: connection
# refused, DNS failure, no answer from /healthz). It needs no .env and no Trac
# credentials. It WRITES a wiki page and a ticket, always to the instance
# named by --instance (default /trac_test), passed on every call and never
# the daemon's own default, which on the shared daemon is a production
# store. Any other --instance is refused (exit 2) unless
# --allow-instance-writes is given too. --expect-sha SHA (7 to 40 lowercase
# hex characters) makes check 1 fail unless the daemon's /healthz reports a
# clean release built from that commit (or one it is the prefix of); without
# it the reported release is only shown. A malformed SHA is refused (exit 2)
# before any network call. Any other argument prints usage and
# exits 2. The daemon must be started WITHOUT --permissions-file: a daemon
# that filters its tool list by permission is unsupported (check 3 expects
# every tool this checkout defines, so it fails by design).

set -e

# Every tool this script calls must be on PATH before anything else runs, so a
# bare shell fails with one line naming the fix, not a "command not found"
# (127) from deep inside that reads like a broken change. Exit 2: not run.
# GitHub's runner has no .venv, so the fix is a PATH one, not a path to
# .venv/bin. --daemon execs only python; the live suite needs pytest too.
# The fix repeats this run's arguments, quoted: a bare ./ci-live.sh would
# run the full live suite, not the --daemon smoke that was asked for.
if [ "$#" -eq 0 ]; then
    needed="python pytest"
else
    needed="python"
fi
missing=""
for tool in $needed; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        missing="${missing:+$missing, }$tool"
    fi
done
if [ -n "$missing" ]; then
    rerun="./ci-live.sh"
    if [ "$#" -gt 0 ]; then
        rerun="$rerun $(printf '%q ' "$@")"
        rerun="${rerun% }"
    fi
    echo "ci-live.sh: not on PATH: $missing. Fix: PATH=<checkout>/.venv/bin:\$PATH $rerun" >&2
    exit 2
fi

# Run from the repo root whatever the caller's cwd, so the .env probe and
# pytest both see the same checkout.
cd "$(dirname "$0")"

usage() {
    echo "usage: ci-live.sh" >&2
    echo "       ci-live.sh --daemon URL [--token-env NAME]" >&2
    echo "                          [--instance PATH [--allow-instance-writes]]" >&2
    echo "                          [--expect-sha SHA]" >&2
    echo "       (--daemon: the daemon must run without --permissions-file)" >&2
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
            --token-env | --instance | --expect-sha)
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
# Any non-zero exit here ends the script before pytest with 2 (cannot run),
# never 1 (failed): no test has run. That covers the probe's own
# sys.exit(2) paths and anything else that stops it, e.g. load_dotenv
# raising UnicodeDecodeError on a .env that is not UTF-8 (Python's exit 1).
#
# Ticket #102 adds a second required pair, TRAC_USERNAME_2/TRAC_PASSWORD_2 --
# the second real Trac account (auto_pm) the multi-identity live test needs
# to prove two identities land as two different Trac usernames. Missing it
# must fail this same way, before pytest ever runs -- not skip silently the
# way a bare @pytest.mark.live would (that shape is exactly what #81 fixed).
probe_rc=0
python - <<'PY' || probe_rc=$?
import os
import sys

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:
    print(
        "ERROR: python-dotenv is not installed, so the live credentials "
        "cannot be resolved. The live suite was NOT run -- this is not a "
        "passing result.",
        file=sys.stderr,
    )
    sys.exit(2)

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
    sys.exit(2)

print(f"TRAC_URL={os.environ['TRAC_URL']} as {os.environ['TRAC_USERNAME']}")
print(f"Second identity: {os.environ['TRAC_USERNAME_2']}")
PY
if [ "$probe_rc" -ne 0 ]; then
    echo "ci-live.sh: the credential probe exited $probe_rc. The live suite was NOT run -- this is not a passing result." >&2
    exit 2
fi

echo ""
echo "=== Full Suite (offline + live) ==="
# pytest's exit 1 is a failed run and stays 1. Its exit 2 is a failure too
# when the output reports errors during collection (a test module that does
# not import); any other 2 (interrupted) and 3 to 5 (internal error, usage
# error, nothing collected) are no verdict, so 2. The output is tee'd to a
# temp file to read that line while it still streams; the pipeline's status
# is tee's, so `set -e` does not end the script before the mapping, and
# PIPESTATUS holds pytest's. ci.sh's step() maps pytest the same way.
live_log="$(mktemp)"
trap 'rm -f "$live_log"' EXIT
pytest tests/ -q --run-live | tee "$live_log"
live_rc="${PIPESTATUS[0]}"
if [ "$live_rc" -eq 1 ]; then
    exit 1
elif [ "$live_rc" -eq 2 ] && grep -Eq 'errors? during collection' "$live_log"; then
    echo "ci-live.sh: pytest could not collect the suite (errors during collection). This is a failure of the change." >&2
    exit 1
elif [ "$live_rc" -ne 0 ]; then
    echo "ci-live.sh: pytest exited $live_rc before giving a verdict. The live suite was NOT run to completion -- this is not a passing result." >&2
    exit 2
fi

echo ""
echo "=== Live checks passed ==="
