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
keymap_dir := absolute_path('keymapdrawer-nix')

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
    python -m keymap_drawer -c {{ keymap_dir }}/keymap-drawer.yaml parse -z {{ config }}/atlas.keymap -c 10 -o {{ keymap_dir }}/keymap.yaml
    python -m keymap_drawer -c {{ keymap_dir }}/keymap-drawer.yaml draw {{ keymap_dir }}/keymap.yaml \
        -n "33333+2 2+33333" \
        -o {{ keymap_dir }}/keymap.svg
    echo "Generated: {{ keymap_dir }}/keymap.svg"

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

# ── PCB generation ───────────────────────────────────────────────

layout := "tools/keyboard.yaml"
kle_json := "tools/build/layout.json"
pcb_out := "tools/build/keyboard.kicad_pcb"
kbplacer_venv := "tools/build/.kbplacer-venv"
kiswitch_dir := "tools/build/.kiswitch/footprints"

# Generate KLE JSON from tools/keyboard.yaml
[group('pcb')]
kle:
    python3 tools/layout2kle.py -i {{ layout }} -o {{ kle_json }}
    @echo "→ {{ kle_json }}"

# Print KLE JSON to stdout
[group('pcb')]
kle-stdout:
    python3 tools/layout2kle.py -i {{ layout }}

# Copy KLE JSON to clipboard
[group('pcb')]
kle-clip:
    python3 tools/layout2kle.py -i {{ layout }} | wl-copy
    @echo "→ KLE JSON copied to clipboard"

# Bootstrap kbplacer venv + kiswitch footprints (run once)
[group('pcb')]
pcb-setup:
    bash tools/kbplacer-setup.sh

# Patch PCB with 3D models, trackpoint holes, controllers, edge cuts
[group('pcb')]
pcb-enhance:
    python3 tools/pcb_enhance.py -i {{ pcb_out }} -l {{ layout }}
    @echo "→ {{ pcb_out }}"

# Open patched PCB in KiCad
[group('pcb')]
pcb-open:
    pcbnew {{ pcb_out }} &

# Export PCB + 3D models + footprints to a directory for manufacturing/STEP export
[group('pcb')]
pcb-export dir="tools/build/export":
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p {{ dir }}
    cp {{ pcb_out }} {{ dir }}/
    # Copy project files if they exist
    for ext in kicad_pro kicad_prl; do
        src="tools/build/keyboard.$ext"
        [ -f "$src" ] && cp "$src" {{ dir }}/
    done
    # Copy 3D models (not symlink, so it works standalone)
    rm -rf {{ dir }}/3dmodels
    cp -r tools/3dmodels {{ dir }}/3dmodels
    # Copy footprints
    rm -rf {{ dir }}/footprints
    cp -r tools/footprints {{ dir }}/footprints
    echo "→ Exported to {{ dir }}/"

# Export STEP 3D model from the exported PCB
[group('pcb')]
pcb-step dir="tools/build/export":
    kicad-cli pcb export step --subst-models -f -o {{ dir }}/keyboard.step {{ dir }}/keyboard.kicad_pcb
    @echo "→ {{ dir }}/keyboard.step"

