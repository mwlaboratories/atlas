# Atlas Keyboard — Build System

default:
    @just --list

# ── Firmware (ZMK BLE) ─────────────────────────────────────────────

# Paths
workspace := absolute_path('firmware')
config := workspace / 'config'
build := workspace / '.build'
out := workspace / 'out'
modules := workspace / 'kb_zmk_ps2_mouse_trackpoint_driver'
nix := absolute_path('nix')

# Board name
board := "xiao_ble"

# Build left side firmware
[group('firmware')]
left *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{ workspace }}
    west build -s zmk/app -d {{ build }}/left -b {{ board }} {{ args }} -- \
        -DSHIELD=atlas_left \
        -DZMK_CONFIG={{ config }} \
        -DZMK_EXTRA_MODULES={{ modules }}
    mkdir -p {{ out }} && cp {{ build }}/left/zephyr/zmk.uf2 {{ out }}/atlas_left.uf2
    echo "Built: {{ out }}/atlas_left.uf2"

# Build right side firmware
[group('firmware')]
right *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{ workspace }}
    west build -s zmk/app -d {{ build }}/right -b {{ board }} {{ args }} -- \
        -DSHIELD=atlas_right \
        -DZMK_CONFIG={{ config }} \
        -DZMK_EXTRA_MODULES={{ modules }}
    mkdir -p {{ out }} && cp {{ build }}/right/zephyr/zmk.uf2 {{ out }}/atlas_right.uf2
    echo "Built: {{ out }}/atlas_right.uf2"

# Build both sides
[group('firmware')]
all: left right

# Clean firmware build artifacts
[group('firmware')]
clean:
    rm -rf {{ build }} {{ out }}

# Initialize west workspace (run once after clone)
[group('firmware')]
init:
    cd {{ workspace }} && west init -l config && west update && west zephyr-export

# Update west modules
[group('firmware')]
update:
    cd {{ workspace }} && west update

# Flash left side (put in bootloader mode first)
[group('firmware')]
flash-left:
    @echo "Put left half in bootloader mode (double-tap reset)..."
    @echo "Then copy {{ out }}/atlas_left.uf2 to the mounted drive"

# Flash right side (put in bootloader mode first)
[group('firmware')]
flash-right:
    @echo "Put right half in bootloader mode (double-tap reset)..."
    @echo "Then copy {{ out }}/atlas_right.uf2 to the mounted drive"

# Generate keymap visualization
[group('firmware')]
keymap:
    #!/usr/bin/env bash
    set -euo pipefail
    python -m keymap_drawer -c {{ nix }}/keymap-drawer.yaml parse -z {{ config }}/atlas.keymap -c 10 -o {{ nix }}/keymap.yaml
    python -m keymap_drawer -c {{ nix }}/keymap-drawer.yaml draw {{ nix }}/keymap.yaml \
        -n "33333+2 2+33333" \
        -o {{ nix }}/keymap.svg
    echo "Generated: {{ nix }}/keymap.svg"

# Rebuild left (clean first)
[group('firmware')]
rebuild-left:
    rm -rf {{ build }}/left
    just left

# Rebuild right (clean first)
[group('firmware')]
rebuild-right:
    rm -rf {{ build }}/right
    just right

# Rebuild both (clean first)
[group('firmware')]
rebuild: clean all

# ── PCB generation (see tools/readme.org) ──────────────────────────

layout := "tools/keyboard.yaml"
kle_json := "tools/build/layout.json"
pcb_out := "tools/build/keyboard.kicad_pcb"

# Generate KLE JSON from tools/keyboard.yaml
[group('pcb')]
kle:
    python3 tools/layout2kle.py -i {{ layout }} -o {{ kle_json }}
    @echo "→ {{ kle_json }}"

# Print KLE JSON to stdout
[group('pcb')]
kle-stdout:
    python3 tools/layout2kle.py -i {{ layout }}

# Copy KLE JSON to clipboard (xclip)
[group('pcb')]
kle-clip:
    python3 tools/layout2kle.py -i {{ layout }} | wl-copy
    @echo "→ KLE JSON copied to clipboard"

# Unzip latest .zip in build/ + patch PCB
[group('pcb')]
pcb-enhance:
    #!/usr/bin/env bash
    set -euo pipefail
    zip=$(ls -t tools/build/*.zip 2>/dev/null | head -1)
    if [ -z "$zip" ]; then
        echo "Error: no .zip found in tools/build/" >&2
        exit 1
    fi
    echo "Extracting: $zip"
    unzip -o -j "$zip" "*.kicad_pcb" -d tools/build/
    # rename to keyboard.kicad_pcb
    for f in tools/build/*.kicad_pcb; do
        if [ "$f" != "{{ pcb_out }}" ]; then
            mv "$f" {{ pcb_out }}
        fi
    done
    python3 tools/pcb_enhance.py -i {{ pcb_out }} -l {{ layout }}
    echo "→ {{ pcb_out }}"

# Calculate optimal thumb angle_step to match ortho grid gap
[group('pcb')]
thumb-calc:
    python3 tools/thumb_calc.py -i {{ layout }}

# Full PCB flow: generate KLE + show next steps
[group('pcb')]
pcb:
    just kle
    @echo ""
    @echo "Next steps:"
    @echo "  1. Paste {{ kle_json }} into editor.keyboard-tools.xyz"
    @echo "  2. Configure: Choc V1 hotswap, SOD-123F diode at (-6,-4) 90°"
    @echo "  3. Download zip → tools/build/"
    @echo "  4. Run: just pcb-enhance"
