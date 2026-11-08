"""Cache for the case pipeline (build.py), under <out>/.cache: built parts with their exported
files, the populated PCB, reference compares and fit check results.

A part build is keyed by what it depends on, nothing more:

  code    sha256 of the part's module and every atlas_case module it imports, transitively
          (read from the import statements), plus this file, CACHE_VERSION and the
          build123d / OCP / Python versions. Editing checks.py or viewexport.py rebuilds no
          part; editing geom.py rebuilds all three; bottomplate.py only the bottom plate.
  params  repr() of the params object passed (None = the build function's default, which
          is part of its module's code).
  board   what the build actually read from the board. The build runs on a TrackedBoard,
          which records each read: the outline, the thickness, a footprint by ref
          (by_ref), the footprints of a kind (of_kind), or all footprints. The entry keeps
          those reads with a hash of their values, and a later build hits only when every
          recorded read gives the same value on the current board (a "verifying trace",
          as in Shake). The housing reads the outline, the switches and its screw holes,
          so moving a diode leaves it cached; the bottom plate reads the diodes and
          rebuilds. A build that took another path would have had to read something
          different first, so equal reads mean an equal build.

Shapes are stored as OCC binary BREP (exact, with the meshes the exports put on them) and
always used read back from that file, on a fresh build too: a hit and a miss hand the
checks the very same shape. Downstream keys (checks, compares, the 3MF) use the sha256 of
that file as the part's content id.

Every entry is written to a temp dir and renamed into place, so a killed build (the viewer
aborts stale builds) never leaves half an entry. A build whose source files change while it
runs isn't stored (its code key would be wrong).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from .kicad import Board, Footprint

CACHE_VERSION = 1
PKG = Path(__file__).resolve().parent
KEEP = 6                 # entries kept per part/half/variant (most recently used)


def _sha(*chunks) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(c if isinstance(c, bytes) else str(c).encode())
        h.update(b"\0")
    return h.hexdigest()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# --- code ---------------------------------------------------------------------------------

def _imports(name: str) -> set[str]:
    """atlas_case modules that atlas_case.<name> imports (anywhere in the file)."""
    tree = ast.parse((PKG / f"{name}.py").read_bytes())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            if n.level == 1:
                out |= {n.module.split(".")[0]} if n.module else {a.name for a in n.names}
            elif n.module and n.module.split(".")[0] == "atlas_case":
                parts = n.module.split(".")
                out |= {parts[1]} if len(parts) > 1 else {a.name for a in n.names}
        elif isinstance(n, ast.Import):
            out |= {a.name.split(".")[1] for a in n.names if a.name.startswith("atlas_case.")}
    return {m for m in out if (PKG / f"{m}.py").exists()}


def closure(*names: str) -> list[str]:
    """The modules plus everything they import from atlas_case, transitively, sorted."""
    seen, todo = set(), list(names)
    while todo:
        m = todo.pop()
        if m not in seen:
            seen.add(m)
            todo += _imports(m)
    return sorted(seen)


def code_key(*names: str) -> str:
    """Hash of the source of the modules' closure, this file and the library versions."""
    import build123d
    import OCP
    chunks = [CACHE_VERSION, build123d.__version__, getattr(OCP, "__version__", "?"),
              sys.version_info[:2], (PKG / "cache.py").read_bytes()]
    for m in closure(*names):
        chunks += [m, (PKG / f"{m}.py").read_bytes()]
    return _sha(*chunks)


# --- board reads --------------------------------------------------------------------------

class TrackedBoard(Board):
    """A Board that records which of its data the code reads (see the module doc)."""

    def __init__(self, board: Board):
        object.__setattr__(self, "reads", set())
        super().__init__(board.half, board.outline, board.footprints, board.thickness, board.mirrored)

    def __getattribute__(self, name):
        if name in ("outline", "footprints", "thickness"):
            object.__getattribute__(self, "reads").add(name)
        return object.__getattribute__(self, name)

    def by_ref(self, ref: str) -> Footprint:
        self.reads.add("ref:" + ref)
        return Board.by_ref(_Raw(self), ref)

    def of_kind(self, name: str) -> list[Footprint]:
        self.reads.add("kind:" + name)
        return Board.of_kind(_Raw(self), name)


class _Raw:
    """Untracked view of a TrackedBoard's footprints, for its own lookups."""

    def __init__(self, b):
        self.footprints = object.__getattribute__(b, "footprints")
        self.half = b.half


def read_value(board: Board, read: str) -> str:
    """Hash of what `read` (a TrackedBoard record) gives on `board`."""
    if read in ("outline", "footprints", "thickness"):
        v = getattr(board, read)
    elif read.startswith("ref:"):
        v = next((f for f in board.footprints if f.ref == read[4:]), "missing")
    elif read.startswith("kind:"):
        v = [f for f in board.footprints if f.name == read[5:]]
    else:
        raise ValueError(read)
    return _sha(repr(v))[:16]


