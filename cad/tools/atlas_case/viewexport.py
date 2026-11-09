"""Files for the web viewer (cad/tools/viewer): GLB meshes per part and half, the populated
PCB, and view.json, which lists them and bumps a version so open pages reload.

Layout under <out>/view/:
  <half>-<part>.glb      housing, switchplate, bottomplate
  <half>-pcb.glb         bare board
  <half>-parts.glb       PCB components (sockets, diodes, XIAO, ...)
  view.json              {"version", "halves": {half: {"parts": [...], "checks": [...] | null,
                          "skipped": [...], "built": "HH:MM:SS"}}}

Both halves build in parallel processes (build.py), so view.json is updated under a file
lock, and every file is written to a temp name and renamed: a polling page never reads half
a file, and a failed build leaves the last good meshes in place.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

from build123d import Compound, export_gltf

from .pcb3d import PopulatedBoard

# stack order, top to bottom; the viewer draws pair views between neighbours from this
STACK = ["housing", "switchplate", "cutop", "pcb", "cubot", "parts", "bottomplate"]


def _baked(shape):
    """The shape with its placement applied to the geometry. glTF applies a node's
    translation after build123d's z-up -> y-up rotation, so a shape that carries a
    Location (the populated PCB from its STEP) would land 120 mm off and mirrored."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.TopLoc import TopLoc_Location
    w = shape.wrapped
    if w.Location().IsIdentity():
        return shape
    return Compound(BRepBuilderAPI_Transform(w.Located(TopLoc_Location()), w.Location().Transformation(), True).Shape())


def glb(shape, path: Path, tol: float = 0.02, angular: float = 0.1) -> None:
    # angular 0.05 rad: on long gentle curves (the thumb wall) a coarse angle gives 18 mm
    # fans that lean off the surface and shade as streaks
    tmp = path.with_name(f".{path.stem}.{os.getpid()}.tmp.glb")
    export_gltf(_baked(shape), str(tmp), binary=True, linear_deflection=tol, angular_deflection=angular)
    tmp.replace(path)


def pcb_glbs(pcb: PopulatedBoard, d: Path, pads=None) -> dict[str, Path]:
    """Meshes of the bare board, its components and (if given) its pads into d:
    {"pcb": path, "parts": path[, "pads": path]}."""
    glb(pcb.board, d / "pcb.glb", tol=0.05, angular=0.3)     # positions only: coarse is fine
    glb(Compound([s for p in pcb.parts for s in p.solids]), d / "parts.glb", tol=0.1, angular=0.5)
    out = {"pcb": d / "pcb.glb", "parts": d / "parts.glb"}
    if pads is not None:
        for name, shp in zip(("cutop", "cubot"), pads):
            if shp is not None:
                glb(shp, d / f"{name}.glb", tol=0.02, angular=0.3)
                out[name] = d / f"{name}.glb"
    return out


@contextmanager
def _manifest(out: Path):
    """view.json for read-modify-write, locked against the other half's process."""
    out.mkdir(parents=True, exist_ok=True)
    with open(out / ".view.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            m = json.loads((out / "view.json").read_text())
        except (OSError, ValueError):
            m = {"version": 0, "halves": {}}
        yield m
        m["version"] = time.time()
        tmp = out / f".view.json.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(m, indent=1))
        tmp.replace(out / "view.json")        # atomic: a polling page never reads half a file


def publish(out: Path, half: str, files: dict[str, Path]) -> None:
    """Show one half's meshes: files = {name: glb} (parts, "pcb", "parts"), copied into
    <out>/view; its checks are cleared until export_checks reports them."""
    out = out / "view"
    out.mkdir(parents=True, exist_ok=True)
    for name, f in files.items():
        dst = out / f"{half}-{name}.glb"
        tmp = out / f".{half}-{name}.{os.getpid()}.tmp.glb"
        shutil.copyfile(f, tmp)
        tmp.replace(dst)
    with _manifest(out) as m:
        m["halves"][half] = {"parts": [n for n in STACK if n in files], "checks": None,
                             "built": time.strftime("%H:%M:%S")}


def export_checks(out: Path, half: str, results: list, skipped: tuple = ()) -> None:
    """The half's check results; `skipped` names the checks a fast build left out."""
    with _manifest(out / "view") as m:
        h = m["halves"].setdefault(half, {"parts": []})
        h["checks"] = [r.__dict__ for r in results]
        h["skipped"] = list(skipped)
