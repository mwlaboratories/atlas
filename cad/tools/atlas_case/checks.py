"""Fit checks on a built half: the case parts against each other and the populated PCB.

Each check returns Results; build.py prints them and fails the run on any failure, so a
broken case never exports silently. Results carry a location for the viewer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from build123d import Box, Compound, GeomType, Plane, Pos, Vector, Vertex
from OCP.GCPnts import GCPnts_AbscissaPoint
from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
from OCP.gp import gp_Pnt

from .bottomplate import BottomplateParams, switch_holes
from .housing import HousingParams
from .geom import poly
from .kicad import MIRROR_X, SWITCH, Board, mirrored
from .pcb3d import PCB_BOTTOM, PopulatedBoard
from .switchplate import SwitchplateParams, ThumbWall

MIN_GAP = 0.05          # mm, any case part to any PCB component
OVERLAP_TOL = 0.01      # mm3, below this an intersection is numerical noise
MIN_WALL = 0.6         # mm, thinnest wall a 0.4 mm nozzle prints reliably (~1.5 lines)
FIT_TOL = 0.05          # mm a designed clearance may fall short of nominal (the Fusion thumb
                        # wall is 0.179 mm from the PCB's sharp far corner)


@dataclass
class Result:
    check: str
    ok: bool
    detail: str
    where: tuple | None = None          # (x, y, z) to look at
    parts: tuple = field(default_factory=tuple)

    def __str__(self):
        loc = f"  at ({self.where[0]:.1f}, {self.where[1]:.1f}, {self.where[2]:.1f})" if self.where else ""
        return f"{'ok  ' if self.ok else 'FAIL'} {self.check}: {self.detail}{loc}"


def _vol(shape) -> float:
    return sum(s.volume for s in shape.solids()) if shape else 0.0


def _pt(v) -> tuple:
    return (round(v.X, 3), round(v.Y, 3), round(v.Z, 3))


def one_solid(parts: dict) -> list[Result]:
    """Each part is exactly one solid and OCC finds it valid: an invalid solid can mesh
    with holes or flipped walls, and a slicer then closes features (the bottom plate's
    screw holes once printed shut that way)."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    out = []
    for name, shape in parts.items():
        n = len(shape.solids())
        valid = shape.is_valid
        where, why = None, ""
        if not valid:
            bad = [f for f in shape.faces() if not BRepCheck_Analyzer(f.wrapped).IsValid()]
            if bad:
                where, why = _pt(bad[0].center()), f", {len(bad)} invalid face(s)"
        out.append(Result("one solid", n == 1 and valid,
                          f"{name}: {n} solid(s), {'valid' if valid else 'INVALID'}{why}", where, (name,)))
    return out


def no_overlaps(parts: dict) -> list[Result]:
    out, names = [], list(parts)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            inter = parts[a] & parts[b]
            v = _vol(inter)
            where = _pt(inter.bounding_box().center()) if v > OVERLAP_TOL else None
            out.append(Result("no overlap", v <= OVERLAP_TOL, f"{a} / {b}: {v:.3f} mm3", where, (a, b)))
    return out


def components_clear(parts: dict, pcb: PopulatedBoard, reach: float = 1.0) -> list[Result]:
    """Every PCB component (sockets, diodes, XIAO, ...) clear of every case part by MIN_GAP.

    Per footprint, each case part is cropped to the footprint's box grown by `reach` and
    measured against that crop: exact for gaps under `reach`, and much faster than
    measuring against whole parts. Gaps beyond reach are reported as >= reach."""
    by_ref = {f"{c.ref} {c.model}": c.solids for c in pcb.parts}
    out = []
    for name, shape in parts.items():
        worst, wref, wpt = reach, None, None
        sbb = shape.bounding_box()
        for ref, solids in by_ref.items():
            bbs = [s.bounding_box() for s in solids]
            lo = Vector(min(b.min.X for b in bbs), min(b.min.Y for b in bbs), min(b.min.Z for b in bbs))
            hi = Vector(max(b.max.X for b in bbs), max(b.max.Y for b in bbs), max(b.max.Z for b in bbs))
            if (lo.X > sbb.max.X + reach or hi.X < sbb.min.X - reach or lo.Y > sbb.max.Y + reach
                    or hi.Y < sbb.min.Y - reach or lo.Z > sbb.max.Z + reach or hi.Z < sbb.min.Z - reach):
                continue
            size = hi - lo + Vector(2 * reach, 2 * reach, 2 * reach)
            crop = shape & Pos((lo + hi) / 2) * Box(size.X, size.Y, size.Z)
            if crop is None or not crop.solids():
                continue
            comp = Compound(solids)          # one distance query per footprint
            d = comp.distance_to(crop)
            if d < 1e-6:                     # touching or overlapping: measure the overlap
                v = sum(_vol(s & crop) for s in solids)
                d = -v if v > 1e-4 else 0.0
            if d < worst:
                worst, wref, wpt = d, ref, comp.bounding_box().center()
        ok = worst >= MIN_GAP
        if wref is None:
            what = f"nothing within {reach} mm"
        elif worst < 0:
            what = f"overlaps {-worst:.3f} mm3"
        else:
            what = f"closest {worst:.2f} mm"
        out.append(Result("components clear", ok, f"{name}: {what}" + (f" ({wref})" if wref else ""),
                          _pt(wpt) if wpt else None, (name,)))
    return out


