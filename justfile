# Atlas Keyboard — Build System

default:
    @just --list

# ── Firmware (ZMK BLE) ─────────────────────────────────────────────

# Paths
workspace := absolute_path('firmware')
config := workspace / 'config'
fw_build := workspace / '.build'
out := workspace / 'out'
modules := workspace / 'kb_zmk_ps2_mouse_trackpoint_driver'
keymap_dir := absolute_path('firmware')

# Board name
board := "xiao_ble"

# Build both sides firmware (.uf2)
[group('firmware')]
build: build-left build-right

# Build left side
[group('firmware')]
build-left *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{ workspace }}
    west build -s zmk/app -d {{ fw_build }}/left -b {{ board }} {{ args }} -- \
        -DSHIELD=atlas_left \
        -DZMK_CONFIG={{ config }} \
        -DZMK_EXTRA_MODULES={{ modules }}
    mkdir -p {{ out }} && cp {{ fw_build }}/left/zephyr/zmk.uf2 {{ out }}/atlas_left.uf2
    echo "Built: {{ out }}/atlas_left.uf2"

# Build right side
[group('firmware')]
build-right *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{ workspace }}
    west build -s zmk/app -d {{ fw_build }}/right -b {{ board }} {{ args }} -- \
        -DSHIELD=atlas_right \
        -DZMK_CONFIG={{ config }} \
        -DZMK_EXTRA_MODULES={{ modules }}
    mkdir -p {{ out }} && cp {{ fw_build }}/right/zephyr/zmk.uf2 {{ out }}/atlas_right.uf2
    echo "Built: {{ out }}/atlas_right.uf2"

# Clean firmware build artifacts
[group('firmware')]
clean:
    rm -rf {{ fw_build }} {{ out }}

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
    python -m keymap_drawer -c {{ keymap_dir }}/keymap-drawer.yaml parse -z {{ config }}/atlas.keymap -c 10 -o {{ keymap_dir }}/keymap.yaml
    python -m keymap_drawer -c {{ keymap_dir }}/keymap-drawer.yaml draw {{ keymap_dir }}/keymap.yaml \
        -n "33333+2 2+33333" \
        -o {{ keymap_dir }}/keymap.svg
    echo "Generated: {{ keymap_dir }}/keymap.svg"

# ── PCB generation ───────────────────────────────────────────────

layout := "tools/keyboard.yaml"
kle_json := "tools/build/layout.json"
pcb_out := "tools/build/atlas.kicad_pcb"

# YAML → tools/build/atlas.kicad_pcb
[group('pcb')]
pcb:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p tools/build

    # Generate KLE JSON
    pcb-python tools/layout2kle.py -i {{ layout }} -o {{ kle_json }}
    echo "✓ Generated KLE layout"

    # Build PCB: kbplacer (switches + diodes) → enhancements → save
    # pcb_build.py symlinks 3dmodels/ and footprints/ into build/ for KiCad
    pcb-python tools/pcb_build.py \
        -l {{ layout }} -o {{ pcb_out }} --kle-json {{ kle_json }}
    echo "✓ {{ pcb_out }}"

# Export tools/build/atlas.step (PCB + trackpoint modules) + open viewer
[group('pcb')]
pcb-step:
    #!/usr/bin/env bash
    set -euo pipefail

    # Export bare PCB STEP
    # kicad-cli returns 2 on warnings (missing 3D models) — treat as success
    kicad-cli pcb export step --subst-models -f \
        -o tools/build/pcb_bare.step \
        {{ pcb_out }} \
        || [ $? -eq 2 ]
    echo "✓ Bare PCB STEP exported"

    # Assemble: bare PCB + trackpoint modules → atlas.step
    cq-python tools/step_build.py \
        -l {{ layout }} \
        --pcb-step tools/build/pcb_bare.step \
        -o tools/build/atlas.step
    echo "✓ tools/build/atlas.step"

    f3d --light-intensity=2 -q tools/build/atlas.step &
