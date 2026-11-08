"""The populated PCB as 3D solids in the case frame, for fit checks and the viewer.

The board STEP is exported by kicad-cli from the .kicad_pcb whenever the board file is
newer than the export, so the checks always see the current board. KiCad's standard
3D models ship zipped (.stpZ) in nixpkgs while footprints ask for .step: the ones a
board uses are unpacked into <out>/3dmodels and kicad-cli is pointed there.
"""
from __future__ import annotations

import zipfile
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from build123d import Pos, Solid, import_step

from .kicad import FRAMES, Board

PCB_BOTTOM = -3.8          # case frame z of the PCB's underside (= bottom plate top face)
STD_VAR = "KICAD10_3DMODEL_DIR"


def _std_model_dir() -> Path | None:
    """KiCad's standard 3D model dir: from the environment, else the nix kicad-cli wrapper."""
    if os.environ.get(STD_VAR):
        return Path(os.environ[STD_VAR])
    cli = shutil.which("kicad-cli")
    if not cli:
        return None
    m = re.search(rf"{STD_VAR}=\$\{{{STD_VAR}-'([^']+)'", Path(cli).resolve().read_text(errors="ignore"))
    return Path(m.group(1)) if m else None


def _unpack_std_models(pcb: Path, out: Path) -> Path:
    """Copy the standard models `pcb` uses into out (as .step) and return out."""
    std = _std_model_dir()
    for rel in set(re.findall(r'\(model "\$\{' + STD_VAR + r'\}/([^"]+)"', pcb.read_text())):
        dst = out / rel
        if dst.exists() or std is None:
            continue
        src = std / rel
        z = src.with_suffix(".stpZ")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copyfile(src, dst)
        elif z.exists():                       # .stpZ: a zip holding the one .step
            with zipfile.ZipFile(z) as zf:
                dst.write_bytes(zf.read(zf.namelist()[0]))
    return out


def board_step(pcb: Path, out_dir: Path) -> Path:
    """Fresh STEP export of `pcb` (cached in out_dir until the board file changes)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    step = out_dir / (pcb.stem + ".step")
    if step.exists() and step.stat().st_mtime >= pcb.stat().st_mtime:
        return step
    models = _unpack_std_models(pcb, out_dir / "3dmodels")
    env = dict(os.environ, **{STD_VAR: str(models)})
    r = subprocess.run(["kicad-cli", "pcb", "export", "step", "--force", "-o", str(step), str(pcb)],
                       env=env, capture_output=True, text=True)
    missing = [l for l in (r.stdout + r.stderr).splitlines() if "Could not add 3D model" in l]
    if r.returncode or not step.exists():
        raise RuntimeError(f"kicad-cli STEP export failed for {pcb}:\n{r.stdout}{r.stderr}")
    if missing:
        print(f"  note: {pcb.name}: " + "; ".join(missing))
    return step


@dataclass
class Part:
    ref: str                  # footprint it belongs to (nearest footprint to the model's centre)
    model: str                # 3D model name, e.g. XIAO-nRF52840
    solids: list[Solid]


@dataclass
class PopulatedBoard:
    board: Solid              # the bare board, case frame
    parts: list[Part]         # one per footprint 3D model


def populated(board: Board, step: Path) -> PopulatedBoard:
    """The STEP's models in the case frame of board.half, PCB underside at PCB_BOTTOM.
    kicad-cli exports in board coordinates with y up (STEP Y = -KiCad Y), one top-level
    group per footprint model plus the board body."""
    root = import_step(str(step))
    body = max(root.solids(), key=lambda s: s.volume)
    bb = body.bounding_box()
    t = Pos(FRAMES[board.half].dx, 120.0, PCB_BOTTOM - bb.min.Z)
    parts = []
    for g in root.children:
        solids = [t * s for s in g.solids() if not s.is_same(body)]
        if not solids:
            continue
        c = (t * g).bounding_box().center()
        fp = min(board.footprints, key=lambda f: (f.x - c.X) ** 2 + (f.y - c.Y) ** 2)
        parts.append(Part(fp.ref, str(g.label), solids))
    return PopulatedBoard(t * body, parts)