def pcb_fits(parts: dict, pcb: PopulatedBoard, hp: HousingParams) -> list[Result]:
    """Bare board inside the case: no part cuts into it, the skirt keeps its clearance."""
    out = []
    for name, shape in parts.items():
        inter = pcb.board & shape
        v = _vol(inter)
        out.append(Result("pcb fits", v <= OVERLAP_TOL, f"{name} into the board: {v:.3f} mm3",
                          _pt(inter.bounding_box().center()) if v > OVERLAP_TOL else None, (name,)))
    if "housing" in parts:
        d = pcb.board.distance_to(parts["housing"])
        ok = d >= hp.pcb_clearance - 0.01
        out.append(Result("pcb fits", ok, f"board edge to housing skirt {d:.3f} mm (want {hp.pcb_clearance})",
                          parts=("housing",)))
    return out


def thumb_wall_clear(board: Board, sp: SwitchplateParams) -> list[Result]:
    """The thumb wall is a design curve, not derived from the PCB: the PCB outline must keep
    the wall's clearance from it (checked in 2D, left orientation)."""
    b = mirrored(board) if board.half == "right" else board
    d = poly(*b.outline).distance_to(ThumbWall(sp).wall_face())
    ok = d >= sp.pcb_clearance - FIT_TOL
    return [Result("pcb fits", ok, f"board edge to thumb wall {d:.3f} mm (nominal {sp.pcb_clearance}, "
                   f"min {sp.pcb_clearance - FIT_TOL:.2f})",
                   parts=("switchplate",))]


def _sloped(shape, z_max: float):
    """Faces at an angle between flat and vertical (45 deg lock faces, whatever their surface
    type) below z_max; cones (countersinks) left out."""
    return [f for f in shape.faces()
            if f.geom_type not in (GeomType.CONE, GeomType.CYLINDER, GeomType.SPHERE, GeomType.TORUS)
            and f.bounding_box().max.Z < z_max and 0.05 < abs(f.normal_at().Z) < 0.95]


