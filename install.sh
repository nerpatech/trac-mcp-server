#!/bin/bash
# Install trac-mcp-server and trac-convert binaries to ~/.local/bin

set -e

BINARIES=(trac-mcp-server trac-convert)
BIN_DIR="${HOME}/.local/bin"

mkdir -p "$BIN_DIR"

for name in "${BINARIES[@]}"; do
    binary="dist/$name"
    dest="$BIN_DIR/$name"

    if [ ! -f "$binary" ]; then
        echo "ERROR: Binary not found at $binary"
        echo "Run ./build.sh first."
        exit 1
    fi

    echo "Installing $name to $dest..."
    cp "$binary" "$dest"
    chmod +x "$dest"

    echo "Verifying installation..."
    "$dest" --version
    echo ""
done

echo "Installation complete: ${BINARIES[*]} in $BIN_DIR"
echo ""
echo "Make sure ~/.local/bin is in your PATH:"
echo '  export PATH="$HOME/.local/bin:$PATH"'
