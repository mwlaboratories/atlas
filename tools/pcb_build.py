#!/usr/bin/env python3
"""Atlas PCB generator — single pipeline from YAML to finished board.

Calls kbplacer as a library for switch/diode placement, then enhances
the board object directly: solder key swap, 3D models, trackpoint holes,
controller, power switch, ADS1220 ADC + passives, and Edge.Cuts outlines.

No file patching — everything operates on the same pcbnew.BOARD object.

Run inside `nix develop` (provides pcbnew, kbplacer, and KISWITCH_DIR).

Usage:
    python3 tools/pcb_build.py -l tools/keyboard.yaml -o tools/build/atlas.kicad_pcb
"""
import argparse
import math
import os
import subprocess
import sys
from pathlib import Path

import pcbnew
import yaml
from kbplacer.board_modifier import set_side, set_position, set_rotation
from kbplacer.element_position import (
    ElementInfo,
    ElementPosition,
    PositionOption,
    Side,
)
from kbplacer.kbplacer_plugin import PluginSettings, run_board

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TOOLS_DIR = Path(__file__).resolve().parent
FOOTPRINTS_DIR = TOOLS_DIR / "kicad" / "footprints"
MODELS_DIR = TOOLS_DIR / "kicad" / "3dmodels"
KISWITCH_DIR = Path(os.environ["KISWITCH_DIR"])

# 3D model paths (${KIPRJMOD} resolved by KiCad relative to .kicad_pcb)
HOTSWAP_MODEL = "${KIPRJMOD}/3dmodels/Choc_V1_Hotswap.step"
SWITCH_BODY_MODEL = "${KIPRJMOD}/3dmodels/Choc_V1_Switch.step"
DIODE_MODEL = "${KIPRJMOD}/3dmodels/D_SOD-123F.wrl"
XIAO_3D_MODEL = "${KIPRJMOD}/3dmodels/XIAO-nRF52840 v15.step"
ADS1220_3D_MODEL = "${KICAD9_3DMODEL_DIR}/Package_SO.3dshapes/TSSOP-16_4.4x5mm_P0.65mm.stpZ"


