#!/usr/bin/env python3
"""Atlas STEP assembly — bare PCB + trackpoint sensor modules.

Reads the bare PCB STEP (from kicad-cli), places two SK8707 sensor modules
at the trackpoint positions read from the .kicad_pcb, and writes a combined
STEP file.

Usage:
    python3 tools/step_build.py -l tools/keyboard.yaml \
        --pcb-step tools/build/pcb_bare.step \
        -o tools/build/atlas.step
"""
import argparse
import os
import re
import sys
from pathlib import Path

import cadquery as cq
import yaml

TOOLS_DIR = Path(__file__).resolve().parent
MODELS_DIR = TOOLS_DIR / "kicad" / "3dmodels"
BUILD_DIR = TOOLS_DIR / "build"


def read_trackpoint_positions(pcb_path: Path) -> list[tuple[float, float]]:
    """Read trackpoint stick positions from the .kicad_pcb file.

    Splits the file into per-footprint chunks; within each, the first `(at X Y)`
    is the footprint position (subsequent `(at ...)` belong to nested properties).
    Reference name lives in `(property "Reference" "TP_..._stick" ...)`.
    """
    if not pcb_path.exists():
        print(f"  Error: {pcb_path} not found.")
        sys.exit(1)

    text = pcb_path.read_text()
    positions: list[tuple[float, float]] = []

    for chunk in re.split(r'(?=\(footprint\b)', text):
        if not chunk.startswith("(footprint"):
            continue
        ref_match = re.search(r'"Reference"\s+"(TP_\w+_stick)"', chunk)
        at_match = re.search(r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)', chunk)
        if ref_match and at_match:
            x, y = float(at_match.group(1)), float(at_match.group(2))
            positions.append((x, -y))  # STEP coords: negate Y

    positions.sort(key=lambda p: p[0])
    if not positions:
        print(f"  Error: no TP_*_stick footprints found in {pcb_path}")
        sys.exit(1)
    return positions


def build_assembly(pcb_step: Path, tp_module_step: Path, pcb_path: Path) -> cq.Workplane:
    """Combine bare PCB STEP with two trackpoint modules."""
    if not pcb_step.exists():
        print(f"Error: {pcb_step} not found. Run 'just pcb' first.")
        sys.exit(1)
    if not tp_module_step.exists():
        print(f"Error: {tp_module_step} not found.")
        sys.exit(1)

    pcb = cq.importers.importStep(str(pcb_step))
    tp_module = cq.importers.importStep(str(tp_module_step))

    tp_positions = read_trackpoint_positions(pcb_path)

    # SK8707 native orientation: flat face in XZ plane, thickness along Y axis.
    # +90° around X lays it flat (thickness → +Z); +90° around Z rotates body in-plane.
    # Z offset separates sensor PCB from keyboard PCB (standoff for hotswap socket clearance).
    pcb_bottom_z = -1.6
    sensor_standoff = 3.0

    parts = [pcb.val()]

    if len(tp_positions) != 2:
        print(f"  Error: expected 2 trackpoint positions, got {len(tp_positions)}")
        sys.exit(1)

    for half, (tx, ty) in zip(["L", "R"], tp_positions):
        m = tp_module.rotate((0, 0, 0), (1, 0, 0), 90)
        m = m.rotate((0, 0, 0), (0, 0, 1), 180)  # pads point up (toward ADS1220) on both halves
        m = m.translate((tx, ty, pcb_bottom_z - sensor_standoff))
        parts.append(m.val())

    combined = cq.Compound.makeCompound(parts)
    return cq.Workplane().add(combined)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-l", "--layout", type=Path, default=TOOLS_DIR / "keyboard.yaml")
    parser.add_argument("--pcb-step", type=Path, required=True, help="Bare PCB STEP from kicad-cli")
    parser.add_argument("-o", "--output", type=Path, default=BUILD_DIR / "atlas.step")
    args = parser.parse_args()

    layout = yaml.safe_load(args.layout.read_text())
    tp_step_name = layout.get("trackpoint", {}).get("assembled_step", "SK8707.STEP")
    tp_module_step = MODELS_DIR / tp_step_name
    pcb_path = BUILD_DIR / "atlas.kicad_pcb"

    print("Building STEP assembly...")
    assembly = build_assembly(args.pcb_step, tp_module_step, pcb_path)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(assembly, str(args.output))
    size = os.path.getsize(args.output) / 1e6
    print(f"  → {args.output} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
