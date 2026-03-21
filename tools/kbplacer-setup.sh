#!/usr/bin/env bash
# Bootstrap a kbplacer virtualenv using KiCad's own Python + pcbnew bindings.
# Run inside `nix develop` (needs KiCad 9, unzip, and curl on PATH).
set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$TOOLS_DIR/build"
VENV_DIR="$BUILD_DIR/.kbplacer-venv"
KISWITCH_DIR="$BUILD_DIR/.kiswitch"
KISWITCH_URL="https://github.com/perigoso/keyswitch-kicad-library/releases/download/v2.3/keyswitch-kicad-library.zip"

# --- Derive paths from KiCad's pcbnew wrapper ---
PCBNEW_BIN="$(which pcbnew 2>/dev/null)" || { echo "Error: pcbnew not on PATH (run inside nix develop)" >&2; exit 1; }

# Source KiCad's env vars (PYTHONPATH with pcbnew + wxPython + numpy etc.)
eval "$(grep '^export ' "$PCBNEW_BIN" | head -30)"

# Find KiCad's Python interpreter from _pcbnew.so's linked libpython
PCBNEW_SITE="$(echo "$PYTHONPATH" | tr ':' '\n' | head -1)"
PCBNEW_SO="$PCBNEW_SITE/_pcbnew.so"
PYTHON_ENV="$(ldd "$PCBNEW_SO" 2>/dev/null | grep -oP '/nix/store/\S+python3[^/]+-env' | head -1)"
KICAD_PYTHON="$PYTHON_ENV/bin/python3"

if [ ! -x "$KICAD_PYTHON" ]; then
    echo "Error: could not find KiCad's Python interpreter" >&2
    exit 1
fi

echo "KiCad Python: $KICAD_PYTHON"
echo "pcbnew path:  $PCBNEW_SITE"

# --- Create venv ---
mkdir -p "$BUILD_DIR"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtualenv..."
    "$KICAD_PYTHON" -m venv --system-site-packages "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet kbplacer pyyaml
    echo "Installed kbplacer"
else
    echo "Virtualenv exists: $VENV_DIR"
fi

# --- Download kiswitch footprint library ---
if [ ! -d "$KISWITCH_DIR/footprints" ]; then
    echo "Downloading kiswitch footprint library..."
    TMP="$(mktemp)"
    curl -sL "$KISWITCH_URL" -o "$TMP"
    mkdir -p "$KISWITCH_DIR"
    unzip -q "$TMP" -d "$KISWITCH_DIR"
    rm "$TMP"
    echo "Installed kiswitch footprints"
else
    echo "kiswitch exists: $KISWITCH_DIR"
fi

echo ""
echo "Ready. kbplacer venv: $VENV_DIR"