XIAO_MODEL_ROTATE = (-90, 0, -90)
XIAO_MODEL_OFFSET = (6.1, -1.75, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mm(val: float) -> int:
    """Convert mm to KiCad internal units."""
    return pcbnew.FromMM(val)


def _vec(x_mm: float, y_mm: float) -> pcbnew.VECTOR2I:
    """Create VECTOR2I from mm coordinates."""
    return pcbnew.VECTOR2I(_mm(x_mm), _mm(y_mm))


def _pos_mm(fp: pcbnew.FOOTPRINT) -> tuple[float, float]:
    """Get footprint center position in mm."""
    p = fp.GetPosition()
    return (pcbnew.ToMM(p.x), pcbnew.ToMM(p.y))


def _bbox_mm(fp: pcbnew.FOOTPRINT) -> tuple[float, float, float, float]:
    """Get footprint bounding box as (left, top, right, bottom) in mm.

    This reflects the ACTUAL extent after rotation/flip — no hardcoded dimensions.
    """
    bb = fp.GetBoundingBox(False, False)  # exclude text
    return (
        pcbnew.ToMM(bb.GetLeft()),
        pcbnew.ToMM(bb.GetTop()),
        pcbnew.ToMM(bb.GetRight()),
        pcbnew.ToMM(bb.GetBottom()),
    )


def _bbox_half_extents(fp: pcbnew.FOOTPRINT) -> tuple[float, float, float, float]:
    """Get (center_to_left, center_to_top, center_to_right, center_to_bottom) in mm.

    Measures from footprint center to each edge of bounding box.
    Accounts for rotation and flip — always accurate.
    """
    cx, cy = _pos_mm(fp)
    l, t, r, b = _bbox_mm(fp)
    return (cx - l, cy - t, r - cx, b - cy)


def _add_model(fp, path: str, offset=(0, 0, 0), rotation=(0, 0, 0)):
    """Add a 3D model to a footprint."""
    m = pcbnew.FP_3DMODEL()
    m.m_Filename = path
    m.m_Scale.x = m.m_Scale.y = m.m_Scale.z = 1.0
    m.m_Offset.x, m.m_Offset.y, m.m_Offset.z = offset
    m.m_Rotation.x, m.m_Rotation.y, m.m_Rotation.z = rotation
    m.m_Show = True
    fp.Models().push_back(m)


def _box_halves(layout: dict, component: str) -> tuple[float, float]:
    """Read component box half-extents from keyboard.yaml.

    Components define their physical extent as 'box: [w, h]' in mm.
    Returns (half_w, half_h) before rotation.
    """
    if component == "switch":
        box = layout.get("switch", {}).get("box", [18.0, 17.0])
    elif component == "controller":
        box = layout.get("controller", {}).get("box", [17.78, 21.0])
    else:
        return (0, 0)
    return (box[0] / 2, box[1] / 2)


def _strip_edge_cuts(fp: pcbnew.FOOTPRINT):
    """Remove all Edge.Cuts graphical items from a footprint."""
    to_remove = [
        item for item in fp.GraphicalItems() if item.GetLayer() == pcbnew.Edge_Cuts
    ]
    for item in to_remove:
        fp.Remove(item)


def _kicad_footprint_dir() -> str:
    """Get KICAD9_FOOTPRINT_DIR from environment."""
    return os.environ.get("KICAD9_FOOTPRINT_DIR", "")


def _find_fp(board: pcbnew.BOARD, ref: str) -> pcbnew.FOOTPRINT | None:
    """Find footprint by reference via iteration (avoids pcbnew SWIG bugs)."""
    for fp in board.GetFootprints():
        if fp.GetReference() == ref:
            return fp
    return None


def _load_and_place(
    board: pcbnew.BOARD,
    lib_path: str,
    fp_name: str,
    ref: str,
    x: float, y: float,
    rotation: float = 0,
    back: bool = False,
) -> pcbnew.FOOTPRINT:
    """Load a footprint, add to board, position, flip, rotate.

    Must add to board BEFORE flip — pcbnew segfaults on Flip without a parent board.
    Returns the placed footprint so caller can query its actual bbox.
    """
    fp = pcbnew.FootprintLoad(lib_path, fp_name)
    fp.SetReference(ref)
    _strip_edge_cuts(fp)
    fp.SetExcludedFromBOM(True)
    fp.SetExcludedFromPosFiles(True)
    board.Add(fp)  # must be on board before Flip
    set_position(fp, _vec(x, y))
    if back:
        set_side(fp, Side.BACK)
    set_rotation(fp, rotation)
    return fp


def _get_trackpoint_switch_refs(layout: dict) -> list[str]:
    tp_cfg = layout.get("trackpoint", {})
    between = tp_cfg.get("between", {})
    tp_cols = between.get("cols", [3, 4])
    tp_rows = between.get("rows", [0, 1])
    grid_cols = layout.get("grid", {}).get("cols", 5)

    refs = []
    for half_offset in (0, grid_cols):
        for row in tp_rows:
            for col in tp_cols:
                if half_offset == 0:
                    ref_num = row * 10 + col + 1
                else:
                    right_col = grid_cols + (grid_cols - 1 - col)
                    ref_num = row * 10 + right_col + 1
                refs.append(f"SW{ref_num}")
    return refs


def _compute_trackpoint_centers(
    board: pcbnew.BOARD,
    tp_cols: list[int],
    tp_rows: list[int],
    grid_cols: int,
) -> list[tuple[float, float]]:
    ref_to_pos: dict[str, tuple[float, float]] = {}
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if ref.startswith("SW"):
            ref_to_pos[ref] = _pos_mm(fp)

    centers = []
    for half_offset in (0, grid_cols):
        points = []
        for row in tp_rows:
            for col in tp_cols:
                if half_offset == 0:
                    ref_num = row * 10 + col + 1
                else:
                    right_col = grid_cols + (grid_cols - 1 - col)
                    ref_num = row * 10 + right_col + 1
                ref = f"SW{ref_num}"
                if ref in ref_to_pos:
                    points.append(ref_to_pos[ref])
        if points:
            cx = sum(p[0] for p in points) / len(points)
            cy = sum(p[1] for p in points) / len(points)
            centers.append((cx, cy))
    return centers


# ---------------------------------------------------------------------------
# Step 0: Run kbplacer as library
# ---------------------------------------------------------------------------


def run_kbplacer(layout: dict, kle_json: str, pcb_path: str) -> pcbnew.BOARD:
    """Run kbplacer to create the base PCB with switches and diodes."""
    sw_cfg = layout.get("switch", {})
    fp_id = sw_cfg.get(
        "footprint", "Switch_Keyboard_Hotswap_Kailh:SW_Hotswap_Kailh_Choc_V1"
    )
    lib_name, fp_name = fp_id.split(":", 1)
    switch_fp = f"{KISWITCH_DIR / f'{lib_name}.pretty'}:{fp_name}_1.00u"

    fp_dir = _kicad_footprint_dir()
    diode_fp = f"{fp_dir}/Diode_SMD.pretty:D_SOD-123F"

    settings = PluginSettings(
        pcb_file_path=pcb_path,
        layout_path=kle_json,
        key_info=ElementInfo(
            "SW{}",
            PositionOption.DEFAULT,
            ElementPosition(0, 0, 180, Side.FRONT),
            "",
        ),
        key_distance=None,
        diode_info=ElementInfo(
            "D{}",
            PositionOption.CUSTOM,
            ElementPosition(-6.0, -4.0, 90, Side.BACK),
            "",
        ),
        route_switches_with_diodes=True,
        optimize_diodes_orientation=False,
        route_rows_and_columns=False,
        additional_elements=[
            ElementInfo("ST{}", PositionOption.CUSTOM, ElementPosition(0, 0, 0, Side.FRONT), "")
        ],
        generate_outline=False,
        outline_delta=0.0,
        template_path="",
        create_pcb_file=True,
        create_sch_file=False,
        sch_file_path="",
        switch_footprint=switch_fp,
        diode_footprint=diode_fp,
    )

    board = run_board(settings)
    print(f"  kbplacer: {len(board.GetFootprints())} footprints placed")
    return board


# ---------------------------------------------------------------------------
# Step 1: Swap trackpoint keys to solder footprints
# ---------------------------------------------------------------------------


def swap_solder_keys(board: pcbnew.BOARD, layout: dict) -> None:
    tp_cfg = layout.get("trackpoint", {})
    if not tp_cfg.get("solder_keys"):
        return

    solder_fp_id = tp_cfg.get("solder_footprint", "")
    if not solder_fp_id:
        return

    lib_name, fp_name = solder_fp_id.split(":", 1)
    lib_path = str(KISWITCH_DIR / f"{lib_name}.pretty")
    fp_name_sized = f"{fp_name}_1.00u"

    target_refs = _get_trackpoint_switch_refs(layout)
    count = 0

    for ref in target_refs:
        old_fp = _find_fp(board,ref)
        if not old_fp:
            continue
        if "Hotswap" not in old_fp.GetFPIDAsString():
            continue

        pos = old_fp.GetPosition()
        rot = old_fp.GetOrientationDegrees()
        val = old_fp.GetValue()

        new_fp = pcbnew.FootprintLoad(lib_path, fp_name_sized)
        new_fp.SetReference(ref)
        set_position(new_fp, pos)
        set_rotation(new_fp, rot)
        new_fp.SetValue(val)

        board.Remove(old_fp)
        board.Add(new_fp)
        count += 1

    if count:
        print(f"  Solder swap: {count} keys ({', '.join(target_refs)})")


# ---------------------------------------------------------------------------
# Step 2: 3D models + locating pin removal
# ---------------------------------------------------------------------------


def set_models(board: pcbnew.BOARD, layout: dict) -> None:
    tp_refs = set()
    tp_cfg = layout.get("trackpoint", {})
    if tp_cfg.get("solder_keys"):
        tp_refs = set(_get_trackpoint_switch_refs(layout))

    hotswap_count = solder_count = diode_count = 0

    for fp in board.GetFootprints():
        ref = fp.GetReference()

        if ref.startswith("SW") and ref[2:].isdigit():
            is_solder = ref in tp_refs
            fp.Models().clear()

            if is_solder:
                _add_model(fp, SWITCH_BODY_MODEL)
                solder_count += 1
            else:
                _add_model(fp, HOTSWAP_MODEL)
                _add_model(fp, SWITCH_BODY_MODEL)
                hotswap_count += 1

                # Remove locating pin pad (-5.22, 4.2) 1.3mm drill
                for pad in list(fp.Pads()):
                    if pad.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH:
                        continue
                    pp = pad.GetFPRelativePosition()
                    px, py = pcbnew.ToMM(pp.x), pcbnew.ToMM(pp.y)
                    drill = pcbnew.ToMM(pad.GetDrillSize().x)
                    if (
                        abs(px - (-5.22)) < 0.1
                        and abs(py - 4.2) < 0.1
                        and abs(drill - 1.3) < 0.1
                    ):
                        fp.Remove(pad)

        elif ref.startswith("D") and ref[1:].isdigit():
            fp.Models().clear()
            _add_model(fp, DIODE_MODEL)
            diode_count += 1

    print(
        f"  Models: {hotswap_count} hotswap + {solder_count} solder, {diode_count} diodes"
    )


# ---------------------------------------------------------------------------
# Step 3: Trackpoint NPTH holes
# ---------------------------------------------------------------------------


def add_trackpoint_holes(board: pcbnew.BOARD, layout: dict) -> None:
    tp_cfg = layout.get("trackpoint", {})
    between = tp_cfg.get("between", {})
    tp_cols = between.get("cols", [3, 4])
    tp_rows = between.get("rows", [0, 1])
    center_diam = tp_cfg.get("center_hole", 5.0)
    screw_cfg = tp_cfg.get("screw_holes", {})
    screw_diam = screw_cfg.get("diameter", 2.2)
    screw_offset = screw_cfg.get("offset", 10.0)
    grid_cols = layout.get("grid", {}).get("cols", 5)

    centers = _compute_trackpoint_centers(board, tp_cols, tp_rows, grid_cols)

    for i, (cx, cy) in enumerate(centers):
        label = "L" if i == 0 else "R"
        holes = [
            (f"TP_{label}_stick", cx, cy, center_diam),
            (f"TP_{label}_screw1", cx, cy - screw_offset, screw_diam),
            (f"TP_{label}_screw2", cx, cy + screw_offset, screw_diam),
        ]
        for ref, hx, hy, diam in holes:
            fp = pcbnew.FOOTPRINT(board)
            fp.SetReference(ref)
            set_position(fp, _vec(hx, hy))
            fp.SetExcludedFromBOM(True)
            fp.SetExcludedFromPosFiles(True)
            fp.Reference().SetVisible(True)

            pad = pcbnew.PAD(fp)
            pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
            pad.SetDrillSize(_vec(diam, diam))
            pad.SetSize(_vec(diam, diam))
            lset = pcbnew.LSET()
            lset.AddLayer(pcbnew.F_Cu)
            lset.AddLayer(pcbnew.B_Cu)
            lset.AddLayer(pcbnew.F_Mask)
            lset.AddLayer(pcbnew.B_Mask)
            pad.SetLayerSet(lset)
            fp.Add(pad)
            board.Add(fp)

        print(f"  Trackpoint {label}: ({cx:.2f}, {cy:.2f})")


# ---------------------------------------------------------------------------
# Step 4: Controller (XIAO BLE)
# ---------------------------------------------------------------------------


def add_controller(board: pcbnew.BOARD, layout: dict) -> None:
    """Place XIAO BLE controllers on B.Cu, above the outermost key column.

    Box sizes from keyboard.yaml (Dwgs.User layer in footprints):
      switch.box: [18, 17] mm → half: 9.0 × 8.5
      controller.box: [17.78, 21] mm → half: 8.89 × 10.5

    After ±90° rotation, XIAO axes swap:
      Vertical half  = xiao_half_w = 8.89 mm
      Horizontal half = xiao_half_h = 10.5 mm

    Placement (left half, right is mirrored):
      Y: XIAO bottom edge flush with key top edge
      X: XIAO inner edge aligned with key inner edge (extends outward)
    """
    ctrl_cfg = layout.get("controller", {})
    if not ctrl_cfg:
        return

    grid_cols = layout.get("grid", {}).get("cols", 5)
    standoff = ctrl_cfg.get("standoff", 0.0)

    sw_half_w, sw_half_h = _box_halves(layout, "switch")
    xiao_half_w, xiao_half_h = _box_halves(layout, "controller")

    # After ±90° rotation, XIAO axes swap
    xiao_v = xiao_half_w   # 8.89mm — vertical half-extent after rotation
    xiao_h = xiao_half_h   # 10.5mm — horizontal half-extent after rotation

    # After set_side(BACK) mirrors the footprint, rotations are mirrored:
    #   Left half: -90° → USB-C points left (outward)
    #   Right half: 90° → USB-C points right (outward)
    halves = [("SW1", "L", -90), (f"SW{grid_cols * 2}", "R", 90)]

    for sw_ref, half_label, rotation in halves:
        anchor = _find_fp(board, sw_ref)
        if not anchor:
            print(f"  Warning: {sw_ref} not found, skipping {half_label} controller")
            continue

        sx, sy = _pos_mm(anchor)

        # Y: XIAO bottom flush with key top, separated by standoff
        ctrl_y = sy - sw_half_h - xiao_v - standoff

        # X: XIAO inner edge aligned with key inner edge (extends outward)
        # Left half: right edges align, XIAO extends left
        # Right half: left edges align, XIAO extends right
        if half_label == "L":
            ctrl_x = sx + sw_half_w - xiao_h
        else:
            ctrl_x = sx - sw_half_w + xiao_h

        fp = _load_and_place(
            board, str(FOOTPRINTS_DIR), "xiao-ble-smd-cutout",
            ref=f"U_{half_label}", x=ctrl_x, y=ctrl_y,
            rotation=rotation, back=True,
        )
        fp.Models().clear()
        _add_model(fp, XIAO_3D_MODEL, offset=XIAO_MODEL_OFFSET, rotation=XIAO_MODEL_ROTATE)

        print(f"  Controller {half_label}: ({ctrl_x:.2f}, {ctrl_y:.2f})")


# ---------------------------------------------------------------------------
# Step 6: Power switch
# ---------------------------------------------------------------------------


def add_power_switch(board: pcbnew.BOARD, layout: dict) -> None:
    pwr_cfg = layout.get("power_switch")
    if not pwr_cfg:
        return

    fp_id = pwr_cfg.get("footprint", "")
    if not fp_id:
        return

    lib_name, fp_name = fp_id.split(":", 1)
    fp_dir = _kicad_footprint_dir()
    if not fp_dir:
        print("  Warning: KICAD9_FOOTPRINT_DIR not set, skipping power switch")
        return
    lib_path = str(Path(fp_dir) / f"{lib_name}.pretty")

    offset_raw = pwr_cfg.get("offset", [0.0, 0.0])
    if isinstance(offset_raw, (int, float)):
        x_off, y_off = 0.0, float(offset_raw)
    else:
        x_off, y_off = float(offset_raw[0]), float(offset_raw[1])
    pwr_rot = float(pwr_cfg.get("rotation", 0.0))
    pwr_model = "${KICAD9_3DMODEL_DIR}/Button_Switch_SMD.3dshapes/SW_SP3T_PCM13.stpZ"

    for hl in ("L", "R"):
        ctrl = _find_fp(board,f"U_{hl}")
        if not ctrl:
            print(f"  Warning: U_{hl} not found, skipping power switch {hl}")
            continue

        cx, cy = _pos_mm(ctrl)
        pwr_x = cx + (x_off if hl == "L" else -x_off)
        pwr_y = cy + y_off
        rot = pwr_rot if hl == "L" else -pwr_rot

        fp = pcbnew.FootprintLoad(lib_path, fp_name)
        fp.SetReference(f"SW_PWR_{hl}")
        _strip_edge_cuts(fp)
        fp.Models().clear()
        _add_model(fp, pwr_model)
        fp.SetExcludedFromBOM(True)
        fp.SetExcludedFromPosFiles(True)
        board.Add(fp)
        set_position(fp, _vec(pwr_x, pwr_y))
        set_side(fp, Side.BACK)
        set_rotation(fp, rot)
        print(f"  Power switch {hl}: ({pwr_x:.2f}, {pwr_y:.2f})")


# ---------------------------------------------------------------------------
# Step 6b: ADS1220 strain-gauge ADC + passives
# ---------------------------------------------------------------------------


def add_ads1220(board: pcbnew.BOARD, layout: dict) -> None:
    """Place ADS1220 (TSSOP-16) on B.Cu near each trackpoint sensor.

    Strain-gauge sensor's [x][y][a][b] pads are hand-soldered directly to
    AIN0/AIN1/REFP0/REFN0. SPI lines route to the XIAO across the board.
    """
    tp_cfg = layout.get("trackpoint", {})
    adc_cfg = tp_cfg.get("adc")
    if not adc_cfg or adc_cfg.get("type") != "ads1220":
        return

    fp_id = adc_cfg.get("footprint", "")
    if not fp_id:
        return

    lib_name, fp_name = fp_id.split(":", 1)
    fp_dir = _kicad_footprint_dir()
    if not fp_dir:
        print("  Warning: KICAD9_FOOTPRINT_DIR not set, skipping ADS1220")
        return
    lib_path = str(Path(fp_dir) / f"{lib_name}.pretty")

    offset_raw = adc_cfg.get("offset", [0.0, 13.0])
    if isinstance(offset_raw, (int, float)):
        x_off, y_off = 0.0, float(offset_raw)
    else:
        x_off, y_off = float(offset_raw[0]), float(offset_raw[1])
    adc_rot = float(adc_cfg.get("rotation", 0.0))

    grid_cols = layout.get("grid", {}).get("cols", 5)
    tp_cols = tp_cfg.get("between", {}).get("cols", [3, 4])
    tp_rows = tp_cfg.get("between", {}).get("rows", [0, 1])
    centers = _compute_trackpoint_centers(board, tp_cols, tp_rows, grid_cols)

    for i, tc in enumerate(centers):
        hl = "L" if i == 0 else "R"
        adc_x = tc[0] + (x_off if hl == "L" else -x_off)
        adc_y = tc[1] + y_off
        rot = adc_rot if hl == "L" else -adc_rot

        fp = pcbnew.FootprintLoad(lib_path, fp_name)
        if fp is None:
            print(f"  Warning: failed to load {lib_path}:{fp_name}, skipping ADS1220 {hl}")
            continue
        fp.SetReference(f"U_ADC_{hl}")
        fp.SetValue("ADS1220")
        _strip_edge_cuts(fp)
        fp.Models().clear()
        _add_model(fp, ADS1220_3D_MODEL)
        fp.SetExcludedFromBOM(True)
        fp.SetExcludedFromPosFiles(True)
        board.Add(fp)
        set_position(fp, _vec(adc_x, adc_y))
        set_side(fp, Side.BACK)
        set_rotation(fp, rot)
        print(f"  ADS1220 {hl}: ({adc_x:.2f}, {adc_y:.2f})")


def add_ads1220_passives(board: pcbnew.BOARD, layout: dict) -> None:
    """Place ADS1220 required passives next to each ADC.

    2× reference resistors (REFP0/REFN0 → AIN2 divider, sets ADC reference
    via IDAC × R_ref), plus 100 nF + 10 µF AVDD decoupling and 100 nF DVDD.
    """
    tp_cfg = layout.get("trackpoint", {})
    adc_cfg = tp_cfg.get("adc")
    if not adc_cfg or adc_cfg.get("type") != "ads1220":
        return

    fp_dir = _kicad_footprint_dir()
    if not fp_dir:
        print("  Warning: KICAD9_FOOTPRINT_DIR not set, skipping ADS1220 passives")
        return
    res_lib = str(Path(fp_dir) / "Resistor_SMD.pretty")
    cap_lib = str(Path(fp_dir) / "Capacitor_SMD.pretty")

    ref_value = adc_cfg.get("ref_resistor", 2400)
    res_fp = "R_0402_1005Metric_Pad0.72x0.64mm_HandSolder"
    cap_0402_fp = "C_0402_1005Metric_Pad0.74x0.62mm_HandSolder"
    cap_0603_fp = "C_0603_1608Metric_Pad1.08x0.95mm_HandSolder"

    # 5 passives stacked vertically next to the chip
    passives_spec = [
        ("R_REF_{}1", str(ref_value), res_lib, res_fp),
        ("R_REF_{}2", str(ref_value), res_lib, res_fp),
        ("C_AVDD_{}1", "100n", cap_lib, cap_0402_fp),
        ("C_AVDD_{}2", "10u", cap_lib, cap_0603_fp),
        ("C_DVDD_{}1", "100n", cap_lib, cap_0402_fp),
    ]

    spacing = 3.0  # mm between passives — safe for 0402+0603 mix with DRC clearance
    side_gap = 5.0  # mm from ADS1220 center to passives column

    # Functional grouping: resistors (indices 0..1) on outer side, caps (indices 2..4) on inner side
    # Refs sit near REFP0/REFN0/AIN2 pin cluster; caps cluster near AVDD/DVDD power pins.
    n_outer = 2  # resistors
    n_inner = 3  # caps

    for hl in ("L", "R"):
        adc = _find_fp(board, f"U_ADC_{hl}")
        if not adc:
            print(f"  Warning: U_ADC_{hl} not found, skipping passives {hl}")
            continue

        cx, cy = _pos_mm(adc)
        inner_x = cx + (side_gap if hl == "L" else -side_gap)
        outer_x = cx - (side_gap if hl == "L" else -side_gap)
        inner_y0 = cy - (n_inner - 1) * spacing / 2
        outer_y0 = cy - (n_outer - 1) * spacing / 2

        for i, (ref_tpl, value, lib, fp_name) in enumerate(passives_spec):
            fp = pcbnew.FootprintLoad(lib, fp_name)
            if fp is None:
                print(f"  Warning: failed to load {lib}:{fp_name}, skipping passive {ref_tpl.format(hl)}")
                continue
            fp.SetReference(ref_tpl.format(hl))
            fp.SetValue(value)
            fp.SetExcludedFromBOM(True)
            fp.SetExcludedFromPosFiles(True)
            board.Add(fp)

            if i < 2:  # resistor → outer side
                px, py = outer_x, outer_y0 + i * spacing
            else:  # cap → inner side
                px, py = inner_x, inner_y0 + (i - 2) * spacing
            set_position(fp, _vec(px, py))
            set_side(fp, Side.BACK)
            set_rotation(fp, 90)

        print(f"  ADS1220 {hl} passives: 2× {ref_value}Ω (outer), 100n+10µ+100n (inner)")


# ---------------------------------------------------------------------------
# Step 7: Edge.Cuts outline
# ---------------------------------------------------------------------------


def _switch_corners(cx, cy, rot_deg, half_ext):
    """Return TL/TR/BR/BL corners of a rotated switch rect."""
    r = math.radians(-rot_deg)
    c, s = math.cos(r), math.sin(r)
    h = half_ext
    return {
        "TL": (cx - h * c + h * s, cy - h * s - h * c),
        "TR": (cx + h * c + h * s, cy + h * s - h * c),
        "BR": (cx + h * c - h * s, cy + h * s + h * c),
        "BL": (cx - h * c - h * s, cy - h * s + h * c),
    }


def _line_x_intersect(x_vert, p1, p2):
    """Point where a vertical line at x_vert crosses line p1→p2."""
    dx = p2[0] - p1[0]
    if abs(dx) < 1e-9:
        return (x_vert, p1[1])
    t = (x_vert - p1[0]) / dx
    return (x_vert, p1[1] + t * (p2[1] - p1[1]))


def _half_outline(switches, ctrl_pos, layout):
    """Build left-half Edge.Cuts outline as a clockwise polygon.

    Uses switch Dwgs.User extents (read dynamically) for the half-extent.
    """
    sw_cfg = layout.get("switch", {})
    cutout = sw_cfg.get("cutout", 14.0)
    pad = layout.get("case", {}).get("plate_padding", 2.0)
    half = cutout / 2 + pad

    grid_sw = [(r, x, y, rot) for r, x, y, rot in switches if abs(rot % 180) <= 1]
    thumb_sw = [(r, x, y, rot) for r, x, y, rot in switches if abs(rot % 180) > 1]
    if not grid_sw:
        return []

    # Group grid switches into columns
    grid_sw.sort(key=lambda s: s[1])
    columns: list[list] = []
    cur = [grid_sw[0]]
    for sw in grid_sw[1:]:
        if abs(sw[1] - cur[-1][1]) < 2:
            cur.append(sw)
        else:
            columns.append(cur)
            cur = [sw]
    columns.append(cur)

    col_ext = []
    for col in columns:
        avg_x = sum(s[1] for s in col) / len(col)
        top = min(s[2] for s in col) - half
        bot = max(s[2] for s in col) + half
        col_ext.append((avg_x, top, bot))

    pinky = col_ext[0]
    pinky_left = pinky[0] - half
    inner_right = col_ext[-1][0] + half
    pinky_bot = pinky[2]

    if len(col_ext) > 1:
        grid_top = min(c[1] for c in col_ext[1:])
        non_pinky_bot = max(c[2] for c in col_ext[1:])
    else:
        grid_top = pinky[1]
        non_pinky_bot = pinky[2]

    step_x = (col_ext[0][0] + col_ext[1][0]) / 2 if len(col_ext) > 1 else pinky_left
    thumb_return_x = (col_ext[-2][0] + col_ext[-1][0]) / 2 if len(col_ext) >= 3 else step_x

    # BLE controller rect
    if ctrl_pos:
        ctrl_hw, ctrl_hh = ctrl_pos[2], ctrl_pos[3]  # half-extents after rotation
        ble_top = ctrl_pos[1] - ctrl_hh
        ble_left = ctrl_pos[0] - ctrl_hw
        ble_right = ctrl_pos[0] + ctrl_hw
        ble_bottom = ctrl_pos[1] + ctrl_hh + 1.0  # 1mm clearance below BLE pads
        outer_left = min(pinky_left, ble_left)
    else:
        ble_top = pinky[1]
        ble_right = step_x
        outer_left = pinky_left

    # All thumb key corners, sorted inner → outer (by x position)
    thumb_keys = []
    if thumb_sw:
        for _, tx, ty, trot in sorted(thumb_sw, key=lambda s: s[1]):
            raw = _switch_corners(tx, ty, trot, half)
            all_c = sorted(raw.values(), key=lambda p: p[1])
            top2 = sorted(all_c[:2], key=lambda p: p[0])
            bot2 = sorted(all_c[2:], key=lambda p: p[0])
            thumb_keys.append({
                "TL": top2[0], "TR": top2[1], "BL": bot2[0], "BR": bot2[1]
            })

    # Build clockwise polygon
    pts = []
    pts.append((outer_left, ble_top))
    pts.append((ble_right, ble_top))
    pts.append((ble_right, grid_top))
    pts.append((inner_right, grid_top))

    if thumb_keys:
        first = thumb_keys[0]
        last = thumb_keys[-1]

        # Top: drop from grid to intersection on first key's top edge, then TR
        pts.append(_line_x_intersect(inner_right, first["TL"], first["TR"]))
        pts.append(first["TR"])
        # Subsequent keys: TL→TR
        for tk in thumb_keys[1:]:
            pts.append(tk["TL"])
            pts.append(tk["TR"])

        # Right side of outermost key: TR→BR (TR already added above)
        pts.append(last["BR"])

        # Bottom: BL of each key, outer to inner (BR already added above)
        pts.append(last["BL"])
        for tk in list(reversed(thumb_keys))[1:]:
            pts.append(tk["BR"])
            pts.append(tk["BL"])

        # Connect to vertical line at thumb_return_x
        pts.append((thumb_return_x, first["BL"][1]))
        pts.append((thumb_return_x, non_pinky_bot))
    else:
        pts.append((inner_right, non_pinky_bot))

    pts.append((step_x, non_pinky_bot))
    if pinky_bot > non_pinky_bot:
        pts.append((step_x, pinky_bot))
    pts.append((pinky_left, pinky_bot))

    if outer_left < pinky_left - 0.1:
        # Step from pinky column to BLE left edge, going low enough to clear BLE bottom
        step_y = ble_bottom if ctrl_pos else pinky[1]
        pts.append((pinky_left, step_y))
        pts.append((outer_left, step_y))
    pts.append((outer_left, ble_top))

    # Deduplicate consecutive points
    cleaned = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - cleaned[-1][0]) > 0.05 or abs(p[1] - cleaned[-1][1]) > 0.05:
            cleaned.append(p)
    if (len(cleaned) > 1
            and abs(cleaned[-1][0] - cleaned[0][0]) < 0.05
            and abs(cleaned[-1][1] - cleaned[0][1]) < 0.05):
        cleaned.pop()
    return cleaned