def lip_lock(parts: dict, bp: BottomplateParams, board: Board | None = None) -> list[Result]:
    """The bottom plate's relief matches the switch plate's lip: every sloped lip face has a
    bevel face of the plate next to it. On the 45 deg face's full-depth part the gap is
    lip_clearance; along the lip's run-outs (switchplate lip_runout, located through `board`)
    the relief stays full depth, so there it must only be no less. Measured at points 0.25 mm
    or more inside the face's edges (they run into the wall and past the plate's outline),
    and at the centre of any other sloped face."""
    if not bp.lip_lock or "bottomplate" not in parts or "switchplate" not in parts:
        return []
    g, sp = bp.lip_clearance, bp.switchplate
    if sp.lip_location == "bottom" and g > sp.lip_relief + 1e-9:
        return [Result("lip lock", False, f"relief {g} mm is larger than the switch plate's lip_relief "
                       f"{sp.lip_relief} mm, which its part clearance assumes", parts=("bottomplate", "switchplate"))]
    lip = sorted(_sloped(parts["switchplate"], PCB_BOTTOM), key=lambda f: -f.area)
    if not lip:
        return [Result("lip lock", False, "switch plate has no sloped lip face", parts=("switchplate",))]
    near = Compound(lip)
    bev = [f for f in _sloped(parts["bottomplate"], PCB_BOTTOM + 0.01) if f.distance_to(near) < 1.0]
    where = _pt(lip[0].center())
    if not bev:
        return [Result("lip lock", False, "no bevel face next to the lip", where, ("bottomplate", "switchplate"))]
    bevels, rim = Compound(bev), Compound(lip[0].edges())
    grid = [Vertex(lip[0].position_at(i / 40, j / 8)) for i in range(1, 40) for j in range(1, 8)]
    pts = [v for v in grid if v.distance_to(lip[0]) < 1e-6 and v.distance_to(rim) >= 0.25]
    if len(pts) < 10:
        return [Result("lip lock", False, f"lip face too narrow to measure ({len(pts)} points)", where,
                       ("bottomplate", "switchplate"))]

    # full depth: arc length along THUMB_INNER (left orientation) inside the run-outs
    def full_depth(v):
        return True
    if sp.lip_location == "bottom" and board is not None:
        tw = ThumbWall(sp)
        right = board.half == "right"
        s0, s1 = tw.lip_extent(mirrored(board) if right else board)
        lo, hi = s0 + sp.lip_runout + 0.1, s1 - sp.lip_runout - 0.1

        def full_depth(v):
            x, y = v.center().X, v.center().Y
            k = GeomAPI_ProjectPointOnCurve(gp_Pnt(MIRROR_X - x if right else x, y, 0), tw.curve).LowerDistanceParameter()
            return lo <= GCPnts_AbscissaPoint.Length_s(tw.adaptor, tw.t0, k) <= hi
    gaps = [(q.distance_to(bevels), full_depth(q)) for q in pts]
    main = [d for d, f in gaps if f]
    runout = [d for d, f in gaps if not f]
    gaps_other = [Vertex(f.center()).distance_to(bevels) for f in lip[1:]]
    bad = ([d for d in main + gaps_other if abs(d - g) > 0.01] + [d for d in runout if d < g - 0.01]
           + ([0.0] if not main else []))
    detail = (f"bevel to lip {min(main, default=0):.3f}..{max(main, default=0):.3f} mm over the 45 deg face's "
              f"full-depth part ({len(main)} points)"
              + (f", >= {min(runout):.3f} mm along the run-outs ({len(runout)} points)" if runout else "")
              + (f", {min(gaps_other):.3f}..{max(gaps_other):.3f} mm on {len(gaps_other)} more sloped faces" if gaps_other else "")
              + f" (want {g})")
    return [Result("lip lock", not bad, detail, where, ("bottomplate", "switchplate"))]


def switches_centred(parts: dict, board: Board, sp: SwitchplateParams) -> list[Result]:
    """Each switch footprint sits in the middle of its switch plate cutout: along both of the
    switch's axes the plate starts at the same distance on either side (an axis open on one
    side, where the cutout is bridged to a hole, is not measured)."""
    if "switchplate" not in parts:
        return []
    plate, z = parts["switchplate"], -sp.plate / 2
    half = sp.key_cut / 2

    def edge(c, d, reach=0.3):
        # distance from c along d to the plate edge, bisected within +-reach of nominal
        lo, hi = half - reach, half + reach
        if plate.is_inside(c + d * lo) or not plate.is_inside(c + d * hi):
            return None                 # no edge near nominal on this side (bridged or missing)
        for _ in range(14):             # 0.6 mm / 2^14 < 0.0001 mm
            mid = (lo + hi) / 2
            lo, hi = (lo, mid) if plate.is_inside(c + d * mid) else (mid, hi)
        return (lo + hi) / 2
    out, worst = [], (-1.0, None)
    for f in board.of_kind(SWITCH):
        c = Vector(f.x, f.y, z)
        if plate.is_inside(c):
            out.append(Result("switch centred", False, f"{f.ref}: no cutout at the switch", _pt(c), ("switchplate",)))
            continue
        a = math.radians(f.rot)
        off = 0.0
        for d in (Vector(math.cos(a), math.sin(a), 0), Vector(-math.sin(a), math.cos(a), 0)):
            t1, t2 = edge(c, d), edge(c, -d)
            if t1 is not None and t2 is not None:
                off = max(off, abs(t1 - t2) / 2)
        if off > 0.05:
            out.append(Result("switch centred", False, f"{f.ref}: cutout off by {off:.2f} mm", _pt(c), ("switchplate",)))
        worst = max(worst, (off, f.ref), key=lambda t: t[0])
    if not out:
        out.append(Result("switch centred", True, f"worst {worst[0]:.3f} mm ({worst[1]})"))
    return out


