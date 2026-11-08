"""Build the Atlas case from the KiCad boards, export it, and check it.

    cad                  # devenv script: build all parts, both halves, run all checks
    cad --fast           # for iterating: no reference compare, no slow print-quality checks
    cad --half left      # one half (repeatable); --part housing: one part
    cad --no-ref         # skip the comparison with the reference STEPs in cad/
    cad --no-check       # skip the fit checks (atlas_case/checks.py)
    cad --no-cache       # build everything from scratch (and don't touch the cache)
    cad --print          # also write print STLs and cad/out/atlas-case.3mf (Bambu)
    cad view             # web viewer on localhost, rebuilding on every change (view.py)

Each part is built for the LEFT half. The right half runs the same code on the right board
mirrored into the left orientation, and the result is mirrored back.

The halves build in parallel processes (OCC isn't thread-safe), and so do the reference
builds in a full run; each job's output is buffered and printed in order. Built parts,
the populated board, compares and check results are cached in <out>/.cache: a part is
rebuilt only when its code, its params or the board data it reads change (atlas_case/
cache.py), so moving a diode rebuilds the bottom plate (and the switch plate, which reads
every footprint) but not the housing.

Outputs go to cad/out/ (gitignored): STEP and STL per part and half, the populated board
STEP (cad/out/pcb/), the fit check results (checks-<half>.json) and the viewer's meshes
(cad/out/view/). Exit status is 1 when a part differs from its reference, an exported STEP
doesn't read back as one solid, or a fit check fails; 2 when a build raised an error.
"""
from __future__ import annotations

import argparse
import importlib
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import time
import traceback
import concurrent.futures
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Only light modules up here: the part modules are imported inside the jobs, after their
# cache keys are taken from the source files (see part_entry).
from atlas_case import cache  # noqa: E402
from atlas_case.cache import put_file, read_shape  # noqa: E402

REPO = HERE.parent.parent
# A part is a module atlas_case/<name>.py with a function build_<name>(board) -> Part (left half).
# A module may define REFERENCE_PARAMS: the parameters that reproduce the reference STEP,
# when the exported part deliberately differs from it (e.g. print tweaks).
PART_NAMES = [n for n in ["housing", "switchplate", "bottomplate"] if (HERE / "atlas_case" / f"{n}.py").exists()]
STL_TOL = dict(tolerance=0.005, angular_tolerance=0.03)


@dataclass(frozen=True)
class Opts:
    out: Path
    pcb_dir: Path
    parts: tuple
    check: bool = True
    skip: tuple = ()              # checks left out (checks.FAST_SKIP in fast mode)
    cache: bool = True            # False: entries go to cache_dir, a temp dir, and nothing is looked up
    cache_dir: Path | None = None
    print: bool = False


def _module(part: str):
    return importlib.import_module(f"atlas_case.{part}")


def reference_params(part: str):
    return getattr(_module(part), "REFERENCE_PARAMS", None)


# --- building, through the cache -------------------------------------------------------------

def part_entry(store: cache.Store, part: str, half: str, board, params=None, export: bool = True,
               code: str | None = None):
    """The cache entry of one built part (built now if not cached): shape.brep, and with
    `export` part.step, part.stl, view.glb. Returns (entry, hit). `code` is the part's
    cache.code_key, taken before its module was first imported (default: now)."""
    from atlas_case.kicad import mirrored
    code = code or cache.code_key(part)
    variant = "" if params is None else "-ref"
    slot = f"parts/{half}-{part}{variant}/{cache._sha(code, repr(params))[:16]}"
    b = board if half == "left" else mirrored(board)
    seen = {}

    def same_board(meta):
        return meta["export"] >= export and all(
            seen.setdefault(r, cache.read_value(b, r)) == v for r, v in meta["reads"].items())
    e = store.find(slot, same_board)
    if e is not None:
        return e, True

    from build123d import Plane, export_step, export_stl, import_step, mirror
    from atlas_case import viewexport
    from atlas_case.kicad import MIRROR_X
    fn = getattr(_module(part), f"build_{part}")
    tracked = cache.TrackedBoard(b)
    t = time.time()
    shape = fn(tracked) if params is None else fn(tracked, params)
    if half == "right":
        shape = mirror(shape, Plane.YZ.offset(MIRROR_X / 2))
    meta = {"reads": {r: cache.read_value(b, r) for r in sorted(tracked.reads)},
            "type": type(shape).__name__, "built_in": round(time.time() - t, 2), "export": export}
    tmp = store.new(slot)
    if export:
        export_step(shape, str(tmp / "part.step"))
        export_stl(shape, str(tmp / "part.stl"), **STL_TOL)
        # what slicers and CAD tools will see: the STEP must read back as one solid
        meta["step_solids"] = len(import_step(str(tmp / "part.step")).solids())
        viewexport.glb(shape, tmp / "view.glb")
    cache.write_shape(shape, tmp / "shape.brep")     # after the exports: with their meshes
    meta["content"] = cache.file_sha(tmp / "shape.brep")
    if not store.enabled or cache.code_key(part) != code:
        return _uncommitted(tmp, meta), False
    name = cache._sha(json.dumps(meta["reads"], sort_keys=True), export)[:16]
    return store.commit(tmp, name, meta), False