def add_edge_cuts(board: pcbnew.BOARD, layout: dict) -> None:
    """Add per-half Edge.Cuts outlines — two separate boards."""
    switches = []
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if ref.startswith("SW") and ref[2:].isdigit():
            x, y = _pos_mm(fp)
            switches.append((ref, x, y, fp.GetOrientationDegrees()))

    if not switches:
        print("  Warning: no switches found, skipping edge cuts")
        return

    all_xs = [x for _, x, _, _ in switches]
    mid_x = (min(all_xs) + max(all_xs)) / 2
    left_sw = [(r, x, y, rot) for r, x, y, rot in switches if x < mid_x]

    # Get controller positions + their rotated half-extents for outline
    ctrl_left = ctrl_right = None
    xiao_hw, xiao_hh = _box_halves(layout, "controller")
    # After ±90° rotation: horizontal = long axis, vertical = short axis
    xiao_h_rot = max(xiao_hw, xiao_hh)  # horizontal half after rotation
    xiao_v_rot = min(xiao_hw, xiao_hh)  # vertical half after rotation

    for hl in ("L", "R"):
        ctrl = _find_fp(board, f"U_{hl}")
        if ctrl:
            cx, cy = _pos_mm(ctrl)
            if hl == "L":
                ctrl_left = (cx, cy, xiao_h_rot, xiao_v_rot)
            else:
                ctrl_right = (cx, cy, xiao_h_rot, xiao_v_rot)

    def _emit(outline):
        for i in range(len(outline)):
            x1, y1 = outline[i]
            x2, y2 = outline[(i + 1) % len(outline)]
            shape = pcbnew.PCB_SHAPE(board)
            shape.SetShape(pcbnew.SHAPE_T_SEGMENT)
            shape.SetLayer(pcbnew.Edge_Cuts)
            shape.SetStart(_vec(x1, y1))
            shape.SetEnd(_vec(x2, y2))
            shape.SetWidth(_mm(0.2))
            board.Add(shape)

    left_outline = _half_outline(left_sw, ctrl_left, layout)
    if left_outline:
        _emit(left_outline)
        xs = [p[0] for p in left_outline]
        ys = [p[1] for p in left_outline]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        print(f"  Edge.Cuts L: {len(left_outline)} pts, {w:.1f} x {h:.1f} mm")

        # Mirror left outline for right half
        right_outline = [(2 * mid_x - x, y) for x, y in left_outline]
        right_outline.reverse()
        _emit(right_outline)
        xs = [p[0] for p in right_outline]
        print(f"  Edge.Cuts R: {len(right_outline)} pts, {max(xs) - min(xs):.1f} x {h:.1f} mm")