def screws_inside(parts: dict, board: Board, bp: BottomplateParams) -> list[Result]:
    """Each countersink/counterbore has plate all round it."""
    if "bottomplate" not in parts:
        return []
    plate = parts["bottomplate"]
    r = (bp.countersink_r if bp.screw_head == "countersunk" else bp.counterbore_r + bp.counterbore_chamfer) + 0.4
    out = []
    for ref in bp.screw_holes:
        h = board.by_ref(ref)
        z = bp.bottom + 0.1
        out_pts = [a for a in range(0, 360, 15)
                   if not plate.is_inside(Vector(h.x + r * math.cos(math.radians(a)), h.y + r * math.sin(math.radians(a)), z))]
        out.append(Result("screw inside plate", not out_pts,
                          f"{ref}: " + ("plate all round" if not out_pts else f"breaks out at {len(out_pts)}/24 angles"),
                          (h.x, h.y, z), ("bottomplate",)))
    return out


def switch_legs_clear(parts: dict, board: Board, bp: BottomplateParams) -> list[Result]:
    """Every hole of the switch footprint has room under it down to the post recess floor.
    The board's 3D export has no switch models, so this goes by the footprint's drills
    (Choc v1 side legs reach about 1 mm below the PCB)."""
    if "bottomplate" not in parts:
        return []
    plate = parts["bottomplate"]
    zs = (bp.top - 0.05, (bp.top + bp.switch_post_floor) / 2, bp.switch_post_floor + 0.05)
    bad, n = [], 0
    for sw in board.of_kind(SWITCH):
        for h in switch_holes(sw):
            n += 1
            r = h.drill / 2
            pts = [(h.x, h.y)] + [(h.x + r * math.cos(a * math.pi / 4), h.y + r * math.sin(a * math.pi / 4))
                                  for a in range(8)]
            if any(plate.is_inside(Vector(x, y, z)) for x, y in pts for z in zs):
                bad.append((sw, h))
    if not bad:
        return [Result("switch legs clear", True, f"{n} switch holes recessed to z {bp.switch_post_floor}",
                       parts=("bottomplate",))]
    return [Result("switch legs clear", False, f"{sw.ref}: plate under its {h.drill} mm hole",
                   (h.x, h.y, bp.top), ("bottomplate",)) for sw, h in bad]


def min_floor(parts: dict, bp: BottomplateParams) -> list[Result]:
    """No pocket floor thinner than floor_skin (a one-layer floor prints badly)."""
    if "bottomplate" not in parts or bp.floor_skin <= 0:
        return []
    bad = [f for f in parts["bottomplate"].faces()
           if f.geom_type == GeomType.PLANE and f.normal_at().Z > 0.99
           and bp.bottom + 1e-3 < f.center().Z < bp.bottom + bp.floor_skin - 1e-3]
    if not bad:
        return [Result("min floor", True, f"all pocket floors >= {bp.floor_skin} mm")]
    return [Result("min floor", False, f"floor {f.center().Z - bp.bottom:.2f} mm thick", _pt(f.center()), ("bottomplate",))
            for f in bad]


# Thin spots that are deliberate design shapes, not stray fins: accepted with a reason.
# (part, anchor, radius mm, reason); anchor = (x, y) in the LEFT case frame (mirrored for the
# right half) or a footprint ref.
ACCEPTED_THIN = [
    ("bottomplate", "SW_PWR", 6.0, "power switch slot steps (as drawn in Fusion)"),
    ("switchplate", (17.1, 13.0), 7.0, "thumb ear runs out to a sharp point (design: the printer "
                                       "lays down as much of it as it can)"),
]
RASTER = 0.05           # mm per pixel for the thin-wall scan (walls measured to +-0.05)
THIN_MIN_AREA = 0.1     # mm2; smaller uncovered bits are acute-corner tips, not walls