def _uncommitted(tmp: Path, meta: dict) -> cache.Entry:
    """An entry used for this run only (no cache, or the sources changed while building);
    its temp dir is cleaned up by a later run."""
    (tmp / "meta.json").write_text(json.dumps(meta))
    return cache.Entry(tmp)


def part_shape(e: cache.Entry):
    return read_shape(e.file("shape.brep"), e.meta["type"])


def pcb_entry(store: cache.Store, board, step: Path, pads_step: Path | None = None) -> cache.Entry:
    """The populated board (pcb3d.populated, ~50 s of STEP import), cached by the STEP's
    content and the board: shape.brep (the board, then every component solid), pcb.glb,
    parts.glb."""
    from atlas_case import pcb3d, viewexport
    slot = f"pcb/{board.half}/{cache.code_key('pcb3d', 'viewexport')[:16]}"
    sha, bk = cache.file_sha(step), cache.board_key(board)
    psha = cache.file_sha(pads_step) if pads_step else None
    e = store.find(slot, lambda m: m["step"] == sha and m["board"] == bk and m.get("pads") == psha)
    if e is None:
        from build123d import Compound
        pcb = pcb3d.populated(board, step)
        tmp = store.new(slot)
        viewexport.pcb_glbs(pcb, tmp, pcb3d.pads(board, pads_step) if pads_step else None)
        cache.write_shape(Compound([pcb.board] + [s for p in pcb.parts for s in p.solids]), tmp / "shape.brep")
        meta = {"step": sha, "board": bk, "pads": psha, "parts": [(p.ref, p.model, len(p.solids)) for p in pcb.parts],
                "content": cache.file_sha(tmp / "shape.brep")}
        e = store.commit(tmp, cache._sha(sha, bk, psha or "")[:16], meta) if store.enabled else _uncommitted(tmp, meta)
    return e


def pcb_shape(e: cache.Entry):
    """The PopulatedBoard of a pcb_entry, read back (as the parts are, on a fresh build too)."""
    from build123d import Shape
    from OCP.TopoDS import TopoDS_Iterator
    from atlas_case import pcb3d
    root = read_shape(e.file("shape.brep"))
    it, kids = TopoDS_Iterator(root.wrapped), []
    while it.More():
        kids.append(Shape.cast(it.Value()))
        it.Next()
    parts, i = [], 1
    for ref, model, n in e.meta["parts"]:
        parts.append(pcb3d.Part(ref, model, kids[i:i + n]))
        i += n
    return pcb3d.PopulatedBoard(kids[0], parts)


# --- jobs, each in a worker process -----------------------------------------------------------
# Per half: "parts" and "pcb" (kicad-cli export + populated board) run side by side, and in a
# full run "ref" (reference builds + compare); once parts and pcb are done the parent shows
# the meshes and starts the check groups. Jobs return plain dicts (they cross processes).