# ---------------------------------------------------------------------------
# Center on sheet
# ---------------------------------------------------------------------------


def center_on_sheet(board: pcbnew.BOARD) -> None:
    """Shift all board items so the design is centered on the A4 sheet.

    A4 = 297 × 210 mm. Computes the bounding box of all footprints + drawings
    and translates everything so the center lands at (148.5, 105).
    """
    # Compute bounding box of all footprints
    xs, ys = [], []
    for fp in board.GetFootprints():
        x, y = _pos_mm(fp)
        xs.append(x)
        ys.append(y)
    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts:
            shape = pcbnew.Cast_to_PCB_SHAPE(d)
            s, e = shape.GetStart(), shape.GetEnd()
            xs.extend([pcbnew.ToMM(s.x), pcbnew.ToMM(e.x)])
            ys.extend([pcbnew.ToMM(s.y), pcbnew.ToMM(e.y)])

    if not xs:
        return

    # Current center
    cur_cx = (min(xs) + max(xs)) / 2
    cur_cy = (min(ys) + max(ys)) / 2

    # Target center (A4 sheet)
    target_cx, target_cy = 148.5, 105.0

    dx = _mm(target_cx - cur_cx)
    dy = _mm(target_cy - cur_cy)
    offset = pcbnew.VECTOR2I(dx, dy)

    # Move all footprints
    for fp in board.GetFootprints():
        fp.Move(offset)

    # Move all drawings (Edge.Cuts, silkscreen, etc.)
    for d in board.GetDrawings():
        d.Move(offset)

    # Move all tracks
    for t in board.GetTracks():
        t.Move(offset)

    print(f"  Centered: shifted ({target_cx - cur_cx:+.1f}, {target_cy - cur_cy:+.1f}) mm")


