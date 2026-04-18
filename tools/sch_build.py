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


def _make_junction(x_mm: float, y_mm: float) -> str:
    uid = str(uuid.uuid4())
    return f"""\t(junction
\t\t(at {x_mm:.2f} {y_mm:.2f})
\t\t(diameter 0)
\t\t(color 0 0 0 0)
\t\t(uuid "{uid}")
\t)
"""


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
    # Deferred junctions: [(x_mm, y_mm)] — emitted at col/row-wire ↔ pin crossings
    pending_junctions: list[tuple[float, float]] = []
    # Track which (ref, pad) pairs already have labels
    labeled_pins: set[tuple[str, str]] = set()
    # Cache of per-symbol pin bounding-box in mm (min_x, min_y, max_x, max_y).
    # Pin-label rotation is determined by which edge of this bbox the pin is
    # nearest — robust for corner pins and for symbols whose placement origin
    # isn't their body centre (e.g. the XIAO, whose origin is at a corner).
    _bbox_cache: dict[str, tuple[float, float, float, float]] = {}

    def get_pin_bbox_mm(ref):
        if ref in _bbox_cache:
            return _bbox_cache[ref]
        try:
            pins = sch.list_component_pins(ref)
        except Exception:
            pins = []
        if not pins:
            _, (cx, cy) = placed_components[ref]
            cxm, cym = cx * GRID_MM, cy * GRID_MM
            bbox = (cxm, cym, cxm, cym)
        else:
            xs = [p.x for _, p in pins]
            ys = [p.y for _, p in pins]
            bbox = (min(xs), min(ys), max(xs), max(ys))
        _bbox_cache[ref] = bbox
        return bbox

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
        # Pick the bbox edge the pin sits closest to.  Horizontal (left/right)
        # wins on ties so corner pins get a side label, not a top/bottom one.
        # Convention per user-edited reference: vertical labels always use
        # rot 90 (arrow points down into pin, text reads upward) regardless of
        # whether the pin sits on the top or bottom edge.  Horizontal labels
        # use rot 180 on the left edge and rot 0 on the right edge.
        # Skip L/R candidates on purely-vertical bboxes (e.g. 2-pin caps/resistors)
        # and T/B on purely-horizontal bboxes so labels always point outward along
        # the component's long axis.
        min_x, min_y, max_x, max_y = get_pin_bbox_mm(ref)
        sides = []
        if max_x - min_x > 0.1:
            sides.append((pin_pos.x - min_x, 0, 180))
            sides.append((max_x - pin_pos.x, 0, 0))
        if max_y - min_y > 0.1:
            sides.append((pin_pos.y - min_y, 1, 90))
            sides.append((max_y - pin_pos.y, 1, 90))
        if sides:
            sides.sort(key=lambda s: (s[0], s[1]))
            rot = sides[0][2]
        else:
            # Single-pin connector (Conn_01x01): bbox is zero-extent on both
            # axes, so fall back to pin position vs stored placement origin.
            # The pin sits on one side of the origin — the label extends in
            # that same direction, pointing away from the body.
            _, (cx_g, cy_g) = placed_components[ref]
            dx = pin_pos.x - cx_g * GRID_MM
            dy = pin_pos.y - cy_g * GRID_MM
            if abs(dy) > abs(dx):
                rot = 90 if dy < 0 else 270
            else:
                rot = 0 if dx > 0 else 180
        pending_labels.append((net_name, pin_pos.x, pin_pos.y, rot))
        labeled_pins.add((ref, pad))
        return True

    # ── Section 1: Controllers ───────────────────────────────────────────────
    # L-half / R-half base positions hand-tuned per user-edited reference
    # (2026-04-18, v3).  Each half has its own X and Y so we can place the
    # R-half as a mirror where the ADC ends up on the outside edge.
    CTRL_L_X, CTRL_L_Y = 122, 51
    CTRL_R_X, CTRL_R_Y = 274, 55
    # Controller frame margins relative to placement origin (XIAO top-left).
    CTRL_FRAME_DX = -19
    CTRL_FRAME_DY = -4
    CTRL_FRAME_W = 72
    CTRL_FRAME_H = 68
    CTRL_TITLE_DX = 36
    CTRL_TITLE_DY = -9
    # Power switch sits to the right of the XIAO body, inside the controller
    # frame.  Text label is left of the switch, aligned on the same Y.
    PWR_TITLE_DX = 19
    PWR_TITLE_DY = 56
    PWR_SW_DX = 36
    PWR_SW_DY = 56

    # "LEFT" / "RIGHT" headers — above the controller frames
    sch.add_text("L E F T",
                 position=(CTRL_L_X + CTRL_FRAME_DX + 3, CTRL_L_Y - 10),
                 size=3.5, bold=True, color=FRAME_COLOR)
    sch.add_text("R I G H T",
                 position=(CTRL_R_X + CTRL_FRAME_DX + 3, CTRL_R_Y - 10),
                 size=3.5, bold=True, color=FRAME_COLOR)

    for half, ctrl_x, ctrl_y in [("L", CTRL_L_X, CTRL_L_Y),
                                 ("R", CTRL_R_X, CTRL_R_Y)]:
        suffix = f"_{half}"

        sch.add_text("M I C R O\nC O N T R O L L E R",
                     position=(ctrl_x + CTRL_TITLE_DX, ctrl_y + CTRL_TITLE_DY),
                     size=TITLE_SIZE, bold=True, color=FRAME_COLOR)
        add_section_frame(sch, ctrl_x + CTRL_FRAME_DX, ctrl_y + CTRL_FRAME_DY,
                          CTRL_FRAME_W, CTRL_FRAME_H, "")
        place(f"U{suffix}", lookup_symbol(f"U{suffix}", layout), ctrl_x, ctrl_y)

        # Power switch (inside the controller frame, right of the XIAO body)
        sch.add_text(
            f"P O W E R\nS W I T C H",
            position=(ctrl_x + PWR_TITLE_DX, ctrl_y + PWR_TITLE_DY),
            size=TITLE_SIZE, bold=True, color=FRAME_COLOR,
        )
        ref = f"SW_PWR{suffix}"
        if ref in placed:
            place(ref, lookup_symbol(ref, layout),
                  ctrl_x + PWR_SW_DX, ctrl_y + PWR_SW_DY)
            queue_label_at_pin(f"BAT+{suffix}", ref, "1")
            queue_label_at_pin(f"VBAT{suffix}", ref, "4")

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
    # Layout hand-tuned per user-edited reference (2026-04-18, v3).  The
    # ADC chip is placed directly at (adc_x, adc_y); every other component's
    # offset is relative to that placement.  The bench places the ADC on the
    # *outer* edge of each half: ADC_L is left of the L-controller, ADC_R is
    # right of the R-controller.
    ADC_L_X = 99
    ADC_L_Y = 164
    ADC_R_X = 314
    ADC_R_Y = 164
    # Frame offsets from (adc_x, adc_y) — 135×70 box with the chip on the
    # right side and R_REF/R_SPI/caps filling the left/bottom.
    ADC_FRAME_DX = -59
    ADC_FRAME_DY = -34
    ADC_FRAME_W = 135
    ADC_FRAME_H = 70
    # Component offsets from the chip placement.  Spacings are deliberately
    # non-uniform to keep labels from colliding.
    CAP_DX = [-16, 1, 20]      # AVDD-100n, AVDD-10µ, DVDD-100n
    CAP_DY = -25
    R_REF_DX = -34
    R_REF_DY0 = -14            # first R_REF; second is +22
    R_SPI_DX = [-21, 14, 48]   # CS, MOSI, SCK
    R_SPI_DY = 23
    TP_PAD_DX = 55
    TP_PAD_DY0 = -23           # first pad; stride +12
    ADC_TITLE_DX = 55
    ADC_TITLE_DY = -38

    for half, adc_x, adc_y in [("L", ADC_L_X, ADC_L_Y), ("R", ADC_R_X, ADC_R_Y)]:
        add_section_frame(sch, adc_x + ADC_FRAME_DX, adc_y + ADC_FRAME_DY,
                          ADC_FRAME_W, ADC_FRAME_H, "")
        sch.add_text(
            f"A D C  F R O N T - E N D",
            position=(adc_x + ADC_TITLE_DX, adc_y + ADC_TITLE_DY),
            size=TITLE_SIZE, bold=True, color=FRAME_COLOR,
        )

        adc_ref = f"U_ADC_{half}"
        if adc_ref in placed:
            place(adc_ref, lookup_symbol(adc_ref, layout), adc_x, adc_y)

        cap_refs = sorted(
            r for r in placed
            if r.startswith("C_") and re.search(rf"_{half}\d", r)
        )
        for ci, cref in enumerate(cap_refs):
            dx = CAP_DX[ci] if ci < len(CAP_DX) else CAP_DX[-1] + (ci - len(CAP_DX) + 1) * 18
            place(cref, "Device:C", adc_x + dx, adc_y + CAP_DY)

        r_refs = sorted(
            r for r in placed
            if r.startswith("R_REF_") and re.search(rf"_{half}\d", r)
        )
        for ri, rref in enumerate(r_refs):
            place(rref, "Device:R", adc_x + R_REF_DX, adc_y + R_REF_DY0 + ri * 22)

        # SPI series resistors (24 Ω on SCK/MOSI/CS) — horizontal row below the
        # chip, rotated 90° so pin-1/pin-2 labels extend left/right.
        r_spi_refs = sorted(
            r for r in placed
            if r.startswith("R_SPI_") and r.endswith(f"_{half}")
        )
        for si, sref in enumerate(r_spi_refs):
            dx = R_SPI_DX[si] if si < len(R_SPI_DX) else R_SPI_DX[-1] + (si - len(R_SPI_DX) + 1) * 35
            place(sref, "Device:R", adc_x + dx, adc_y + R_SPI_DY, rot=90)

        tp_refs = sorted([r for r in placed if r.startswith(f"TP_{half}_pad_")])
        for ti, tref in enumerate(tp_refs):
            # Use short value (pad letter) to avoid double-naming overlap
            pad_letter = tref.split("_")[-1]  # 'a', 'b', 'x', 'y'
            place(tref, "Connector_Generic:Conn_01x01",
                  adc_x + TP_PAD_DX, adc_y + TP_PAD_DY0 + ti * 12,
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
    MATRIX_Y = 224
    COL_SPACING = 10    # TOTEM uses ~8 grid spacing between columns
    ROW_SPACING = 14    # TOTEM uses ~12 grid spacing between rows
    SW_CY = 6           # switch centre Y offset
    ROW_WIRE_DY = 12    # row wire Y = ry + SW_CY + 6 = ry + 12
    COL_LABEL_DY = -2   # col label above first switch
    SPLIT_COL = 5
    LEFT_X = 119
    RIGHT_X = 263

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

    # Column wires: segmented at each SW pin-1 crossing with a junction so
    # KiCad actually ties the column net to each switch (a continuous wire
    # passing through a pin without a junction does *not* connect).
    for col in range(n_cols):
        if col not in rows_in_col:
            continue
        cx = col_x(col)
        col_wx = cx - 2  # pin 1 X for SW_Push_45deg
        rows_sorted = sorted(rows_in_col[col])

        top_y = MATRIX_Y + rows_sorted[0] * ROW_SPACING + SW_CY - 2 + COL_LABEL_DY
        bot_y = MATRIX_Y + rows_sorted[-1] * ROW_SPACING + ROW_WIRE_DY + 2
        # SW pin-1 y for each row in this column
        sw_pin_ys = [MATRIX_Y + r * ROW_SPACING + SW_CY - 2 for r in rows_sorted]

        prev_y = top_y
        for sw_y in sw_pin_ys:
            try:
                sch.add_wire(start=(col_wx, prev_y), end=(col_wx, sw_y))
            except Exception:
                pass
            pending_junctions.append((col_wx * GRID_MM, sw_y * GRID_MM))
            prev_y = sw_y
        # Final segment past last switch down to row-wire level
        if bot_y > prev_y:
            try:
                sch.add_wire(start=(col_wx, prev_y), end=(col_wx, bot_y))
            except Exception:
                pass

        # Single COL label at the top of the wire — body extends upward (rot 90),
        # so the arrow points down into the matrix.
        half_suffix = "_L" if col < SPLIT_COL else "_R"
        col_in_half = col if col < SPLIT_COL else col - SPLIT_COL
        col_net = f"COL{col_in_half}{half_suffix}"
        pending_labels.append((col_net, col_wx * GRID_MM, top_y * GRID_MM, 90))

    # Row wires: segmented at each D pin-2 crossing with a junction.
    for half_label, cols_in_row_half, hx in [
        ("L", cols_in_row_left, LEFT_X),
        ("R", cols_in_row_right, RIGHT_X),
    ]:
        for row, cols_present in sorted(cols_in_row_half.items()):
            cols_sorted = sorted(cols_present)
            wire_y = MATRIX_Y + row * ROW_SPACING + ROW_WIRE_DY

            left_cx = col_x(cols_sorted[0])
            right_cx = col_x(cols_sorted[-1])
            left_end = left_cx - 5  # where the ROW label anchors
            right_end = right_cx + 5
            # D pin-2 x for each column in this row (D placed at col_x(c)+2)
            d_pin_xs = [col_x(c) + 2 for c in cols_sorted]

            prev_x = left_end
            for dp_x in d_pin_xs:
                try:
                    sch.add_wire(start=(prev_x, wire_y), end=(dp_x, wire_y))
                except Exception:
                    pass
                pending_junctions.append((dp_x * GRID_MM, wire_y * GRID_MM))
                prev_x = dp_x
            if right_end > prev_x:
                try:
                    sch.add_wire(start=(prev_x, wire_y), end=(right_end, wire_y))
                except Exception:
                    pass

            row_suffix = "_L" if half_label == "L" else "_R"
            row_net = f"ROW{row}{row_suffix}"
            pending_labels.append((row_net, left_end * GRID_MM, wire_y * GRID_MM, 180))

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

    # ── No-connects on unused XIAO pins + SP3T unused throws ────────────────
    for sweep_ref in ["U_L", "U_R", "SW_PWR_L", "SW_PWR_R"]:
        if sweep_ref not in placed_components:
            continue
        try:
            pins = sch.list_component_pins(sweep_ref)
        except Exception:
            continue
        for pin_num, pin_pos in pins:
            if (sweep_ref, str(pin_num)) not in labeled_pins:
                pending_nc.append((pin_pos.x, pin_pos.y))

    # ── Save & post-process ──────────────────────────────────────────────────
    print(f"  Symbols: {len(placed_components)} placed")
    print(f"  Global labels to inject: {len(pending_labels)}")
    print(f"  No-connect markers: {len(pending_nc)}")
    print(f"  Junctions: {len(pending_junctions)}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sch.save(str(args.output))

    # Post-process: inject global labels, no-connects, and junctions.
    # The kicad-sch-api doesn't expose rotation on global labels or junction
    # creation, so we write them directly as s-expressions.
    text = args.output.read_text()

    extra_blocks = []
    for net_name, x_mm, y_mm, rot in pending_labels:
        extra_blocks.append(_make_global_label(net_name, x_mm, y_mm, rot))
    for x_mm, y_mm in pending_nc:
        extra_blocks.append(_make_no_connect(x_mm, y_mm))
    for x_mm, y_mm in pending_junctions:
        extra_blocks.append(_make_junction(x_mm, y_mm))

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
