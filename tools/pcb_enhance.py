#!/usr/bin/env python3
"""Patch kbplacer-generated KiCad PCB with project-local 3D models,
trackpoint mounting holes, and XIAO BLE controller footprints.

Replaces broken 3rd-party model paths (from kbplacer/kiswitch) with
project-local .step files shipped in 3dmodels/.  Also injects a switch
body model alongside each hotswap socket model, cuts NPTH holes
for the trackpoint stick and screws on each half, and places XIAO BLE
controller footprints above the outermost keys.

Idempotent — running twice produces the same result.

Usage:
    python3 tools/pcb_enhance.py -i output/keyboard/keyboard.kicad_pcb
    python3 tools/pcb_enhance.py -i output/keyboard/keyboard.kicad_pcb -l layout.yaml
"""
import argparse
import re
import shutil
import sys
import uuid
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Model path replacements
# ---------------------------------------------------------------------------

# Broken kiswitch PCM path → project-local hotswap socket
OLD_HOTSWAP = (
    "${KICAD6_3RD_PARTY}/3dmodels/"
    "com_github_perigoso_keyswitch-kicad-library/"
    "3d-library.3dshapes/SW_Hotswap_Kailh_Choc_V1.wrl"
)
NEW_HOTSWAP = "${KIPRJMOD}/3dmodels/Choc_V1_Hotswap.step"

# Diode: nix packages3d has .stpZ/.wrl not .step → use project-local .wrl
OLD_DIODE = "${KICAD9_3DMODEL_DIR}/Diode_SMD.3dshapes/D_SOD-123F.step"
NEW_DIODE = "${KIPRJMOD}/3dmodels/D_SOD-123F.wrl"

# Switch body model to inject alongside each hotswap socket
SWITCH_BODY = "${KIPRJMOD}/3dmodels/Choc_V1_Switch.step"

# Template for a (model ...) s-expression block (tabs match KiCad formatting)
MODEL_BLOCK = (
    '\t\t(model "{path}"\n'
    "\t\t\t(offset\n"
    "\t\t\t\t(xyz {ox} {oy} {oz})\n"
    "\t\t\t)\n"
    "\t\t\t(scale\n"
    "\t\t\t\t(xyz 1 1 1)\n"
    "\t\t\t)\n"
    "\t\t\t(rotate\n"
    "\t\t\t\t(xyz {rx} {ry} {rz})\n"
    "\t\t\t)\n"
    "\t\t)"
)


def _model_block(path: str, *, offset=(0, 0, 0), rotate=(0, 0, 0)) -> str:
    return MODEL_BLOCK.format(
        path=path,
        ox=offset[0], oy=offset[1], oz=offset[2],
        rx=rotate[0], ry=rotate[1], rz=rotate[2],
    )

# Regex matching a full (model "...Choc_V1_Hotswap...") block (after path replacement)
HOTSWAP_MODEL_RE = re.compile(
    r'\(model\s+"[^"]*Choc_V1_Hotswap[^"]*"'
    r"\s*\(offset\s*\(xyz[^)]*\)\s*\)"
    r"\s*\(scale\s*\(xyz[^)]*\)\s*\)"
    r"\s*\(rotate\s*\(xyz[^)]*\)\s*\)"
    r"\s*\)",
    re.DOTALL,
)

# Regex to detect an already-injected switch body model
SWITCH_BODY_RE = re.compile(re.escape(SWITCH_BODY))

# Sentinel to detect already-inserted trackpoint holes
TP_HOLE_SENTINEL = '"Atlas:TrackpointHole'

# Sentinel to detect already-inserted controller footprints
CTRL_SENTINEL = '"Atlas:XIAO_BLE_'

# 3D model for the XIAO BLE controller (official Seeed Studio STEP)
XIAO_3D_MODEL = "${KIPRJMOD}/3dmodels/XIAO-nRF52840 v15.step"
XIAO_MODEL_ROTATE = (-90, 0, -90)
XIAO_MODEL_OFFSET = (6.1, -1.75, 0)

