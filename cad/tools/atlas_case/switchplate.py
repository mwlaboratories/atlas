"""Switch plate: holds the choc switches, rests on the PCB, and closes the housing's thumb side.

Built for the LEFT half in the case frame (see kicad.Frame); the right half is
the same construction on the mirrored board, mirrored back (see build.py).

Layers (z, mm):
  -1.2 .. 0     plate: 14 mm choc cutouts, screw / standoff / trackpoint holes
  -2.2 .. -1.2  relief: 16 mm pockets for the switch housings; underside = PCB top
  -6.2 .. -2.2  thumb wall around the thumb part of the PCB, with a small lip at the
                bottom of its far side

The plate sits inside the housing frame (housing outer box inset by the rim) and fills
the housing's open thumb side, so its outline is tied to HousingParams. The thumb wall
itself is hand-drawn (see THUMB_*).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from build123d import (Circle, Edge, Face, Kind, Pos, Rectangle, Rot, Spline, ThreePointArc, Vector,
                       Wire, extrude, make_face, offset)
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.Geom import Geom_OffsetCurve, Geom_TrimmedCurve
from OCP.GeomAbs import GeomAbs_C2
from OCP.GeomConvert import GeomConvert_ApproxCurve
from OCP.gp import gp_Dir, gp_Pnt, gp_Vec

from .geom import box, poly, prism
from .housing import HousingParams
from .kicad import FRAMES, MIRROR_X, Board, _chain, find, find_all, parse_sexpr, xy

PCB_DIR = Path(__file__).resolve().parents[3] / "pcb"

# --- hand-drawn thumb wall (case frame, left half) -----------------------------------------
# Drawn freehand in Fusion; nothing on the PCB defines these. The curves are frozen as
# interpolation points sampled from the reference plate (fit error < 0.003 mm).
#
# Inner face of the wall's long bottom side, from the far corner to the thumb-side end.
THUMB_INNER = ((25.2823, -40.4105), (23.0691, -39.3187), (20.8523, -38.2341), (18.6315, -37.1578),
               (16.4061, -36.0912), (14.1754, -35.0355), (11.9388, -33.9926), (9.6951, -32.9647),
               (7.4434, -31.9548), (5.182, -30.9667), (2.9087, -30.0062), (0.6206, -29.0818),
               (-1.687, -28.2071), (-4.0202, -27.4034), (-6.3877, -26.7079), (-8.8, -26.1914))
# Outer edge of the "ear" that wraps the thumb keys, from the far corner round up to the
# housing's side (x = housing.x_max).
THUMB_EAR = ((34.8309, -21.6303), (32.407, -19.7333), (30.1075, -17.6876), (27.9587, -15.4844),
             (25.9872, -13.1212), (24.2172, -10.6037), (22.6643, -7.9468), (21.3336, -5.1718),
             (20.2192, -2.303), (19.3078, 0.6368), (18.5776, 3.6269), (18.0063, 6.6514),
             (17.5758, 9.6993), (17.2716, 12.7624), (17.0825, 15.8348), (17.0, 18.9119))
# Rest of the wall's inner face, continuing from THUMB_INNER[0]: top of the far side, end of
# the top side, and where it meets the housing's slant edge.
THUMB_INNER_TOP = ((33.69237, -23.590339), (14.883695, -14.177915), (12.383695, -10.691847))


@dataclass(frozen=True)
class SwitchplateParams:
    housing: HousingParams = field(default_factory=HousingParams)
    rim: float = 5.0                 # plate = housing outer box inset by this frame rim
    plate: float = 1.2               # plate thickness (choc spec)
    pcb_top: float = -2.2            # PCB top = underside of the relief layer
    floor: float = -6.2              # bottom of the thumb wall (= housing skirt depth)
    pcb_clearance: float = 0.2       # thumb wall's inner side to the PCB's bottom edge
    key_cut: float = 14.0            # choc plate cutout
    key_pocket: float = 16.0         # relief for the switch housing under the plate
    # the bottom notch (housing.notch_x) reaches up to housing.notch_y[1] in the plate layer
    # and only to here in the relief layer (level with the SW1/SW7 pockets)
    relief_notch_top: float = -6.0
    # battery-lead clearance next to BT1: from this x to the notch, up to this y
    battery_cut: tuple = (-68.0, -14.0)
    # XIAO corner: plate cut away for x < xiao_cut[0], y > xiao_cut[1], except a tab
    # around H4 for x > h4_tab[0], y > h4_tab[1]
    xiao_cut: tuple = (-72.0, 36.2)
    h4_tab: tuple = (-74.0, 45.0)
    # holes: screws pass the PCB holes at PCB drill size; the trackpoint standoffs (H6, H7)
    # and the trackpoint (H1) get clearance holes
    screw_holes: tuple = ("H2", "H3", "H4", "H5")
    standoff_holes: tuple = ("H6", "H7")
    standoff_r: float = 2.1
    trackpoint_hole: str = "H1"
    trackpoint_r: float = 3.0
    # The Fusion plate was drawn against an earlier PCB: there these holes sit 0.01-0.02 mm
    # lower. () follows the PCB.
    hole_shift: tuple = (("H1", 0.0, -0.02), ("H6", 0.0, -0.01), ("H7", 0.0, -0.01))
    # Thumb keys as placed in the Fusion plate (ref, x, y, rot). The PCB has since moved them
    # (SW16 by 0.08 mm / 0.48 deg, SW17 by 0.02 mm / 0.57 deg). () follows the PCB.
    thumb_keys: tuple = (("SW16", 1.825337, -19.487075, -18.520149),
                         ("SW17", 21.0026835, -27.752573, -math.degrees(math.atan(0.5))))
    # thumb wall
    wall: float = 2.605              # bottom side: outer edge = inner face offset by this
    side_wall: float = 1.8           # far side thickness
    corner_r: float = 2.0            # rounds where the far side meets the bottom and the ear
    # lip on the far side's inner face: a ledge, then a 45 deg chamfer up to the wall
    lip_span: tuple = (3.9371, 15.6381)   # along the far side from its bottom corner
    lip_depth: float = 1.129
    lip_ledge: float = 0.2
    # the lip is notched wherever a hotswap socket (its Fab outline + clearance) reaches it;
    # the notch stops notch_skin short of the lip face, as in the Fusion model
    socket_fp: str = "kailh_choc_hotswap"
    socket_clearance: float = 0.2
    notch_skin: float = 0.04


def key_places(board: Board, p: SwitchplateParams):
    """(x, y, rot) of every switch; thumb keys overridden by p.thumb_keys."""
    over = {r: (x, y, a) for r, x, y, a in p.thumb_keys}
    return [over.get(f.ref, (f.x, f.y, f.rot)) for f in board.of_kind("choc_v1_v2_hotswap")]


def key_squares(board: Board, p: SwitchplateParams, size: float):
    sqs = [Pos(x, y) * Rot(0, 0, a) * Rectangle(size, size) for x, y, a in key_places(board, p)]
    face = sqs[0]
    for s in sqs[1:]:
        face += s
    return face


def holes(board: Board, p: SwitchplateParams):
    shift = {r: (dx, dy) for r, dx, dy in p.hole_shift}

    def at(ref, r):
        f = board.by_ref(ref)
        dx, dy = shift.get(ref, (0.0, 0.0))
        return Pos(f.x + dx, f.y + dy) * Circle(r)

    face = at(p.trackpoint_hole, p.trackpoint_r)
    for ref in p.screw_holes:
        face += at(ref, next(pd.drill for pd in board.by_ref(ref).pads if pd.drill) / 2)
    for ref in p.standoff_holes:
        face += at(ref, p.standoff_r)
    return face


# --- thumb wall --------------------------------------------------------------------------------

def _unit(v: Vector) -> Vector:
    return v / v.length


class ThumbWall:
    """The hand-drawn thumb wall resolved into case-frame faces."""

    def __init__(self, p: SwitchplateParams):
        self.p = p
        r = p.corner_r
        self.inner = Spline(*THUMB_INNER)                  # P0 (far corner) -> P1 (thumb end)
        self.curve = BRep_Tool.Curve_s(self.inner.wrapped, 0.0, 1.0)
        self.t0, self.t1 = self.curve.FirstParameter(), self.curve.LastParameter()
        self.P0, self.P1 = Vector(*THUMB_INNER[0]), Vector(*THUMB_INNER[-1])
        self.top = [Vector(*q) for q in THUMB_INNER_TOP]
        self.w = _unit(self.top[0] - self.P0)              # along the far side, upwards
        self.n = Vector(self.w.Y, -self.w.X)               # far side's outward normal
        u_out = self.P0.dot(self.n) + p.side_wall          # far side's outer face: n . x = u_out

        # bottom round: centre r inside the far side and (wall - r) outside the inner curve
        def f(t):
            return self.off(t, p.wall - r).dot(self.n) - (u_out - r)
        lo, hi = self.t0, self.t0 + 0.3 * (self.t1 - self.t0)
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if (f(lo) > 0) == (f(mid) > 0) else (lo, mid)
        self.tc = (lo + hi) / 2
        self.C1 = self.off(self.tc, p.wall - r)
        self.T1 = self.off(self.tc, p.wall)                # where the round leaves the outer curve
        self.out_lo = self.C1 + self.n * r
        # top round: tangent to the ear at its start
        self.ear = Spline(*THUMB_EAR)
        self.E0 = Vector(*THUMB_EAR[0])
        self.C2 = self.E0 + self._left(self.ear.tangent_at(0)) * r
        self.out_hi = self.C2 + self.n * r

    @staticmethod
    def _left(v: Vector) -> Vector:
        v = _unit(v)
        return Vector(-v.Y, v.X)

    def off(self, t: float, d: float) -> Vector:
        """Point of the inner curve at parameter t, moved d outward (to the curve's left)."""
        P, V = gp_Pnt(), gp_Vec()
        self.curve.D1(t, P, V)
        return Vector(P.X(), P.Y()) + self._left(Vector(V.X(), V.Y())) * d

    def outer_curve(self) -> Edge:
        """Inner curve offset by the wall, from the bottom round to the thumb end."""
        oc = Geom_OffsetCurve(self.curve, -self.p.wall, gp_Dir(0, 0, 1))  # OCC: + = right
        # as a plain B-spline (within 1e-5 mm) so the STEP carries no offset-curve surfaces
        bs = GeomConvert_ApproxCurve(Geom_TrimmedCurve(oc, self.tc, self.t1), 1e-5, GeomAbs_C2, 50, 8).Curve()
        return Edge(BRepBuilderAPI_MakeEdge(bs).Edge())

    def outer_face(self):
        """Everything the thumb side of the plate covers below the main rectangle."""
        p, h = self.p, self.p.housing
        r = p.corner_r
        x_main, y_main = h.x_max - p.rim, h.y_min + p.rim
        (sx0, sy0), (sx1, sy1) = h.thumb_slant

        def slant(x):
            return Vector(x, sy0 + (sy1 - sy0) / (sx1 - sx0) * (x - sx0))

        A = Vector(h.thumb_gap_x, y_main)
        B = Vector(h.thumb_gap_x, self.P1.Y)
        a_end = self.off(self.t1, p.wall)

        def arc(c, a, b, rad):
            return ThreePointArc(a, c + _unit((a - c) + (b - c)) * rad, b)

        edges = [
            Edge.make_line(A, B),
            arc(self.P1, B, a_end, p.wall),            # offset of the sharp thumb-end corner
            self.outer_curve(),
            arc(self.C1, self.T1, self.out_lo, r),
            Edge.make_line(self.out_lo, self.out_hi),
            arc(self.C2, self.out_hi, self.E0, r),
            self.ear,
            Edge.make_line(Vector(*THUMB_EAR[-1]), slant(h.x_max)),
            Edge.make_line(slant(h.x_max), slant(x_main)),
            Edge.make_line(slant(x_main), Vector(x_main, y_main)),
            Edge.make_line(Vector(x_main, y_main), A),
        ]
        return make_face(Wire(edges))

    def pocket_face(self):
        """What the thumb wall must not fill: its inside, and everything from 0.2 mm below the
        main plate's bottom edge up (the wall only exists under the thumb side)."""
        p, h = self.p, self.p.housing
        y_in = h.y_min + p.rim - p.pcb_clearance
        far = 500.0
        ur, top_l, slant_a = self.top
        pts = [self.P1, Vector(self.P1.X, y_in), Vector(-far, y_in), Vector(-far, far),
               Vector(slant_a.X, far), slant_a, top_l, ur, self.P0]
        edges = [Spline(*THUMB_INNER)] + [Edge.make_line(a, b) for a, b in zip(pts, pts[1:])]
        return make_face(Wire(edges))

    def lip(self, board: Board):
        """Ledge + 45 deg chamfer along the far side's inner face, notched for socket terminals."""
        p = self.p
        s0, s1 = p.lip_span
        a = self.P0 + self.w * s0
        b = a - self.n * p.lip_depth
        z0, z1 = p.floor, p.floor + p.lip_ledge
        sec = Wire.make_polygon([Vector(a.X, a.Y, z0), Vector(b.X, b.Y, z0), Vector(b.X, b.Y, z1),
                                 Vector(a.X, a.Y, z1 + p.lip_depth)], close=True)
        lip = extrude(Face(sec), amount=s1 - s0, dir=self.w)
        # the notch keeps notch_skin of lip on the inside
        e = b + self.n * p.notch_skin
        keep = poly(*[(q.X, q.Y) for q in (e - self.w * 10, e + self.w * 30,
                                            e + self.w * 30 + self.n * 10, e - self.w * 10 + self.n * 10)])
        sockets = socket_outlines(board, p.socket_fp, p.socket_clearance)
        return lip - prism(sockets & keep, p.floor - 1, p.pcb_top)


@lru_cache(maxsize=None)
def _fab_outlines(half: str, lib_part: str):
    """{ref: (footprint position, Fab outline)} in the case frame of `half`, for footprints
    whose lib contains lib_part. Footprint graphics are not in kicad.Board, so they are read
    from the same .kicad_pcb here."""
    root = parse_sexpr((PCB_DIR / f"atlas-{half}.kicad_pcb").read_text())
    fr = FRAMES[half]
    out = {}
    for f in find_all(root, "footprint"):
        if lib_part not in f[1]:
            continue
        at = find(f, "at")
        X, Y = float(at[1]), float(at[2])
        a = math.radians(float(at[3]) if len(at) > 3 else 0.0)
        segs = [(xy(find(g, "start")), xy(find(g, "end"))) for g in find_all(f, "fp_line")
                if str(find(g, "layer")[1]).endswith(".Fab")]
        if not segs:
            continue
        # footprint-local -> board exactly as for pads in kicad.load_board, then -> case frame
        loop = [fr.p(X + lx * math.cos(a) + ly * math.sin(a), Y - lx * math.sin(a) + ly * math.cos(a))
                for lx, ly in _chain(segs)]
        ref = next(str(q[2]) for q in find_all(f, "property") if q[1] == "Reference")
        out[ref] = (fr.p(X, Y), loop)
    return out


def socket_outlines(board: Board, lib_part: str, grow: float):
    """Union of the sockets' Fab outlines grown by `grow`, in the frame of `board`
    (which may be a kicad.mirrored() right half: detected from the footprint positions)."""
    face = None
    for ref, ((x, _), loop) in _fab_outlines(board.half, lib_part).items():
        if abs(x - board.by_ref(ref).x) > 1e-6:
            loop = [(MIRROR_X - qx, qy) for qx, qy in loop]
        o = offset(poly(*loop), grow, kind=Kind.INTERSECTION)
        face = o if face is None else face + o
    return face


def outline(p: SwitchplateParams, notch_top: float, thumb: ThumbWall):
    """Plate outline: housing box inset by the rim, corner and notch cut-outs, plus the thumb side."""
    h = p.housing
    x0, x1, y0, y1 = h.x_min + p.rim, h.x_max - p.rim, h.y_min + p.rim, h.y_max - p.rim
    nx0, nx1 = h.notch_x
    xc, yc = p.xiao_cut
    tx, ty = p.h4_tab
    face = box(x0, x1, y0, y1)
    face -= box(x0 - 1, xc, yc, y1 + 1) - box(tx, xc, ty, y1 + 1)   # XIAO corner, tab at H4
    face -= box(nx0, nx1, y0 - 1, notch_top)                          # bottom notch
    face -= box(p.battery_cut[0], nx0, y0 - 1, p.battery_cut[1])      # battery leads
    return face + thumb.outer_face()


def build_switchplate(board: Board, p: SwitchplateParams = SwitchplateParams()):
    thumb = ThumbWall(p)
    hole2d = holes(board, p)
    plate2d = outline(p, p.housing.notch_y[1], thumb) - key_squares(board, p, p.key_cut) - hole2d
    relief2d = outline(p, p.relief_notch_top, thumb) - key_squares(board, p, p.key_pocket) - hole2d
    wall2d = thumb.outer_face() - thumb.pocket_face()

    part = prism(plate2d, -p.plate, 0.0)
    part += prism(relief2d, p.pcb_top, -p.plate)
    part += prism(wall2d, p.floor, p.pcb_top)
    part += thumb.lip(board)
    return part.clean()
