#!/usr/bin/env python3
"""Split a STEP file into left and right halves on the X axis.

Uses pythonocc-core (OpenCASCADE Python bindings) directly, which is
available in nixpkgs as python3Packages.pythonocc-core. No cadquery needed.

Usage:
    nix-shell -p 'python3.withPackages (ps: with ps; [ pythonocc-core ])' \\
        --run "python3 split_step.py [pcb.step]"

Outputs `<name>-left.step` and `<name>-right.step` alongside the input.
"""
import sys
from pathlib import Path

from OCC.Core.STEPControl import (
    STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs,
)
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib_Add
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common
from OCC.Core.gp import gp_Pnt

HERE = Path(__file__).resolve().parent
DEFAULT_STEP = HERE / "pcb.step"


def read_step(path: Path):
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise SystemExit(f"failed to read {path}")
    reader.TransferRoots()
    return reader.OneShape()


def bbox(shape):
    box = Bnd_Box()
    brepbndlib_Add(shape, box)
    return box.Get()  # (xmin, ymin, zmin, xmax, ymax, zmax)


def make_box(p1: tuple[float, float, float],
             p2: tuple[float, float, float]):
    return BRepPrimAPI_MakeBox(gp_Pnt(*p1), gp_Pnt(*p2)).Shape()


def write_step(shape, path: Path):
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    if w.Write(str(path)) != IFSelect_RetDone:
        raise SystemExit(f"failed to write {path}")


def main(step_path: Path):
    print(f"loading {step_path}")
    shape = read_step(step_path)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox(shape)
    cx = (xmin + xmax) / 2
    print(f"bbox: x [{xmin:.2f}, {xmax:.2f}]  center = {cx:.2f}")
    print(f"      y [{ymin:.2f}, {ymax:.2f}]  z [{zmin:.2f}, {zmax:.2f}]")

    pad = 50.0
    left_box = make_box(
        (xmin - pad, ymin - pad, zmin - pad),
        (cx,         ymax + pad, zmax + pad),
    )
    right_box = make_box(
        (cx,         ymin - pad, zmin - pad),
        (xmax + pad, ymax + pad, zmax + pad),
    )

    print("intersecting left half (this is the slow step)...")
    left = BRepAlgoAPI_Common(shape, left_box).Shape()
    print("intersecting right half...")
    right = BRepAlgoAPI_Common(shape, right_box).Shape()

    out_left  = step_path.with_name(step_path.stem + "-left.step")
    out_right = step_path.with_name(step_path.stem + "-right.step")
    print(f"writing {out_left.name}")
    write_step(left, out_left)
    print(f"writing {out_right.name}")
    write_step(right, out_right)
    print("done")


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STEP
    if not p.exists():
        raise SystemExit(f"no STEP at {p}")
    main(p)
