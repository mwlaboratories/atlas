"""Make the boards' thumb-cluster edge follow the case's thumb wall.

    python pcb/tools/thumb_outline.py [--pcb-dir pcb] [--clearance 0.2] [--sag 0.005] [--dry-run]

The thumb wall (cad/tools/atlas_case/switchplate.py, THUMB_INNER / THUMB_INNER_TOP) is a
design curve drawn in Fusion; the bottom plate keeps fit_gap from it, so its edge is the
wall's inner face offset inward. This script gives the PCB the same edge: the Edge.Cuts
segments of the thumb cluster are replaced by

  * the thumb-end side   x = P1.x + clearance   (existing vertical edge, extended down),
  * the long bottom side  THUMB_INNER offset inward by clearance, as a gr_line chain whose
                          vertices sit on the (clearance + sag) offset, so every point of
                          the chain is clearance .. clearance + sag from the wall,
  * the far side          line P0 -> THUMB_INNER_TOP[0] offset inward by clearance, up to
                          the existing top edge (whose start point is moved onto it).

Corners stay sharp, like the wall's inner corners and the bottom plate's edge.
gr_line only: the case pipeline (atlas_case.kicad.load_board) reads lines, not arcs/curves.

Both halves: the right board is the mirror of the left (left-like case x = 72 - X, the
left board's case x = X - 120, both y = 120 - Y). Re-running is idempotent (deterministic
uuids; the previous chain is found and replaced the same way).
"""
from __future__ import annotations

import argparse
import math
import re
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2] if (Path(__file__).resolve().parents[2] / "cad/tools").exists() \
    else Path.cwd()
sys.path.insert(0, str(REPO / "cad" / "tools"))

from build123d import Spline  # noqa: E402
from OCP.BRep import BRep_Tool  # noqa: E402
from OCP.gp import gp_Pnt, gp_Vec  # noqa: E402

from atlas_case.switchplate import THUMB_INNER, THUMB_INNER_TOP  # noqa: E402

# KiCad board (X, Y down) <-> left-like case frame (x, y up), per half
TO_CASE = {"left": lambda X, Y: (X - 120.0, 120.0 - Y), "right": lambda X, Y: (72.0 - X, 120.0 - Y)}
TO_KICAD = {"left": lambda x, y: (x + 120.0, 120.0 - y), "right": lambda x, y: (72.0 - x, 120.0 - y)}
UUID_NS = uuid.UUID("6f1c8a52-3b7e-4d0c-9a51-7e2f4c1d0a42")

BLOCK = re.compile(r"\t\(gr_line\n(?:\t\t.*\n)*?\t\)\n")
START = re.compile(r"\(start ([-\d.]+) ([-\d.]+)\)")
END = re.compile(r"\(end ([-\d.]+) ([-\d.]+)\)")
UUID = re.compile(r'\(uuid "([^"]+)"\)')


# --- design curve ---------------------------------------------------------------------------

class Wall:
    def __init__(self):
        self.curve = BRep_Tool.Curve_s(Spline(*THUMB_INNER).wrapped, 0.0, 1.0)   # P0 -> P1
        self.t0, self.t1 = self.curve.FirstParameter(), self.curve.LastParameter()
        self.P0, self.P1 = THUMB_INNER[0], THUMB_INNER[-1]
        tx, ty = THUMB_INNER_TOP[0][0] - self.P0[0], THUMB_INNER_TOP[0][1] - self.P0[1]
        L = math.hypot(tx, ty)
        self.w = (tx / L, ty / L)                       # along the far side, upwards
        self.n = (self.w[1], -self.w[0])                # far side's outward normal

    def inset(self, t, d):
        """Point of the inner curve at t moved d into the pocket (to the right of P0 -> P1)."""
        P, V = gp_Pnt(), gp_Vec()
        self.curve.D1(t, P, V)
        L = math.hypot(V.X(), V.Y())
        return P.X() + V.Y() / L * d, P.Y() - V.X() / L * d

    def far(self, p):
        """Signed distance of p outside the far side's inner face (negative = in the pocket)."""
        return (p[0] - self.P0[0]) * self.n[0] + (p[1] - self.P0[1]) * self.n[1]

    def dist_inner(self, p, samples=4000):
        """Distance from p to the inner curve (dense sampling + local refine)."""
        best = min(range(samples + 1), key=lambda i: _d2(p, self.inset(self.t0 + (self.t1 - self.t0) * i / samples, 0)))
        lo = self.t0 + (self.t1 - self.t0) * max(best - 1, 0) / samples
        hi = self.t0 + (self.t1 - self.t0) * min(best + 1, samples) / samples
        for _ in range(60):
            a, b = lo + (hi - lo) / 3, hi - (hi - lo) / 3
            if _d2(p, self.inset(a, 0)) < _d2(p, self.inset(b, 0)):
                hi = b
            else:
                lo = a
        return math.sqrt(_d2(p, self.inset((lo + hi) / 2, 0)))


