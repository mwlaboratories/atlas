#!/usr/bin/env python3
"""Build the complete trackpoint module STEP: sensor + companion board.

Reads dimensions from keyboard.yaml, combines sensor STEP with a modeled
companion board, outputs a single STEP file.

Usage:
    python3 tools/case/tp_assembly.py -l tools/keyboard.yaml
"""
import argparse
from pathlib import Path

import cadquery as cq
import yaml

TOOLS_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = TOOLS_DIR / "3dmodels"


def build_trackpoint_module(layout: dict) -> cq.Workplane:
    """Combine sensor STEP + companion board into one module.

    From the datasheet (SK8707-01):
      Sensor PCB:      18.29mm tall (Y), 23mm wide (X with bracket tabs)
      Companion board: 14.5mm tall, 23mm wide, 0.3mm thick
      Total assembly:  29.55mm tall
      Overlap:         18.29 + 14.5 - 29.55 = 3.24mm

    The companion board lies on the bracket side of the sensor (top),
    extending from the pin connector edge, overlapping 3.24mm.
    Its top face is against the sensor bracket top face.
    """
    tp = layout["trackpoint"]

    # Load sensor, center on stick
    sensor_file = MODELS_DIR / tp["sensor_step"]
    sensor = cq.importers.importStep(str(sensor_file))
    stick_y = tp.get("stick_offset_y", 1.70)
    sensor = sensor.translate((0, -stick_y, 0))

    sbb = sensor.val().BoundingBox()
    print(f"  Sensor bbox: X({sbb.xmin:.1f}..{sbb.xmax:.1f}) "
          f"Y({sbb.ymin:.1f}..{sbb.ymax:.1f}) Z({sbb.zmin:.1f}..{sbb.zmax:.1f})")

    # Companion board dimensions from config
    cb = tp["companion_board"]
    comp_w = cb["width"]       # 23mm
    comp_h = cb["height"]      # 14.5mm
    comp_t = cb["thickness"]   # 0.3mm

    # Overlap calculation
    sensor_height = 18.29  # from datasheet
    total_height = 29.55   # from datasheet
    overlap = sensor_height + comp_h - total_height  # 3.24mm

    # Companion board position (in sensor-centered coords):
    #   Y: connector/pin edge is at sbb.ymin (-10.8), companion overlaps
    #      inward by 3.24mm, extends outward in -Y
    #      companion center Y = sbb.ymin + overlap - comp_h/2
    #   Z: co-planar with sensor PCB base (z ≈ 0), companion top face
    #      against sensor PCB underside
    # sbb.ymin includes the serrated pins which extend past the PCB edge.
    # The actual PCB edge is ~1mm inward from ymin. Use sensor_height/2 instead.
    sensor_pcb_edge = -sensor_height / 2  # -9.145 (bottom edge of sensor PCB body)
    comp_y = sensor_pcb_edge - comp_h / 2 + overlap
    comp_z = sbb.zmin - comp_t / 2

    print(f"  Companion: {comp_w}x{comp_h}x{comp_t}mm, overlap={overlap:.2f}mm")
    print(f"  Companion center: Y={comp_y:.1f}, Z={comp_z:.1f}")

    companion = cq.Workplane("XY").box(comp_w, comp_h, comp_t)
    companion = companion.translate((0, comp_y, comp_z))

    # Combine into single compound
    combined = cq.Compound.makeCompound([sensor.val(), companion.val()])
    return cq.Workplane().add(combined)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-l", "--layout", type=Path, default=TOOLS_DIR / "keyboard.yaml")
    args = parser.parse_args()

    layout = yaml.safe_load(args.layout.read_text())

    print("Building trackpoint module...")
    module = build_trackpoint_module(layout)

    out = MODELS_DIR / "trackpoint_module.step"
    cq.exporters.export(module, str(out))
    print(f"  → {out}")


if __name__ == "__main__":
    main()
