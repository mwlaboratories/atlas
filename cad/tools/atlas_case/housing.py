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

from build123d import (BuildLine, BuildSketch, CenterArc, Circle, Line, Plane, Polygon, Pos,
                       Rectangle, RectangleRounded, extrude, make_face, offset, Kind)

from .geom import box, prism
from .kicad import Board


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
    # notch below the middle columns: a 1.4 mm recess under the frame
    notch_x: tuple = (-63.0, -17.0)
    notch_y: tuple = (-23.25, -7.5)
    notch_depth: float = 1.4
    # XIAO corner: no skirt there; pocket + USB-C slot through the side wall
    xiao_zone: tuple = (-97.0, -68.0, 28.0, 57.0)          # x0, x1, y0, y1
    usb_pocket: tuple = (-90.0, -75.963, 39.0, 53.0, 2.7)  # x0, x1, y0, y1, depth
    usb_slot: tuple = (41.23, 50.77, -1.295, 2.515)          # USB-C opening: y0, y1, z0, z1
    usb_slot_r: float = 1.55                                 # its corner radius
    # screw holes from below, at the PCB mounting holes
    screw_r: float = 1.85
    screw_depth: float = 3.0
    screw_holes: tuple = ("H2", "H3", "H4", "H5")


def key_columns(board: Board):
    """Main-cluster switches grouped by column (same x), as {x: [y, ...]}; thumbs (rotated) left out."""
    cols = defaultdict(list)
    for f in board.of_kind("choc_v1_v2_hotswap"):
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


def build_housing(board: Board, p: HousingParams = HousingParams()):
    pcb = Polygon(*board.outline, align=None)
    tall = 50.0

    # frame: block minus the key opening and the open thumb side
    part = frame_body(p)
    cut2d = key_opening(board, p) + thumb_gap(p)
    part -= prism(cut2d, -tall, tall)

    # skirt: outer box minus the PCB (+clearance), the notch, the thumb gap and the XIAO corner
    skirt2d = (box(p.x_min, p.x_max, p.y_min, p.y_max)
               - offset(pcb, p.pcb_clearance, kind=Kind.INTERSECTION)
               - box(*p.notch_x, p.notch_y[0], 0.0)
               - thumb_gap(p)
               - box(*p.xiao_zone))
    part += prism(skirt2d, -p.skirt_depth, 0)

    # recess under the frame at the notch
    part -= prism(box(*p.notch_x, *p.notch_y), 0, p.notch_depth)

    # XIAO pocket and USB-C slot through the side wall
    x0, x1, y0, y1, d = p.usb_pocket
    part -= prism(box(x0, x1, y0, y1), 0, d)
    ya, yb, za, zb = p.usb_slot
    slot = Plane.YZ * Pos((ya + yb) / 2, (za + zb) / 2) * RectangleRounded(yb - ya, zb - za, p.usb_slot_r)
    part -= Pos(p.x_min - 1, 0, 0) * extrude(slot, amount=x0 - p.x_min + 1, dir=(1, 0, 0))

    # blind screw holes from below at the PCB mounting holes
    for ref in p.screw_holes:
        h = board.by_ref(ref)
        part -= prism(Pos(h.x, h.y) * Circle(p.screw_r), 0, p.screw_depth)
    return part