def parts_job(half: str, o: Opts) -> dict:
    """Build (or fetch) and export one half's parts: {"parts": {part: line}, "failed": n,
    "entries": {part: entry dir}}."""
    from atlas_case.kicad import load_board
    store = cache.Store(o.cache_dir, o.cache)
    board = load_board(o.pcb_dir / f"atlas-{half}.kicad_pcb", half)
    rep = {"parts": {}, "failed": 0, "entries": {}}
    for part in o.parts:
        t = time.time()
        e, hit = part_entry(store, part, half, board)
        stem = o.out / f"atlas-{half}-{part}"
        put_file(e.file("part.step"), stem.with_suffix(".step"))
        put_file(e.file("part.stl"), stem.with_suffix(".stl"))
        how = (f"cached      ({e.meta['built_in']:4.1f} s to build)" if hit
               else f"built in {time.time() - t:4.1f} s")
        line = f"{half:5} {part:12} {how} -> {stem.name}.step/.stl"
        if e.meta["step_solids"] != 1:
            rep["failed"] += 1
            line += f"\n      FAIL step read-back: {e.meta['step_solids']} solids (want 1)"
        rep["parts"][part] = line
        rep["entries"][part] = str(e.path)
    if o.print:
        print_stls(rep, half, o.out)
    return rep


def print_stls(rep: dict, half: str, out: Path) -> None:
    """Plain STLs, one per part, already in print orientation (Bambu Studio slices these
    correctly; it warns on the hand-made 3MF and once closed screw holes). Made once per
    cached part, with its validity."""
    from build123d import export_stl
    from atlas_case import printfiles
    d = out / "print"
    d.mkdir(exist_ok=True)
    rep["print"], rep["valid"] = [], {}
    for name, path in rep["entries"].items():
        e = cache.Entry(Path(path))
        f, v = e.file("print.stl"), e.file("valid.json")
        shape = None
        if not v.exists():
            shape = part_shape(e)
            _atomic_write(v, json.dumps(shape.is_valid))
        valid = rep["valid"][name] = json.loads(v.read_text())
        if not valid:
            rep["print"].append(f"  skipped {half}-{name}: invalid solid (see the 'one solid' check)")
            rep["failed"] += 1
            continue
        if not f.exists():
            tmp = e.path / f".print.{os.getpid()}.stl"
            export_stl(printfiles.oriented(name, shape or part_shape(e)), str(tmp), **STL_TOL)
            tmp.replace(f)
        put_file(f, d / f"atlas-{half}-{name}.stl")


def _atomic_write(f: Path, text: str) -> None:
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.replace(f)


def pcb_job(half: str, o: Opts) -> dict:
    """The populated board of one half: {"entry": dir}."""
    from atlas_case import pcb3d
    from atlas_case.kicad import load_board
    store = cache.Store(o.cache_dir, o.cache)
    pcb_file = o.pcb_dir / f"atlas-{half}.kicad_pcb"
    board = load_board(pcb_file, half)
    # kicad-cli exports into a dir named by the board file's content, so it runs once per
    # board revision (board_step's own check goes by mtime, which a copied or checked-out
    # board can fool). kicad-cli writes through a fixed temp name ($tempfile$.step), so each
    # export has its own dir, as cwd too; the STEP is copied out to <out>/pcb/.
    d = o.out / "pcb" / f".{half}-{cache.file_sha(pcb_file)[:16]}"
    d.mkdir(parents=True, exist_ok=True)
    os.utime(d)
    done = d / f"{pcb_file.stem}.step"
    if done.exists():
        os.utime(done)                         # this content's export: tell board_step it's fresh
    os.chdir(d)
    step = pcb3d.board_step(pcb_file, d)
    put_file(step, o.out / "pcb" / step.name)
    try:                                       # viewer only: where the copper pads are
        pstep = pcb3d.pads_step(pcb_file, d)
    except RuntimeError as e:
        print(f"  note: no pads in the viewer: {str(e).splitlines()[0]}")
        pstep = None
    old = sorted(d.parent.glob(f".{half}-*"), key=lambda p: -p.stat().st_mtime)
    for x in old[3:]:                          # keep the last few revisions' exports
        shutil.rmtree(x, ignore_errors=True)
    return {"entry": str(pcb_entry(store, board, step, pstep).path)}