# ---------------------------------------------------------------------------
# Step 9: Silkscreen — hide labels + geometric pattern
# ---------------------------------------------------------------------------


def _extract_outline_polygon(board: pcbnew.BOARD) -> list[list[tuple[float, float]]]:
    """Extract Edge.Cuts as ordered polygon(s) — one per half."""
    # Collect all edge segments
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts:
            shape = pcbnew.Cast_to_PCB_SHAPE(d)
            s = shape.GetStart()
            e = shape.GetEnd()
            p1 = (pcbnew.ToMM(s.x), pcbnew.ToMM(s.y))
            p2 = (pcbnew.ToMM(e.x), pcbnew.ToMM(e.y))
            segments.append((p1, p2))

    if not segments:
        return []

    # Build ordered polygons by chaining segments
    polys: list[list[tuple[float, float]]] = []
    used = [False] * len(segments)

    while True:
        # Find first unused segment
        start_idx = None
        for i, u in enumerate(used):
            if not u:
                start_idx = i
                break
        if start_idx is None:
            break

        poly = [segments[start_idx][0], segments[start_idx][1]]
        used[start_idx] = True

        # Chain segments
        changed = True
        while changed:
            changed = False
            for i, (p1, p2) in enumerate(segments):
                if used[i]:
                    continue
                tail = poly[-1]
                if abs(p1[0] - tail[0]) < 0.1 and abs(p1[1] - tail[1]) < 0.1:
                    poly.append(p2)
                    used[i] = True
                    changed = True
                elif abs(p2[0] - tail[0]) < 0.1 and abs(p2[1] - tail[1]) < 0.1:
                    poly.append(p1)
                    used[i] = True
                    changed = True

        # Close if needed
        if len(poly) > 2:
            if abs(poly[-1][0] - poly[0][0]) < 0.1 and abs(poly[-1][1] - poly[0][1]) < 0.1:
                poly.pop()
        polys.append(poly)

    return polys