# Full PCB flow: YAML → KLE → kbplacer → enhance → KiCad (fully automated)
[group('pcb')]
pcb:
    #!/usr/bin/env bash
    set -euo pipefail

    # Ensure kbplacer venv exists
    if [ ! -d "{{ kbplacer_venv }}" ]; then
        echo "First run — setting up kbplacer..."
        bash tools/kbplacer-setup.sh
    fi

    # Step 1: Generate KLE JSON
    python3 tools/layout2kle.py -i {{ layout }} -o {{ kle_json }}
    echo "✓ Generated KLE layout"

    # Clean previous kbplacer output (kbplacer refuses to overwrite)
    rm -f {{ pcb_out }} tools/build/keyboard.kicad_pro tools/build/keyboard.kicad_prl

    # Step 2: Run kbplacer locally (no browser needed)
    # Source KiCad's env vars (PYTHONPATH, KICAD9_FOOTPRINT_DIR) from pcbnew wrapper
    eval "$(grep '^export ' "$(which pcbnew)" | head -30)"

    # Read footprint settings from keyboard.yaml
    SWITCH_FP="$(python3 -c "import yaml; y=yaml.safe_load(open('{{ layout }}')); print(y['switch'].get('footprint', 'Switch_Keyboard_Hotswap_Kailh:SW_Hotswap_Kailh_Choc_V1'))")"
    SWITCH_LIB="${SWITCH_FP%%:*}"
    SWITCH_NAME="${SWITCH_FP##*:}"

    echo "Running kbplacer (${SWITCH_NAME})..."
    {{ kbplacer_venv }}/bin/python3 -m kbplacer \
        --pcb-file {{ pcb_out }} \
        --create-pcb-file \
        --switch-footprint "{{ kiswitch_dir }}/${SWITCH_LIB}.pretty:${SWITCH_NAME}_1.00u" \
        --diode-footprint "$KICAD9_FOOTPRINT_DIR/Diode_SMD.pretty:D_SOD-123F" \
        --layout {{ kle_json }} \
        --route-switches-with-diodes \
        --switch "SW{} 180 FRONT" \
        --diode "D{} CUSTOM 6.0 4.0 270 BACK" \
        --log-level WARNING
    echo "✓ kbplacer generated PCB"

    # Generate solder reference PCB for trackpoint keys (if needed)
    SOLDER_FP="$(python3 -c "import yaml; y=yaml.safe_load(open('{{ layout }}')); tp=y.get('trackpoint',{}); print(tp.get('solder_footprint','')) if tp.get('solder_keys') else print('')")"
    SOLDER_REF=""
    if [ -n "$SOLDER_FP" ]; then
        SOLDER_LIB="${SOLDER_FP%%:*}"
        SOLDER_NAME="${SOLDER_FP##*:}"
        SOLDER_REF="tools/build/.solder-ref.kicad_pcb"
        rm -f "$SOLDER_REF"
        echo "Generating solder reference (${SOLDER_NAME})..."
        {{ kbplacer_venv }}/bin/python3 -m kbplacer \
            --pcb-file "$SOLDER_REF" \
            --create-pcb-file \
            --switch-footprint "{{ kiswitch_dir }}/${SOLDER_LIB}.pretty:${SOLDER_NAME}_1.00u" \
            --diode-footprint "$KICAD9_FOOTPRINT_DIR/Diode_SMD.pretty:D_SOD-123F" \
            --layout {{ kle_json }} \
            --switch "SW{} 180 FRONT" \
            --diode "D{} CUSTOM 6.0 4.0 270 BACK" \
            --log-level WARNING
        echo "✓ Solder reference generated"
    fi

    # Step 3: Enhance (3D models, trackpoint holes, controllers, edge cuts, solder swap)
    SOLDER_ARG=""
    [ -n "$SOLDER_REF" ] && SOLDER_ARG="--solder-ref $SOLDER_REF"
    python3 tools/pcb_enhance.py -i {{ pcb_out }} -l {{ layout }} $SOLDER_ARG
    echo "✓ Enhanced PCB"

    # Step 4: Export + open in KiCad for manual XIAO flip
    just pcb-export

    EXPORT_DIR="tools/build/export"
    echo ""
    echo "┌─────────────────────────────────────────────────────┐"
    echo "│  Opening PCB in KiCad for manual edits:             │"
    echo "│    1. Select both XIAO BLE controllers (U_L, U_R)   │"
    echo "│    2. Press F to flip them to the back side          │"
    echo "│    3. Save (Ctrl+S)                                  │"
    echo "│    4. Close KiCad                                    │"
    echo "└─────────────────────────────────────────────────────┘"
    pcbnew "$EXPORT_DIR/keyboard.kicad_pcb"

    # Step 5: STEP export
    echo ""
    read -p "Export STEP model? [Y/n] " answer
    if [ "${answer:-y}" != "n" ]; then
        just pcb-step
    fi
