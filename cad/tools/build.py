"""Build the Atlas case from the KiCad boards, export it, and check it.

    case                 # devenv script: build all parts, both halves, run checks
    case --part housing  # one part
    case --no-ref        # skip the comparison with the reference STEPs in cad/

Each part is built for the LEFT half. The right half runs the same code on the
right board mirrored into the left orientation, and the result is mirrored back.

Outputs go to cad/out/ (gitignored): STEP and STL per part and half.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from build123d import Plane, export_step, export_stl, mirror

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import importlib  # noqa: E402

from atlas_case.compare import compare  # noqa: E402
from atlas_case.kicad import MIRROR_X, load_board, mirrored  # noqa: E402

REPO = HERE.parent.parent
# A part is a module atlas_case/<name>.py with a function build_<name>(board) -> Part (left half).
PART_NAMES = ["housing", "switchplate", "bottomplate"]
PARTS = {}
for _n in PART_NAMES:
    try:
        PARTS[_n] = getattr(importlib.import_module(f"atlas_case.{_n}"), f"build_{_n}")
    except ModuleNotFoundError as e:
        if e.name != f"atlas_case.{_n}":
            raise


def build(part: str, half: str):
    board = load_board(REPO / "pcb" / f"atlas-{half}.kicad_pcb", half)
    if half == "left":
        return PARTS[part](board)
    return mirror(PARTS[part](mirrored(board)), Plane.YZ.offset(MIRROR_X / 2))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", choices=sorted(PARTS), action="append")
    ap.add_argument("--half", choices=["left", "right"], action="append")
    ap.add_argument("--no-ref", action="store_true", help="don't compare with cad/atlas-*-<part>.step")
    ap.add_argument("--out", default=str(REPO / "cad" / "out"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    failed = 0
    for part in a.part or sorted(PARTS):
        for half in a.half or ["left", "right"]:
            t = time.time()
            shape = build(part, half)
            stem = out / f"atlas-{half}-{part}"
            export_step(shape, str(stem) + ".step")
            export_stl(shape, str(stem) + ".stl", tolerance=0.01, angular_tolerance=0.1)
            line = f"{half:5} {part:12} built in {time.time() - t:4.1f} s -> {stem.name}.step/.stl"
            ref = REPO / "cad" / f"atlas-{half}-{part}.step"
            if not a.no_ref and ref.exists():
                d = compare(f"{half} {part}", shape, str(ref))
                failed += not d.ok()
                line += "\n      " + str(d).replace("\n", "\n      ")
            print(line, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
