"""Switch plate: holds the choc switches, rests on the PCB, and closes the housing's thumb side.

Built for the LEFT half in the case frame (see kicad.Frame); the right half is
the same construction on the mirrored board, mirrored back (see build.py).

Layers (z, mm):
  -1.2 .. 0     plate: 14 mm choc cutouts, screw / standoff / trackpoint holes
  -2.2 .. -1.2  relief: 16 mm pockets for the switch housings; underside = PCB top
  -6.2 .. -2.2  thumb wall around the thumb part of the PCB, with a small lip at the
                bottom of its long bottom side that the bottom plate hooks under

The plate sits inside the housing frame (housing outer box inset by the rim) and fills
the housing's open thumb side, so its outline is tied to HousingParams. The thumb wall
itself is hand-drawn (see THUMB_*).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from build123d import (Circle, Edge, Face, Keep, Kind, Plane, Pos, Rectangle, Rot, Solid, Spline,
                       ThreePointArc, Vector, Wire, extrude, make_face, offset, split)
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.GCPnts import GCPnts_AbscissaPoint
from OCP.GeomAPI import GeomAPI_Interpolate, GeomAPI_ProjectPointOnCurve
from OCP.GeomAdaptor import GeomAdaptor_Curve
from OCP.Geom import Geom_OffsetCurve, Geom_TrimmedCurve
from OCP.GeomAbs import GeomAbs_C2
from OCP.GeomConvert import GeomConvert_ApproxCurve
from OCP.gp import gp_Dir, gp_Pnt, gp_Vec
from OCP.collections import HArray1_double, HArray1_gp_Pnt

from .geom import box, bridges, poly, prism
from .housing import HousingParams
from .kicad import SOCKET, SWITCH, Board


# --- hand-drawn thumb wall (case frame, left half) -----------------------------------------
# Drawn freehand in Fusion; nothing on the PCB defines these. The curves are frozen as
# interpolation points sampled from the reference plate (fit error < 0.003 mm).
#
# Inner face of the wall's long bottom side, from the far corner to the thumb-side end.
THUMB_INNER = ((25.2823, -40.4105), (23.0691, -39.3187), (20.8523, -38.2341), (18.6315, -37.1578),
               (16.4061, -36.0912), (14.1754, -35.0355), (11.9388, -33.9926), (9.6951, -32.9647),
               (7.4434, -31.9548), (5.182, -30.9667), (2.9087, -30.0062), (0.6206, -29.0818),
               (-1.687, -28.2071), (-4.0202, -27.4034), (-6.3877, -26.7079), (-8.8, -26.1914))
# (THUMB_EAR's fit error is larger, up to 0.008 mm, at its far-corner end.)
# Outer edge of the "ear" that wraps the thumb keys, from the far corner round up to the
# housing's side (x = housing.x_max).
THUMB_EAR = ((34.8309, -21.6303), (32.407, -19.7333), (30.1075, -17.6876), (27.9587, -15.4844),
             (25.9872, -13.1212), (24.2172, -10.6037), (22.6643, -7.9468), (21.3336, -5.1718),
             (20.2192, -2.303), (19.3078, 0.6368), (18.5776, 3.6269), (18.0063, 6.6514),
             (17.5758, 9.6993), (17.2716, 12.7624), (17.0825, 15.8348), (17.0, 18.9119))
# The Fusion plate was drawn against an earlier PCB: its H1/H6/H7 holes sit 0.01-0.02 mm
# lower, and SW16/SW17 were placed where the PCB had them then (SW16 0.08 mm / 0.48 deg,
# SW17 0.02 mm / 0.57 deg from today's PCB). Used only to reproduce the reference STEP.
FUSION_HOLE_SHIFT = (("H1", 0.0, -0.02), ("H6", 0.0, -0.01), ("H7", 0.0, -0.01))
FUSION_THUMB_KEYS = (("SW16", 1.825337, -19.487075, -18.520149),
                     ("SW17", 21.0026835, -27.752573, -math.degrees(math.atan(0.5))))
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
    # overrides: holes moved by (ref, dx, dy), keys placed at (ref, x, y, rot) instead of
    # where the PCB has them; () = follow the PCB (see FUSION_* for the reference values)
    hole_shift: tuple = ()
    thumb_keys: tuple = ()
    # holes closer than this are joined (the wall between wouldn't print); 0 = keep it
    bridge_walls: float = 0.6
    main_rect: str = "pcb"           # main rectangle from the "pcb" outline or the "housing" box
    # thumb wall
    wall: float = 2.605              # bottom side: outer edge = inner face offset by this
    side_wall: float = 1.8           # far side thickness
    corner_r: float = 2.0            # rounds where the far side meets the bottom and the ear
    # the ear's tail runs into the housing side (x = housing.x_max) almost tangentially;
    # 0 = as drawn, a sharp point (default: the better the printer, the sharper it comes
    # out; user, 2026-11-08); > 0 = it ends where it is this wide, with a round end of half
    # this radius
    min_feature: float = 0.0
    # top end of the wall: True fills it out to the housing frame's inner line
    # (x = housing.x_max - rim, flush with the skirt's inner face beside it) along the top
    # side's line, so no pocket is left between wall, skirt and PCB; False ends it on
    # THUMB_INNER_TOP's last point, a sharp tip on the housing slant (Fusion)
    wall_top_fill: bool = True
    # lip on the wall's inner face: a ledge, then a 45 deg chamfer up to the wall; the
    # bottom plate hooks under it (its relief: BottomplateParams.lip_clearance)
    lip_depth: float = 1.129
    lip_ledge: float = 0.2
    # "bottom": along the long bottom side (THUMB_INNER), around lip_centre; "far_side":
    # along the far side, as in Fusion (lip_span, lip_sockets below)
    lip_location: str = "bottom"
    # "bottom": lip_length at full depth (arc length along the wall face) plus a run-out of
    # lip_runout at each end, where the depth eases from 0 to full (cosine), so the wall's
    # inner outline has no steps; centred at lip_centre (fraction of THUMB_INNER's length
    # from its far corner), on the longest stretch whose relief (the lip grown by
    # lip_relief) keeps lip_part_clearance from every part under the PCB (see
    # underside_keepouts); a full-depth part shorter than lip_min_length fails
    lip_length: float = 12.0
    lip_min_length: float = 8.0
    lip_runout: float = 3.0
    lip_centre: float = 0.5
    lip_part_clearance: float = 1.0
    lip_relief: float = 0.15         # = BottomplateParams.lip_clearance (checks.lip_lock)
    lip_run: float = 0.3             # the 45 deg face runs on this far into the wall
    lip_span: tuple = (3.9371, 15.6381)   # "far_side": along the far side from its bottom corner
    # "far_side": where a hotswap socket (its Fab outline + clearance) reaches the lip:
    # "shorten" (default) keeps the longest stretch of lip clear of every socket, so it
    # ends cleanly before the socket leg; "notch" cuts the socket's shape out of the lip
    # and leaves notch_skin of it on the inside, as in the Fusion model
    lip_sockets: str = "shorten"
    lip_margin: float = 0.5          # "shorten": extra gap between the lip's end and the socket
    socket_fp: str = SOCKET
    socket_clearance: float = 0.2
    notch_skin: float = 0.04


def key_places(board: Board, p: SwitchplateParams):
    """(x, y, rot) of every switch; thumb keys overridden by p.thumb_keys."""
    over = {r: (x, y, a) for r, x, y, a in p.thumb_keys}
    return [over.get(f.ref, (f.x, f.y, f.rot)) for f in board.of_kind(SWITCH)]


def holes(board: Board, p: SwitchplateParams):
    shift = {r: (dx, dy) for r, dx, dy in p.hole_shift}

    def at(ref, r):
        f = board.by_ref(ref)
        dx, dy = shift.get(ref, (0.0, 0.0))
        return Pos(f.x + dx, f.y + dy) * Circle(r)

    circles = [at(p.trackpoint_hole, p.trackpoint_r)]
    circles += [at(ref, next(pd.drill for pd in board.by_ref(ref).pads if pd.drill) / 2) for ref in p.screw_holes]
    circles += [at(ref, p.standoff_r) for ref in p.standoff_holes]
    return circles


def union(faces):
    face = faces[0]
    for f in faces[1:]:
        face += f
    return face


def plate_cuts(board: Board, p: SwitchplateParams, key_size: float):
    """Key cutouts and holes at one layer; where two come closer than bridge_walls, the
    wall between them is cut too (the trackpoint hole sits 0.04 mm from two key cutouts)."""
    faces = [Pos(x, y) * Rot(0, 0, a) * Rectangle(key_size, key_size) for x, y, a in key_places(board, p)]
    faces += holes(board, p)
    if p.bridge_walls > 0:
        faces += [piece for piece, _ in bridges(faces, p.bridge_walls)]
    return union(faces)


# --- thumb wall --------------------------------------------------------------------------------

def _unit(v: Vector) -> Vector:
    return v / v.length


def _approx(curve, t0: float, t1: float):
    """`curve` over [t0, t1] as a B-spline within 1e-5 mm, keeping its parameters (offset
    curves become plain B-splines, which the STEP carries cleanly). OCC offsets: + = right."""
    return GeomConvert_ApproxCurve(Geom_TrimmedCurve(curve, t0, t1), 1e-5, GeomAbs_C2, 50, 8).Curve()


class ThumbWall:
    """The hand-drawn thumb wall resolved into case-frame faces."""

    def __init__(self, p: SwitchplateParams):
        self.p = p
        r = p.corner_r
        self.inner = Spline(*THUMB_INNER)                  # P0 (far corner) -> P1 (thumb end)
        self.curve = BRep_Tool.Curve_s(self.inner.wrapped, 0.0, 1.0)
        self.t0, self.t1 = self.curve.FirstParameter(), self.curve.LastParameter()
        self.adaptor = GeomAdaptor_Curve(self.curve)
        self.length = GCPnts_AbscissaPoint.Length_s(self.adaptor)
        self._offsets, self._reports = {}, {}
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

    def outer_face(self, rect=None):
        """Everything the thumb side of the plate covers below the main rectangle
        (rect = (x0, x1, y0, y1); default: the housing box inset by the rim)."""
        p, h = self.p, self.p.housing
        r = p.corner_r
        x_main, y_main = (rect[1], rect[2]) if rect else (h.x_max - p.rim, h.y_min + p.rim)
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
            *self.ear_edges(),
            Edge.make_line(self.ear_foot, slant(h.x_max)),
            Edge.make_line(slant(h.x_max), slant(x_main)),
            Edge.make_line(slant(x_main), Vector(x_main, y_main)),
            Edge.make_line(Vector(x_main, y_main), A),
        ]
        return make_face(Wire(edges))

    def ear_edges(self) -> list:
        """The ear's outer edge, up to where it runs into the housing side (x = housing.x_max).
        It meets that line almost tangentially, so with min_feature it stops where the ear is
        min_feature wide: a round end of radius min_feature / 2 tangent to both the ear and the
        housing side. Sets self.ear_foot, where the outline continues down the housing side."""
        p, xs = self.p, self.p.housing.x_max
        r = p.min_feature / 2
        if r <= 0:
            self.ear_foot = Vector(*THUMB_EAR[-1])
            return [self.ear]

        # centre (xs + r, y) of the round end: r from the ear (the ear is wider below the tail)
        def gap(y):
            return self.ear.distance_to(Vector(xs + r, y)) - r
        lo, hi = THUMB_EAR[0][1], THUMB_EAR[-1][1]
        if gap(lo) <= 0:
            raise ValueError(f"thumb ear: nowhere {p.min_feature} mm wide")
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if gap(mid) > 0 else (lo, mid)
        c = Vector(xs + r, (lo + hi) / 2)
        crv = BRep_Tool.Curve_s(self.ear.wrapped, 0.0, 1.0)
        k = GeomAPI_ProjectPointOnCurve(gp_Pnt(c.X, c.Y, 0), crv).LowerDistanceParameter()
        u = (k - crv.FirstParameter()) / (crv.LastParameter() - crv.FirstParameter())   # 0..1
        q_ear = self.ear @ u
        self.ear_foot = Vector(xs, c.Y)
        mid = c + _unit((q_ear - c) + (self.ear_foot - c)) * r          # over the tail end
        return [self.ear.trim(0.0, u), ThreePointArc(q_ear, mid, self.ear_foot)]

    def pocket_face(self):
        """What the thumb wall must not fill: its inside, and everything from 0.2 mm below the
        main plate's bottom edge up (the wall only exists under the thumb side)."""
        p, h = self.p, self.p.housing
        y_in = h.y_min + p.rim - p.pcb_clearance
        far = 500.0
        ur, top_l, slant_a = self.top
        if p.wall_top_fill:
            # the top side's line (top_l -> ur) run on to the frame's inner line
            xf = h.x_max - p.rim
            yf = top_l.Y + (ur.Y - top_l.Y) / (ur.X - top_l.X) * (xf - top_l.X)
            end = [Vector(xf, far), Vector(xf, yf)]
        else:
            end = [Vector(slant_a.X, far), slant_a, top_l]
        pts = [self.P1, Vector(self.P1.X, y_in), Vector(-far, y_in), Vector(-far, far), *end, ur, self.P0]
        edges = [Spline(*THUMB_INNER)] + [Edge.make_line(a, b) for a, b in zip(pts, pts[1:])]
        return make_face(Wire(edges))

    # --- lip -------------------------------------------------------------------------------
    # The lip's inner edge is THUMB_INNER moved in by a depth that eases from 0 to lip_depth
    # over lip_runout at each end (_inner_edge); the lip is that edge drafted at 45 deg up into
    # the wall over a lip_ledge-high ledge (_hook). The relief is simpler: the full-depth lip
    # grown by the relief, over the whole lip and a little past it (_plain_hook).
    def lip(self, board: Board):
        """The lip: a ledge (floor .. floor + lip_ledge), then a 45 deg chamfer up into the
        wall; full depth in the middle, easing out to nothing at both ends."""
        p = self.p
        if p.lip_location == "far_side":
            return self._far_lip(board)
        return self._hook(self._inner_edge(board), p.lip_depth, p.floor, p.floor + p.lip_ledge)

    def lip_cutter(self, board: Board, grow: float):
        """The relief cut out of the bottom plate for the lip: the lip at full depth grown by
        `grow` normal to its inner faces (ledge side, 45 deg face), over the whole lip, run-outs
        included, plus `grow` past each end (so the run-outs have room to spare, which eases
        hooking in). In plan each end tapers at 45 deg from the inner edge back to the wall
        face (lip_band), so the plate's edge has no sharp inside corner there; the end faces are
        vertical. Reaches into the wall and below the floor so it cuts cleanly. Built on
        THUMB_INNER's offsets (the bottom plate's outline offsets digest those quickly)."""
        p = self.p
        if p.lip_location == "far_side":
            return self._far_cutter(board, grow)
        s0, s1 = self.lip_extent(board)
        # the 45 deg face moved `grow` along its normal meets the ledge's grown face here
        z_mid = p.floor + p.lip_ledge + grow * (math.sqrt(2) - 1)
        return self._plain_hook(p.lip_depth + grow, s0 - grow, s1 + grow, p.floor - 1, z_mid)

    def lip_band(self, board: Board, grow: float):
        """2D footprint of lip_cutter(board, grow) from the wall face in: where the bottom
        plate is cut for the lock. Full depth over the lip plus `grow`, then at each end a
        straight 45 deg line from the inner edge back to the wall face (a trapezoid's end)."""
        p = self.p
        if p.lip_location == "far_side":
            return self._far_band(board, grow)
        s0, s1 = self.lip_extent(board)
        reach = p.lip_depth + grow
        (a, b), (c, d) = self._taper(reach, s0 - grow, s1 + grow)
        wall = Edge(BRepBuilderAPI_MakeEdge(self.curve, self.t_at(s0 - grow - reach), self.t_at(s1 + grow + reach)).Edge())
        inner = Edge(BRepBuilderAPI_MakeEdge(self._offset(reach), self.t_at(s0 - grow), self.t_at(s1 + grow)).Edge())
        face = Face(Wire([wall, Edge.make_line(wall @ 1, inner @ 1), inner.reversed(), Edge.make_line(inner @ 0, wall @ 0)]))
        return face if face.normal_at().Z > 0 else -face

    def _taper(self, reach: float, s0: float, s1: float):
        """The relief's plan ends: for each end, (point of the inner edge at the end, point of
        the wall face `reach` further along), i.e. a straight line at about 45 deg."""
        return [(self.off(self.t_at(s), -reach), self.off(self.t_at(s - sign * reach), 0.0))
                for s, sign in ((s0, 1.0), (s1, -1.0))]

    def _plain_hook(self, reach: float, s0: float, s1: float, z0: float, z_mid: float):
        """Ledge from z0 to z_mid reaching `reach` in from the wall face, then 45 deg up to
        lip_run into the wall, over [s0, s1] of arc length at full depth; the ends are vertical
        planes through the plan taper's lines (_taper), cut from a longer piece."""
        p = self.p
        h = reach + p.lip_run                           # height of the 45 deg part
        back = reach + 2 * p.lip_run + 0.4              # into the wall; top face 0.4 wide
        ext = reach + back + 1.0                        # past where the taper planes leave the back
        ta, tb = self.t_at(s0 - ext), self.t_at(s1 + ext)

        def band(lo, hi, z):
            return self._face(*[Edge(BRepBuilderAPI_MakeEdge(self._offset(o), ta, tb).Edge()) for o in (lo, hi)], z)
        base, top = band(-back, reach, z_mid), band(-back + h, reach - h, z_mid + h)
        solid = (extrude(base, amount=z_mid - z0, dir=(0, 0, -1))
                 + Solid.make_loft([base.outer_wire(), top.outer_wire()], ruled=True))
        for (q_in, q_wall), s in zip(self._taper(reach, s0, s1), (s0, s1)):
            d = q_wall - q_in
            nrm = Vector(-d.Y, d.X, 0)
            inside = self.off(self.t_at((s0 + s1) / 2), 0.0)          # a point of the lip's middle
            if nrm.dot(inside - q_in) < 0:
                nrm = -nrm
            solid = split(solid, Plane(Vector(q_in.X, q_in.Y, 0), z_dir=nrm), keep=Keep.TOP)
        return solid

    def depth(self, s: float, s0: float, s1: float, full: float) -> float:
        """Lip depth at arc length s for a lip over [s0, s1]: `full`, easing to 0 over
        lip_runout at each end (half a cosine wave: tangent to THUMB_INNER at the ends)."""
        x = min(s - s0, s1 - s, self.p.lip_runout)
        return full * 0.5 * (1 - math.cos(math.pi * x / self.p.lip_runout)) if x > 0 else 0.0

    def _inner_edge(self, board: Board):
        """(curve, ta, tb): the lip's inner edge as a B-spline over THUMB_INNER's parameters
        [ta, tb] (point for point with it): THUMB_INNER moved in by depth(s); it starts and ends
        on THUMB_INNER, tangent to it."""
        key = ("edge", id(board))
        if key not in self._offsets:
            p = self.p
            s0, s1 = self.lip_extent(board)
            R = p.lip_runout
            # dense along the run-outs, where the depth changes
            ss = sorted({*(s0 + R * i / 80 for i in range(81)), *(s1 - R * i / 80 for i in range(81)),
                         *(s0 + R + (s1 - s0 - 2 * R) * i / 60 for i in range(61))})
            pts, prm = HArray1_gp_Pnt(1, len(ss)), HArray1_double(1, len(ss))
            for i, s in enumerate(ss, 1):
                t = self.t_at(s)
                q = self.off(t, -self.depth(s, s0, s1, p.lip_depth))
                pts.SetValue(i, gp_Pnt(q.X, q.Y, 0))
                prm.SetValue(i, t)
            ta, tb = prm.Value(1), prm.Value(len(ss))
            it = GeomAPI_Interpolate(pts, prm, False, 1e-9)
            it.Load(self.curve.DN(ta, 1), self.curve.DN(tb, 1), False)   # tangent to THUMB_INNER
            it.Perform()
            self._offsets[key] = (it.Curve(), ta, tb)
        return self._offsets[key]

    def _face(self, a: Edge, b: Edge, z: float = 0.0) -> Face:
        """Face between two edges that run the same way, closed by straight ends, at z."""
        face = Face(Wire([a, Edge.make_line(a @ 1, b @ 1), b.reversed(), Edge.make_line(b @ 0, a @ 0)]))
        return Pos(0, 0, z) * (face if face.normal_at().Z > 0 else -face)

    def _hook(self, inner, reach: float, z0: float, z_mid: float):
        """Ledge from z0 to z_mid out to the edge `inner` = (curve, ta, tb), then the edge
        drafted at 45 deg (each point moved toward the wall along the edge's normal as it
        rises) until lip_run into the wall, `reach` being the edge's greatest depth. The ends
        are THUMB_INNER's normals at ta and tb (the edge starts and ends on its normal, tangent
        to it), its back is far enough into the wall for the 45 deg face to run out inside."""
        p = self.p
        edge, ta, tb = inner
        h = reach + p.lip_run                           # height of the 45 deg part
        back = self._offset(-(reach + 2 * p.lip_run + 0.4))   # into the wall; top face >= 0.4 wide

        def e(c):
            return Edge(BRepBuilderAPI_MakeEdge(c, ta, tb).Edge())
        base = self._face(e(back), e(edge), z_mid)
        top = self._face(e(back), e(_approx(Geom_OffsetCurve(edge, -h, gp_Dir(0, 0, 1)), ta, tb)), z_mid + h)
        # ledge down from the loft's own base face, so the two share it exactly
        return (extrude(base, amount=z_mid - z0, dir=(0, 0, -1))
                + Solid.make_loft([base.outer_wire(), top.outer_wire()], ruled=True))

    def lip_extent(self, board: Board):
        """(s0, s1): where the lip runs, as arc length along THUMB_INNER from P0 ("bottom")
        or along the far side from P0 ("far_side")."""
        p = self.p
        if p.lip_location == "far_side":
            return self._far_extent(board)
        if p.lip_location != "bottom":
            raise ValueError(f"lip_location {p.lip_location!r}")
        return self.lip_report(board)["span"]

    # arc length along the inner curve; the B-spline is parametrised near arc length, but
    # the lip is placed by true arc length
    def t_at(self, s: float) -> float:
        if not 0.0 <= s <= self.length:
            raise ValueError(f"thumb lip: {s:.2f} mm is off THUMB_INNER (0 .. {self.length:.2f})")
        return GCPnts_AbscissaPoint(self.adaptor, s, self.t0).Parameter()

    def _offset(self, o: float):
        """The inner curve moved o inward (to the pocket side; negative: into the wall), as a
        B-spline with the inner curve's parametrisation (so offsets built from it line up
        point for point, and the STEP carries no offset curves)."""
        key = round(o, 9)
        if key not in self._offsets:
            if key == 0:
                self._offsets[key] = self.curve
            else:
                self._offsets[key] = _approx(Geom_OffsetCurve(self.curve, o, gp_Dir(0, 0, 1)), self.t0, self.t1)
        return self._offsets[key]

    def lip_report(self, board: Board) -> dict:
        """The "bottom" lip's placement: span (s0, s1) along THUMB_INNER (run-outs included),
        its ends on the wall face and the ends of its full-depth part (case frame of this
        build), and the closest part under the PCB to the relief (ref, distance; the relief
        taken at full depth all along, so the run-outs are covered)."""
        key = id(board)
        if key in self._reports:
            return self._reports[key][1]
        p = self.p
        reach = p.lip_depth + p.lip_relief                 # the bottom plate's cut
        ds = 0.05
        n = int(self.length / ds)
        ss = [self.length * i / n for i in range(n + 1)]
        segs = []
        for s in ss:
            P, V = gp_Pnt(), gp_Vec()
            self.curve.D1(self.t_at(s), P, V)
            a = Vector(P.X(), P.Y())
            segs.append((a, a - self._left(Vector(V.X(), V.Y())) * reach))
        lo = (min(min(a.X, b.X) for a, b in segs), min(min(a.Y, b.Y) for a, b in segs))
        hi = (max(max(a.X, b.X) for a, b in segs), max(max(a.Y, b.Y) for a, b in segs))
        near = [(ref, pts) for ref, pts in underside_keepouts(board)
                if _bbox_gap(pts, lo, hi) < p.lip_part_clearance + 1.0]
        dist = []                                          # per sample: (distance, ref)
        for a, b in segs:
            dist.append(min(((_seg_poly_dist((a.X, a.Y), (b.X, b.Y), pts), ref) for ref, pts in near),
                            default=(math.inf, None)))
        # clear runs of samples, shrunk by the relief past the lip's ends and kept far enough
        # from the curve's ends for the relief's run-on (_plain_hook)
        edge = p.lip_relief + 2 * reach + 2 * p.lip_run + 1.5   # the relief's run-on stays on the curve
        R = p.lip_runout
        runs, start = [], None
        for i, (d, _) in enumerate(dist + [(-1.0, None)]):
            if d >= p.lip_part_clearance and start is None:
                start = i
            elif d < p.lip_part_clearance and start is not None:
                runs.append((max(ss[start] + p.lip_relief + reach, edge),          # relief + taper
                             min(ss[i - 1] - p.lip_relief - reach, self.length - edge)))
                start = None
        c = p.lip_centre * self.length
        best = None
        for a, b in runs:
            if b <= a:
                continue
            ln = min(p.lip_length + 2 * R, b - a)
            s0 = min(max(c - ln / 2, a), b - ln)
            cand = (ln - 2 * R, -abs(s0 + ln / 2 - c), (s0, s0 + ln))
            best = cand if best is None or cand[:2] > best[:2] else best
        if best is None or best[0] < p.lip_min_length:
            worst = sorted({ref for d, ref in dist if d < p.lip_part_clearance})
            raise ValueError(f"thumb lip: no stretch of THUMB_INNER {p.lip_min_length} mm long (plus "
                             f"{R} mm run-outs) keeps "
                             f"{p.lip_part_clearance} mm from the parts under the PCB "
                             f"(longest {0 if best is None else best[0]:.1f} mm; in the way: {', '.join(worst)})")
        s0, s1 = best[2]
        inside = [d for s, d in zip(ss, dist) if s0 - p.lip_relief - reach - ds <= s <= s1 + p.lip_relief + reach + ds]
        d, ref = min(inside, key=lambda t: t[0])
        def at(s):
            q = self.off(self.t_at(s), 0)
            return (round(q.X, 2), round(q.Y, 2))
        rep = {"span": (s0, s1), "ends": (at(s0), at(s1)), "full": (at(s0 + R), at(s1 - R)), "closest": (ref, d)}
        self._reports[key] = (board, rep)                 # (board kept so its id stays unique)
        return rep

    def wall_face(self):
        """Footprint of the thumb wall (without the lip)."""
        return self.outer_face() - self.pocket_face()

    def _far_extent(self, board: Board):
        """(s0, s1): where the far-side lip runs along the far side, from P0 in direction w."""
        p = self.p
        s0, s1 = p.lip_span
        if p.lip_sockets == "shorten":
            grown = socket_outlines(board, p.socket_fp, p.socket_clearance + p.lip_margin)
            return self._clear_span(grown, s0, s1)
        if p.lip_sockets != "notch":
            raise ValueError(f"lip_sockets {p.lip_sockets!r}")
        return s0, s1

    def _clear_span(self, sockets, s0, s1):
        """Longest part of [s0, s1] (along the far side) whose lip footprint no socket reaches."""
        p = self.p
        a, b = self.P0 + self.w * s0, self.P0 + self.w * s1
        band = poly(*[(q.X, q.Y) for q in (a, b, b - self.n * p.lip_depth, a - self.n * p.lip_depth)])
        hit = []
        for f in (sockets & band).faces() if sockets else []:
            ss = [(v.center() - self.P0).dot(self.w) for v in f.vertices()]
            hit.append((min(ss), max(ss)))
        free, cur = [], s0
        for h0, h1 in sorted(hit):
            if h0 > cur:
                free.append((cur, h0))
            cur = max(cur, h1)
        if cur < s1:
            free.append((cur, s1))
        if not free:
            raise ValueError("thumb lip: sockets cover all of it")
        return max(free, key=lambda t: t[1] - t[0])


    # --- far-side lip (Fusion) ---------------------------------------------------------------
    def _far_lip(self, board: Board):
        """Ledge + 45 deg chamfer along the far side's inner face, kept clear of the sockets
        (see lip_sockets)."""
        p = self.p
        s0, s1 = self._far_extent(board)
        sockets = socket_outlines(board, p.socket_fp, p.socket_clearance)
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
        if p.lip_sockets == "shorten":
            return lip
        return lip - prism(sockets & keep, p.floor - 1, p.pcb_top)

    def _far_cutter(self, board: Board, grow: float):
        """The lip grown by `grow` on its inner faces (ledge side and 45 deg face), for cutting
        the matching relief out of the bottom plate. Reaches into the wall and below the
        floor so it cuts cleanly; runs `grow` past both ends of the lip."""
        p = self.p
        s0, s1 = self._far_extent(board)
        z0, z1, d = p.floor, p.floor + p.lip_ledge, p.lip_depth
        a = self.P0 + self.w * (s0 - grow)

        def pt(t, z):                                  # t inward from the wall's inner face
            q = a - self.n * t
            return Vector(q.X, q.Y, z)
        lift = grow * math.sqrt(2)                     # 45 deg face moved `grow` along its normal
        sec = Wire.make_polygon([pt(-1, z0 - 1), pt(d + grow, z0 - 1),
                                 pt(d + grow, z1 + lift - grow), pt(-1, z1 + d + 1 + lift)], close=True)
        return extrude(Face(sec), amount=s1 - s0 + 2 * grow, dir=self.w)

    def _far_band(self, board: Board, grow: float):
        """2D footprint of lip_cutter(board, grow): where the bottom plate is cut for the lock."""
        p = self.p
        s0, s1 = self._far_extent(board)
        a, b = self.P0 + self.w * (s0 - grow), self.P0 + self.w * (s1 + grow)
        d = p.lip_depth + grow
        return poly(*[(q.X, q.Y) for q in (a, b, b - self.n * d, a - self.n * d)])

