#!/usr/bin/env python3
"""Atlas schematic generator — emits tools/build/atlas.kicad_sch.

Reads the placed footprints from tools/build/atlas.kicad_pcb and the net spec
from keyboard.yaml, then produces a schematic organised into functional blocks
with properly rotated global labels (matching the hand-drawn pcb/atlas style).

Run inside `nix develop` (provides pcb-python with kicad-sch-api).

Usage:
    pcb-python tools/sch_build.py
"""
import argparse
import re
import sys
import uuid
from pathlib import Path

import kicad_sch_api as ksa
import yaml

# Relax kicad-sch-api's strict reference-format validator so our descriptive
# refs like U_ADC_L / R_REF_L1 / TP_L_pad_x are accepted.
from kicad_sch_api.utils.validation import SchematicValidator as _KSAValidator
_KSAValidator.validate_reference = lambda self, ref: bool(ref)

ksa.use_grid_units(True)

TOOLS_DIR = Path(__file__).resolve().parent
BUILD_DIR = TOOLS_DIR / "build"
SYMBOLS_DIR = TOOLS_DIR / "kicad" / "symbols"

# Register project-local symbol libraries
_cache = ksa.get_symbol_cache()
for lib_file in SYMBOLS_DIR.glob("*.kicad_sym"):
    _cache.add_library_path(lib_file)

GRID_MM = 1.27


# ── Helpers ──────────────────────────────────────────────────────────────────

def read_pcb_footprints(pcb_path: Path) -> dict:
    """Return {ref: {footprint, value}} for every placed footprint in the PCB."""
    text = pcb_path.read_text()
    out = {}
    for chunk in re.split(r"(?=\(footprint\b)", text):
        if not chunk.startswith("(footprint"):
            continue
        fp_match = re.match(r'\(footprint\s+"([^"]*)"', chunk)
        ref_match = re.search(r'"Reference"\s+"([^"]+)"', chunk)
        val_match = re.search(r'"Value"\s+"([^"]+)"', chunk)
        if not (fp_match and ref_match):
            continue
        out[ref_match.group(1)] = {
            "footprint": fp_match.group(1),
            "value": val_match.group(1) if val_match else "",
        }
    return out


def read_pcb_pad_nets(pcb_path: Path) -> dict:
    """Return {ref: {pad_num: net_name}} from the PCB file.

    Splits each footprint into individual pad blocks to avoid cross-pad
    regex matches (the old greedy .*? with DOTALL would match pad N's
    number with pad N+1's net).
    """
    text = pcb_path.read_text()
    result = {}
    for chunk in re.split(r"(?=\(footprint\b)", text):
        if not chunk.startswith("(footprint"):
            continue
        ref_m = re.search(r'"Reference"\s+"([^"]+)"', chunk)
        if not ref_m:
            continue
        ref = ref_m.group(1)
        nets = {}
        # Split into individual pad blocks, then extract net within each
        for pad_block in re.split(r"(?=\(pad\s+\")", chunk):
            if not pad_block.startswith("(pad"):
                continue
            pad_m = re.match(r'\(pad\s+"([^"]+)"', pad_block)
            net_m = re.search(r'\(net\s+\d+\s+"([^"]*)"', pad_block)
            if pad_m and net_m and net_m.group(1):
                nets[pad_m.group(1)] = net_m.group(1)
        if nets:
            result[ref] = nets
    return result


def lookup_symbol(ref: str, layout: dict) -> str | None:
    """Resolve a footprint reference to a schematic symbol id, or None to skip."""
    if ref.startswith("TP_") and ("_stick" in ref or "_screw" in ref):
        return None
    if ref.startswith("U_ADC_"):
        return layout.get("trackpoint", {}).get("adc", {}).get(
            "symbol", "Analog_ADC:ADS1220xPW"
        )
    if ref.startswith("SW_PWR_"):
        return layout.get("power_switch", {}).get("symbol", "Switch:SW_SP3T")
    if ref.startswith("U_"):
        return layout.get("controller", {}).get(
            "symbol", "Connector_Generic:Conn_01x14"
        )
    if ref.startswith("R_"):
        return "Device:R"
    if ref.startswith("C_"):
        return "Device:C"
    if ref.startswith("D") and ref[1:].isdigit():
        return "Device:D"
    if ref.startswith("TP_") and "_pad_" in ref:
        return "Connector_Generic:Conn_01x01"
    if ref.startswith("SW") and ref[2:].isdigit():
        return layout.get("switch", {}).get("symbol", "Switch:SW_Push")
    return None


