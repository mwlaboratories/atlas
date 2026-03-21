#!/usr/bin/env python3
"""Atlas case parts generator — CadQuery scripts driven by keyboard.yaml.

Generates:
  - Trackpoint spacer (flat plate between PCB underside and metal bracket)
  - (future) bottom plate, keyplate, housing

Usage:
    python3 tools/case/case_build.py -l tools/keyboard.yaml -o tools/build/case/
"""
import argparse
import sys
from pathlib import Path

import cadquery as cq
import yaml

TOOLS_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Trackpoint spacer
# ---------------------------------------------------------------------------

def build_trackpoint_spacer(layout: dict) -> cq.Workplane:
    """Thin plate between PCB underside and trackpoint metal bracket.

    Provides a flat surface for the bracket to tighten against,
    covering any protruding solder joints or component leads.

    Holes: center (stick), 2× screw (M2) at ±offset from center.
    """
    tp_cfg = layout.get("trackpoint", {})
    center_diam = tp_cfg.get("center_hole", 5.0)
    screw_cfg = tp_cfg.get("screw_holes", {})
    screw_diam = screw_cfg.get("diameter", 2.2)
    screw_offset = screw_cfg.get("offset", 10.0)

    # Spacer dimensions
    thickness = 1.2       # mm — enough to clear PCB protrusions
    width = 12.0          # mm — wider than the stick hole
    height = screw_offset * 2 + 6.0  # mm — spans both screw holes + margin

    spacer = (
        cq.Workplane("XY")
        .box(width, height, thickness)
        # Center hole for trackpoint stick
        .faces(">Z").workplane()
        .hole(center_diam)
        # Screw holes
        .faces(">Z").workplane()
        .pushPoints([(0, -screw_offset), (0, screw_offset)])
        .hole(screw_diam + 0.2)  # slight clearance for M2 screws
    )

    return spacer


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-l", "--layout", type=Path, default=TOOLS_DIR / "keyboard.yaml",
        help="keyboard.yaml config",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=TOOLS_DIR / "build" / "case",
        help="Output directory for STEP files",
    )
    parser.add_argument(
        "--part", choices=["spacer", "all"], default="all",
        help="Which part to generate",
    )
    args = parser.parse_args()

    layout = yaml.safe_load(args.layout.read_text())
    out_dir: Path = args.output
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.part in ("spacer", "all"):
        print("Building trackpoint spacer...")
        spacer = build_trackpoint_spacer(layout)
        out_file = out_dir / "trackpoint_spacer.step"
        cq.exporters.export(spacer, str(out_file))
        print(f"  → {out_file}")

        # Also export STL for 3D printing
        stl_file = out_dir / "trackpoint_spacer.stl"
        cq.exporters.export(spacer, str(stl_file))
        print(f"  → {stl_file}")

    print("Done.")


if __name__ == "__main__":
    main()
