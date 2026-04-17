# Atlas Keyboard — Build System
#
# Five recipes total:
#   build-fw       — build firmware (.uf2) for both halves
#   build-keymap   — render keymap.svg from atlas.keymap
#   init-west      — one-time west workspace init (after clone)
#   gen-kicad      — YAML → tools/build/{atlas.kicad_pcb, atlas.kicad_sch, atlas.step}
#   open-step      — open tools/build/atlas.step in f3d (3D viewer)

default:
    @just --list

# ── Paths ─────────────────────────────────────────────────────────

workspace      := absolute_path('firmware')
config         := workspace / 'config'
fw_build       := workspace / '.build'
fw_out         := workspace / 'out'
fw_modules     := workspace / 'kb_zmk_ps2_mouse_trackpoint_driver'
keymap_dir     := absolute_path('firmware')
board          := "xiao_ble"

layout         := "tools/keyboard.yaml"
kle_json       := "tools/build/layout.json"
pcb_out        := "tools/build/atlas.kicad_pcb"
sch_out        := "tools/build/atlas.kicad_sch"
step_out       := "tools/build/atlas.step"
pcb_bare_step  := "tools/build/pcb_bare.step"

# ── Firmware ──────────────────────────────────────────────────────

# Build firmware for both halves (left + right .uf2)
build-fw *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{ workspace }}
    for half in left right; do
        west build -s zmk/app -d {{ fw_build }}/$half -b {{ board }} {{ args }} -- \
            -DSHIELD=atlas_$half \
            -DZMK_CONFIG={{ config }} \
            -DZMK_EXTRA_MODULES={{ fw_modules }}
        mkdir -p {{ fw_out }} && cp {{ fw_build }}/$half/zephyr/zmk.uf2 {{ fw_out }}/atlas_$half.uf2
        echo "Built: {{ fw_out }}/atlas_$half.uf2"
    done

# Render firmware/keymap.svg from atlas.keymap
build-keymap:
    #!/usr/bin/env bash
    set -euo pipefail
    python -m keymap_drawer -c {{ keymap_dir }}/keymap-drawer.yaml parse -z {{ config }}/atlas.keymap -c 10 -o {{ keymap_dir }}/keymap.yaml
    python -m keymap_drawer -c {{ keymap_dir }}/keymap-drawer.yaml draw {{ keymap_dir }}/keymap.yaml \
        -n "33333+2 2+33333" \
        -o {{ keymap_dir }}/keymap.svg
    echo "Generated: {{ keymap_dir }}/keymap.svg"

# Initialize west workspace (run once after clone)
init-west:
    cd {{ workspace }} && west init -l config && west update && west zephyr-export

# ── KiCad project generation ─────────────────────────────────────

# YAML → KiCad project (PCB + schematic + STEP) under tools/build/
gen-kicad:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p tools/build

    # Layout JSON for kbplacer
    pcb-python tools/layout2kle.py -i {{ layout }} -o {{ kle_json }}
    echo "✓ KLE layout"

    # PCB: footprints + nets
    pcb-python tools/pcb_build.py \
        -l {{ layout }} -o {{ pcb_out }} --kle-json {{ kle_json }}
    echo "✓ {{ pcb_out }}"

    # Schematic: symbols + labels (parallel to PCB; same source of truth)
    pcb-python tools/sch_build.py \
        -l {{ layout }} --pcb {{ pcb_out }} -o {{ sch_out }}
    echo "✓ {{ sch_out }}"

    # 3D STEP assembly (bare PCB STEP + sensor modules)
    # kicad-cli returns 2 on warnings (missing 3D models) — treat as success
    kicad-cli pcb export step --subst-models -f \
        -o {{ pcb_bare_step }} {{ pcb_out }} \
        || [ $? -eq 2 ]
    cq-python tools/step_build.py \
        -l {{ layout }} \
        --pcb-step {{ pcb_bare_step }} \
        -o {{ step_out }}
    echo "✓ {{ step_out }}"

# Open the 3D STEP assembly in f3d
open-step:
    f3d --light-intensity=2 -q {{ step_out }}

# ── PCB autorouting ──────────────────────────────────────────────

dsn_out       := "tools/build/atlas.dsn"
ses_out       := "tools/build/atlas.ses"

# Export PCB to Specctra DSN, autoroute with freerouting, import result
route-pcb:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Exporting DSN..."
    pcb-python tools/route_pcb.py --export-dsn \
        --pcb {{ pcb_out }} --dsn {{ dsn_out }}
    echo "✓ {{ dsn_out }}"

    echo "Running freerouting autorouter..."
    freerouting -de {{ dsn_out }} -do {{ ses_out }} -mp 20
    echo "✓ {{ ses_out }}"

    echo "Importing routed SES back into PCB..."
    pcb-python tools/route_pcb.py --import-ses \
        --pcb {{ pcb_out }} --ses {{ ses_out }}
    echo "✓ {{ pcb_out }} (routed)"
