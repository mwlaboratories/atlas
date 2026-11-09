"""Housing: the frame around the keys, with a skirt that wraps the PCB edge.

Built for the LEFT half in the case frame (see kicad.Frame); the right half is
the same construction on the mirrored board, mirrored back (see build.py).

Layers (z, mm):
  -6.2 .. 0   skirt: a wall hugging the PCB outline (+0.2 mm clearance)
   0   .. 5   frame: top surface with the key opening, rounded front/back edges
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from build123d import (Align, BuildLine, BuildSketch, CenterArc, Circle, Compound, Cylinder, Line, Plane,
                       Polygon, Pos, Rectangle, RectangleRounded, extrude, make_face, offset, Kind)

from .geom import box, prism
from .kicad import SWITCH, Board


@dataclass(frozen=True)
class HousingParams:
    # outer box of the frame (case frame, left half)
    x_min: float = -97.0
    x_max: float = 17.0
    y_min: float = -25.0
    y_max: float = 57.0
    top: float = 5.0                 # frame height above the skirt
    edge_round: float = 5.0          # front/back top edges: quarter round over the full height
    skirt_depth: float = 6.2
    pcb_clearance: float = 0.2       # skirt inner wall to PCB edge
    # key opening: per switch column, a rectangle around its keys
    key_half_w: float = 11.0         # column centre -> opening side
    key_end: float = 9.5             # first/last key centre -> opening end
    # the thumb side is open: left edge of the gap and its slanted lower edge
    thumb_gap_x: float = -11.405
    thumb_gap_top: float = -7.5
    thumb_slant: tuple = ((11.0, -10.0), (17.0, -13.0))
    # battery tray: the PCB notch below the middle columns, with no skirt. The cell
    # (701535 pouch, 7 x 15 x 35, readme BOM) lies on the bottom plate's bay floor and its
    # soft edge slides a little under the PCB, so the tray needs no room past the PCB's
    # front edge (checked on a print, 2026-11-08); it does need the recess in the wood
    # above it (without it the print came out ~1.5 mm short, 2026-11-08).
    notch_x: tuple = (-63.0, -17.0)          # = the PCB notch (x)
    # (front, back): the front is the skirt's inner face along the PCB's front edge
    # (y -20 minus pcb_clearance) plus a 1.6 mm lip into the front wall for the pouch (the
    # flush tray came out tight on a print, user 2026-11-09); 1.6 = tray_lip_corner_r, so
    # the lip's rounded corners are full quarter rounds that end tangent on the skirt face;
    # the wall keeps 3.2 mm there; the back is where the switch plate's layer is cut back to (it keeps a 2.5 mm strip
    # in front of the first row's 14 mm cutouts). Fusion had a 3.25 mm lip: (-23.25, -7.5).
    notch_y: tuple = (-21.8, -7.5)
    # recess in the wood above z = 0 (the tray ceiling); None = derived: the cell's top
    # (tray_floor + battery thickness) + battery_clearance = 1.4, as in Fusion. The
    # clearance is kept at 0.2 so the wood over the tray stays 3.6 mm for the CNC
    # (0.3 gave 1.5 and thinner wood; user, 2026-11-08).
    notch_depth: float | None = 1.4          # kept at 1.4 for the wood over the tray (user)
    # a groove in that roof where the pouch is thickest: this wide, centred on the tray
    # opening (between notch_y), along the tray's whole length, this much deeper (user,
    # 2026-11-09); width 0 = none
    tray_groove_width: float = 9.5
    tray_groove_depth: float = 0.75
    # the groove's centre line (y); None = the tray opening's centre (user, 2026-11-09:
    # centred on the pouch it sat 0.35 mm off the opening's centre and looked off)
    tray_groove_y: float | None = None
    # the lip's two front corners (into the front wall): the 1/8" end mill's radius, so
    # CAM cuts them as drawn; the lip is this deep, so they meet the skirt face tangent
    tray_lip_corner_r: float = 1.6
    battery: tuple = (35.0, 15.0, 7.0)       # 701535 pouch: length (x), width (y), thickness (z)
    battery_clearance: float = 0.2
    # the bottom plate's bay floor as printed (its bottom + floor_skin); keep in step with
    # bottomplate.py (importing it here would be circular)
    tray_floor: float = -5.5
    # CNC: the housing is milled in wood with a 1/8" (3.175 mm) end mill, so an inside
    # vertical corner is at least its radius; applied to the tray's inside corners
    # (0 = sharp, as drawn in Fusion)
    # 0 = sharp corners everywhere (default): the CAM software rounds inside corners to the
    # tool itself; rounding them in the model left thin wood flaps where an opening corner
    # met the tray recess or the thumb gap (user, 2026-11-08: "back to straight corners")
    cnc_corner_r: float = 0.0
    # corners of the key openings / thumb gap in the top of the housing (seen from above):
    # rounded so the end mill can cut them and they follow the keycaps' corners (MBK
    # low-profile keycaps: 17.45 x 16.45, corner r 1.75); 0 = sharp, as drawn in Fusion
    opening_corner_r: float = 0.0
    # XIAO corner: no skirt there; pocket + USB-C slot through the side wall
    xiao_zone: tuple = (-97.0, -68.0, 28.0, 57.0)          # x0, x1, y0, y1
    usb_pocket: tuple = (-90.0, -75.963, 39.0, 53.0, 2.7)  # x0, x1, y0, y1, depth
    usb_slot: tuple = (41.23, 50.77, -1.295, 2.515)          # USB-C opening: y0, y1, z0, z1
    usb_slot_r: float = 1.55                                 # its corner radius
    # screw holes from below, at the PCB mounting holes
    screw_r: float = 1.85
    screw_depth: float = 3.0
    screw_holes: tuple = ("H2", "H3", "H4", "H5")


    def __post_init__(self):
        if self.notch_depth is None:
            depth = self.tray_floor + self.battery[2] + self.battery_clearance
            object.__setattr__(self, "notch_depth", round(max(0.0, depth), 6))

def key_columns(board: Board):
    """Main-cluster switches grouped by column (same x), as {x: [y, ...]}; thumbs (rotated) left out."""
    cols = defaultdict(list)
    for f in board.of_kind(SWITCH):
        if abs(f.rot % 180) < 1e-6:
            cols[round(f.x, 3)].append(f.y)
    return {x: sorted(ys) for x, ys in sorted(cols.items())}


def key_opening(board: Board, p: HousingParams):
    face = None
    for x, ys in key_columns(board).items():
        r = Pos((x), (ys[0] + ys[-1]) / 2) * Rectangle(2 * p.key_half_w, ys[-1] - ys[0] + 2 * p.key_end)
        face = r if face is None else face + r
    return face


def thumb_gap(p: HousingParams):
    (sx0, sy0), (sx1, sy1) = p.thumb_slant
    far = p.y_min - 10
    return Polygon((p.thumb_gap_x, far), (p.thumb_gap_x, p.thumb_gap_top), (sx0, p.thumb_gap_top),
                   (sx0, sy0), (sx1, sy1), (p.x_max + 10, sy1 + (sy1 - sy0) / (sx1 - sx0) * 10),
                   (p.x_max + 10, far), align=None)


def tray_face(p: HousingParams, y1: float, recess: bool = False):
    """The battery tray from its front edge to y1. Its front is flush with the skirt's
    inner face along the PCB edge, so it has no inside corners of its own; were it to
    reach past that face again (REFERENCE_PARAMS), its two front corners get cnc_corner_r."""
    (x0, x1), y0 = p.notch_x, p.notch_y[0]
    face = box(x0, x1, y0, y1)
    # the recess above z = 0 is a pocket in the wood, so its front corners are inside
    # corners there even when the tray front is flush with the skirt below
    lip = y0 < -20.0 - p.pcb_clearance - 1e-6
    r = p.tray_lip_corner_r if lip and p.tray_lip_corner_r > 0 else (
        p.cnc_corner_r if p.cnc_corner_r > 0 and (recess or lip) else 0.0)
    if r > 0:
        f = face.faces()[0]
        face = f.fillet_2d(r, [v for v in f.vertices() if abs(v.Y - y0) < 1e-6])
    return face


def frame_body(p: HousingParams):
    """Outer block with both long top edges rounded, as a YZ profile run along x."""
    r = p.edge_round
    with BuildSketch(Plane.YZ) as sk:
        with BuildLine():
            Line((p.y_min, 0), (p.y_max, 0))
            CenterArc((p.y_max - r, p.top - r), r, 0, 90)
            Line((p.y_max - r, p.top), (p.y_min + r, p.top))
            CenterArc((p.y_min + r, p.top - r), r, 90, 90)
        make_face()
    return Pos(p.x_min, 0, 0) * extrude(sk.sketch, amount=p.x_max - p.x_min)


def skirt_face(board: Board, p: HousingParams):
    """Footprint of the skirt: outer box minus the PCB (+clearance), the notch, the thumb
    gap and the XIAO corner."""
    pcb = Polygon(*board.outline, align=None)
    return (box(p.x_min, p.x_max, p.y_min, p.y_max)
            - offset(pcb, p.pcb_clearance, kind=Kind.INTERSECTION)
            - tray_face(p, 0.0)
            - thumb_gap(p)
            - box(*p.xiao_zone))


def dogbone_centres(material, r: float, min_turn: float = 30.0):
    """Tool centres for dogbones at the inside corners of a 2D material face: vertices
    where the material's boundary turns into the material by more than min_turn degrees
    (so not the small turns between the chords of a curve). The tool circle (radius r)
    passes through the corner, centred r along the corner's bisector into the void."""
    import math
    out = []
    for w in [material.outer_wire()] + list(material.inner_wires()):
        es = w.order_edges()
        n = len(es)
        for k in range(n):
            e0, e1 = es[k], es[(k + 1) % n]
            v = e0.position_at(1)
            t0 = e0.tangent_at(1)
            t1 = e1.tangent_at(0)
            cross = t0.X * t1.Y - t0.Y * t1.X
            ang = math.degrees(math.atan2(cross, t0.X * t1.X + t0.Y * t1.Y))
            # the outer wire runs counter-clockwise around the material (material on the
            # left); a right turn (negative angle) is then a reflex corner of the material,
            # i.e. a convex corner of the void the mill can't reach
            if w is not material.outer_wire():
                pass
            if -180 + 1e-6 < ang < -min_turn:
                bis = (t1 - t0)
                if bis.length < 1e-9:
                    continue
                bis = bis.normalized()          # points into the void at a right turn
                out.append(v + bis * r)
    return out


