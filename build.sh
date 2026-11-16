#!/bin/bash
# Build standalone binaries for trac-mcp-server and trac-convert using PyInstaller

set -e

echo "==================================================================="
echo "  Building standalone binaries: trac-mcp-server + trac-convert"
echo "==================================================================="
echo ""

# --- Pre-flight checks --------------------------------------------------------

if ! python -c "import PyInstaller" 2>/dev/null; then
    echo "PyInstaller not found. Installing dev dependencies..."
    pip install -e ".[dev]"
fi

# --- Clean previous builds ----------------------------------------------------

echo "Cleaning previous build artifacts..."
rm -rf build/ dist/ *.spec

# --- Hidden imports -----------------------------------------------------------

HIDDEN_IMPORTS=(
    # Root package
    trac_mcp_server
    trac_mcp_server.config
    trac_mcp_server.config_loader
    trac_mcp_server.config_schema
    trac_mcp_server.file_handler
    trac_mcp_server.logger
    trac_mcp_server.validators
    trac_mcp_server.version

    # core/ subpackage
    trac_mcp_server.core
    trac_mcp_server.core.client
    trac_mcp_server.core.async_utils

    # converters/ subpackage
    trac_mcp_server.converters
    trac_mcp_server.converters.common
    trac_mcp_server.converters.markdown_to_tracwiki
    trac_mcp_server.converters.html_to_markdown

    # detection/ subpackage
    trac_mcp_server.detection
    trac_mcp_server.detection.capabilities
    trac_mcp_server.detection.processor_utils
    trac_mcp_server.detection.web_scraper

    # mcp/ subpackage
    trac_mcp_server.mcp
    trac_mcp_server.mcp.server
    trac_mcp_server.mcp.http_app
    trac_mcp_server.mcp.lifespan
    trac_mcp_server.mcp.oidc
    trac_mcp_server.mcp.tools
    trac_mcp_server.mcp.tools.errors
    trac_mcp_server.mcp.tools.ticket_read
    trac_mcp_server.mcp.tools.ticket_write
    trac_mcp_server.mcp.tools.wiki_read
    trac_mcp_server.mcp.tools.wiki_write
    trac_mcp_server.mcp.tools.wiki_file
    trac_mcp_server.mcp.tools.milestone
    trac_mcp_server.mcp.tools.system
    trac_mcp_server.mcp.resources
    trac_mcp_server.mcp.resources.wiki

    # Third-party libraries
    xmlrpc.client
    mistune
    lxml
    cssselect
    # html_to_markdown: markdownify parses through bs4's lxml builder.
    # bs4 registers its builders in try/except imports; naming them
    # keeps the bundle from depending on the analysis following those.
    markdownify
    bs4
    bs4.builder._lxml
    soupsieve
    six
    urllib3
    charset_normalizer
    mcp
    mcp.server
    mcp.server.stdio
    mcp.server.models
    mcp.server.streamable_http
    mcp.server.streamable_http_manager
    mcp.server.transport_security
    mcp.types
    pydantic
    pydantic_core
    yaml
    anyio
    dotenv
    merge3
    starlette
    sse_starlette
    uvicorn
    uvicorn.loops.auto
    uvicorn.protocols.http.auto
    uvicorn.protocols.http.h11_impl
    uvicorn.protocols.websockets.auto
    uvicorn.lifespan.on
)

# Extra hidden imports for trac-convert (clipboard support)
CONVERT_EXTRA_IMPORTS=(
    pyperclip
)

# Build hidden-import flags
SERVER_IMPORT_FLAGS=""
for mod in "${HIDDEN_IMPORTS[@]}"; do
    SERVER_IMPORT_FLAGS="$SERVER_IMPORT_FLAGS --hidden-import $mod"
done

CONVERT_IMPORT_FLAGS=""
for mod in "${HIDDEN_IMPORTS[@]}"; do
    CONVERT_IMPORT_FLAGS="$CONVERT_IMPORT_FLAGS --hidden-import $mod"