def _d2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _root(f, lo, hi):
    flo = f(lo)
    if (flo > 0) == (f(hi) > 0):
        raise ValueError("no crossing in the interval")
    for _ in range(100):
        mid = (lo + hi) / 2
        if (f(mid) > 0) == (flo > 0):
            lo, flo = mid, f(mid)
        else:
            hi = mid
    return (lo + hi) / 2


def _seg_dist(p, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / (vx * vx + vy * vy)))
    return math.hypot(p[0] - a[0] - t * vx, p[1] - a[1] - t * vy)


def chain(wall: Wall, ta: float, tc: float, d: float, sag: float):
    """Parameters from ta down to tc so the chords of the d-inset curve sag at most `sag`."""
    out = [ta]

    def split(a, b):
        pa, pb = wall.inset(a, d), wall.inset(b, d)
        dev = max(_seg_dist(wall.inset(a + (b - a) * k / 16, d), pa, pb) for k in range(1, 16))
        if dev > sag:
            m = (a + b) / 2
            split(a, m)
            split(m, b)
        else:
            out.append(b)
    split(ta, tc)
    return out


# --- board file -----------------------------------------------------------------------------

def edge_lines(text):
    """[(match span, uuid, start, end)] of the Edge.Cuts gr_lines (KiCad coords)."""
    out = []
    for m in BLOCK.finditer(text):
        b = m.group(0)
        if '(layer "Edge.Cuts")' not in b:
            continue
        s, e = START.search(b), END.search(b)
        out.append((m.span(), UUID.search(b).group(1), (float(s[1]), float(s[2])), (float(e[1]), float(e[2]))))
    if re.search(r'\(gr_(arc|curve|circle|rect|poly)\n(?:\t\t.*\n)*?\t\t\(layer "Edge.Cuts"\)', text):
        raise SystemExit("board outline has non-line Edge.Cuts items; this script handles gr_line outlines")
    return out


def loop_of(segs, half, tol=0.01):
    """Closed CCW loop in the case frame: vertices v[k], edge k = v[k] -> v[k+1] with its uuid."""
    cs = [(u, TO_CASE[half](*a), TO_CASE[half](*b)) for _, u, a, b in segs]
    u, a, b = cs.pop(0)
    verts, uu = [a, b], [u]
    while cs:
        end = verts[-1]
        for i, (u, a, b) in enumerate(cs):
            if math.dist(a, end) < tol or math.dist(b, end) < tol:
                cs.pop(i)
                verts.append(b if math.dist(a, end) < tol else a)
                uu.append(u)
                break
        else:
            raise SystemExit(f"{half}: outline not closed near {end}")
    if math.dist(verts[0], verts[-1]) > tol:
        raise SystemExit(f"{half}: outline not closed")
    verts.pop()
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(verts, verts[1:] + verts[:1]))
    if area < 0:                       # reverse: edge k (v[k] -> v[k+1]) keeps its uuid
        verts = verts[::-1]
        uu = uu[::-1]
        verts = verts[-1:] + verts[:-1]
    return verts, uu


def fmt(v):
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def gr_line(a, b, uid):
    return (f"\t(gr_line\n\t\t(start {fmt(a[0])} {fmt(a[1])})\n\t\t(end {fmt(b[0])} {fmt(b[1])})\n"
            f"\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n"
            f"\t\t(layer \"Edge.Cuts\")\n\t\t(uuid \"{uid}\")\n\t)\n")


def move_end(block, old_k, new_k):
    """Move whichever endpoint of a gr_line block is at old_k (KiCad coords) to new_k."""
    s, e = START.search(block), END.search(block)
    ps, pe = (float(s[1]), float(s[2])), (float(e[1]), float(e[2]))
    key, m = ("start", s) if math.dist(ps, old_k) < math.dist(pe, old_k) else ("end", e)
    assert math.dist((float(m[1]), float(m[2])), old_k) < 0.02, (block, old_k)
    return block[:m.start()] + f"({key} {fmt(new_k[0])} {fmt(new_k[1])})" + block[m.end():]