def _point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    """Ray casting point-in-polygon test."""
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _dist_to_polygon_edge(x: float, y: float, poly: list[tuple[float, float]]) -> float:
    """Minimum distance from point to any polygon edge."""
    min_d = float("inf")
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        len_sq = dx * dx + dy * dy
        if len_sq < 1e-12:
            continue
        t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / len_sq))
        px, py = x1 + t * dx, y1 + t * dy
        d = math.sqrt((x - px) ** 2 + (y - py) ** 2)
        if d < min_d:
            min_d = d
    return min_d


def _point_inside_with_margin(
    x: float, y: float, poly: list[tuple[float, float]], margin: float = 0.5
) -> bool:
    """Check if point is inside polygon with a margin from edges."""
    if not _point_in_polygon(x, y, poly):
        return False
    return _dist_to_polygon_edge(x, y, poly) >= margin



def _noise2d(x: float, y: float, seed: int = 42) -> float:
    """Simple value noise with smooth interpolation. Returns 0..1."""
    # Hash function for grid points
    def _hash(ix: int, iy: int) -> float:
        n = ix * 374761393 + iy * 668265263 + seed
        n = (n ^ (n >> 13)) * 1274126177
        n = n ^ (n >> 16)
        return (n & 0x7FFFFFFF) / 0x7FFFFFFF

    ix, iy = int(math.floor(x)), int(math.floor(y))
    fx, fy = x - ix, y - iy

    # Smoothstep interpolation
    sx = fx * fx * (3 - 2 * fx)
    sy = fy * fy * (3 - 2 * fy)

    v00 = _hash(ix, iy)
    v10 = _hash(ix + 1, iy)
    v01 = _hash(ix, iy + 1)
    v11 = _hash(ix + 1, iy + 1)

    return (v00 * (1 - sx) + v10 * sx) * (1 - sy) + \
           (v01 * (1 - sx) + v11 * sx) * sy