def _raster(shape, z, res=RASTER):
    """Section of `shape` at height z as a boolean image (True = material), and its origin."""
    import numpy as np
    from PIL import Image, ImageDraw
    sec = shape.intersect(Plane.XY.offset(z))
    faces = sec.faces() if sec else []
    if not faces:
        return None, (None, None)
    bb = Compound(faces).bounding_box()
    x0, y1 = bb.min.X - 1, bb.max.Y + 1
    w, h = int((bb.size.X + 2) / res), int((bb.size.Y + 2) / res)
    im = Image.new("1", (w, h), 0)
    d = ImageDraw.Draw(im)

    # fill the section's triangles (its faces tessellated within res/4): no wire walking,
    # holes come out by themselves, and degenerate section edges (which made OCC's curve
    # adaptor segfault or hang on the right bottom plate) don't matter
    for f in faces:
        verts, tris = f.tessellate(res / 4, 0.2)
        P = [((v.X - x0) / res, (y1 - v.Y) / res) for v in verts]
        for a, b_, c in tris:
            d.polygon([P[a], P[b_], P[c]], fill=1)
    return np.array(im, dtype=bool), (x0, y1)


def section_heights(shape):
    """One height between each pair of neighbouring horizontal-face levels: a vertical-walled
    section only changes at those levels (sloped faces get the midpoints too)."""
    zs = sorted({round(f.center().Z, 3) for f in shape.faces()
                 if f.geom_type == GeomType.PLANE and abs(f.normal_at().Z) > 0.99})
    return [round((a + b) / 2, 3) for a, b in zip(zs, zs[1:]) if b - a > 0.02]


def _accepted(name: str, x: float, y: float, board: Board | None):
    for part, anchor, r, why in ACCEPTED_THIN:
        if part != name:
            continue
        if isinstance(anchor, str):
            if board is None:
                continue
            f = board.by_ref(anchor)
            ax, ay = f.x, f.y
        else:
            ax, ay = anchor
            if board is not None and board.half == "right":
                ax = MIRROR_X - ax
        if math.hypot(x - ax, y - ay) <= r:
            return why
    return None


def thin_walls(parts: dict, w: float = MIN_WALL, board: Board | None = None) -> list[Result]:
    """Material thinner than w, per horizontal section: rasterised, the parts a disc of
    diameter w can't reach when rolled around inside the material (a morphological
    opening via distance transforms). Unlike comparing boundaries, this also sees fins
    hanging off one pocket and walls where pockets touch each other or the edge."""
    import numpy as np
    from scipy import ndimage as nd
    out = []
    r = w / 2
    for name, shape in parts.items():
        found = []
        for z in section_heights(shape):
            m, (x0, y1) = _raster(shape, z)
            if m is None:
                continue
            dt = nd.distance_transform_edt(m) * RASTER
            covered = nd.distance_transform_edt(~(dt >= r)) * RASTER <= r + RASTER / 2
            thin = m & ~covered
            lab, n = nd.label(thin)
            if not n:
                continue
            idx = np.arange(1, n + 1)
            areas = nd.sum(thin, lab, idx) * RASTER * RASTER
            widths = 2 * nd.maximum(dt, lab, idx)
            for i, a, wd in zip(idx, areas, widths):
                if a >= THIN_MIN_AREA:
                    rr, cc = nd.center_of_mass(thin, lab, i)
                    found.append((float(a), float(wd), z, (round(x0 + cc * RASTER, 2), round(y1 - rr * RASTER, 2))))
        seen, accepted, bad = set(), {}, []
        for a, wd, z, (x, y) in sorted(found, key=lambda t: -t[0]):
            key = (round(x), round(y))
            if key in seen:
                continue                  # the same wall at another height
            seen.add(key)
            why = _accepted(name, x, y, board)
            if why:
                accepted[why] = min(accepted.get(why, 9.0), wd)
            else:
                bad.append(Result("min wall", False, f"{name}: {a:.2f} mm2 of wall ~{wd:.2f} mm thick (min {w}) at z {z}",
                                  (x, y, z), (name,)))
        if not bad:
            note = "; ".join(f"accepted ~{wd:.2f} mm: {why}" for why, wd in accepted.items())
            out.append(Result("min wall", True, f"{name}: no wall under {w} mm" + (f" ({note})" if note else ""),
                              parts=(name,)))
        out += bad
    return out