def checks_job(half: str, o: Opts, names: tuple, entries: dict, pcb_dir: str) -> dict:
    """Some checks on one half: {"results": {check: [Result dicts]}, "ran": [names run]}.
    Each check's results are cached apart, keyed by the parts' content ids, the board and
    the populated board, so a fast run's results serve a later full run too."""
    code = cache.code_key("checks", "speedups")             # before the modules are imported
    from atlas_case.kicad import load_board
    store = cache.Store(o.cache_dir, o.cache)
    board = load_board(o.pcb_dir / f"atlas-{half}.kicad_pcb", half)
    es = {p: cache.Entry(Path(d)) for p, d in entries.items()}
    pe = cache.Entry(Path(pcb_dir))
    base = cache._sha(code, sorted((p, e.meta["content"]) for p, e in es.items()),
                      cache.board_key(board), pe.meta["content"])
    out, todo = {}, []
    for n in names:
        saved = store.get_json(f"checks/{half}", cache._sha(base, n)[:32])
        if saved is None:
            todo.append(n)
        else:
            out[n] = saved
    if todo:
        from atlas_case import checks
        from atlas_case.speedups import classifier_per_shape
        parts = {p: part_shape(e) for p, e in es.items()}
        pcb = pcb_shape(pe)
        with classifier_per_shape():
            for n in todo:
                out[n] = [r.__dict__ for r in checks.run(parts, board, pcb, only=(n,))]
                store.put_json(f"checks/{half}", cache._sha(base, n)[:32], out[n])
    return {"results": out, "ran": todo}


def ref_job(half: str, o: Opts) -> dict:
    """The reference compare of one half's parts: {"parts": {part: lines}, "failed": n}."""
    from atlas_case.kicad import load_board
    store = cache.Store(o.cache_dir, o.cache)
    board = load_board(o.pcb_dir / f"atlas-{half}.kicad_pcb", half)
    rep = {"parts": {}, "failed": 0}
    for part in o.parts:
        ref = REPO / "cad" / f"atlas-{half}-{part}.step"
        if not ref.exists():
            continue
        code = cache.code_key(part)                # before reference_params imports the module
        params = reference_params(part)
        e, _ = part_entry(store, part, half, board, params, export=False, code=code)
        key = cache._sha(cache.code_key("compare"), e.meta["content"], cache.file_sha(ref))[:32]
        d = store.get_json("compare", key)
        if d is None:
            from atlas_case.compare import compare
            diff = compare(f"{half} {part}", part_shape(e), str(ref))
            d = {"ok": diff.ok(), "text": str(diff)}
            store.put_json("compare", key, d)
        lines = []
        if params is not None:
            lines.append("(checked as drawn in Fusion; the export differs on purpose, see REFERENCE_PARAMS)")
        lines.append(d["text"].replace("\n", "\n      "))
        rep["parts"][part] = "".join("\n      " + s for s in lines)
        rep["failed"] += not d["ok"]
    return rep


JOBS = {"parts": parts_job, "pcb": pcb_job, "checks": checks_job, "ref": ref_job}


def _run_job(kind: str, half: str, o: Opts, *args) -> dict:
    """A job with its output (also OCC's, written to fd 1/2 from C++) captured, and any
    exception turned into an "error" entry, so the parent prints each half in one piece."""
    t, cwd = time.time(), os.getcwd()
    with tempfile.TemporaryFile("w+") as buf:
        sys.stdout.flush(), sys.stderr.flush()
        saved = os.dup(1), os.dup(2)
        os.dup2(buf.fileno(), 1), os.dup2(buf.fileno(), 2)
        try:
            rep = JOBS[kind](half, o, *args)
        except Exception:
            rep = {"error": traceback.format_exc()}
        finally:
            os.chdir(cwd)                          # pool workers are reused
            sys.stdout.flush(), sys.stderr.flush()
            os.dup2(saved[0], 1), os.dup2(saved[1], 2)
            os.close(saved[0]), os.close(saved[1])
        buf.seek(0)
        rep["log"] = buf.read()
    rep["time"] = time.time() - t
    return rep


# --- main -------------------------------------------------------------------------------------

# Check groups that each get a worker process (the slow ones, ~14 s each on the left half);
# the other checks share one (~11 s).
CHECK_GROUPS = (("components_clear",), ("thin_walls",))