def _fbm(x: float, y: float, octaves: int = 4, seed: int = 42) -> float:
    """Fractal Brownian Motion — layered noise for natural terrain."""
    val = 0.0
    amp = 0.5
    freq = 1.0
    for i in range(octaves):
        val += amp * _noise2d(x * freq, y * freq, seed + i * 31)
        amp *= 0.5
        freq *= 2.0
    return val


def _contourpy_lines(
    grid,
    threshold: float,
    x_min: float, y_min: float,
    cell_size: float,
) -> list[list[tuple[float, float]]]:
    """Extract contour polylines using contourpy (matplotlib's contour engine)."""
    import contourpy
    import numpy as np

    z = np.asarray(grid, dtype=np.float64)
    rows, cols = z.shape
    x = np.linspace(x_min, x_min + (cols - 1) * cell_size, cols)
    y = np.linspace(y_min, y_min + (rows - 1) * cell_size, rows)

    gen = contourpy.contour_generator(x, y, z, line_type=contourpy.LineType.SeparateCode)
    vertices_list, _ = gen.lines(threshold)

    polylines = []
    for verts in vertices_list:
        pts = [(float(verts[i, 0]), float(verts[i, 1])) for i in range(verts.shape[0])]
        if len(pts) >= 2:
            polylines.append(pts)
    return polylines