# XIAO BLE footprint filename (Totem keyboard footprint, in tools/)
XIAO_FP_FILE = "XIAO_BLE_custom.kicad_mod"

# XIAO BLE board dimensions (origin at center)
XIAO_HALF_HEIGHT = 10.5  # 21mm / 2 — along the long axis (USB-C to battery)
XIAO_HALF_WIDTH = 8.89  # 17.78mm / 2 — across the castellated pads

# ---------------------------------------------------------------------------
# NPTH hole footprint template (KiCad 9 format)
# ---------------------------------------------------------------------------

NPTH_FOOTPRINT = """\t(footprint "Atlas:TrackpointHole_{name}"
\t\t(layer "F.Cu")
\t\t(uuid "{uuid}")
\t\t(at {x:.4f} {y:.4f})
\t\t(attr exclude_from_pos_files exclude_from_bom)
\t\t(fp_text reference "TP_{name}"
\t\t\t(at 0 -{label_offset})
\t\t\t(layer "F.SilkS")
\t\t\t(uuid "{uuid_ref}")
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 0.8 0.8)
\t\t\t\t\t(thickness 0.1)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(pad "" np_thru_hole circle
\t\t\t(at 0 0)
\t\t\t(size {d:.2f} {d:.2f})
\t\t\t(drill {d:.2f})
\t\t\t(layers "*.Cu" "*.Mask")
\t\t\t(uuid "{uuid_pad}")
\t\t)
\t)"""


# ---------------------------------------------------------------------------
# Trackpoint hole logic
# ---------------------------------------------------------------------------


def _parse_switches(pcb_text: str) -> list[tuple[str, float, float]]:
    """Return list of (reference, x, y) for each switch footprint."""
    switches = []
    for m in re.finditer(
        r'\(footprint\s+"(SW_[^"]+)"\s*\n\s*\(layer\s+"F\.Cu"\)\s*\n'
        r'\s*\(uuid\s+"[^"]+"\)\s*\n\s*\(at\s+([\d.-]+)\s+([\d.-]+)',
        pcb_text,
    ):
        x, y = float(m.group(2)), float(m.group(3))
        # find reference in the next ~500 chars
        ref_m = re.search(r'"Reference"\s+"(SW\d+)"', pcb_text[m.start() : m.start() + 500])
        ref = ref_m.group(1) if ref_m else "?"
        switches.append((ref, x, y))
    return switches


def _compute_trackpoint_centers(
    switches: list[tuple[str, float, float]],
    tp_cols: list[int],
    tp_rows: list[int],
    grid_cols: int,
) -> list[tuple[float, float]]:
    """Compute trackpoint centroid(s) from switch positions.

    Uses the reference numbering convention from kbplacer:
      SW<n> where n = row * grid_cols + col + 1  (left half, 1-indexed)
      Right half continues from grid_cols + 1.
    """
    ref_to_pos = {ref: (x, y) for ref, x, y in switches}

    centers = []
    for half_offset in (0, grid_cols):  # 0 = left, grid_cols = right
        points = []
        for row in tp_rows:
            for col in tp_cols:
                # Left half: ref = row * 10 + col + 1  (SW1..SW5, SW11..SW15, ...)
                # Right half: ref = row * 10 + (grid_cols + (grid_cols - 1 - col)) + 1
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


def _make_holes(
    center: tuple[float, float],
    center_diam: float,
    screw_diam: float,
    screw_offset: float,
    half_label: str,
) -> str:
    """Generate NPTH footprint s-expressions for one trackpoint."""
    cx, cy = center
    holes = []

    holes.append(
        NPTH_FOOTPRINT.format(
            name=f"{half_label}_stick",
            uuid=str(uuid.uuid4()),
            uuid_ref=str(uuid.uuid4()),
            uuid_pad=str(uuid.uuid4()),
            x=cx,
            y=cy,
            d=center_diam,
            label_offset=center_diam / 2 + 1.5,
        )
    )

    for i, dy in enumerate([-screw_offset, screw_offset]):
        holes.append(
            NPTH_FOOTPRINT.format(
                name=f"{half_label}_screw{i+1}",
                uuid=str(uuid.uuid4()),
                uuid_ref=str(uuid.uuid4()),
                uuid_pad=str(uuid.uuid4()),
                x=cx,
                y=cy + dy,
                d=screw_diam,
                label_offset=screw_diam / 2 + 1.0,
            )
        )

    return "\n".join(holes)