def check_groups(names: tuple) -> list[tuple]:
    alone = [g for g in CHECK_GROUPS if g[0] in names]
    rest = tuple(n for n in names if all(n not in g for g in alone))
    return [g for g in [rest] + alone if g]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", choices=sorted(PART_NAMES), action="append")
    ap.add_argument("--half", choices=["left", "right"], action="append")
    ap.add_argument("--fast", action="store_true",
                    help="iterate quickly: --no-ref, and skip the slow print-quality checks (checks.FAST_SKIP)")
    ap.add_argument("--no-ref", action="store_true", help="don't compare with cad/atlas-*-<part>.step")
    ap.add_argument("--no-check", action="store_true", help="skip the fit checks")
    ap.add_argument("--no-cache", action="store_true", help="build everything; don't read or write the cache")
    ap.add_argument("--print", action="store_true", help="write print STLs and cad/out/atlas-case.3mf")
    ap.add_argument("--first", choices=["left", "right"],
                    help="the half wanted first (the viewer's shown half): the other runs at lower priority")
    ap.add_argument("--out", default=str(REPO / "cad" / "out"))
    ap.add_argument("--pcb-dir", default=str(REPO / "pcb"), help="where atlas-{left,right}.kicad_pcb are")
    a = ap.parse_args()
    t0 = time.time()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    halves = a.half or ["left", "right"]
    from atlas_case import checks, viewexport
    skip = checks.FAST_SKIP if a.fast else ()
    names = tuple(n for n in checks.ORDER if n not in skip) if not a.no_check else ()
    with_ref = not (a.no_ref or a.fast)
    if a.fast:
        print(f"fast mode: no reference compare; checks skipped: {', '.join(skip)} (`cad` runs everything)",
              flush=True)
    cache_dir = Path(tempfile.mkdtemp(prefix="atlas-nocache-")) if a.no_cache else out / ".cache"
    for d in (out, out / "view", out / "print"):     # temp files of builds the viewer aborted
        for f in d.glob(".*.tmp*"):
            if time.time() - f.stat().st_mtime > 600:
                f.unlink(missing_ok=True)
    pcb_dir = Path(a.pcb_dir).resolve()

    o = Opts(out=out, pcb_dir=pcb_dir, parts=tuple(sorted(a.part or PART_NAMES)), check=bool(names),
             skip=skip, cache=not a.no_cache, cache_dir=cache_dir, print=a.print)
    kinds = ("parts", "pcb") + (("ref",) if with_ref else ())
    # a pool per half; with --first the other half's workers run at lower priority
    workers = max(len(kinds), len(check_groups(names)))
    pools = {h: ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn"),
                                    initializer=os.nice, initargs=(10 if a.first not in (None, h) else 0,))
             for h in halves}
    futs = {(k, h): pools[h].submit(_run_job, k, h, o) for h in halves for k in kinds}
    shown, groups = set(), {h: check_groups(names) for h in halves}

    def advance():
        """Start what has become possible: meshes and checks once parts and pcb are done."""
        for h in halves:
            if h in shown or not all(futs[(k, h)].done() for k in ("parts", "pcb")):
                continue
            shown.add(h)
            p, b = futs[("parts", h)].result(), futs[("pcb", h)].result()
            if "error" in p or "error" in b:
                continue                       # keep the last good meshes
            pe = cache.Entry(Path(b["entry"]))
            viewexport.publish(out, h, {**{n: Path(d) / "view.glb" for n, d in p["entries"].items()},
                                        "pcb": pe.file("pcb.glb"), "parts": pe.file("parts.glb"),
                                        **({"pads": pe.file("pads.glb")} if (pe.path / "pads.glb").exists() else {})})
            for g in groups[h]:
                futs[("checks", h, g)] = pools[h].submit(_run_job, "checks", h, o, g, p["entries"], b["entry"])

    def wait(keys):
        while not all(k in futs and futs[k].done() for k in keys):
            pending = [f for f in futs.values() if not f.done()]
            if not pending:
                break
            concurrent.futures.wait(pending, timeout=1.0, return_when=concurrent.futures.FIRST_COMPLETED)
            advance()
        advance()

    failed, errors, printable, times = 0, 0, [], []
    for h in halves:
        wait([(k, h) for k in kinds])
        wait([("checks", h, g) for g in groups[h] if ("checks", h, g) in futs])
        reps = {k: futs[(k, h)].result() for k in ("parts", "pcb", "ref") if (k, h) in futs}
        reps.update({("checks", g): futs[("checks", h, g)].result() for g in groups[h] if ("checks", h, g) in futs})
        for r in reps.values():
            if r.get("log", "").strip():
                print(r["log"].rstrip())
        for k, r in reps.items():
            if "error" in r:
                errors += 1
                what = k if isinstance(k, str) else "checks " + ", ".join(k[1])
                print(f"ERROR {h} {what}: {r['error'].strip().splitlines()[-1]}\n{r['error'].rstrip()}")
        p, ref = reps["parts"], reps.get("ref", {"parts": {}})
        for part in o.parts:
            line = p.get("parts", {}).get(part)
            if line is not None or part in ref.get("parts", {}):
                print((line or f"{h:5} {part:12} (not built)") + ref.get("parts", {}).get(part, ""))
        failed += p.get("failed", 0) + ref.get("failed", 0)
        cr = [r for k, r in reps.items() if not isinstance(k, str)]
        if names and cr and not any("error" in r for r in cr):
            ran = sum(len(r["ran"]) for r in cr)
            failed += report_checks(h, out, names, {n: v for r in cr for n, v in r["results"].items()}, skip,
                                    f"in {max(r['time'] for r in cr):4.1f} s" + (f" ({len(names) - ran} of {len(names)} cached)"
                                                           if ran < len(names) else ""))
        for line in p.get("print", []):
            print(line)
        took = {k: r["time"] for k, r in reps.items() if isinstance(k, str)}
        if cr:
            took["checks"] = max(r["time"] for r in cr)       # the groups run side by side
        times.append(f"{h}: " + ", ".join(f"{k} {v:.0f} s" for k, v in took.items()))
        printable += [(f"{h}-{n}", d) for n, d in p.get("entries", {}).items() if p.get("valid", {}).get(n)]
        sys.stdout.flush()
    for pool in pools.values():
        pool.shutdown()
    if a.print and printable and not errors:
        write_3mf(out, printable)
    if a.no_cache:
        shutil.rmtree(cache_dir, ignore_errors=True)
    print(f"done in {time.time() - t0:.1f} s ({'; '.join(times)})", flush=True)
    return 2 if errors else 1 if failed else 0