# --- what the lip and its relief keep clear of ----------------------------------------------

def _hull(pts):
    """Convex hull (counter-clockwise) of 2D points."""
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def half(seq):
        h = []
        for q in seq:
            while len(h) >= 2 and ((h[-1][0] - h[-2][0]) * (q[1] - h[-2][1])
                                   - (h[-1][1] - h[-2][1]) * (q[0] - h[-2][0])) <= 0:
                h.pop()
            h.append(q)
        return h[:-1]
    return half(pts) + half(reversed(pts))


def _ngon(x, y, r, n=8):
    """n-gon around the circle (x, y, r) (circumscribed: covers the circle)."""
    rr = r / math.cos(math.pi / n)
    return [(x + rr * math.cos(2 * math.pi * (i + 0.5) / n), y + rr * math.sin(2 * math.pi * (i + 0.5) / n))
            for i in range(n)]


def underside_keepouts(board: Board):
    """[(name, polygon)] of what hangs below the PCB, case frame: each back-side footprint as
    the hull of its Fab outline and its pads (each pad as the circle through its corners, so
    the pad's rotation doesn't matter), and each drilled hole of a front-side footprint (a
    switch's legs and posts come through it)."""
    out = []
    for f in board.footprints:
        if f.side == "B":
            pts = list(f.fab or [])
            for pd in f.pads:
                pts += _ngon(pd.x, pd.y, max(math.hypot(*pd.size) / 2, (pd.drill or 0) / 2))
            if pts:
                out.append((f.ref, _hull(pts)))
        else:
            out += [(f"{f.ref} hole", _ngon(pd.x, pd.y, pd.drill / 2)) for pd in f.pads if pd.drill]
    return out