# ---------------------------------------------------------------------------
# Controller (XIAO BLE) placement
# ---------------------------------------------------------------------------


def _find_xiao_footprint() -> str | None:
    """Find the XIAO BLE .kicad_mod file in tools/ next to this script."""
    fp_path = Path(__file__).resolve().parent / XIAO_FP_FILE
    if fp_path.exists():
        return fp_path.read_text()
    return None


def _convert_footprint_to_pcb(
    mod_text: str,
    fp_name: str,
    x: float,
    y: float,
    rotation: float,
    ref: str,
    model_path: str,
    model_offset: tuple[float, float, float] = (0, 0, 0),
    model_rotate: tuple[float, float, float] = (0, 0, 0),
) -> str:
    """Convert a .kicad_mod file to an embedded PCB footprint s-expression.

    Handles KiCad 6–8 format differences and sets placement coordinates
    plus a 3D model reference.
    """
    lines = mod_text.strip().split("\n")
    # Strip outer wrapper: first line = (footprint "..." ...), last line = )
    inner_lines = lines[1:-1]

    # Filter header elements we regenerate (any nesting level)
    skip_any = ("(layer ", "(tedit ", "(version ", "(generator ", "(generator_version ")
    # PCB-specific lines that only appear at the top level (1-tab indent)
    skip_toplevel = ("(locked ", "(at ", "(path ", "(sheetname ", "(sheetfile ")
    filtered = []
    for line in inner_lines:
        s = line.strip()
        if any(s.startswith(p) for p in skip_any):
            continue
        # Only skip PCB-placement lines at the footprint top level (1 tab)
        if line.startswith("\t") and not line.startswith("\t\t"):
            if any(s.startswith(p) for p in skip_toplevel):
                continue
        filtered.append(line)

    inner = "\n".join(filtered)

    # Strip PCB-specific net assignments from pads
    inner = re.sub(r'\n[\t ]*\(net \d+ "[^"]*"\)', "", inner)

    # Replace KiCad 6 tstamp with fresh uuid (KiCad 8+ already has uuid)
    inner = re.sub(
        r"\(tstamp\s+[0-9a-f-]+\)",
        lambda _: f'(uuid "{uuid.uuid4()}")',
        inner,
    )

    # Update reference designator — handles both KiCad 6 and 8+ formats
    inner = re.sub(r'reference\s+"[^"]*"', f'reference "{ref}"', inner)
    inner = re.sub(
        r'\(property\s+"Reference"\s+"[^"]*"',
        f'(property "Reference" "{ref}"',
        inner,
    )

    # Ensure exclude_from_bom attribute
    inner = inner.replace(
        "(attr smd exclude_from_pos_files)",
        "(attr smd exclude_from_pos_files exclude_from_bom)",
    )

    # Normalise indentation: detect whether input uses tabs or spaces,
    # then re-indent with tabs at +1 nesting level (inside PCB file).
    converted = []
    for line in inner.split("\n"):
        stripped = line.lstrip()
        if not stripped:
            continue
        leading = line[: len(line) - len(stripped)]
        if "\t" in leading:
            # Tab-indented (KiCad 8+): count existing tabs
            tab_level = leading.count("\t") + 1
        else:
            # Space-indented (KiCad 6): 2 spaces per level
            tab_level = len(leading) // 2 + 1
        converted.append("\t" * tab_level + stripped)

    inner_text = "\n".join(converted)

    # Add attr line if the footprint has none
    if "(attr " not in inner_text:
        # Insert after the first property/fp_text block
        inner_text = inner_text + "\n\t\t(attr exclude_from_pos_files exclude_from_bom)"

    # Adjust pad shape rotation angles for footprint placement rotation.
    # KiCad rotates pad POSITIONS when a footprint is placed with rotation,
    # but does NOT rotate pad SHAPE orientations (the angle in pad (at x y angle)).
    # Oval/rect pads end up with wrong orientation unless we compensate here.
    if rotation:

        def _adjust_pad_at(m: re.Match) -> str:
            prefix, x, y = m.group(1), m.group(2), m.group(3)
            existing = float(m.group(4)) if m.group(4) else 0.0
            # Add 180° to keep drill offsets pointing outward after rotation
            new_angle = (existing + rotation + 180) % 360
            if new_angle == 0:
                return f"{prefix}(at {x} {y})"
            return f"{prefix}(at {x} {y} {new_angle:g})"

        # Only match (at ...) inside (pad ...) lines — identified by leading (pad
        inner_text = re.sub(
            r"(\(pad\s[^)]*\s[a-z_]+\s)"          # up to pad shape type
            r"\(at\s+([\d.-]+)\s+([\d.-]+)"         # (at x y
            r"(?:\s+([\d.-]+))?\)",                  # optional angle)
            _adjust_pad_at,
            inner_text,
        )

    rot_str = f" {rotation:.0f}" if rotation else ""
    model = _model_block(model_path, offset=model_offset, rotate=model_rotate)

    return (
        f'\t(footprint "{fp_name}"\n'
        f'\t\t(layer "F.Cu")\n'
        f'\t\t(uuid "{uuid.uuid4()}")\n'
        f"\t\t(at {x:.4f} {y:.4f}{rot_str})\n"
        f"{inner_text}\n"
        f"{model}\n"
        f"\t)"
    )


