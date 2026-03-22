#!/usr/bin/env python3
"""Atlas STEP assembly — bare PCB + trackpoint modules.

Reads the bare PCB STEP (from kicad-cli), places two assembled_trackpoint.step
modules at the trackpoint positions read from the .kicad_pcb, and writes a
combined STEP file.

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

TOOLS_DIR = Path(__file__).resolve().parent
MODELS_DIR = TOOLS_DIR / "kicad" / "3dmodels"
BUILD_DIR = TOOLS_DIR / "build"


def read_trackpoint_positions(pcb_path: Path) -> list[tuple[float, float]]:
    """Read trackpoint stick positions from the .kicad_pcb file.

    Parses text directly to avoid pcbnew dependency (conflicts with CadQuery).
    """
    if not pcb_path.exists():
        print(f"  Warning: {pcb_path} not found, using default positions")
        return [(92.59, -85.95), (204.32, -85.95)]

    text = pcb_path.read_text()
    positions = []
    for m in re.finditer(
        r'\(footprint\b[^)]*\n.*?reference\s+"(TP_\w+_stick)".*?\(at\s+([\d.]+)\s+([\d.]+)',
        text, re.DOTALL,
    ):
        x, y = float(m.group(2)), float(m.group(3))
        positions.append((x, -y))  # STEP coords: negate Y

    positions.sort(key=lambda p: p[0])
    return positions if positions else [(92.59, -85.95), (204.32, -85.95)]


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

    # Module Z: bracket contact surface (Z≈0.8 in module) touches PCB bottom (Z=-1.6 in STEP)
    bracket_surface_z = 1.1
    pcb_bottom_z = -1.6
    module_z = pcb_bottom_z - bracket_surface_z

    parts = [pcb.val()]

    for half, (tx, ty) in zip(["L", "R"], tp_positions):
        # Left: -90° (companion points inward), Right: 90°
        rot = -90 if half == "L" else 90
        m = tp_module.rotate((0, 0, 0), (0, 0, 1), rot)
        m = m.translate((tx, ty, module_z))
        parts.append(m.val())

    combined = cq.Compound.makeCompound(parts)
    return cq.Workplane().add(combined)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-l", "--layout", type=Path, default=TOOLS_DIR / "keyboard.yaml")
    parser.add_argument("--pcb-step", type=Path, required=True, help="Bare PCB STEP from kicad-cli")
    parser.add_argument("-o", "--output", type=Path, default=BUILD_DIR / "atlas.step")
    args = parser.parse_args()

    tp_module_step = MODELS_DIR / "assembled_trackpoint.step"
    pcb_path = BUILD_DIR / "atlas.kicad_pcb"

    print("Building STEP assembly...")
    assembly = build_assembly(args.pcb_step, tp_module_step, pcb_path)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(assembly, str(args.output))
    size = os.path.getsize(args.output) / 1e6
    print(f"  → {args.output} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