def add_silkscreen(board: pcbnew.BOARD, layout: dict) -> None:
    """Hide labels and add topographic contour lines from a terrain heightmap."""
    # Hide all reference/value text and footprint silk/fab drawings
    for fp in board.GetFootprints():
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        # Only move silk/fab to hidden layer (for our custom silkscreen pattern)
        # Keep CrtYd and Dwgs_User — they define component extents for outline planning
        for item in fp.GraphicalItems():
            layer = item.GetLayer()
            if layer in (pcbnew.F_SilkS, pcbnew.B_SilkS,
                         pcbnew.F_Fab, pcbnew.B_Fab):
                item.SetLayer(pcbnew.Cmts_User)

    polys = _extract_outline_polygon(board)
    if not polys:
        return

    line_width = _mm(0.15)
    cell_size = 0.35    # mm grid resolution (smaller = smoother curves)
    n_levels = 20       # number of contour levels

    # Load terrain data (Matterhorn heightmap) for contour lines
    terrain_path = TOOLS_DIR / "kicad" / "terrain.json"
    terrain_data = None
    if terrain_path.exists():
        import json
        terrain_data = json.loads(terrain_path.read_text())
        print(f"  Using real terrain ({len(terrain_data)}x{len(terrain_data[0])})")

    def _add_seg(x1, y1, x2, y2, layer):
        shape = pcbnew.PCB_SHAPE(board)
        shape.SetShape(pcbnew.SHAPE_T_SEGMENT)
        shape.SetLayer(layer)
        shape.SetStart(_vec(x1, y1))
        shape.SetEnd(_vec(x2, y2))
        shape.SetWidth(line_width)
        board.Add(shape)

    total_segs = 0

    for poly in polys:
        if len(poly) < 3:
            continue

        xs_all = [p[0] for p in poly]
        ys_all = [p[1] for p in poly]
        x_min, x_max = min(xs_all), max(xs_all)
        y_min, y_max = min(ys_all), max(ys_all)

        # Build heightmap grid — sample from real terrain or fallback to noise
        nx = int((x_max - x_min) / cell_size) + 2
        ny = int((y_max - y_min) / cell_size) + 2
        grid = []

        if terrain_data:
            t_rows = len(terrain_data)
            t_cols = len(terrain_data[0])
            for iy in range(ny):
                row = []
                for ix in range(nx):
                    # Map board coordinates to terrain tile coordinates
                    tx = (ix / nx) * (t_cols - 1)
                    ty = (iy / ny) * (t_rows - 1)
                    # Bilinear interpolation
                    tx0, ty0 = int(tx), int(ty)
                    tx1 = min(tx0 + 1, t_cols - 1)
                    ty1 = min(ty0 + 1, t_rows - 1)
                    fx, fy = tx - tx0, ty - ty0
                    h = (terrain_data[ty0][tx0] * (1 - fx) * (1 - fy) +
                         terrain_data[ty0][tx1] * fx * (1 - fy) +
                         terrain_data[ty1][tx0] * (1 - fx) * fy +
                         terrain_data[ty1][tx1] * fx * fy)
                    row.append(h)
                grid.append(row)
        else:
            for iy in range(ny):
                row = []
                for ix in range(nx):
                    px = x_min + ix * cell_size
                    py = y_min + iy * cell_size
                    h = _fbm(px * 0.025, py * 0.025, octaves=4, seed=42)
                    row.append(h)
                grid.append(row)

        # Trace contour polylines and clip against board outline
        h_min = min(min(row) for row in grid)
        h_max = max(max(row) for row in grid)
        margin = (h_max - h_min) * 0.05
        clip_margin = 0.3  # mm from board edge

        def _emit_run(run):
            nonlocal total_segs
            for j in range(len(run) - 1):
                _add_seg(run[j][0], run[j][1], run[j+1][0], run[j+1][1], pcbnew.B_SilkS)
                _add_seg(run[j][0], run[j][1], run[j+1][0], run[j+1][1], pcbnew.F_SilkS)
                total_segs += 2

        for i in range(n_levels):
            level = h_min + margin + (h_max - h_min - 2 * margin) * i / (n_levels - 1)
            polylines = _contourpy_lines(grid, level, x_min, y_min, cell_size)

            for pline in polylines:
                # Clip: split polyline at points outside the board outline
                run = []
                for pt in pline:
                    if _point_inside_with_margin(pt[0], pt[1], poly, clip_margin):
                        run.append(pt)
                    else:
                        _emit_run(run)
                        run = []
                _emit_run(run)

    print(f"  Silkscreen: {total_segs} segments ({n_levels} contour levels)")


# ---------------------------------------------------------------------------
# KLE generation (inline — avoids needing nix python)
# ---------------------------------------------------------------------------


def generate_kle(layout: dict, out_path: str) -> None:
    """Generate KLE JSON from keyboard.yaml — equivalent to layout2kle.py."""
    proc = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "layout2kle.py"), "-i", "/dev/stdin", "-o", out_path],
        input=yaml.dump(layout),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(f"Error generating KLE: {proc.stderr}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-l", "--layout", type=Path, default=TOOLS_DIR / "keyboard.yaml",
        help="keyboard.yaml config",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=TOOLS_DIR / "build" / "atlas.kicad_pcb",
        help="Output .kicad_pcb path",
    )
    parser.add_argument(
        "--kle-json", type=Path, default=None,
        help="Pre-generated KLE JSON (skip generation)",
    )
    args = parser.parse_args()

    layout = yaml.safe_load(args.layout.read_text())
    out_path: Path = args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove stale output (kbplacer refuses to overwrite)
    for ext in ("kicad_pcb", "kicad_pro", "kicad_prl"):
        p = out_path.with_suffix(f".{ext}")
        p.unlink(missing_ok=True)

    # Generate KLE JSON
    kle_json = args.kle_json
    if not kle_json:
        kle_json = out_path.parent / "layout.json"
        print("Generating KLE layout...")
        generate_kle(layout, str(kle_json))

    # Step 0: kbplacer creates board with switches + diodes
    print("Running kbplacer...")
    board = run_kbplacer(layout, str(kle_json), str(out_path))

    # Step 1: Swap trackpoint keys to solder
    print("Swapping solder keys...")
    swap_solder_keys(board, layout)

    # Step 2: Set 3D models
    print("Setting 3D models...")
    set_models(board, layout)

    # Step 3: Trackpoint holes
    print("Adding trackpoint holes...")
    add_trackpoint_holes(board, layout)

    # Step 4: Controller
    print("Adding controllers...")
    add_controller(board, layout)

    # Step 6: Power switch
    print("Adding power switches...")
    add_power_switch(board, layout)

    # Step 6b: ADS1220 ADC + passives (close to trackpoint sensor)
    print("Adding ADS1220...")
    add_ads1220(board, layout)
    print("Adding ADS1220 passives...")
    add_ads1220_passives(board, layout)

    # Step 7: Edge cuts
    print("Adding edge cuts...")
    add_edge_cuts(board, layout)

    # Center on A4 sheet (before silkscreen so contours clip correctly)
    print("Centering on sheet...")
    center_on_sheet(board)

    # Step 8: Silkscreen
    print("Adding silkscreen...")
    add_silkscreen(board, layout)

    # Save final board
    pcbnew.SaveBoard(str(out_path), board)
    print(f"Saved: {out_path} ({len(board.GetFootprints())} footprints)")

    # Symlink 3dmodels/ and footprints/ so KiCad resolves ${KIPRJMOD} paths
    for name, src in [("3dmodels", MODELS_DIR), ("footprints", FOOTPRINTS_DIR)]:
        link = out_path.parent / name
        if src.is_dir() and not link.exists():
            link.symlink_to(src)


if __name__ == "__main__":
    main()