# Switch footprint half-extent (Choc V1 hotswap courtyard ±9 mm from center)
SWITCH_HALF_EXTENT = 9.0


def patch_controller(pcb_text: str, layout: dict) -> str:
    """Insert XIAO BLE controller footprints above the outermost keys.

    Orientation logic (left half shown, right is mirrored):

    Footprint at 0°:  USB-C at −Y (top),  battery at +Y (bottom)
    KiCad +90° CW:    USB-C at −X (left), battery at +X (right)  ← left half
    KiCad −90° CCW:   USB-C at +X (right), battery at −X (left)  ← right half

    After ±90° rotation the footprint's short axis (17.78 mm) is vertical.
    Y clearance is computed from the switch courtyard edge, not center.
    """
    if CTRL_SENTINEL in pcb_text:
        return pcb_text  # already present

    ctrl_cfg = layout.get("controller", {})
    if not ctrl_cfg:
        return pcb_text

    standoff = ctrl_cfg.get("standoff", 4.0)
    grid_cols = layout.get("grid", {}).get("cols", 5)

    mod_text = _find_xiao_footprint()
    if not mod_text:
        print(f"Warning: {XIAO_FP_FILE} not found in tools/, skipping controller.")
        return pcb_text

    switches = _parse_switches(pcb_text)
    if not switches:
        print("Warning: no switches found, skipping controller.")
        return pcb_text

    ref_to_pos = {ref: (x, y) for ref, x, y in switches}

    # Left half: above SW1 (top-left pinky key)
    # Right half: above SW<2*grid_cols> (top-right pinky key, mirrored)
    sw_left = "SW1"
    sw_right = f"SW{grid_cols * 2}"

    # +90° CW → USB-C points left;  −90° CCW → USB-C points right
    halves = [
        (sw_left, "L", 90),
        (sw_right, "R", -90),
    ]

    blocks = []

    for sw_ref, half_label, rotation in halves:
        if sw_ref not in ref_to_pos:
            print(f"Warning: {sw_ref} not found, skipping {half_label} controller.")
            continue

        sx, sy = ref_to_pos[sw_ref]
        # Place above the switch courtyard edge (not center)
        # After ±90° rotation the short axis (17.78 mm) is vertical
        switch_top_edge = sy - SWITCH_HALF_EXTENT
        # Tight placement: minimal gap to keycaps
        ctrl_y = switch_top_edge + 1.0 - XIAO_HALF_WIDTH  # snug fit
        # Align XIAO outer edge with switch outer edge (for choc spacing compatibility)
        # After 90° rotation, XIAO_HALF_HEIGHT is horizontal extent
        edge_align = SWITCH_HALF_EXTENT - XIAO_HALF_HEIGHT  # -1.5mm
        ctrl_x = sx + edge_align if half_label == "L" else sx - edge_align

        fp = _convert_footprint_to_pcb(
            mod_text,
            fp_name=f"Atlas:XIAO_BLE_{half_label}",
            x=ctrl_x,
            y=ctrl_y,
            rotation=rotation,
            ref=f"U_{half_label}",
            model_path=XIAO_3D_MODEL,
            model_offset=XIAO_MODEL_OFFSET,
            model_rotate=XIAO_MODEL_ROTATE,
        )
        blocks.append(fp)
        print(f"  Controller {half_label}: ({ctrl_x:.2f}, {ctrl_y:.2f}) rot={rotation}°")

    if not blocks:
        return pcb_text

    insert_text = "\n".join(blocks) + "\n"
    last_paren = pcb_text.rfind(")")
    return pcb_text[:last_paren] + insert_text + pcb_text[last_paren:]


