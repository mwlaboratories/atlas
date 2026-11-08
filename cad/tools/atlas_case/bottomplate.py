"""Bottom plate: the plate under the PCB, with pockets for what sits on its back.

Built for the LEFT half in the case frame (see kicad.Frame); the right half is
the same construction on the mirrored board, mirrored back (see build.py).
On that mirrored board a back-side part keeps its position but its outline is
seen from the other side, so footprint-local shapes are mirrored in x there
(`place`).

Layers (z, mm):
  -6.2 .. -3.8  the plate; pockets cut down from its top, a few holes from below;
                socket pockets stop 0.4 mm above the bottom (floor_skin)
  -3.8 .. 0     a thin wall around the XIAO corner, where the housing has no skirt
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from build123d import (Axis, Circle, Compound, Face, Kind, Line, Plane, Pos, Rectangle,
                       RectangleRounded, Rot, Spline, ThreePointArc, Wire, extrude, mirror, offset,
                       revolve)

from .geom import (box, drop_short_edges, fuse, fuzzy_cut, mean_width, no_offset_curves, open_thin_walls, pinches, poly,
                   split_circles,
                   prism, subtract, tidy)
from .housing import REFERENCE_PARAMS as HOUSING_REFERENCE, HousingParams, skirt_face
from .kicad import DIODE, SOCKET, SOT23, SWITCH, Board, Footprint
from .switchplate import SwitchplateParams, ThumbWall

H = HousingParams()      # defaults below are taken from the default housing


@dataclass(frozen=True)
class BottomplateParams:
    housing: HousingParams = field(default_factory=HousingParams)
    switchplate: SwitchplateParams = field(default_factory=SwitchplateParams)
    bottom: float = -6.2
    top: float = -3.8                    # plate top face
    # outline: "rule" = the space the housing skirt and the switch plate's thumb wall leave,
    # fit_gap from each of them (so flush with the PCB where the skirt hugs it), out to
    # the housing's outer box where nothing surrounds the plate (XIAO corner);
    # "fusion" = OUTLINE as drawn in Fusion
    outline_mode: str = "rule"
    fit_gap: float = 0.2
    # minimum feature size (3 nozzle widths): no material narrower than this is left
    # between pockets, between a pocket and the plate edge, or as a tip or stick of
    # material; such walls are cut away down to the shallower floor, so cuts that nearly
    # touch merge into one, and every corner of material between pockets is rounded to
    # min_feature / 2. A wall that is opened is opened wherever it is thinner than
    # 4/3 min_feature, so one just under the limit gets a clean opening, not a slit
    # (see geom.open_thin_walls). 0 = pockets as drawn. This replaces the hand-drawn
    # FIXUPS, which are used in "fusion" outline mode only.
    min_feature: float = 1.2
    # floor left under the socket pockets, and the minimum under every other pocket,
    # so the first printed layer is a solid sheet (0 = as drawn in Fusion). Two
    # 0.2 mm layers: with one, the slicer fills each pocket floor as its own (top
    # surface) region inside layer 1 and seams it. The sockets hang 1.9 mm below
    # the PCB (its STEP), so 0.4 leaves 0.1 mm under them.
    floor_skin: float = 0.4
    # print layer height: every pocket floor is moved down onto this grid (from the
    # bottom face), so pocket floors differ by whole layers; 0 = floors as specified
    layer: float = 0.2
    # --- per key ---------------------------------------------------------------
    # Kailh socket pocket, through the plate, in the socket footprint's frame:
    # the socket body + its two terminals with ~0.5 mm clearance, as drawn in
    # Fusion (measured; clearance is 0.49-0.50 and the two ends differ by 0.01).
    # Points; an entry ((x, y), (cx, cy)) is an arc to (x, y) around (cx, cy).
    socket_pocket: tuple = (
        (1.18486, 1.72), (4.19426, 1.72), (5.275, 0.63927), (5.275, 0.24), (7.075, 0.24),
        (7.075, -2.44), (5.275, -2.44), (5.275, -2.83926), (4.19426, -3.92), (0.62574, -3.92),
        (-0.47, -2.82426), (-0.47, -2.43985), ((-1.18486, -1.725), (-1.18402, -2.43902)),
        (-4.07992, -1.725), ((-5.275, -0.52992), (-4.08048, -0.53047)), (-5.275, -0.24),
        (-7.075, -0.24), (-7.075, 2.44), (-5.275, 2.44), (-5.275, 2.82927), (-4.18426, 3.92),
        (-0.75026, 3.92), ((0.47, 2.69974), (-0.74843, 2.70157)), (0.47, 2.43486),
        ((1.18486, 1.72), (1.18402, 2.43402)),
    )
    # recess under the switch's centre post, and a relief joining it to the socket pocket
    # through hole under each socket's plastic body only (the metal terminal tabs sit at
    # the PCB face): the pocket's own outline across the body's x span (socket_body_hole);
    # None = no through hole. (socket_through_grow/_r are no longer used by the hole.) The rest of the pocket keeps floor_skin, so layer 1 has one
    # plain rounded hole per key. None = no through hole (0.10 mm left under the socket).
    socket_through_grow: float | None = 0.25
    socket_through_r: float = 0.75
    switch_post_r: float = 2.6
    switch_post_floor: float = -5.7
    # recess under every other hole of the switch footprint (the Choc v1 side legs at
    # x +-5.5 stick out under the PCB), radius drill/2 + this, at switch_post_floor;
    # 0 = none (Fusion had none: 30 of 34 legs landed on plate material)
    switch_hole_clearance: float = 0.25
    post_relief: tuple = (-0.6, 4.28, 0.0, 4.85)   # x0, x1, y0, y1 in the socket frame
    # where a diode sits right off a socket's terminal tip (HS3/6/9 on both halves, at
    # socket-local (8.5, 0.855)), a relief joins the two pockets
    terminal_tip: tuple = (7.075, -1.1)            # socket frame
    bridge_reach: float = 3.0                      # diode centre this close to the tip
    terminal_bridge: tuple = (6.918, 7.557, -1.815, 0.71)
    # --- other back-side parts: rectangle (length x width in the footprint frame) and floor
    diode_pocket: tuple = (4.4, 2.2)
    diode_floor: float = -5.24
    sot23_pocket: tuple = (3.1, 3.6)
    sot23_floor: float = -5.34
    # FFC pads (J1): pocket in its frame (x0, x1, y0, y1; pad 1 at the origin, pads along -y)
    ffc_pocket: tuple = (-2.951, 1.29, -9.71, 1.91)
    ffc_floor: float = -4.4
    # through window around the middle holes H1/H6/H7 (offsets from H1), and the
    # channel joining it to the FFC pocket: the pads' span +- ffc_channel_margin
    window: tuple = (-5.2, 7.95, -11.9, 11.9)
    ffc_channel_margin: float = 3.0
    # --- screws at the PCB mounting holes, from below (M2) ---------------------
    screw_holes: tuple = H.screw_holes
    screw_r: float = 1.2
    counterbore_r: float = 2.1
    counterbore_top: float = -4.4
    counterbore_chamfer: float = 0.1
    # "countersunk" (default: a 90 deg cone that prints without overhang, for M2 flat
    # heads, DIN 7991 / ISO 10642, head 3.8-4.0 mm) or "counterbore" (as in Fusion)
    screw_head: str = "countersunk"
    countersink_r: float = 2.1           # cone radius at the bottom face
    # --- battery bay under the PCB notch (the housing's notch recess is above it)
    bay_x: tuple = H.notch_x
    bay_y: tuple = (H.notch_y[0], H.notch_y[1], -3.0)   # front, step, PCB edge
    bay_floors: tuple = (-5.95, -5.5)                    # front part, back part
    # the bay reaches under the diode lying along its back edge: x half-width and
    # how far past the diode's centre line it goes (diode frame, towards the bay = +y)
    bay_tongue: tuple = (2.469, 0.95)
    bay_tongue_reach: float = 3.0       # a diode closer than this to the bay edge gets it
    # battery leads pocket beside the bay (same on both halves, not mirrored about BT1:
    # it is tied to the bay and the PCB's front edge, not to the pads)
    battery_leads: tuple = (-70.5, -63.0, -20.0, -13.9)
    # --- XIAO corner: the plate fills the housing's skirt-free zone ------------
    xiao_clearance: float = 0.2          # plate wall to the housing cut-out / PCB edge
    wall_top: float = 0.0
    # pocket for the parts under the XIAO (drawn in Fusion; identical on both halves
    # although those parts moved on the right PCB, so not derived from them)
    xiao_pocket: tuple = ((-96.3, -76.0, 40.15, 56.3), (-76.0, -68.2, 51.84, 56.3))
    xiao_floor: float = -5.5
    # reset button hole: centred on SW_RST to within 0.03 mm; kept as drawn so
    # the check stays exact (switch to board.by_ref("SW_RST") if it ever moves)
    reset_hole: tuple = (-93.9, -88.5, 37.7, 41.7)
    # power switch actuator slot through the wall, in SW_PWR's frame: steps of
    # (y0, y1, floor, ceiling); x runs from x_inner out through the wall. One plain slot
    # spanning the three steps drawn in Fusion (FUSION_POWER_SLOT), ceiling on the layer
    # grid above the PCB underside
    power_slot: tuple = ((-2.7, 1.7, -5.8, -3.6),)
    power_slot_x_inner: float = 1.78
    # --- thumb lock: the switch plate's thumb wall has a lip with a 45 deg top face
    # (switchplate.ThumbWall.lip); the plate gets the lip's shape grown by lip_clearance
    # cut out of its edge, so it hooks over the lip: a 45 deg bevel underneath, which
    # prints as a 45 deg overhang
    lip_lock: bool = True
    lip_clearance: float = 0.15          # normal to the 45 deg faces

    # --- feet: shallow round recesses from below, placed freely per half -------
    # off (0) by default: a 0.2 mm recess is a one-layer step at the bed, which
    # prints badly; Fusion had 0.2
    foot_r: float = 5.4
    foot_depth: float = 0.0
    feet: tuple = (("left", ((-86.2, -13.0), (-85.6, 46.7), (-7.7, 46.2), (18.7, -30.5))),
                   ("right", ((-86.2, -13.9), (-85.6, 46.7), (0.0, 46.2), (21.7, -32.0))))


# Plate outline. Frozen: it is a Fusion sketch, the same on both halves although the
# right PCB's thumb edge differs. It runs on the PCB edge at x=-92, y=-20 and y=52
# but 0.2 mm proud of it at x=12, fills the PCB notch down to the housing's notch
# line, takes the XIAO corner out to the housing's outer box, and follows two
# freehand splines plus a stepped edge around the thumb keys.
OUTLINE = (
    (-17.0, -23.25), (-17.0, -20.0), (-8.6, -20.0), (-8.602, -26.021),
    ("spline", ((-7.2174, -26.2948), (-5.8507, -26.6484), (-4.4947, -27.0415), (-3.1533, -27.4815),
                (-1.8227, -27.9533), (-0.4975, -28.4403), (0.8217, -28.943), (2.1337, -29.4644),
                (3.4379, -30.0051), (4.7358, -30.5605), (6.0311, -31.1226)),
     ((0.9889, -0.1489), (0.9168, -0.3993))),
    (7.3254, -31.686),
    ("spline", ((8.8239, -32.3703), (10.3222, -33.0549), (11.8203, -33.7399), (13.318, -34.426),
                (14.8142, -35.1152), (16.306, -35.814), (17.7917, -36.5255), (19.2742, -37.2436),
                (20.7556, -37.9641), (22.2364, -38.686), (23.7162, -39.4097)),
     ((0.9097, -0.4154), (0.8947, -0.4466))),
    (25.1927, -40.1402), (25.443, -39.642), (26.775, -36.978), (25.944, -36.563), (31.355, -25.74),
    (32.186, -26.155), (33.424, -23.68), (14.718, -14.326), (14.291, -13.695),
    (12.0, -10.5), (12.0, 52.0), (-68.2, 52.0), (-68.2, 57.0), (-97.0, 57.0),
    (-97.0, 28.2), (-92.0, 28.2), (-92.0, -20.0), (-63.0, -20.0), (-63.0, -23.25),
)


# One-off cuts drawn by hand in Fusion, different on each half and not following any
# rule found in the boards. Kept as drawn: (half, frame, polygon, floor) where frame is
# a footprint ref (polygon in its frame, mirrored on the right like everything else)
# or None (case frame of the left-oriented build).
FIXUPS = (
    # left: notch off HS8's terminal into the window's top edge, at the post recess depth
    ("left", None, ((-5.045, 38.42), (-2.775, 38.42), (-2.775, 38.89), (-5.045, 38.89)), -5.7),
    # left: HS6's terminal bridge starts 0.09 mm further in than HS3's and HS9's
    ("left", "HS6", ((6.83, 0.24), (7.0, 0.24), (7.0, 0.71), (6.83, 0.71)), -5.24),
    # right: HS17's pocket opened through the thin (~0.4 mm) wall to the plate edge
    # (it leaves the pocket's two corner chamfers and closes inside the pocket);
    # floor None = down to the socket pocket floor
    ("right", "HS17", ((1.0, -3.0), (0.2421, -3.5363), (0.1794, -3.5851), (-0.102, -4.354),
                       (-0.47, -5.36), (5.5, -5.36), (4.9547, -4.304), (4.9893, -4.251),
                       (4.6306, -3.4837), (4.0, -3.0)), None),
    # right: D8 sits under the window's top edge; its pocket is stretched to the window and
    # to the U14 pocket
    ("right", None, ((-10.395, 38.42), (-7.605, 38.42), (-7.605, 39.45), (-10.395, 39.45)), -5.24),
    ("right", None, ((-8.05, 39.98), (-7.43, 39.98), (-7.43, 43.824), (-8.05, 43.824)), -5.24),
)


# the power switch slot as drawn in Fusion: three steps (y0, y1, floor, ceiling)
FUSION_POWER_SLOT = ((-2.7, 1.2, -5.34, -3.64), (1.2, 1.7, -5.8, -3.7), (1.1, 1.2, -5.8, -3.64))


def path_face(entries):
    """Closed face from points; ((x, y), (cx, cy)) arcs to (x, y) around (cx, cy);
    ("spline", through_points, (t0, t1)) runs a spline through to the next point."""
    edges = []
    for i, e in enumerate(entries):
        if isinstance(e[0], str):
            continue
        prev = entries[i - 1]
        if isinstance(prev[0], str):                       # spline from the point before it
            a = entries[i - 2]
            a = a if isinstance(a[0], (int, float)) else a[0]
        else:
            a = prev if isinstance(prev[0], (int, float)) else prev[0]
        if isinstance(e[0], (int, float)):
            b, c = e, None
        else:
            b, c = e
        if isinstance(prev[0], str):
            _, through, (t0, t1) = prev
            edges.append(Spline(a, *through, b, tangents=[t0, t1]))
        elif c is None:
            if math.dist(a, b) > 1e-9:
                edges.append(Line(a, b))
        else:
            r = (math.dist(a, c) + math.dist(b, c)) / 2
            mx, my = (a[0] + b[0]) / 2 - c[0], (a[1] + b[1]) / 2 - c[1]
            k = r / math.hypot(mx, my)
            edges.append(ThreePointArc(a, (c[0] + mx * k, c[1] + my * k), b))
    return Face(Wire(edges))


def place(fp: Footprint, face, flip: bool):
    """A face drawn in a footprint's frame, put on the board."""
    if flip:
        face = mirror(face, Plane.YZ)
    return Pos(fp.x, fp.y) * Rot(0, 0, fp.rot) * face