def report_checks(half: str, out: Path, names: tuple, results: dict, skip: tuple, how: str) -> int:
    """Print one half's checks, write checks-<half>.json and hand them to the viewer."""
    from atlas_case import checks, viewexport
    res = [checks.Result(**{**d, "where": tuple(d["where"]) if d["where"] else None, "parts": tuple(d["parts"])})
           for n in names for d in results[n]]
    bad = [r for r in res if not r.ok]
    print(f"{half:5} fit checks: {len(res) - len(bad)}/{len(res)} ok {how}"
          + (f"; fast: skipped {', '.join(skip)}" if skip else ""))
    # failures always; passing checks only where they carry a measurement worth seeing
    shown = ("components clear", "lip lock", "switch centred", "min floor")
    for r in res:
        if not r.ok or r.check in shown or "skirt" in r.detail or "thumb wall" in r.detail:
            print("      " + str(r))
    _atomic_write(out / f"checks-{half}.json", json.dumps([r.__dict__ for r in res], indent=1))
    viewexport.export_checks(out, half, res, skip)
    return len(bad)


def write_3mf(out: Path, printable: list) -> None:
    """cad/out/atlas-case.3mf from the cached shapes (the print STLs were written per half)."""
    from atlas_case import printfiles
    print(f"print STLs -> {out / 'print'}/ (one per part, print orientation)")
    shapes = [(n, part_shape(cache.Entry(Path(d)))) for n, d in printable]
    f = out / "atlas-case.3mf"
    try:
        for w in printfiles.write_3mf(f, shapes):
            print("  warning:", w)
        print(f"print file -> {f}")
    except Exception as e:                     # the STLs are the print files
        print(f"  3MF not written: {type(e).__name__}: {e}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:                       # e.g. a board file KiCad is still writing
        traceback.print_exc()
        print(f"ERROR: {type(e).__name__}: {e}".splitlines()[0], flush=True)
        raise SystemExit(2)