def board_key(board: Board) -> str:
    """Hash of everything load_board read (for keys that depend on the whole board)."""
    return _sha(repr(board))[:16]


# --- shapes -------------------------------------------------------------------------------

def write_shape(shape, path: Path) -> None:
    from OCP.BinTools import BinTools
    if not BinTools.Write_s(shape.wrapped, str(path)):
        raise OSError(f"could not write {path}")


def read_shape(path: Path, cls: str = "Compound"):
    """A shape written by write_shape, as the build123d class it had (Part, Solid, ...)."""
    import build123d
    from build123d import Shape
    from OCP.BinTools import BinTools
    from OCP.TopoDS import TopoDS_Shape
    obj = TopoDS_Shape()
    if not BinTools.Read_s(obj, str(path)):
        raise OSError(f"could not read {path}")
    shape = Shape.cast(obj)                   # downcast to the TopoDS type (solid, compound)
    k = getattr(build123d, cls, None)
    return k(shape.wrapped) if k is not None and k is not type(shape) else shape


# --- store --------------------------------------------------------------------------------

class Entry:
    """A finished cache entry: a directory of files plus meta.json."""

    def __init__(self, path: Path):
        self.path = path
        self.meta = json.loads((path / "meta.json").read_text())

    def file(self, name: str) -> Path:
        return self.path / name

    def touch(self) -> None:
        try:
            os.utime(self.path / "meta.json")
        except OSError:
            pass


class Store:
    def __init__(self, root: Path, enabled: bool = True):
        """enabled=False (build.py --no-cache): nothing is looked up or committed; entries
        stay temp dirs under root, which the caller removes."""
        self.root = root
        self.enabled = enabled
        root.mkdir(parents=True, exist_ok=True)
        if enabled:
            self._clean_tmp()

    def _clean_tmp(self) -> None:
        # temp dirs of killed builds (the viewer aborts stale ones); live builds' are fresh
        for d in self.root.glob("**/.tmp-*"):
            try:
                if time.time() - d.stat().st_mtime > 3600:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

    # A slot is a directory of entries for one thing (e.g. "parts/left-bottomplate/<code>");
    # entries in it are told apart by their meta (e.g. board reads).
    def find(self, slot: str, accept=lambda meta: True) -> Entry | None:
        if not self.enabled:
            return None
        d = self.root / slot
        if not d.is_dir():
            return None
        for e in sorted(d.iterdir(), key=lambda p: -_mtime(p / "meta.json")):
            if e.name.startswith(".") or not (e / "meta.json").exists():
                continue
            try:
                entry = Entry(e)
            except (OSError, ValueError):
                continue
            if accept(entry.meta):
                entry.touch()
                return entry
        return None

    def new(self, slot: str) -> Path:
        """A temp dir to fill; commit() it when complete."""
        d = self.root / slot / f".tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        d.mkdir(parents=True)
        return d

    def commit(self, tmp: Path, name: str, meta: dict) -> Entry:
        (tmp / "meta.json").write_text(json.dumps(meta, indent=1))
        final = tmp.parent / name
        if not self.enabled:
            return Entry(tmp)
        try:
            tmp.rename(final)
        except OSError:              # another process stored the same entry meanwhile
            shutil.rmtree(tmp, ignore_errors=True)
        self._prune(final.parent.parent)
        return Entry(final)

    def _prune(self, group: Path) -> None:
        """Keep the KEEP most recently used entries of a part/half/variant over all code keys."""
        entries = [e for c in group.iterdir() if c.is_dir() for e in c.iterdir()
                   if not e.name.startswith(".") and (e / "meta.json").exists()]
        for e in sorted(entries, key=lambda p: -_mtime(p / "meta.json"))[KEEP:]:
            shutil.rmtree(e, ignore_errors=True)
        for c in group.iterdir():
            if c.is_dir() and not any(c.iterdir()):
                c.rmdir()

    # Small JSON values (check results, compares): one file per key.
    def get_json(self, slot: str, key: str):
        if not self.enabled:
            return None
        f = self.root / slot / f"{key}.json"
        try:
            v = json.loads(f.read_text())
            os.utime(f)
            return v
        except (OSError, ValueError):
            return None

    def put_json(self, slot: str, key: str, value) -> None:
        if not self.enabled:
            return
        d = self.root / slot
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        tmp.write_text(json.dumps(value))
        tmp.replace(d / f"{key}.json")
        files = sorted(d.glob("*.json"), key=lambda p: -_mtime(p))
        for f in files[KEEP * 4:]:
            f.unlink(missing_ok=True)


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def put_file(src: Path, dst: Path) -> None:
    """Copy src to dst atomically (a viewer or slicer never reads half a file)."""
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    shutil.copyfile(src, tmp)
    tmp.replace(dst)
