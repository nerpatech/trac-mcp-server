#!/bin/bash
# Local CI — mirrors GitHub Actions checks: the same constraints
# (constraints/deploy.txt) and Python (.python-version), verified first
# by the Environment step. Run before pushing to catch issues locally
#
# This script is hermetic on purpose: no credentials, no daemon, no network,
# so it can only fail for reasons belonging to the change under test. The
# live suite is therefore NOT run here — use ci-live.sh for that half
# (ticket #81).
set -e

echo "=== Environment ==="
# Refuses a stale or drifted venv, so a local pass means GitHub (same
# constraints, same Python) passes (Trac #262).
python scripts/check_env.py

echo ""
echo "=== Lint ==="
ruff check src/ tests/ scripts/ plugins/
ruff format --check src/ tests/ scripts/ plugins/

echo ""
echo "=== Type check ==="
# src/ only, zero errors (Trac #236): pyright exits non-zero on any error,
# so there is no script to keep. --pythonpath names the interpreter that
# has the dependencies (the venv first on PATH here, setup-python's in
# Actions); pyrightconfig.json hard-codes no venv. The ignore-warnings
# variable stops the pip wrapper's newer-version check calling PyPI, which
# this script must not need.
PYRIGHT_PYTHON_IGNORE_WARNINGS=1 pyright --pythonpath "$(command -v python)" src/

echo ""
echo "=== Unit Tests (no credentials, like GitHub Actions) ==="
# Two channels, both closed. conftest.py disables .env loading process-wide
# without --run-live (bootstrap_config() reads .env itself, so skipping only
# conftest's own load leaves the live tests fed); the scrub here covers the
# other one, an operator who exported TRAC_* in their shell. An offline test
# that needs credentials must fail here, where it is cheap, not on Actions
# two merges later (#85). PYTHON_DOTENV_DISABLED is belt and braces, and
# states the invariant where a reader of this script can see it.
env -u TRAC_URL -u TRAC_USERNAME -u TRAC_PASSWORD -u TRAC_INSECURE \
    PYTHON_DOTENV_DISABLED=1 pytest tests/

echo ""
echo "=== Build Verification ==="
bash build.sh

echo ""
echo "=== All checks passed ==="