done
for mod in "${CONVERT_EXTRA_IMPORTS[@]}"; do
    CONVERT_IMPORT_FLAGS="$CONVERT_IMPORT_FLAGS --hidden-import $mod"
done

# --- Release stamp ------------------------------------------------------------
# A PyInstaller binary never runs setup.py's build step, so it has no
# trac_mcp_server/_release.py. Bundle the sha as a data file instead;
# version.release_sha() reads it from sys._MEIPASS (Trac /trac_mcp_server
# #230). The sha comes from setup.py's own function so the binary and the
# wheel agree. Nothing is written into src/.

RELEASE_SHA=$(python -c 'import sys; from pathlib import Path; sys.path.insert(0, "."); import setup; print(setup.release_sha(Path(".").resolve()) or "")')

STAMP_DIR=$(mktemp -d)
trap 'rm -rf "$STAMP_DIR"' EXIT

STAMP_FLAGS=()
if [ -n "$RELEASE_SHA" ]; then
    printf '%s\n' "$RELEASE_SHA" > "$STAMP_DIR/trac_mcp_server_RELEASE_SHA"
    STAMP_FLAGS=(--add-data "$STAMP_DIR/trac_mcp_server_RELEASE_SHA:.")
    echo "Release stamp: $RELEASE_SHA"
else
    echo "No release stamp (not a git checkout with a commit): the binaries will report \"commit unknown\"."
fi

# --- Build: trac-mcp-server ---------------------------------------------------

echo "Running PyInstaller for trac-mcp-server..."
pyinstaller \
    --onefile \
    --console \
    --name trac-mcp-server \
    --paths src \
    "${STAMP_FLAGS[@]}" \
    $SERVER_IMPORT_FLAGS \
    --exclude-module logfire \
    --clean \
    src/trac_mcp_server/mcp/__main__.py

# --- Build: trac-convert ------------------------------------------------------

echo "Running PyInstaller for trac-convert..."
pyinstaller \
    --onefile \
    --console \
    --name trac-convert \
    --paths src \
    "${STAMP_FLAGS[@]}" \
    $CONVERT_IMPORT_FLAGS \
    --exclude-module logfire \
    --clean \
    src/trac_mcp_server/cli/__main__.py

# --- Verify -------------------------------------------------------------------

verify_binary() {
    local name=$1
    local path
    if [ -f "dist/$name" ]; then
        path="dist/$name"
    elif [ -f "dist/$name.exe" ]; then
        path="dist/$name.exe"
    else
        echo "ERROR: dist/$name (or .exe) not found after build!"
        return 1
    fi
    echo ""
    echo "  $name → $path ($(du -h $path | cut -f1))"
    local smoke
    smoke=$(./$path --version)
    echo "  Smoke: $smoke"
    # A stamp was bundled, so the binary must name that commit: a binary
    # that says "commit unknown", or another commit, lost or mangled it.
    # Fail the build rather than ship an unattributable binary. Windows
    # (.exe) builds are not exercised (CI is Linux-only), so there a
    # stamp that did not land is a warning.
    if [ -n "$RELEASE_SHA" ] && [[ "$smoke" != *"+g${RELEASE_SHA:0:12}"* ]]; then
        if [[ "$path" == *.exe ]]; then
            echo "WARNING: $name does not report the stamped commit ${RELEASE_SHA:0:12} (Windows builds are not verified)"
            return 0
        fi
        echo "ERROR: $name does not report the stamped commit: expected +g${RELEASE_SHA:0:12} (from $RELEASE_SHA)"
        return 1
    fi
}

verify_binary trac-mcp-server
verify_binary trac-convert

echo ""
echo "==================================================================="
echo "  Build successful!"
echo "==================================================================="
echo ""
echo "Build complete. Binaries in dist/."