# ---------------------------------------------------------------------------
# Main patch logic
# ---------------------------------------------------------------------------


def patch_models(pcb_text: str) -> str:
    """Apply 3D model path patches."""
    patched = pcb_text.replace(OLD_HOTSWAP, NEW_HOTSWAP)
    patched = patched.replace(OLD_DIODE, NEW_DIODE)

    if not SWITCH_BODY_RE.search(patched):
        switch_block = _model_block(SWITCH_BODY)

        def _inject(match: re.Match) -> str:
            return match.group(0) + "\n" + switch_block

        patched = HOTSWAP_MODEL_RE.sub(_inject, patched)

    return patched


def patch_trackpoint_holes(pcb_text: str, layout: dict) -> str:
    """Insert NPTH holes for trackpoint stick and screws."""
    if TP_HOLE_SENTINEL in pcb_text:
        return pcb_text  # already present

    tp_cfg = layout.get("trackpoint", {})
    between = tp_cfg.get("between", {})
    tp_cols = between.get("cols", [3, 4])
    tp_rows = between.get("rows", [0, 1])
    center_diam = tp_cfg.get("center_hole", 5.0)
    screw_cfg = tp_cfg.get("screw_holes", {})
    screw_diam = screw_cfg.get("diameter", 2.2)
    screw_offset = screw_cfg.get("offset", 10.0)
    grid_cols = layout.get("grid", {}).get("cols", 5)

    switches = _parse_switches(pcb_text)
    if not switches:
        print("Warning: no switch footprints found, skipping trackpoint holes.")
        return pcb_text

    centers = _compute_trackpoint_centers(switches, tp_cols, tp_rows, grid_cols)

    hole_blocks = []
    for i, center in enumerate(centers):
        label = "L" if i == 0 else "R"
        hole_blocks.append(_make_holes(center, center_diam, screw_diam, screw_offset, label))
        print(f"  Trackpoint {label}: center ({center[0]:.2f}, {center[1]:.2f})")

    if not hole_blocks:
        return pcb_text

    # Insert before the final closing paren of the PCB
    insert_text = "\n".join(hole_blocks) + "\n"
    last_paren = pcb_text.rfind(")")
    return pcb_text[:last_paren] + insert_text + pcb_text[last_paren:]


# ---------------------------------------------------------------------------
# Edge.Cuts bounding box
# ---------------------------------------------------------------------------

EDGE_CUTS_SENTINEL = "atlas-bbox-"


def _compute_pcb_bounds(pcb_text: str, margin: float = 5.0) -> tuple[float, float, float, float]:
    """Compute bounding box of all footprints in the PCB."""
    min_x, min_y = float("inf"), float("inf")
    max_x, max_y = float("-inf"), float("-inf")

    # Find all footprint positions
    for m in re.finditer(r'\(footprint\s+"[^"]+"\s*\n\s*\(layer\s+"F\.Cu"\)\s*\n'
                         r'\s*\(uuid\s+"[^"]+"\)\s*\n\s*\(at\s+([\d.-]+)\s+([\d.-]+)', pcb_text):
        x, y = float(m.group(1)), float(m.group(2))
        min_x, min_y = min(min_x, x), min(min_y, y)
        max_x, max_y = max(max_x, x), max(max_y, y)

    # Add margin and account for typical footprint extent (~10mm for switches)
    extent = 15.0  # Half-extent of largest components
    return (min_x - extent - margin, min_y - extent - margin,
            max_x + extent + margin, max_y + extent + margin)