def place_point(fp: Footprint, pt, flip: bool):
    x, y = (-pt[0] if flip else pt[0]), pt[1]
    a = math.radians(fp.rot)
    return fp.x + x * math.cos(a) - y * math.sin(a), fp.y + x * math.sin(a) + y * math.cos(a)


# Round cuts get their seam turned from +x to +y. With the seam at +x, where the
# mirrored right reference STEP has its seams too, OpenCascade's (fuzzy) boolean
# between the two misclassifies the whole part. Geometry is the same either way.
SEAM = Rot(0, 0, 90)


def disc(r: float):
    return SEAM * Circle(r)


def switch_holes(sw: Footprint):
    """The switch footprint's drilled holes apart from the centre post (which has its own
    recess): Choc v1 side legs and the hotswap pin holes."""
    return [h for h in sw.pads if h.drill and math.dist((h.x, h.y), (sw.x, sw.y)) > 0.1]


def socket_body_hole(p: BottomplateParams):
    """Through hole under a socket's plastic body, in the socket's frame: the pocket itself
    (body + 0.5 mm, two lobes) across the body's x span, so the pocket walls run straight
    down along the body and no ledge is left there (a 0.25 mm ledge was unprintable and,
    where corner rounding narrowed it to a crescent, unmeshable). The floor stays only
    under the terminal tabs (|x| past the body) and the post relief."""
    pocket = path_face(p.socket_pocket)
    clearance = 0.5                                  # pocket = body + this (see socket_pocket)
    xb = max(abs(x) for x, y in (q if isinstance(q[0], float) else q[0] for q in p.socket_pocket)
             if abs(y) > 2.6) - clearance            # body x span: the lobes' far ends
    hole = (pocket & box(-xb - clearance, xb + clearance, -9, 9)).faces()[0]
    return no_offset_curves(hole)