def materialize_nets(nets_spec: dict) -> dict:
    """Apply the L/R mirror rule: a net ending in _L auto-emits an _R counterpart."""
    materialized = {}
    for net_name, fp_map in nets_spec.items():
        materialized[net_name] = dict(fp_map)
        if not net_name.endswith("_L"):
            continue
        mirror_name = net_name[:-2] + "_R"
        if mirror_name in nets_spec:
            continue
        mirrored_map = {}
        for ref, pads in fp_map.items():
            new_ref = (
                ref[:-2] + "_R" if ref.endswith("_L") else ref.replace("_L", "_R")
            )
            mirrored_map[new_ref] = list(pads)
        materialized[mirror_name] = mirrored_map
    return materialized


def build_matrix_map(pad_nets: dict) -> dict:
    """Build {sw_num: (row_int, col_int)} from PCB pad-net data."""
    matrix = {}
    for ref, nets in pad_nets.items():
        if not (ref.startswith("SW") and ref[2:].isdigit()):
            continue
        sw_num = int(ref[2:])
        col_net = nets.get("1", "")
        col_m = re.match(r"COL(\d+)", col_net)
        if not col_m:
            continue
        col = int(col_m.group(1))
        d_ref = f"D{sw_num}"
        d_nets = pad_nets.get(d_ref, {})
        row_net = d_nets.get("1", "")
        row_m = re.match(r"ROW(\d+)", row_net)
        if not row_m:
            continue
        row = int(row_m.group(1))
        matrix[sw_num] = (row, col)
    return matrix


# ── Post-processing: inject global labels + no-connects into the .kicad_sch ─

def _make_global_label(net_name: str, x_mm: float, y_mm: float, rot: int) -> str:
    """Return a KiCad s-expression global_label block."""
    justify = "right" if rot == 180 else "left"
    uid = str(uuid.uuid4())
    return f"""\t(global_label "{net_name}"
\t\t(shape input)
\t\t(at {x_mm:.2f} {y_mm:.2f} {rot})
\t\t(fields_autoplaced yes)
\t\t(effects
\t\t\t(font
\t\t\t\t(size 1.27 1.27)
\t\t\t)
\t\t\t(justify {justify})
\t\t)
\t\t(uuid "{uid}")
\t\t(property "Intersheetrefs" "${{INTERSHEET_REFS}}"
\t\t\t(at 0 0 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t\t(hide yes)
\t\t\t)
\t\t)
\t)
"""


def _make_no_connect(x_mm: float, y_mm: float) -> str:
    uid = str(uuid.uuid4())
    return f"""\t(no_connect
\t\t(at {x_mm:.2f} {y_mm:.2f})
\t\t(uuid "{uid}")
\t)
"""


def pin_label_rotation(comp_x_grid, pin_x_mm, comp_y_grid, pin_y_mm, comp_rot=0):
    """Determine the global-label rotation so it points away from the component.

    Returns 0 (pointing right), 90 (pointing up), 180 (pointing left), 270 (pointing down).
    """
    cx_mm = comp_x_grid * GRID_MM
    cy_mm = comp_y_grid * GRID_MM
    dx = pin_x_mm - cx_mm
    dy = pin_y_mm - cy_mm
    # For vertical components or very small dx
    if abs(dx) < 0.5 and abs(dy) > 0.5:
        return 270 if dy > 0 else 90  # pin below → label points down; pin above → up
    if dx > 0.5:
        return 0    # pin to right → label points right
    if dx < -0.5:
        return 180  # pin to left → label points left
    return 0


# ── Section drawing helpers ──────────────────────────────────────────────────

FRAME_COLOR = (0, 0, 0, 0.6)
TITLE_SIZE = 2.5
SUBTITLE_SIZE = 1.8