def build_housing(board: Board, p: HousingParams = HousingParams()):
    tall = 50.0

    # frame: block minus the key opening and the open thumb side
    part = frame_body(p)
    cut2d = key_opening(board, p) + thumb_gap(p)
    if p.opening_corner_r > 0:
        # round the opening's corners (the void's convex corners): shrink it by r, grow it
        # back with round corners; its straight sides stay where they are
        r = p.opening_corner_r
        cut2d = offset(offset(cut2d, -r, kind=Kind.INTERSECTION), r, kind=Kind.ARC)
    part -= prism(cut2d, -tall, tall)
    skirt = skirt_face(board, p)
    part += prism(skirt, -p.skirt_depth, 0)
    if p.cnc_corner_r > 0:
        # the skirt's inside corners where it hugs a convex PCB corner can't be rounded
        # (wood would cover the PCB corner): dogbones, a tool-sized relief into the wood
        # (hidden under the bottom plate; the plate keeps following the plain skirt)
        part -= Compound([Pos(c.X, c.Y, -p.skirt_depth - 1) * Cylinder(p.cnc_corner_r, p.skirt_depth + 1,
                                                                    align=(Align.CENTER, Align.CENTER, Align.MIN))
                          for f in skirt.faces() for c in dogbone_centres(f, p.cnc_corner_r)])

    # battery tray ceiling: recess under the frame at the notch
    if p.notch_depth > 0:
        part -= prism(tray_face(p, p.notch_y[1], recess=True), 0, p.notch_depth)
        if p.tray_groove_width > 0 and p.tray_groove_depth > 0:
            (gx0, gx1) = p.notch_x
            yc = (p.notch_y[0] + p.notch_y[1]) / 2 if p.tray_groove_y is None else p.tray_groove_y
            hw = p.tray_groove_width / 2
            part -= prism(box(gx0, gx1, yc - hw, yc + hw), p.notch_depth - 0.01, p.notch_depth + p.tray_groove_depth)

    # XIAO pocket and USB-C slot through the side wall
    x0, x1, y0, y1, d = p.usb_pocket
    # a pocket milled from below: its four vertical inside corners get the end mill's
    # radius (cnc_corner_r; 0 = sharp, as drawn in Fusion)
    pocket = RectangleRounded(x1 - x0, y1 - y0, p.cnc_corner_r) if p.cnc_corner_r > 0 \
        else Rectangle(x1 - x0, y1 - y0)
    part -= prism(Pos((x0 + x1) / 2, (y0 + y1) / 2) * pocket, 0, d)
    ya, yb, za, zb = p.usb_slot
    slot = Plane.YZ * Pos((ya + yb) / 2, (za + zb) / 2) * RectangleRounded(yb - ya, zb - za, p.usb_slot_r)
    part -= Pos(p.x_min - 1, 0, 0) * extrude(slot, amount=x0 - p.x_min + 1, dir=(1, 0, 0))

    # blind screw holes from below at the PCB mounting holes
    for ref in p.screw_holes:
        h = board.by_ref(ref)
        part -= prism(Pos(h.x, h.y) * Circle(p.screw_r), 0, p.screw_depth)
    return part


# The Fusion housing (cad/atlas-*-housing.step): a roomier battery tray with sharp
# corners; build.py compares this variant.
REFERENCE_PARAMS = HousingParams(notch_y=(-23.25, -7.5), notch_depth=1.4, cnc_corner_r=0.0,
                                 opening_corner_r=0.0, tray_lip_corner_r=0.0)