def screw_hole(p: BottomplateParams):
    """One solid for a screw hole at the origin: countersink (or chamfered
    counterbore) from below, then the through hole."""
    rc, ch, b = p.counterbore_r, p.counterbore_chamfer, p.bottom
    if p.screw_head == "countersunk":
        rk = p.countersink_r
        profile = poly((0, b - 1), (rk + 1, b - 1), (p.screw_r, b + rk - p.screw_r),
                       (p.screw_r, p.top + 1), (0, p.top + 1))
        return SEAM * revolve(Plane.XZ * profile, Axis.Z)
    if p.screw_head != "counterbore":
        raise ValueError(f"screw_head {p.screw_head!r}")
    profile = poly((0, b - 1), (rc + ch + 1, b - 1), (rc, b + ch), (rc, p.counterbore_top),
                   (p.screw_r, p.counterbore_top), (p.screw_r, p.top + 1), (0, p.top + 1))
    return SEAM * revolve(Plane.XZ * profile, Axis.Z)


def rule_outline(board: Board, p: BottomplateParams):
    """The plate's outline: the housing outer box and the switch plate's thumb side,
    minus the skirt and the thumb wall grown by fit_gap; the piece under the PCB."""
    h = p.housing
    tw = ThumbWall(p.switchplate)
    region = box(h.x_min, h.x_max, h.y_min, h.y_max) + tw.outer_face()
    walls = skirt_face(board, h) + tw.wall_face()
    face = region - offset(walls, p.fit_gap, kind=Kind.INTERSECTION)
    return no_offset_curves(max(face.faces(), key=lambda f: f.area))