def patch_edge_cuts_box(pcb_text: str, margin: float = 5.0) -> str:
    """Add a simple rectangular Edge.Cuts outline around all components."""
    if EDGE_CUTS_SENTINEL in pcb_text:
        return pcb_text  # already present

    x1, y1, x2, y2 = _compute_pcb_bounds(pcb_text, margin)

    # Create Edge.Cuts rectangle as gr_rect (use tstamp with sentinel prefix for idempotency)
    sentinel_uuid = f"atlas-bbox-{uuid.uuid4()}"
    rect_block = (
        f'\t(gr_rect (start {x1:.4f} {y1:.4f}) (end {x2:.4f} {y2:.4f})\n'
        f'\t\t(stroke (width 0.1) (type solid))\n'
        f'\t\t(fill none)\n'
        f'\t\t(layer "Edge.Cuts")\n'
        f'\t\t(uuid "{sentinel_uuid}")\n'
        f'\t)\n'
    )

    print(f"  Edge.Cuts box: ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f})")

    last_paren = pcb_text.rfind(")")
    return pcb_text[:last_paren] + rect_block + pcb_text[last_paren:]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-i", "--input", required=True, type=Path, help=".kicad_pcb file to patch"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path (default: overwrite input)",
    )
    parser.add_argument(
        "-l",
        "--layout",
        type=Path,
        default=None,
        help="layout.yaml (default: auto-detect from repo root)",
    )
    args = parser.parse_args()

    pcb_path: Path = args.input
    out_path: Path = args.output or pcb_path

    if not pcb_path.exists():
        print(f"Error: {pcb_path} not found", file=sys.stderr)
        sys.exit(1)

    # Find layout.yaml
    layout_path = args.layout
    if layout_path is None:
        layout_path = Path(__file__).resolve().parent.parent / "layout.yaml"
    if not layout_path.exists():
        print(f"Warning: {layout_path} not found, skipping trackpoint holes.", file=sys.stderr)
        layout = {}
    else:
        layout = yaml.safe_load(layout_path.read_text())

    pcb_text = pcb_path.read_text()

    # Apply patches
    patched = patch_models(pcb_text)
    patched = patch_trackpoint_holes(patched, layout)
    patched = patch_controller(patched, layout)
    patched = patch_edge_cuts_box(patched)

    if patched == pcb_text:
        print("No changes needed — PCB already patched.")
        return

    out_path.write_text(patched)

    # Copy 3dmodels/ into the KiCad project directory (next to .kicad_pcb)
    # so ${KIPRJMOD}/3dmodels/... resolves correctly
    repo_models = Path(__file__).resolve().parent.parent / "3dmodels"
    proj_models = out_path.parent / "3dmodels"
    if repo_models.is_dir():
        if proj_models.exists():
            shutil.rmtree(proj_models)
        shutil.copytree(repo_models, proj_models)
        print(f"Copied 3dmodels/ → {proj_models}")

    # Count replacements
    hotswap_count = patched.count(NEW_HOTSWAP)
    switch_count = patched.count(SWITCH_BODY)
    diode_count = patched.count(NEW_DIODE)
    hole_count = patched.count(TP_HOLE_SENTINEL)
    ctrl_count = patched.count(CTRL_SENTINEL)
    has_edge_box = EDGE_CUTS_SENTINEL in patched
    print(f"Patched {hotswap_count} hotswap + {switch_count} switch + {diode_count} diode model entries.")
    print(f"Added {hole_count} trackpoint holes.")
    print(f"Added {ctrl_count} controller footprint(s).")
    print(f"Added Edge.Cuts bounding box: {has_edge_box}")
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
