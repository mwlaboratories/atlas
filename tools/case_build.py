#!/usr/bin/env python3
"""Atlas case generator — programmatic CAD from outline.json + keyboard.yaml.

Reads `build/outline.json` (emitted by pcb_build.py) and extrudes each
half polygon into a solid. Case features (walls, lips, standoffs, holes)
are added edge-by-edge based on layout.yaml's `case:` section.

v1 scope: flat floor only — one extrusion per half, no opinions about
walls or features. User dictates edge-by-edge additions from here.

Usage:
    cq-python tools/case_build.py \\
        -l tools/keyboard.yaml \\
        --outline tools/build/outline.json \\
        -o tools/build/case.step
"""
import argparse
import json
import sys
from pathlib import Path

import cadquery as cq
import yaml


def load_outline(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"  Error: {path} not found. Run `just gen-kicad` first.")
    return json.loads(path.read_text())


def half_floor(polygon: list[list[float]], thickness: float) -> cq.Workplane:
    """Extrude a closed polygon to a flat floor of given thickness.

    Input polygon is in KiCad coords (Y-down). We flip Y so the resulting
    STEP renders right-side-up in f3d and matches mechanical convention.
    """
    pts = [(x, -y) for x, y in polygon]
    return cq.Workplane("XY").polyline(pts).close().extrude(thickness)


def build_case(outline: dict, layout: dict) -> cq.Assembly:
    case_cfg = layout.get("case", {})
    bottom_thickness = case_cfg.get("bottom_thickness", 2.0)

    left = half_floor(outline["left"], bottom_thickness)
    right = half_floor(outline["right"], bottom_thickness)

    asm = cq.Assembly(name="atlas_case")
    asm.add(left, name="left", color=cq.Color(0.6, 0.6, 0.7, 1.0))
    asm.add(right, name="right", color=cq.Color(0.6, 0.6, 0.7, 1.0))
    return asm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-l", "--layout", type=Path, required=True,
                    help="keyboard.yaml")
    ap.add_argument("--outline", type=Path, required=True,
                    help="outline.json emitted by pcb_build.py")
    ap.add_argument("-o", "--output", type=Path, required=True,
                    help="output STEP path")
    args = ap.parse_args()

    layout = yaml.safe_load(args.layout.read_text())
    outline = load_outline(args.outline)

    print(f"Left:  {len(outline['left'])} pts")
    print(f"Right: {len(outline['right'])} pts")

    asm = build_case(outline, layout)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    asm.save(str(args.output))
    print(f"✓ {args.output}")


if __name__ == "__main__":
    main()
