# Atlas Keyboard — Build System

default:
    @just --list

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
all: left right

# Clean build artifacts
clean:
    rm -rf {{ build }} {{ out }}

# Initialize west workspace (run once after clone)
init:
    cd {{ workspace }} && west init -l config && west update && west zephyr-export

# Update west modules
update:
    cd {{ workspace }} && west update

# Flash left side (put in bootloader mode first)
flash-left:
    @echo "Put left half in bootloader mode (double-tap reset)..."
    @echo "Then copy {{ out }}/atlas_left.uf2 to the mounted drive"

# Flash right side (put in bootloader mode first)
flash-right:
    @echo "Put right half in bootloader mode (double-tap reset)..."
    @echo "Then copy {{ out }}/atlas_right.uf2 to the mounted drive"

# Generate keymap visualization
keymap:
    #!/usr/bin/env bash
    set -euo pipefail
    python -m keymap_drawer -c {{ nix }}/keymap-drawer.yaml parse -z {{ config }}/atlas.keymap -c 10 -o {{ nix }}/keymap.yaml
    python -m keymap_drawer -c {{ nix }}/keymap-drawer.yaml draw {{ nix }}/keymap.yaml \
        -n "33333+2 2+33333" \
        -o {{ nix }}/keymap.svg
    echo "Generated: {{ nix }}/keymap.svg"

# Rebuild left (clean first)
rebuild-left:
    rm -rf {{ build }}/left
    just left

# Rebuild right (clean first)
rebuild-right:
    rm -rf {{ build }}/right
    just right

# Rebuild both (clean first)
rebuild: clean all

# ── PCB generation ──────────────────────────────────────────────────

pcb_out := "output/keyboard"

# Generate KLE-NG JSON from layout.yaml (paste into editor.keyboard-tools.xyz)
kle:
    @mkdir -p output
    python3 tools/layout2kle.py -i layout.yaml -o output/layout.json
    @echo "→ output/layout.json — paste into editor.keyboard-tools.xyz"

# Print KLE JSON to stdout (for piping / clipboard)
kle-stdout:
    python3 tools/layout2kle.py -i layout.yaml

# Copy KLE JSON to clipboard (xclip)
kle-clip:
    python3 tools/layout2kle.py -i layout.yaml | xclip -selection clipboard
    @echo "→ KLE JSON copied to clipboard"

# Patch kbplacer-generated PCB with 3D models, trackpoint holes, controller, edge cuts
pcb-enhance:
    python3 tools/pcb_enhance.py -i {{ pcb_out }}/keyboard.kicad_pcb
    @echo "→ PCB patched"

# Full PCB flow: generate KLE, remind to use editor, then enhance
pcb:
    just kle
    @echo ""
    @echo "Next steps:"
    @echo "  1. Copy output/layout.json contents"
    @echo "  2. Paste into editor.keyboard-tools.xyz"
    @echo "  3. Configure: Choc V1 hotswap, SOD-123F diode at (-6,-4) 90°"
    @echo "  4. Download .kicad_pcb to {{ pcb_out }}/keyboard.kicad_pcb"
    @echo "  5. Run: just pcb-enhance"