def _bbox_gap(pts, lo, hi) -> float:
    """Gap between the polygon's bounding box and the box lo..hi (0 if they overlap)."""
    dx = max(lo[0] - max(x for x, _ in pts), min(x for x, _ in pts) - hi[0], 0.0)
    dy = max(lo[1] - max(y for _, y in pts), min(y for _, y in pts) - hi[1], 0.0)
    return math.hypot(dx, dy)


def _seg_dist(a, b, c, d) -> float:
    """Distance between the 2D segments ab and cd."""
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    if (cross(a, b, c) > 0) != (cross(a, b, d) > 0) and (cross(c, d, a) > 0) != (cross(c, d, b) > 0):
        return 0.0

    def pt(p, s, e):
        vx, vy = e[0] - s[0], e[1] - s[1]
        L2 = vx * vx + vy * vy
        k = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - s[0]) * vx + (p[1] - s[1]) * vy) / L2))
        return math.hypot(p[0] - s[0] - k * vx, p[1] - s[1] - k * vy)
    return min(pt(a, c, d), pt(b, c, d), pt(c, a, b), pt(d, a, b))


def _inside(p, poly_pts) -> bool:
    x, y, n, ins = p[0], p[1], len(poly_pts), False
    for i in range(n):
        (x0, y0), (x1, y1) = poly_pts[i], poly_pts[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            ins = not ins
    return ins


def _seg_poly_dist(a, b, poly_pts) -> float:
    """Distance from segment ab to a closed polygon (0 if it reaches inside)."""
    if _inside(a, poly_pts) or _inside(b, poly_pts):
        return 0.0
    n = len(poly_pts)
    return min(_seg_dist(a, b, poly_pts[i], poly_pts[(i + 1) % n]) for i in range(n))


def socket_outlines(board: Board, name: str, grow: float):
    """Union of the sockets' Fab outlines (from the board) grown by `grow`."""
    face = None
    for f in board.of_kind(name):
        if not f.fab:
            raise ValueError(f"{f.ref}: footprint {name} has no closed Fab outline")
        o = offset(poly(*f.fab), grow, kind=Kind.INTERSECTION)
        face = o if face is None else face + o
    return face


def main_rect(board: Board, p: SwitchplateParams):
    """(x0, x1, y0, y1) of the plate's main rectangle.

    "pcb": the PCB outline's straight outer edges (flush with the PCB, so the skirt's
    0.2 mm clearance applies): right edge above the thumb side, left edge below the
    XIAO corner, bottom edge left of the thumb side, top edge right of the XIAO corner.
    "housing": the housing box inset by the rim, as in Fusion (right edge 0.2 mm proud
    of the PCB, line-to-line with the skirt)."""
    h = p.housing
    if p.main_rect == "housing":
        return h.x_min + p.rim, h.x_max - p.rim, h.y_min + p.rim, h.y_max - p.rim
    if p.main_rect != "pcb":
        raise ValueError(f"main_rect {p.main_rect!r}")
    pts = board.outline
    x1 = max(x for x, y in pts if y > h.thumb_gap_top)
    x0 = min(x for x, y in pts if y < h.xiao_zone[2])
    y0 = min(y for x, y in pts if x < h.thumb_gap_x)
    y1 = max(y for x, y in pts if x > h.xiao_zone[1])
    return x0, x1, y0, y1


def outline(p: SwitchplateParams, notch_top: float, thumb: ThumbWall, rect):
    """Plate outline: the main rectangle, corner and notch cut-outs, plus the thumb side."""
    h = p.housing
    x0, x1, y0, y1 = rect
    nx0, nx1 = h.notch_x
    xc, yc = p.xiao_cut
    tx, ty = p.h4_tab
    face = box(x0, x1, y0, y1)
    face -= box(x0 - 1, xc, yc, y1 + 1) - box(tx, xc, ty, y1 + 1)   # XIAO corner, tab at H4
    face -= box(nx0, nx1, y0 - 1, notch_top)                          # bottom notch
    face -= box(p.battery_cut[0], nx0, y0 - 1, p.battery_cut[1])      # battery leads
    return face + thumb.outer_face(rect)


def build_switchplate(board: Board, p: SwitchplateParams = SwitchplateParams()):
    thumb = ThumbWall(p)
    rect = main_rect(board, p)
    plate2d = outline(p, p.housing.notch_y[1], thumb, rect) - plate_cuts(board, p, p.key_cut)
    relief2d = outline(p, p.relief_notch_top, thumb, rect) - plate_cuts(board, p, p.key_pocket)
    wall2d = thumb.wall_face()

    part = prism(plate2d, -p.plate, 0.0)
    part += prism(relief2d, p.pcb_top, -p.plate)
    part += prism(wall2d, p.floor, p.pcb_top)
    part += thumb.lip(board)
    return part.clean()


# The Fusion reference has the lip on the far side, notched around the socket, the wall's
# top end as a tip on the housing slant, the ear's knife-edge tail, and has the holes and
# thumb keys where the earlier PCB had them.
REFERENCE_PARAMS = SwitchplateParams(lip_location="far_side", lip_sockets="notch", wall_top_fill=False,
                                     min_feature=0.0, hole_shift=FUSION_HOLE_SHIFT,
                                     thumb_keys=FUSION_THUMB_KEYS, bridge_walls=0.0,
                                     main_rect="housing")