def process(path: Path, half: str, c: float, sag: float, dry: bool):
    text = path.read_text()
    segs = edge_lines(text)
    by_uuid = {u: (span, a, b) for span, u, a, b in segs}
    v, uu = loop_of(segs, half)
    n = len(v)
    wall = Wall()
    xe = wall.P1[0] + c                                   # thumb-end side of the PCB

    # A_old: lowest vertex on the thumb-end vertical, reached going down it (CCW loop)
    cand = [k for k in range(n) if abs(v[k][0] - xe) < 1e-3 and abs(v[k - 1][0] - xe) < 1e-3
            and v[k][1] < v[k - 1][1]]
    if len(cand) != 1:
        raise SystemExit(f"{half}: no unique thumb-end vertical edge at x = {xe:.4f} (case frame)")
    ia = cand[0]

    # far side inset line: far(p) = -c ; top edge = the edge it crosses near THUMB_INNER_TOP[0]
    top0 = THUMB_INNER_TOP[0]
    j = None
    for s in range(1, n):
        k = (ia + s) % n
        a, b = v[k], v[(k + 1) % n]
        fa, fb = wall.far(a) + c, wall.far(b) + c
        if abs(fa - fb) < 0.3 * math.dist(a, b):          # (nearly) parallel to the far side
            continue
        t = fa / (fa - fb)                                # on the edge's line, may extend it
        x = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        if -0.1 < t < 1 and math.dist(x, top0) < 3.0:
            j, B = k, x
            break
    if j is None:
        raise SystemExit(f"{half}: far-side line meets no outline edge near {top0}")

    # new chain: A (thumb end) -> inset curve -> C (far corner) -> B (top edge)
    ta = _root(lambda t: wall.inset(t, c + sag)[0] - xe, wall.t0, wall.t1)
    tc = _root(lambda t: wall.far(wall.inset(t, c + sag)) + c, wall.t0, ta)
    ts = chain(wall, ta, tc, c + sag, sag)
    pts = [wall.inset(t, c + sag) for t in ts]
    new = pts + [B]

    removed = [(k % n) for k in range(ia, j if j > ia else j + n)]
    print(f"{half}: thumb-end vertical edge {uu[ia - 1][:8]} {fmt_p(v[ia - 1])} -> {fmt_p(v[ia])}"
          f"  becomes  -> {fmt_p(new[0])}")
    print(f"{half}: replaced {len(removed)} edges:")
    for k in removed:
        a, b = by_uuid[uu[k]][1:]
        print(f"    {uu[k]}  {fmt_p(v[k])} -> {fmt_p(v[(k + 1) % n])}   (KiCad {fmt_p(a)} {fmt_p(b)})")
    print(f"{half}: top edge {uu[j][:8]} start {fmt_p(v[j])} -> {fmt_p(B)} (to {fmt_p(v[(j + 1) % n])})")
    print(f"{half}: new chain {len(new) - 1} gr_lines: A {fmt_p(new[0])}, curve {len(pts) - 1} chords "
          f"(sag <= {sag}), C {fmt_p(pts[-1])}, far side to B {fmt_p(B)}")
    lens = [math.dist(p, q) for p, q in zip(new, new[1:])]
    print(f"{half}: chord lengths {min(lens):.3f} .. {max(lens):.3f} mm")

    # checks: clearance of the new chain to the wall (sampled along each chord)
    ds = []
    for p, q in zip(pts, pts[1:]):
        for k in range(11):
            r = (p[0] + (q[0] - p[0]) * k / 10, p[1] + (q[1] - p[1]) * k / 10)
            ds.append(wall.dist_inner(r))
    fs = [-wall.far(p) for p in (pts[-1], B)]
    print(f"{half}: chain to wall inner curve {min(ds):.4f} .. {max(ds):.4f} mm; far side {min(fs):.4f} .. "
          f"{max(fs):.4f} mm; thumb end {xe - wall.P1[0]:.4f} mm (target {c})")

    if dry:
        return
    K = TO_KICAD[half]
    edits = []                                            # (span, replacement)
    for k in removed:
        edits.append((by_uuid[uu[k]][0], ""))
    span = by_uuid[uu[ia - 1]][0]
    edits.append((span, move_end(text[span[0]:span[1]], K(*v[ia]), K(*new[0]))))
    span = by_uuid[uu[j]][0]
    blk = move_end(text[span[0]:span[1]], K(*v[j]), K(*B))
    blk += "".join(gr_line(K(*p), K(*q), uuid.uuid5(UUID_NS, f"{half}-thumb-{i}"))
                   for i, (p, q) in enumerate(zip(new, new[1:])))
    edits.append((span, blk))
    for (s, e), rep in sorted(edits, reverse=True):
        text = text[:s] + rep + text[e:]
    path.write_text(text)
    # read back
    v2, _ = loop_of(edge_lines(text), half)
    print(f"{half}: written {path} ({len(v2)} outline vertices)")


def fmt_p(p):
    return f"({p[0]:.4f}, {p[1]:.4f})"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pcb-dir", default=str(REPO / "pcb"))
    ap.add_argument("--clearance", type=float, default=0.2, help="PCB edge to thumb wall inner face (mm)")
    ap.add_argument("--sag", type=float, default=0.005, help="max chord deviation from the offset curve (mm)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    for half in ("left", "right"):
        process(Path(a.pcb_dir) / f"atlas-{half}.kicad_pcb", half, a.clearance, a.sag, a.dry_run)


if __name__ == "__main__":
    main()
