#!/bin/bash
# Local CI — mirrors GitHub Actions checks: the same constraints
# (constraints/deploy.txt) and Python (.python-version), verified first
# by the Environment step. Run before pushing to catch issues locally
#
# This script is hermetic on purpose: no credentials, no daemon, no network,
# so it can only fail for reasons belonging to the change under test. The
# live suite is therefore NOT run here — use ci-live.sh for that half
# (ticket #81).
#
# Exit codes (Trac #279), whatever code the failing step itself exited with:
#   0  every check passed.
#   1  a check ran and FAILED -- fix the change: ruff or pyright found
#      something (their exit 1), a test failed or a test module did not
#      import (pytest's 1, or its 2 reporting errors during collection), or
#      build.sh failed.
#   2  the checks could NOT run -- fix the environment: a tool is not on
#      PATH, check_env.py refused the venv, ruff or pyright stopped on an
#      error of their own (2 and up), pytest stopped with no verdict (any
#      other 2, or 3 to 5: interrupted, internal error, usage error, nothing
#      collected), or build.sh hit "command not found" (127).
# The script stops at the first failing step and names it with both codes.
set -e

# Before anything else: every tool this script calls must be on PATH (pip
# and pyinstaller are build.sh's). A shell without the venv would otherwise
# die deep inside with "command not found" (127), which reads like a broken
# change. GitHub's runner has no .venv, so the fix is a PATH one, not a path
# to .venv/bin. The fix repeats this run's arguments, quoted.
missing=""
for tool in python ruff pyright pytest pip pyinstaller; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        missing="${missing:+$missing, }$tool"
    fi
done
if [ -n "$missing" ]; then
    rerun="./ci.sh"
    if [ "$#" -gt 0 ]; then
        rerun="$rerun $(printf '%q ' "$@")"
        rerun="${rerun% }"
    fi
    echo "ci.sh: not on PATH: $missing. Fix: PATH=<checkout>/.venv/bin:\$PATH $rerun" >&2
    exit 2
fi

# pytest's output is copied here (tee, so it still streams) to tell a
# collection error from an interruption; removed however the script ends.
pytest_log=""
trap 'rm -f "$pytest_log"' EXIT

# step KIND CMD...: run CMD; if it fails, name the step and exit with the
# contract's code for KIND (see the header):
#   env            any non-zero -> 2
#   ruff, pyright  1 -> 1; anything else -> 2
#   pytest         1 -> 1; 2 -> 1 when its output reports errors during
#                  collection, else 2; anything else -> 2
#   build          127 -> 2; anything else -> 1
step() {
    local kind="$1"
    shift
    local rc=0 mapped meaning
    if [ "$kind" = pytest ]; then
        pytest_log="$(mktemp)"
        "$@" | tee "$pytest_log"
        rc="${PIPESTATUS[0]}"
    else
        "$@" || rc=$?
    fi
    [ "$rc" -eq 0 ] && return 0
    case "$kind" in
        env) mapped=2 ;;
        ruff | pyright) if [ "$rc" -eq 1 ]; then mapped=1; else mapped=2; fi ;;
        pytest)
            if [ "$rc" -eq 1 ]; then
                mapped=1
            elif [ "$rc" -eq 2 ] && grep -Eq 'errors? during collection' "$pytest_log"; then
                mapped=1
            else
                mapped=2
            fi
            ;;
        build) if [ "$rc" -eq 127 ]; then mapped=2; else mapped=1; fi ;;
    esac
    if [ "$mapped" -eq 1 ]; then
        meaning="a check failed: fix the change"
    else
        meaning="could not run: fix the environment"
    fi
    echo "ci.sh: step $kind ($*) exited $rc; ci.sh exits $mapped ($meaning)" >&2
    exit "$mapped"
}

echo "=== Environment ==="
# Refuses a stale or drifted venv, so a local pass means GitHub (same
# constraints, same Python) passes (Trac #262).
step env python scripts/check_env.py

echo ""
echo "=== Lint ==="
step ruff ruff check src/ tests/ scripts/ plugins/
step ruff ruff format --check src/ tests/ scripts/ plugins/

echo ""
echo "=== Type check ==="
# src/ plus scripts/check_env.py, zero errors (Trac #236, #279): pyright
# exits non-zero on any error, so there is no baseline to keep. The other
# scripts/ files have errors of their own and are not checked yet.
# --pythonpath names the interpreter that has the dependencies (the venv
# first on PATH here, setup-python's in Actions). pyrightconfig.json does
# set venvPath/venv, to ".venv" in the checkout, but that is only the
# fallback for a bare `pyright` run: --pythonpath overrides it, so a
# checkout without a .venv (a worktree, GitHub's runner) works, and pyright
# only prints "venv .venv subdirectory not found". The ignore-warnings
# variable stops the pip wrapper's newer-version check calling PyPI, which
# this script must not need.
step pyright env PYRIGHT_PYTHON_IGNORE_WARNINGS=1 pyright --pythonpath "$(command -v python)" src/ scripts/check_env.py

echo ""
echo "=== Unit Tests (no credentials, like GitHub Actions) ==="
# Two channels, both closed. conftest.py disables .env loading process-wide
# without --run-live (bootstrap_config() reads .env itself, so skipping only
# conftest's own load leaves the live tests fed); the scrub here covers the
# other one, an operator who exported TRAC_* in their shell. An offline test
# that needs credentials must fail here, where it is cheap, not on Actions
# two merges later (#85). PYTHON_DOTENV_DISABLED is belt and braces, and
# states the invariant where a reader of this script can see it.
step pytest env -u TRAC_URL -u TRAC_USERNAME -u TRAC_PASSWORD -u TRAC_INSECURE \
    PYTHON_DOTENV_DISABLED=1 pytest tests/

echo ""
echo "=== Build Verification ==="
step build bash build.sh

echo ""
echo "=== All checks passed ==="
