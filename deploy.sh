#!/bin/bash
# One-action deploy for the shared trac-http daemon AND the trac-convert CLI.
#
# Run this FROM the deploy clone (~/srv/trac-mcp-server-live), not from a
# working checkout -- it refuses to run anywhere deploy-constraints.txt is
# absent, which the working checkout deliberately does not carry.
#
# Pulls master, reinstalls trac-mcp-server into this clone's own (non-
# editable) venv, restarts both systemd --user units, health-checks them,
# then rebuilds trac-convert with PyInstaller from that SAME checkout state
# and installs it to ~/.local/bin. One script run, one commit, both
# binaries -- see ticket #84 (auto_pm instance) for why the daemon's
# converter and the human-facing CLI converter used to drift apart.
#
# What this script does NOT do, on purpose:
#   - Land a change on master (that's a normal PR merge, done beforehand).
#   - Confirm a SPECIFIC change actually landed in site-packages, or smoke-
#     test the particular tool/converter behaviour that changed -- those
#     are change-shaped and need a human (or the deploying session) to
#     know what to look for. Do that after this script exits 0.
#   - Reconnect any already-open MCP session -- restarting the units drops
#     existing connections; sessions with an old tool schema cached need a
#     fresh connection to see a changed tool name/params/description.

set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f deploy-constraints.txt ]; then
    echo "ERROR: deploy-constraints.txt not found in $(pwd)." >&2
    echo "This script runs from the deploy clone (~/srv/trac-mcp-server-live)," >&2
    echo "which carries deploy-constraints.txt; a working checkout does not." >&2
    exit 1
fi

BIN_DIR="${HOME}/.local/bin"
UNITS=(trac-mcp-server-http.service trac-mcp-server-http-autopm.service)
PORTS=(8080 8081)

echo "=== 1/5: pull master ==="
git fetch origin master
git checkout master
git pull origin master
COMMIT="$(git rev-parse --short HEAD)"
echo "Deploying commit: $COMMIT"

echo ""
echo "=== 2/5: reinstall trac-mcp-server (daemon) ==="
.venv/bin/pip install -q -c deploy-constraints.txt .

echo ""
echo "=== 3/5: restart daemon units ==="
systemctl --user restart "${UNITS[@]}"
sleep 1

echo ""
echo "=== 4/5: verify daemon health ==="
for i in "${!UNITS[@]}"; do
    unit="${UNITS[$i]}"
    port="${PORTS[$i]}"
    if ! systemctl --user is-active --quiet "$unit"; then
        echo "ERROR: $unit is not active after restart" >&2
        exit 1
    fi
    resp="$(curl -sf "http://127.0.0.1:${port}/healthz")"
    echo "$unit (port $port): $resp"
done

echo ""
echo "=== 5/5: rebuild + install trac-convert from $COMMIT ==="
# Pre-install pyinstaller via the pinned constraint BEFORE calling build.sh.
# build.sh's own preflight (`pip install -e ".[dev]"` when PyInstaller is
# missing) would otherwise flip this non-editable deploy clone into an
# editable install -- exactly the drift-on-a-stray-pull failure mode the
# deploy clone is designed to avoid. Pre-installing here means build.sh's
# check finds PyInstaller already present and never reaches that branch.
if ! .venv/bin/python -c "import PyInstaller" 2>/dev/null; then
    echo "Installing pyinstaller (pinned in deploy-constraints.txt)..."
    .venv/bin/pip install -q -c deploy-constraints.txt pyinstaller
fi
# Delegate to build.sh (not a re-implementation) so the hidden-import list
# and PyInstaller flags have exactly one source of truth. It builds both
# dist/trac-mcp-server and dist/trac-convert; only the latter is installed
# below -- the daemon runs from this clone's venv console script, not from
# a PyInstaller binary.
PATH="$(pwd)/.venv/bin:$PATH" ./build.sh

mkdir -p "$BIN_DIR"
cp dist/trac-convert "$BIN_DIR/trac-convert"
chmod +x "$BIN_DIR/trac-convert"
echo ""
echo "Installed: $("$BIN_DIR/trac-convert" --version) (built from commit $COMMIT)"

echo ""
echo "=== Deploy complete: commit $COMMIT ==="
echo "Still manual: confirm the specific change landed (grep site-packages/"
echo "trac_mcp_server/ for something it introduced) and smoke-test the tool"
echo "that changed through a connected session -- this script only proves"
echo "the mechanical steps succeeded, not that the change behaves."