# outline edges shorter than this are merged into their neighbours: in a wall one layer
# (0.2 mm) tall, an edge under 0.2 mm is a sliver face (clean geometry check)
MIN_EDGE = 0.25
SLIVER = 0.1           # mm mean width (2 area / perimeter): narrower is a sliver
PINCH = 0.3            # radius of the bite taken where a ledge pinches to a point


def smoothed_cuts(cuts, outline2d, lock_band, p: BottomplateParams):
    """The pockets open from the top as one region per floor height, with thin walls
    opened and corners rounded (geom.open_thin_walls), as tools (solids).

    Level by level: the region cut down to a floor z is everything open from the top
    whose floor is at z or deeper (and the region of the level below), so a wall between
    two pockets goes down to the shallower floor, and a pocket's walls are the same rule
    at every depth (no separately cut pieces whose walls nearly coincide with the
    pocket's: those left micron-wide faces). The plate's outside and the lip lock band
    take part as neighbours (walls to them are opened too) but are not cut."""
    open_top = [(f, z0, z1) for f, z0, z1 in cuts if z1 >= p.top]
    bb = outline2d.bounding_box()
    outside = box(bb.min.X - 2, bb.max.X + 2, bb.min.Y - 2, bb.max.Y + 2) - outline2d
    # where the plate edge carries the corner wall (no housing skirt), the edge is the
    # outside of the case: walls to it stay as drawn
    H = p.housing
    c = p.xiao_clearance
    wall_zone = box(H.x_min - 3, H.xiao_zone[1] - c, H.xiao_zone[2] + c, H.y_max + 3)
    hold = (outside & wall_zone).faces()
    free = (outside - wall_zone).faces()
    true_outside = free + hold              # without the lock band: cuts are sealed to this
    if lock_band is not None:
        # the lock band overlaps the fit gap to the thumb wall: merged with the outside it
        # is one region, so the corner where they meet isn't taken for a thin wall
        # between two regions (that opened a stray 1.6 mm notch past the band's end)
        free = fuse(free + lock_band.faces())
    keep_out = free + hold
    # each level's region includes the one below it (it is built on it), so the void is
    # a stack of slabs: level region from its floor up to the next floor, and no two
    # tools overlap (overlapping tools with coinciding walls confuse the boolean)
    levels = sorted({z0 for _, z0, _ in open_top})
    regions, below = [], []
    for z in levels:
        level = [f for f, z0, _ in open_top if z0 <= z + 1e-9] + below
        region = [drop_short_edges(v, MIN_EDGE) for v in
                  open_thin_walls(level, keep_out, p.min_feature, p.min_feature * 4 / 3, p.min_feature / 2, hold,
                                  seal=true_outside)]
        if below:
            # the level below exactly, plus what this level adds where that is more than a
            # hairline: where a corner comes out a few microns different from the level
            # below's, the walls carry on straight instead of leaving a sliver of floor
            region = fuse(below + [q for q in subtract(region, below) if mean_width(q) > SLIVER])
        regions.append(region)
        below = region
    # a ledge (floor between a deeper cut and a wall) pinched to a point between two parts
    # makes a floor face OCC can't represent (invalid, and booleans on it go wrong):
    # there the deeper cut takes a small round bite out of the ledge (top down, so a new
    # pinch below is caught too)
    for k in range(len(regions) - 2, -1, -1):
        ledge = subtract(regions[k + 1], regions[k])
        pts = [q for f in ledge for q in pinches(f)]
        if pts:
            regions[k] = fuse(regions[k] + [Pos(q.X, q.Y) * disc(PINCH) for q in pts])
    tools = [prism(split_circles(v), z, z_next)
             for region, z, z_next in zip(regions, levels, levels[1:] + [p.top + 1]) for v in region]
    # above the plate top (the XIAO pocket and the power switch slot go into the corner
    # wall) as drawn
    tools += [prism(f, p.top, z1) for f, _, z1 in open_top if z1 > p.top and abs(z1 - (p.top + 1)) > 1e-9]
    tools += [prism(tidy(f), z0, z1) for f, z0, z1 in cuts if z1 < p.top]
    return tools