SLIVER_W = 0.1          # mm: a face narrower than this on average (2 x area / perimeter) is a sliver
SHORT_EDGE = 0.02       # mm: shorter edges are leftovers of booleans, not design
PRINTED = ("bottomplate", "switchplate")
LAYER = 0.2             # mm: printed parts' vertical steps should be at least one layer


def clean_geometry(parts: dict, board: Board | None = None) -> list[Result]:
    """No stray geometry: sliver faces, edges a few microns long, and (printed parts) vertical
    steps lower than one layer, which print as stray lines. Deliberate shapes listed in
    ACCEPTED_THIN are skipped."""
    out = []
    for name, shape in parts.items():
        bad = []
        for f in shape.faces():
            bb = f.bounding_box()
            c = bb.center()
            if _accepted(name, c.X, c.Y, board):
                continue
            per = sum(e.length for e in f.edges())
            width = 2 * f.area / per if per else 0.0
            if width < SLIVER_W:
                bad.append((f"sliver face {width:.3f} mm wide", c))
            elif (name in PRINTED and f.geom_type == GeomType.PLANE and abs(f.normal_at().Z) < 0.01
                  and bb.size.Z < LAYER - 0.01):
                bad.append((f"step {bb.size.Z:.2f} mm high", c))
        for e in shape.edges():
            c = e.center()
            if e.length < SHORT_EDGE and not _accepted(name, c.X, c.Y, board):
                bad.append((f"edge {e.length * 1000:.0f} um long", c))
        if not bad:
            out.append(Result("clean geometry", True, f"{name}: no slivers, micro-edges or sub-layer steps",
                              parts=(name,)))
            continue
        kinds = {}
        for what, _ in bad:
            k = what.split()[0]
            kinds[k] = kinds.get(k, 0) + 1
        summary = ", ".join(f"{n} {k}" for k, n in kinds.items())
        what, c = bad[0]
        out.append(Result("clean geometry", False, f"{name}: {summary} (first: {what})", _pt(c), (name,)))
    return out


# Every check, in report order (the names run() takes in `skip` and `only`).
ORDER = ("one_solid", "no_overlaps", "pcb_fits", "components_clear", "thumb_wall_clear", "lip_lock",
         "switches_centred", "screws_inside", "switch_legs_clear", "min_floor", "thin_walls", "clean_geometry")
# Checks left out by a fast build (build.py --fast, the viewer): the thin-wall raster scan,
# slow and about print quality. Everything a moved footprint can break stays in, and so does
# clean_geometry (under a second, and a moved pocket can leave slivers).
FAST_SKIP = ("thin_walls",)


def run(parts: dict, board: Board, pcb: PopulatedBoard | None,
        bp: BottomplateParams = BottomplateParams(), sp: SwitchplateParams = SwitchplateParams(),
        hp: HousingParams = HousingParams(), skip: tuple = (), only: tuple | None = None) -> list[Result]:
    """The checks in ORDER, except those in `skip`, and if given only those in `only`
    (build.py runs groups of them in parallel processes)."""
    has_pcb, has_sp = pcb is not None, "switchplate" in parts
    steps = {
        "one_solid": lambda: one_solid(parts),
        "no_overlaps": lambda: no_overlaps(parts),
        "pcb_fits": lambda: pcb_fits(parts, pcb, hp) if has_pcb else [],
        "components_clear": lambda: components_clear(parts, pcb) if has_pcb else [],
        "thumb_wall_clear": lambda: thumb_wall_clear(board, sp) if has_sp else [],
        "lip_lock": lambda: lip_lock(parts, bp, board),
        "switches_centred": lambda: switches_centred(parts, board, sp),
        "screws_inside": lambda: screws_inside(parts, board, bp),
        "switch_legs_clear": lambda: switch_legs_clear(parts, board, bp),
        "min_floor": lambda: min_floor(parts, bp),
        "thin_walls": lambda: thin_walls(parts, board=board),
        "clean_geometry": lambda: clean_geometry(parts, board),
    }
    assert tuple(steps) == ORDER
    unknown = (set(skip) | set(only or ())) - set(ORDER)
    if unknown:
        raise ValueError(f"no such checks: {sorted(unknown)}")
    res = []
    for name in ORDER:
        if name not in skip and (only is None or name in only):
            res += steps[name]()
    return res
