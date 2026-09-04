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

set -e

# Run from the repo root whatever the caller's cwd, so the .env probe and
# pytest both see the same checkout.
cd "$(dirname "$0")"

echo "=== Live credentials ==="

# Resolve credentials exactly the way the tests resolve them -- .env first
# (conftest.py loads it under --run-live), then the environment. Testing
# shell variables alone would tell a developer with a working .env that they
# have no credentials.
# A non-zero exit here aborts the script under `set -e`, before pytest.
python - <<'PY'
import os
import sys

from dotenv import load_dotenv

# Explicit path: find_dotenv() walks the CALLING FILE's directory, and this
# script has no file -- it arrives on stdin.
load_dotenv(".env")

REQUIRED = ("TRAC_URL", "TRAC_USERNAME", "TRAC_PASSWORD")
missing = [name for name in REQUIRED if not os.environ.get(name)]

if missing:
    print("")
    print("ERROR: live Trac credentials are not available.", file=sys.stderr)
    print("Missing: " + ", ".join(missing), file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "Set them in .env (see .env.example) or export them. The live suite "
        "was NOT run -- this is not a passing result.",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"TRAC_URL={os.environ['TRAC_URL']} as {os.environ['TRAC_USERNAME']}")
PY

echo ""
echo "=== Full Suite (offline + live) ==="
pytest tests/ -q --run-live

echo ""
echo "=== Live checks passed ==="