def build_bottomplate(board: Board, p: BottomplateParams = BottomplateParams()):
    flip = board.mirrored              # right half, built as a mirrored left (build.py)
    cuts = []                                     # (face, z0, z1)

    def cut(face, z0, z1=None):
        # every pocket that doesn't go through keeps at least floor_skin under it,
        # so no floor is a single printed layer (see floor_skin)
        if z0 > p.bottom:
            z0 = max(z0, p.bottom + p.floor_skin)
            if p.layer > 0:
                # down onto the layer grid: no floor step thinner than a layer (a 0.1 mm
                # step prints as a stray line), and deeper only ever adds clearance
                z0 = max(p.bottom + math.floor((z0 - p.bottom) / p.layer + 1e-6) * p.layer,
                         p.bottom + p.floor_skin)
        cuts.append((face, z0, p.top + 1 if z1 is None else z1))

    H = p.housing
    # plate, and the wall round the XIAO corner
    if p.outline_mode == "rule":
        outline2d = rule_outline(board, p)
    elif p.outline_mode == "fusion":
        outline2d = path_face(OUTLINE)
    else:
        raise ValueError(f"outline_mode {p.outline_mode!r}")
    part = prism(outline2d, p.bottom, p.top)
    zx0, zx1, zy0, zy1 = H.xiao_zone
    c = p.xiao_clearance
    wall = (box(H.x_min, zx1 - c, zy0 + c, H.y_max)
            - box(min(o[0] for o in board.outline) - c, zx1 - c, zy0 + c, max(o[1] for o in board.outline) + c))
    part += prism(wall, p.top, p.wall_top)
    if p.lip_lock:
        part -= ThumbWall(p.switchplate).lip_cutter(board, p.lip_clearance)
        # its footprint takes part in the thin-wall bridging below, as a through cut
        lock_band = ThumbWall(p.switchplate).lip_band(board, p.lip_clearance)

    # keys: socket pocket through the plate, switch post recess + relief
    socket = path_face(p.socket_pocket)
    body_hole = socket_body_hole(p) if p.socket_through_grow is not None else None
    for hs in board.of_kind(SOCKET):
        # the socket pocket through the plate (its outline is the only socket feature on
        # layer 1); a separate through hole inside the pocket shared the pocket's edges
        # along the body and the two depth levels broke the booleans into slivers.
        # socket_through_grow None = keep floor_skin under the socket instead.
        if p.socket_through_grow is not None:
            cut(place(hs, socket, flip), p.bottom - 1)
        else:
            cut(place(hs, socket, flip), p.bottom + p.floor_skin)
        cut(place(hs, box(*p.post_relief), flip), p.switch_post_floor)
        tip = place_point(hs, p.terminal_tip, flip)
        if any(math.dist(tip, (d.x, d.y)) < p.bridge_reach for d in board.of_kind(DIODE)):
            cut(place(hs, box(*p.terminal_bridge), flip), p.diode_floor)
    for sw in board.of_kind(SWITCH):
        cut(Pos(sw.x, sw.y) * disc(p.switch_post_r), p.switch_post_floor)
        for h in switch_holes(sw) if p.switch_hole_clearance > 0 else ():
            cut(Pos(h.x, h.y) * disc(h.drill / 2 + p.switch_hole_clearance), p.switch_post_floor)

    # diodes and SOT-23s
    for d in board.of_kind(DIODE):
        cut(place(d, Rectangle(*p.diode_pocket), flip), p.diode_floor)
    for u in board.of_kind(SOT23):
        cut(place(u, Rectangle(*p.sot23_pocket), flip), p.sot23_floor)

    # FFC pads, the window round the middle holes and the channel between them
    j1 = board.by_ref("J1")
    ffc = place(j1, box(*p.ffc_pocket), flip)
    cut(ffc, p.ffc_floor)
    h1 = board.by_ref("H1")
    wx0, wx1, wy0, wy1 = p.window
    cut(box(h1.x + wx0, h1.x + wx1, h1.y + wy0, h1.y + wy1), p.bottom - 1)
    pad_y = [pd.y for pd in j1.pads]
    cut(box(ffc.bounding_box().max.X, h1.x + wx0, min(pad_y) - p.ffc_channel_margin,
            max(pad_y) + p.ffc_channel_margin), p.bottom - 1)

    # battery bay under the PCB notch, its tongue under the diode along its edge, leads pocket
    bx0, bx1 = p.bay_x
    yf, ys, ye = p.bay_y
    cut(box(bx0, bx1, yf, ys), p.bay_floors[0])
    cut(box(bx0, bx1, ys, ye), p.bay_floors[1])
    tw, tback = p.bay_tongue
    for d in board.of_kind(DIODE):
        if abs(d.rot % 180) < 1e-6 and bx0 < d.x < bx1 and 0 < d.y - ye < p.bay_tongue_reach:
            # in the diode frame +y points into the bay when rot = 180, -y when rot = 0
            s = 1 if abs(d.rot % 360 - 180) < 1e-6 else -1
            y_a, y_b = s * tback, s * (d.y - ys + 1)
            cut(place(d, box(-tw, tw, min(y_a, y_b), max(y_a, y_b)), False), p.bay_floors[1])
    cut(box(*p.battery_leads), p.bay_floors[1])

    # XIAO corner: parts pocket, reset button hole, power switch slot, USB-C opening
    for b in p.xiao_pocket:
        cut(box(*b), p.xiao_floor, p.wall_top + 1)
    cut(box(*p.reset_hole), p.bottom - 1)
    pwr = board.by_ref("SW_PWR")
    for y0, y1, z0, z1 in p.power_slot:
        cut(place(pwr, box(p.power_slot_x_inner, p.power_slot_x_inner + 5, y0, y1), flip), z0, z1)

    # feet recesses from below
    for x, y in dict(p.feet)["right" if flip else "left"] if p.foot_depth > 0 else ():
        cut(Pos(x, y) * disc(p.foot_r), p.bottom - 1, p.bottom + p.foot_depth)

    # hand-drawn one-offs (Fusion only; the rule build bridges thin walls instead)
    for half, frame, pts, floor in FIXUPS if p.outline_mode == "fusion" else ():
        if half != ("right" if flip else "left"):
            continue
        face = poly(*pts) if frame is None else place(board.by_ref(frame), poly(*pts), flip)
        cut(face, p.bottom + p.floor_skin if floor is None else floor)

    if p.min_feature > 0:
        tools = smoothed_cuts(cuts, outline2d, lock_band if p.lip_lock else None, p)
    else:
        tools = [prism(tidy(f) if p.layer > 0 else f, z0, z1) for f, z0, z1 in cuts]
    # screws at the PCB mounting holes, from below (screw_hole)
    hole = screw_hole(p)
    tools += [Pos(h.x, h.y) * hole for h in map(board.by_ref, p.screw_holes)]
    ya, yb, za, zb = H.usb_slot
    slot = Plane.YZ * Pos((ya + yb) / 2, (za + zb) / 2) * RectangleRounded(yb - ya, zb - za, H.usb_slot_r)
    tools.append(Pos(H.x_min - 1, 0, 0) * extrude(slot, amount=3, dir=(1, 0, 0)))

    part = fuzzy_cut(part, tools) if p.layer > 0 else part - Compound(tools)
    if p.layer > 0 and len(part.solids()) > 1:
        # pieces cut loose from the plate (a corner between a through pocket and the edge,
        # at HS17) would just fall out of the print: keep the plate itself, say what went
        solids = sorted(part.solids(), key=lambda x: x.volume)
        for so in solids[:-1]:
            c = so.bounding_box().center()
            print(f"  bottomplate: dropped a loose piece of {so.volume:.1f} mm3 at ({c.X:.1f}, {c.Y:.1f})")
        part = solids[-1]
    return part


# The Fusion reference has the socket pockets cut through, foot recesses,
# counterbored screw holes, its hand-drawn outline and no lip lock;
# build.py checks this variant.
REFERENCE_PARAMS = BottomplateParams(floor_skin=0.0, layer=0.0, switch_hole_clearance=0.0, socket_through_grow=None, foot_depth=0.2, screw_head="counterbore",
                                     lip_lock=False, outline_mode="fusion", min_feature=0.0,
                                     power_slot=FUSION_POWER_SLOT,
                                     switchplate=SwitchplateParams(lip_sockets="notch"),
                                     housing=HOUSING_REFERENCE, bay_y=(-23.25, -7.5, -3.0))
# (the switch plate's thumb wall shape doesn't depend on its overrides, so its own
# REFERENCE_PARAMS needn't be repeated here beyond the lip)