def add_section_frame(sch, x, y, w, h, title: str = ""):
    """Draw a dashed rectangle frame with an optional title."""
    sch.add_rectangle(
        start=(x, y), end=(x + w, y + h),
        stroke_width=0.15, stroke_type="dash",
        stroke_color=FRAME_COLOR,
    )
    if title:
        sch.add_text(
            title, position=(x + 2, y + 3),
            size=TITLE_SIZE, bold=True, color=FRAME_COLOR,
        )


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-l", "--layout", type=Path, default=TOOLS_DIR / "keyboard.yaml")
    parser.add_argument("--pcb", type=Path, default=BUILD_DIR / "atlas.kicad_pcb")
    parser.add_argument("-o", "--output", type=Path, default=BUILD_DIR / "atlas.kicad_sch")
    args = parser.parse_args()

    if not args.pcb.exists():
        print(f"Error: {args.pcb} not found. Run 'just gen-kicad' first.", file=sys.stderr)
        sys.exit(1)

    layout = yaml.safe_load(args.layout.read_text())
    placed = read_pcb_footprints(args.pcb)
    pad_nets = read_pcb_pad_nets(args.pcb)
    matrix_map = build_matrix_map(pad_nets)

    sch = ksa.create_schematic("atlas")
    sch.set_paper_size("A2")
    sch.set_title_block(
        title="Atlas Split Keyboard",
        rev="auto-generated",
        comments={1: "XIAO nRF52840 Plus + ADS1220 strain-gauge trackpoint"},
    )

    placed_components: dict[str, tuple[str, tuple[int, int]]] = {}
    # Deferred global labels: [(net_name, x_mm, y_mm, rotation_deg)]
    pending_labels: list[tuple[str, float, float, int]] = []
    # Deferred no-connects: [(x_mm, y_mm)]
    pending_nc: list[tuple[float, float]] = []
    # Track which (ref, pad) pairs already have labels
    labeled_pins: set[tuple[str, str]] = set()

    def place(ref, sym_id, x, y, rot=0, value=None, pcb_ref=None):
        lookup = pcb_ref or ref
        info = placed.get(lookup, {"footprint": "", "value": ref})
        val = value if value is not None else (info["value"] or ref)
        try:
            sch.components.add(
                lib_id=sym_id,
                reference=ref,
                value=val,
                position=(x, y),
                footprint=info["footprint"],
                rotation=rot,
            )
            placed_components[ref] = (sym_id, (x, y))
        except Exception as e:
            print(f"  Warning: failed to add {ref} ({sym_id}): {e}", file=sys.stderr)

    def queue_label_at_pin(net_name: str, ref: str, pad: str):
        """Queue a global label at the pin position of ref.pad, with auto-rotation."""
        if ref not in placed_components or (ref, pad) in labeled_pins:
            return False
        try:
            pin_pos = sch.get_component_pin_position(ref, pad)
        except Exception:
            return False
        if pin_pos is None:
            return False
        _, (cx, cy) = placed_components[ref]
        rot = pin_label_rotation(cx, pin_pos.x, cy, pin_pos.y)
        pending_labels.append((net_name, pin_pos.x, pin_pos.y, rot))
        labeled_pins.add((ref, pad))
        return True

    # ── Section 1: Controllers ───────────────────────────────────────────────
    CTRL_Y = 31
    CTRL_L_X = 99
    CTRL_R_X = 315

    # "LEFT" / "RIGHT" headers
    sch.add_text("L E F T", position=(CTRL_L_X + 20, CTRL_Y - 15),
                 size=3.5, bold=True, color=FRAME_COLOR)
    sch.add_text("R I G H T", position=(CTRL_R_X + 20, CTRL_Y - 15),
                 size=3.5, bold=True, color=FRAME_COLOR)

    # Section title
    sch.add_text("M I C R O\nC O N T R O L L E R",
                 position=(CTRL_L_X + 45, CTRL_Y - 6),
                 size=TITLE_SIZE, bold=True, color=FRAME_COLOR)
    add_section_frame(sch, CTRL_L_X - 10, CTRL_Y - 10, 72, 68, "")
    place("U_L", lookup_symbol("U_L", layout), CTRL_L_X, CTRL_Y)

    sch.add_text("M I C R O\nC O N T R O L L E R",
                 position=(CTRL_R_X + 45, CTRL_Y - 6),
                 size=TITLE_SIZE, bold=True, color=FRAME_COLOR)
    add_section_frame(sch, CTRL_R_X - 10, CTRL_Y - 10, 72, 68, "")
    place("U_R", lookup_symbol("U_R", layout), CTRL_R_X, CTRL_Y)

    # ── Section 2: Power switch — split per half, TOTEM style ───────────────
    # Each half gets: power switch (SW_SP3T) with BAT+/VBAT labels,
    # plus a section title.
    pwr_num = 1
    for half, pwr_x, pwr_y in [
        ("L", CTRL_L_X - 30, CTRL_Y + 40),
        ("R", CTRL_R_X - 30, CTRL_Y + 40),
    ]:
        suffix = f"_{half}"

        # Section title
        sch.add_text(
            f"P O W E R\nS W I T C H",
            position=(pwr_x + 3, pwr_y + 15),
            size=TITLE_SIZE, bold=True, color=FRAME_COLOR,
        )

        # Power switch
        ref = f"SW_PWR_{half}"
        if ref in placed:
            place(ref, lookup_symbol(ref, layout), pwr_x + 5, pwr_y + 5)
            # Labels on switch pins — BAT+ on input, VBAT on output
            queue_label_at_pin(f"BAT+{suffix}", ref, "1")
            queue_label_at_pin(f"VBAT{suffix}", ref, "4")

        # Power flags (VCC/GND) near the controller, not in the power switch area
        for i, (power_net, power_lib_id) in enumerate([("VCC", "power:VCC"), ("GND", "power:GND")]):
            pf_ref = f"#PWR{pwr_num:02d}"
            pwr_num += 1
            # Place near the controller, above it
            pf_x = CTRL_L_X - 18 if half == "L" else CTRL_R_X - 18
            pf_y = CTRL_Y + 2 + i * 8
            try:
                sch.components.add(
                    lib_id=power_lib_id,
                    reference=pf_ref,
                    value=power_net,
                    position=(pf_x, pf_y),
                )
            except Exception as e:
                print(f"  Warning: power flag {pf_ref}: {e}", file=sys.stderr)

    # ── Controller matrix pin labels ────────────────────────────────────────
    # XIAO BLE Plus pin→matrix mapping (from reference_xiao_ble_plus_pinout):
    #   Pad 1-4 (D0-D3) → ROW0-ROW3
    #   Pad 5-6 (D4-D5) → COL0-COL1
    #   Pad 9-11 (D8-D10) → COL2-COL4 (primary SPI pins reused as matrix cols)
    CTRL_MATRIX_PINS = {
        "1": "ROW0", "2": "ROW1", "3": "ROW2", "4": "ROW3",
        "5": "COL0", "6": "COL1",
        "9": "COL4", "10": "COL3", "11": "COL2",
    }
    for ctrl_ref, suffix in [("U_L", "_L"), ("U_R", "_R")]:
        if ctrl_ref not in placed_components:
            continue
        for pad, net_base in CTRL_MATRIX_PINS.items():
            net_name = net_base + suffix
            queue_label_at_pin(net_name, ctrl_ref, pad)

    # ── Section 3: ADC Front-End ─────────────────────────────────────────────
    ADC_L_X = 93
    ADC_L_Y = 118
    ADC_R_X = 308
    ADC_R_Y = 118

    for half, adc_x, adc_y in [("L", ADC_L_X, ADC_L_Y), ("R", ADC_R_X, ADC_R_Y)]:
        add_section_frame(sch, adc_x - 15, adc_y - 8, 100, 70, "")
        sch.add_text(
            f"A D C  F R O N T - E N D",
            position=(adc_x + 20, adc_y - 4),
            size=TITLE_SIZE, bold=True, color=FRAME_COLOR,
        )

        adc_ref = f"U_ADC_{half}"
        adc_cx = adc_x + 25
        adc_cy = adc_y + 18
        if adc_ref in placed:
            place(adc_ref, lookup_symbol(adc_ref, layout), adc_cx, adc_cy)

        cap_refs = sorted(
            r for r in placed
            if r.startswith("C_") and re.search(rf"_{half}\d", r)
        )
        for ci, cref in enumerate(cap_refs):
            place(cref, "Device:C", adc_x + 8 + ci * 14, adc_y + 3)

        r_refs = sorted(
            r for r in placed
            if r.startswith("R_REF_") and re.search(rf"_{half}\d", r)
        )
        for ri, rref in enumerate(r_refs):
            place(rref, "Device:R", adc_x - 5, adc_y + 15 + ri * 22)

        tp_refs = sorted([r for r in placed if r.startswith(f"TP_{half}_pad_")])
        for ti, tref in enumerate(tp_refs):
            # Use short value (pad letter) to avoid double-naming overlap
            pad_letter = tref.split("_")[-1]  # 'a', 'b', 'x', 'y'
            place(tref, "Connector_Generic:Conn_01x01",
                  adc_x + 65, adc_y + 3 + ti * 12,
                  value=f"pad_{pad_letter}")

    # ── Section 4: Key Matrix ────────────────────────────────────────────────
    #
    # Cell anatomy (all Y offsets relative to row origin ry):
    #   ry + 0:   COL global label (rot 90, pointing up)
    #   ry + 0..4: wire from label down to SW pin 1
    #   ry + 4:   SW pin 1 (top)      ← SW centre at ry + SW_CY (= ry+8)
    #   ry + 12:  SW pin 2 (bottom)
    #   ry + 12..15: wire from SW pin 2 to D pin 2/A
    # TOTEM-style layout using SW_Push_45deg + D_Small.
    # SW_Push_45deg at (cx, ry+SW_CY): pin1 at (cx-2, ry+SW_CY-2), pin2 at (cx+2, ry+SW_CY+2)
    # D_Small at (cx+2, ry+SW_CY+4) rot=270: anode at (cx+2, ry+SW_CY+2) = SW.pin2!
    #   cathode at (cx+2, ry+SW_CY+6) = ROW wire position.
    # Column wire at x=cx-2 (through pin1), vertical.
    # ROW wire at y=ry+SW_CY+6 (at cathode), horizontal.
    # Column wire crosses ROW wire without connecting (different X).
    MATRIX_Y = 200
    COL_SPACING = 10    # TOTEM uses ~8 grid spacing between columns
    ROW_SPACING = 14    # TOTEM uses ~12 grid spacing between rows
    SW_CY = 6           # switch centre Y offset
    ROW_WIRE_DY = 12    # row wire Y = ry + SW_CY + 6 = ry + 12
    COL_LABEL_DY = -2   # col label above first switch
    SPLIT_COL = 5
    LEFT_X = 75
    RIGHT_X = 290

    n_rows = max(r for r, c in matrix_map.values()) + 1 if matrix_map else 4
    n_cols = max(c for r, c in matrix_map.values()) + 1 if matrix_map else 10

    for label, hx, col_start, col_end in [
        ("Left", LEFT_X, 0, SPLIT_COL),
        ("Right", RIGHT_X, SPLIT_COL, n_cols),
    ]:
        n_half_cols = col_end - col_start
        half_rows = set()
        for sw_num, (r, c) in matrix_map.items():
            if col_start <= c < col_end:
                half_rows.add(r)
        max_r = max(half_rows) if half_rows else 0
        add_section_frame(
            sch, hx - 8, MATRIX_Y - 8,
            n_half_cols * COL_SPACING + 14, (max_r + 1) * ROW_SPACING + 10, ""
        )
        sch.add_text(
            "S W I T C H\nM A T R I X",
            position=(hx + n_half_cols * COL_SPACING // 2 - 5, MATRIX_Y - 4),
            size=TITLE_SIZE, bold=True, color=FRAME_COLOR,
        )

    def col_x(col: int) -> int:
        if col < SPLIT_COL:
            return LEFT_X + col * COL_SPACING
        return RIGHT_X + (col - SPLIT_COL) * COL_SPACING

    cols_in_row_left: dict[int, list[int]] = {}
    cols_in_row_right: dict[int, list[int]] = {}
    for sw_num, (row, col) in matrix_map.items():
        if col < SPLIT_COL:
            cols_in_row_left.setdefault(row, []).append(col)
        else:
            cols_in_row_right.setdefault(row, []).append(col)

    # Build column occupancy: which rows exist per column
    rows_in_col: dict[int, list[int]] = {}
    for sw_num, (row, col) in matrix_map.items():
        rows_in_col.setdefault(col, []).append(row)

    # Build L/R renaming: SWL1-17 / DL1-17 for left, SWR1-17 / DR1-17 for right
    left_keys = sorted(
        [(sw, r, c) for sw, (r, c) in matrix_map.items() if c < SPLIT_COL],
        key=lambda x: (x[1], x[2])  # sort by row, then col
    )
    right_keys = sorted(
        [(sw, r, c) for sw, (r, c) in matrix_map.items() if c >= SPLIT_COL],
        key=lambda x: (x[1], x[2])
    )
    # Maps: old_sw_num → (new_sw_ref, new_d_ref)
    ref_map: dict[int, tuple[str, str]] = {}
    for i, (sw_num, r, c) in enumerate(left_keys, 1):
        ref_map[sw_num] = (f"SWL{i}", f"DL{i}")
    for i, (sw_num, r, c) in enumerate(right_keys, 1):
        ref_map[sw_num] = (f"SWR{i}", f"DR{i}")

    for sw_num, (row, col) in sorted(matrix_map.items()):
        sw_ref, d_ref = ref_map[sw_num]
        pcb_sw_ref = f"SW{sw_num}"
        pcb_d_ref = f"D{sw_num}"

        cx = col_x(col)
        ry = MATRIX_Y + row * ROW_SPACING

        # SW_Push_45deg: pin1 at (cx-2, ry+SW_CY-2), pin2 at (cx+2, ry+SW_CY+2)
        place(sw_ref, "Switch:SW_Push_45deg", cx, ry + SW_CY, rot=0,
              pcb_ref=pcb_sw_ref)

        # D_Small rot=90: cathode at top = SW.pin2, anode at bottom → ROW wire
        d_x = cx + 2
        d_cy = ry + SW_CY + 4
        place(d_ref, "Device:D_Small", d_x, d_cy, rot=90,
              pcb_ref=pcb_d_ref)

        # Wire from D.anode (bottom) down to ROW wire
        d_a_y = d_cy + 2
        row_wire_y = ry + ROW_WIRE_DY
        if d_a_y < row_wire_y:
            try:
                sch.add_wire(start=(d_x, d_a_y), end=(d_x, row_wire_y))
            except Exception:
                pass

        labeled_pins.add((sw_ref, "1"))
        labeled_pins.add((sw_ref, "2"))
        labeled_pins.add((d_ref, "2"))

        labeled_pins.add((sw_ref, "1"))
        labeled_pins.add((sw_ref, "2"))
        labeled_pins.add((d_ref, "2"))

    # Column wires: one continuous vertical wire per column at x=cx-2
    # (through SW pin 1). No endpoints at row wire crossings → no connection.
    for col in range(n_cols):
        if col not in rows_in_col:
            continue
        cx = col_x(col)
        col_wx = cx - 2  # pin 1 X for SW_Push_45deg
        rows_sorted = sorted(rows_in_col[col])

        # Wire from COL label above row 0 all the way past last row wire
        top_y = MATRIX_Y + rows_sorted[0] * ROW_SPACING + SW_CY - 2 + COL_LABEL_DY
        bot_y = MATRIX_Y + rows_sorted[-1] * ROW_SPACING + ROW_WIRE_DY + 2
        try:
            sch.add_wire(start=(col_wx, top_y), end=(col_wx, bot_y))
        except Exception:
            pass

        # Single COL label at top — rotation=270 (pointing down)
        half_suffix = "_L" if col < SPLIT_COL else "_R"
        col_in_half = col if col < SPLIT_COL else col - SPLIT_COL
        col_net = f"COL{col_in_half}{half_suffix}"
        pending_labels.append((col_net, col_wx * GRID_MM, top_y * GRID_MM, 270))

    # Row wires + ROW labels (per half)
    for half_label, cols_in_row_half, hx in [
        ("L", cols_in_row_left, LEFT_X),
        ("R", cols_in_row_right, RIGHT_X),
    ]:
        for row, cols_present in sorted(cols_in_row_half.items()):
            cols_sorted = sorted(cols_present)
            wire_y = MATRIX_Y + row * ROW_SPACING + ROW_WIRE_DY

            left_cx = col_x(cols_sorted[0])
            right_cx = col_x(cols_sorted[-1])
            # ROW wire from label to past rightmost cathode (at cx+2)
            try:
                sch.add_wire(start=(left_cx - 5, wire_y), end=(right_cx + 5, wire_y))
            except Exception:
                pass

            row_suffix = "_L" if half_label == "L" else "_R"
            row_net = f"ROW{row}{row_suffix}"
            pending_labels.append((row_net, (left_cx - 5) * GRID_MM, wire_y * GRID_MM, 180))

            for c in cols_sorted:
                sw_num = [s for s, (r, cc) in matrix_map.items() if r == row and cc == c][0]
                _, d_new_ref = ref_map[sw_num]
                labeled_pins.add((d_new_ref, "1"))

    # ── Net labels from YAML + PCB ───────────────────────────────────────────
    nets_spec = layout.get("nets") or {}
    materialized = materialize_nets(nets_spec)

    label_count = 0
    for net_name, fp_map in sorted(materialized.items()):
        for fp_ref, pads in fp_map.items():
            for pad in pads:
                if queue_label_at_pin(net_name, fp_ref, str(pad)):
                    label_count += 1
    print(f"  YAML net labels: {label_count} placed")

    # PCB-level net labels for remaining unlabeled pins (per-pad-block parsing)
    pcb_label_count = 0
    for fp_ref, pad_nets_map in pad_nets.items():
        if fp_ref not in placed_components:
            continue
        for pad_num, net_name in pad_nets_map.items():
            if not net_name or (fp_ref, pad_num) in labeled_pins:
                continue
            if queue_label_at_pin(net_name, fp_ref, pad_num):
                pcb_label_count += 1
    print(f"  PCB net labels: {pcb_label_count} placed")

    # ── No-connects on unused XIAO pins ──────────────────────────────────────
    for ctrl_ref in ["U_L", "U_R"]:
        if ctrl_ref not in placed_components:
            continue
        try:
            pins = sch.list_component_pins(ctrl_ref)
        except Exception:
            continue
        for pin_num, pin_pos in pins:
            if (ctrl_ref, str(pin_num)) not in labeled_pins:
                pending_nc.append((pin_pos.x, pin_pos.y))

    # ── Save & post-process ──────────────────────────────────────────────────
    print(f"  Symbols: {len(placed_components)} placed")
    print(f"  Global labels to inject: {len(pending_labels)}")
    print(f"  No-connect markers: {len(pending_nc)}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sch.save(str(args.output))

    # Post-process: inject global labels and no-connects into the saved file.
    # The kicad-sch-api doesn't support rotation on global labels, so we write
    # them directly as s-expressions before the final closing paren.
    text = args.output.read_text()

    extra_blocks = []
    for net_name, x_mm, y_mm, rot in pending_labels:
        extra_blocks.append(_make_global_label(net_name, x_mm, y_mm, rot))
    for x_mm, y_mm in pending_nc:
        extra_blocks.append(_make_no_connect(x_mm, y_mm))

    # Also remove any regular labels the API may have generated
    text = re.sub(r'\t\(label\s+"[^"]*".*?\n\t\)\n', '', text, flags=re.DOTALL)

    # Insert before final closing paren
    insert_text = "\n".join(extra_blocks)
    last_paren = text.rfind(")")
    text = text[:last_paren] + insert_text + "\n" + text[last_paren:]

    args.output.write_text(text)
    print(f"  → {args.output} ({args.output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
