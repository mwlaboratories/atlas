"""Bottom plate: the plate under the PCB, with pockets for what sits on its back.

Built for the LEFT half in the case frame (see kicad.Frame); the right half is
the same construction on the mirrored board, mirrored back (see build.py).
On that mirrored board a back-side part keeps its position but its outline is
seen from the other side, so footprint-local shapes are mirrored in x there
(`place`).

Layers (z, mm):
  -5.9 .. -3.8  the plate; pockets cut down from its top (holes under the socket bodies
                and the window through it), screw countersinks from below
  -3.8 .. 0     a thin wall around the XIAO corner, where the housing has no skirt

The pockets open from the top are worked out in 2D on polygons (geom2d, shapely), one
region per floor level with the print rules applied (pocket_regions), and only then made
solid: each level's region extruded once as a slab, all cut from the plate in one boolean.
The plate outline, the lip lock cutter, the countersinks, the USB slot and the corner
wall stay exact OpenCascade geometry.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from build123d import (Axis, Circle, Face, Kind, Line, Plane, Pos, Rectangle, RectangleRounded, Rot,
                       ThreePointArc, Wire, extrude, mirror, offset, revolve)

from . import geom2d as g2
from .geom import box, fuzzy_cut, no_offset_curves, poly, prism
from .housing import HousingParams, skirt_face
from .kicad import DIODE, SOCKET, SOT23, SWITCH, Board, Footprint
from .switchplate import SwitchplateParams, ThumbWall

H = HousingParams()      # defaults below are taken from the default housing


@dataclass(frozen=True)
class BottomplateParams:
    housing: HousingParams = field(default_factory=HousingParams)
    switchplate: SwitchplateParams = field(default_factory=SwitchplateParams)
    # 0.3 mm above the housing skirt's foot (-6.2): printed plates come out thicker than
    # drawn (measured 2026-11-08: bottom plate +0.15, switch plate +0.08), and a plate
    # proud of the housing rocks on the table; recessed, the keyboard stands on the wood
    bottom: float = -5.9
    top: float = -3.8                    # plate top face
    # outline: the space the housing skirt and the switch plate's thumb wall leave, fit_gap
    # from each of them (so flush with the PCB where the skirt hugs it), out to the
    # housing's outer box where nothing surrounds the plate (XIAO corner)
    fit_gap: float = 0.2
    # minimum feature size (3 nozzle widths): no material narrower than this is left
    # between pockets, between a pocket and the plate edge, or as a tip or stick of
    # material; such walls are cut away down to the shallower floor, so cuts that nearly
    # touch merge into one, and every corner of material between pockets is rounded to
    # min_feature / 2. A wall that is opened is opened wherever it is thinner than
    # 4/3 min_feature, so one just under the limit gets a clean opening, not a slit
    # (see geom2d.open_thin_walls).
    min_feature: float = 1.2
    # floor left under every pocket that doesn't go through, so the first printed layer is
    # a solid sheet. Two 0.2 mm layers: with one, the slicer fills each pocket floor as its
    # own (top surface) region inside layer 1 and seams it.
    floor_skin: float = 0.4
    # print layer height: every pocket floor is moved down onto this grid (from the
    # bottom face), so pocket floors differ by whole layers
    layer: float = 0.2
    # --- per key ---------------------------------------------------------------
    # Kailh socket pocket, in the socket footprint's frame:
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
    # the socket pocket keeps floor_skin under it, except under the socket's plastic body:
    # there a through hole, the pocket's own outline across the body's x span (the pocket
    # is the body + this clearance), so the pocket walls run straight down along the body
    # and layer 1 has one plain hole per key (socket_body_span)
    socket_clearance: float = 0.5
    # recess under the switch's centre post, and a relief joining it to the socket pocket
    switch_post_r: float = 2.6
    switch_post_floor: float = -5.5            # = bottom + floor_skin (Choc posts/legs reach ~1.05 below the PCB)
    # recess under every other hole of the switch footprint (the Choc v1 side legs at
    # x +-5.5 stick out under the PCB), radius drill/2 + this, at switch_post_floor;
    # 0 = none
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
    # heads, DIN 7991 / ISO 10642, head 3.8-4.0 mm) or "counterbore"
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
    # spanning the three steps drawn in Fusion, ceiling on the layer grid above the PCB
    # underside
    power_slot: tuple = ((-2.7, 1.7, -5.8, -3.6),)
    power_slot_x_inner: float = 1.78
    # --- thumb lock: the switch plate's thumb wall has a lip with a 45 deg top face
    # (switchplate.ThumbWall.lip); the plate gets the lip's shape grown by lip_clearance
    # cut out of its edge, so it hooks over the lip: a 45 deg bevel underneath, which
    # prints as a 45 deg overhang
    lip_lock: bool = True
    lip_clearance: float = 0.15          # normal to the 45 deg faces


def path_face(entries):
    """Closed face from points; ((x, y), (cx, cy)) arcs to (x, y) around (cx, cy)."""
    edges = []
    for i, e in enumerate(entries):
        prev = entries[i - 1]
        a = prev if isinstance(prev[0], (int, float)) else prev[0]
        b, c = (e, None) if isinstance(e[0], (int, float)) else e
        if c is None:
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


# Round cuts get their seam turned from +x to +y (kept from when the plate was compared
# with a mirrored reference STEP, whose seams at +x confused the boolean; harmless).
SEAM = Rot(0, 0, 90)


def disc(r: float):
    return SEAM * Circle(r)


def socket_body_span(p: BottomplateParams):
    """The socket body's x span (+ socket_clearance) as a band in the socket's frame: the
    through hole under the body is the pocket within it (the pocket itself there, so the
    pocket walls run straight down along the body and no ledge is left there; the floor
    stays only under the terminal tabs, |x| past the body, and the post relief). Cut in
    2D from the pocket's own polygon, so hole and pocket share their walls exactly."""
    pts = [q if isinstance(q[0], float) else q[0] for q in p.socket_pocket]
    xb = max(abs(x) for x, y in pts if abs(y) > 2.6) - p.socket_clearance   # body: the lobes' far ends
    c = p.socket_clearance
    return box(-xb - c, xb + c, -9, 9)


def switch_holes(sw: Footprint):
    """The switch footprint's drilled holes apart from the centre post (which has its own
    recess): Choc v1 side legs and the hotswap pin holes."""
    return [h for h in sw.pads if h.drill and math.dist((h.x, h.y), (sw.x, sw.y)) > 0.1]


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


# --- the pockets open from the top, in 2D --------------------------------------------------
SLIVER = 0.1     # mm: what a level adds beyond the level below where no disc of this
                 # diameter fits in it is dropped (its walls carry on straight). Not a mean
                 # width: a corner fillet's piece (r = 0.6, 90 deg) has a mean width of only
                 # 0.07 but fits a 0.2 mm disc
PINCH = 0.3      # radius of the bite taken where a region or a floor pinches to a point
SNAP = 0.02      # mm: a level's outline vertices this close to the level below's go onto them
# The 2D regions overlap what they border by a little, so no OCC face of a pocket lies
# on (or within a sampling sag of) a face it meets, which would leave slivers:
EDGE_RUN_ON = 0.02   # mm: the plate's outside is taken this much further out for the
                     # rules, so a pocket opened to the edge runs on past it and corners
                     # rounded against the edge cross it at an angle instead of touching it
OUTSIDE_RING = 0.3   # mm: and where a pocket reaches the edge, the regions keep the outside
                     # up to this far out of the plate (it cuts nothing), so the pocket's
                     # outline carries on along the edge: no cusp where a rounded corner
                     # meets the edge, and no pocket face near the plate's own edge faces
BAND_RUN_ON = 0.02   # mm: the lock band is taken this much smaller, so a pocket opened to
                     # it overlaps the lip cutter's ledge face (above the ledge it takes this
                     # much off the hook's inner face)


def pocket_regions(open_top, outline, lock_band, p: BottomplateParams, walls=g2.EMPTY):
    """The pockets open from the top as one region per floor level, with the print rules
    applied: [(z, region)], deepest first; each region contains the ones below it.

    `open_top` is [(polygon, floor)], `outline` the plate outline and `lock_band` the lip
    lock band (polygons, geom2d); `walls`: cuts that carry on above the plate as drawn
    (corners are rounded against them like against the holes through the plate, rule 2).
    Rules, in order:
      1. levels: the region cut down to a floor z is everything open from the top whose
         floor is at z or deeper, plus the region of the level below (so it contains it),
         so a wall between two pockets goes down to the shallower floor and a pocket's
         walls are the same rule at every depth.
      2. thin walls (geom2d.open_thin_walls, per level): walls under min_feature between
         two regions (pockets, the plate's outside, the lock band) are opened to
         4/3 min_feature, necks widened to min_feature, corners of material rounded
         min_feature / 2, where cuts join min_feature (one smooth pocket), and what the
         rules added that is narrower than min_feature taken back (no nubs). The outside and the lock band take part as neighbours but are
         not cut. Where the plate edge carries the XIAO corner wall (no housing skirt)
         it is the visible outside of the case: nothing is added near it (`hold`). The
         lock band overlaps the fit gap to the thumb wall and is merged with the outside
         there (one region, so the corner where they meet isn't taken for a thin wall
         between two regions). Corners rounded against the deeper levels' walls (and
         against the cuts that carry on above the plate as drawn, `walls`) are rounded
         against those taken 0.1 mm smaller, so they cross the wall at an angle instead
         of running into it tangentially (a cusp of floor, which could only go by biting
         a nibble out of the deeper level; geom2d.open_thin_walls `soft`).
      3. a level adds to the level below only where that is more than a hairline
         (SLIVER): where a boundary comes out a few microns off the one below, the wall
         carries on straight.
      4. where pockets reach the edge, every region gets the ring of outside next to the
         plate there (OUTSIDE_RING), and hairline cracks and spikes (under 0.1 mm) are
         cleaned off (geom2d.clean; also after rule 2 on every level).
      5. specks (pockets or islands of material under SLIVER across) go, and each
         level's outline is snapped onto the level below's within SNAP.
      6. ledges: floor left between a level's region and the next deeper one that is
         narrower than min_feature / 2 all along (a crescent) or 2 SLIVER on average
         is taken by the deeper level whole, top down; into the holes through the plate
         only hairline specks (layer 1 keeps its outline).
      7. pinches: where a region, or a floor between two levels, pinches to a point (or a
         hairline under 0.02 mm) the deeper level takes a round bite of radius PINCH
         (within the level above, for a floor), so no face is pinched.
"""
    w = p.min_feature
    x0, y0, x1, y1 = outline.bounds
    big = g2.snap(g2.shapely.box(x0 - 3, y0 - 3, x1 + 3, y1 + 3))
    outside = g2.diff(big, outline)
    outside_morph = g2.buffer(outside, -EDGE_RUN_ON)       # the plate EDGE_RUN_ON larger
    hp, c = p.housing, p.xiao_clearance
    wall_zone = g2.snap(g2.shapely.box(hp.x_min - 3, hp.xiao_zone[2] + c, hp.xiao_zone[1] - c, hp.y_max + 3))
    hold = g2.inter(outside_morph, wall_zone)
    keep_out = outside_morph
    ring = g2.inter(outside_morph, g2.buffer(outline, OUTSIDE_RING))
    if lock_band is not None:
        band = g2.buffer(lock_band, -BAND_RUN_ON)
        keep_out = g2.union([keep_out, band])
        ring = g2.diff(ring, band)

    levels = sorted({z for _, z in open_top})
    regions, below = [], g2.EMPTY
    for z in levels:
        level = [f for f, z0 in open_top if z0 <= z + 1e-9] + [below]
        # corners rounded against the deeper levels' walls cross them at an angle (soft):
        # a cusp of floor there (a corner tangent to the wall) could only go by biting
        # into the deeper level, a nibble; the same against the cuts carrying on above the
        # plate (a cusp in the top face)
        soft = g2.union([below, walls])
        region = g2.clean(g2.open_thin_walls(level, keep_out, w, w * 4 / 3, w / 2, hold, soft=soft, r_join=w))
        if not below.is_empty:
            more = [q for q in g2.parts(g2.diff(region, below)) if not g2.buffer(q, -SLIVER / 2).is_empty]
            region = g2.union([below] + more)
        regions.append(region)
        below = region

    # the outside ring (OUTSIDE_RING), the same in every level, only near where the
    # pockets reach the edge (those of the top level, which contains all others: the rest
    # of the ring would only be work for the boolean); hairlines cleaned off
    # (geom2d.clean), and each region made to contain the one below again exactly (the
    # cleaning can leave them a grid step apart)
    reach = g2.inter(regions[-1], g2.buffer(outside_morph, 2 * g2.GRID))
    ring = g2.inter(ring, g2.buffer(reach, 1.0))
    regions = [g2.clean(g2.union([r, ring])) for r in regions]
    for k in range(1, len(regions)):
        regions[k] = g2.union([regions[k], regions[k - 1]])

    # specks: pockets and islands of material no SLIVER-wide disc fits in go (an island goes
    # on every level above too, so each region still contains the one below); where a
    # region's outline leaves the one below, its vertices within SNAP of the lower one's
    # are put on them (a junction is one point, not a cluster of nodes microns apart,
    # which no fitted edge can follow)
    for k in range(len(regions)):
        regions[k] = g2.drop_specks(regions[k], SLIVER)
        if k:
            snapped = g2.snap(g2.shapely.make_valid(g2.shapely.snap(regions[k], regions[k - 1], SNAP)))
            regions[k] = g2.union([snapped, regions[k - 1]])

    # ledges narrower than min_feature / 2 all along (no disc of that diameter fits in
    # them: a crescent), or under 2 SLIVER wide on average (a sliver of floor in the
    # solid), beside the deeper region are taken by it whole, top down (what a level takes
    # can make a narrow ledge below it): the deeper region then simply follows the
    # shallower one's wall, and no new boundary is made. Into the holes through the plate
    # only hairline specks, so layer 1 keeps the outline the thin-wall rules gave it.
    def ledges():
        for k in range(len(regions) - 2, -1, -1):
            ledge = g2.diff(regions[k + 1], regions[k])
            # into the holes through the plate only specks (no SLIVER-wide disc fits, or under
            # 2 SLIVER wide on average: a hairline where a corner crosses a hole wall twice)
            narrow = SLIVER / 2 if levels[k] < p.bottom else w / 4
            near = g2.buffer(regions[k], g2.TOUCH)
            take = [q for q in g2.parts(ledge)
                    if (g2.buffer(q, -narrow).is_empty or g2.mean_width(q) < 2 * SLIVER)
                    and g2.shapely.intersection(q.boundary, near).length > 0.01]
            if take:
                regions[k] = g2.union([regions[k]] + take)

    def pinches() -> bool:
        # pinches: of a region (with the plate's outside: material pinched against the edge
        # counts too), then of the floor between it and the level above (where the deeper
        # level takes a round bite)
        bitten = False
        for k in range(len(regions) - 1, -1, -1):
            for x, y in g2.pinch_points(g2.union([regions[k], outside])):
                d = g2.disc(x, y, PINCH)
                for j in range(k, len(regions)):
                    regions[j] = g2.union([regions[j], d])
                bitten = True
            if k + 1 < len(regions):
                for x, y in g2.pinch_points(g2.diff(regions[k + 1], regions[k])):
                    regions[k] = g2.union([regions[k], g2.inter(g2.disc(x, y, PINCH), regions[k + 1])])
                    bitten = True
        return bitten

    # (a bite taken at a pinch can leave a sliver of floor, which the ledge rule takes)
    for _ in range(6):
        ledges()
        if not pinches():
            break
    else:
        raise RuntimeError("bottomplate: pinches keep coming back")

    return list(zip(levels, regions))


def pocket_tools(cuts, outline2d, lock_band, p: BottomplateParams):
    """The cuts as tools (solids): the ones open from the top as stacked slabs, one per
    floor level (pocket_regions, extruded from its floor to the next level's, the top one
    to above the plate), so no two tools overlap and a wall that carries on down is the
    same edges in every slab (geom2d.to_faces); cuts above the plate top (the XIAO pocket
    and the power switch slot into the corner wall) as drawn, through the same noding so
    their walls meet the slabs' exactly."""
    to2d = {}

    def poly2d(f):                     # a face, or a polygon made from one already
        if isinstance(f, g2.BaseGeometry):
            return f
        if id(f) not in to2d:
            to2d[id(f)] = g2.from_face(f)
        return to2d[id(f)]
    open_top = [(poly2d(f), z0) for f, z0, z1 in cuts if z1 >= p.top]
    band = g2.from_face(lock_band) if lock_band is not None else None
    above = [(poly2d(f), z1) for f, _, z1 in cuts if z1 > p.top and abs(z1 - (p.top + 1)) > 1e-9]
    levels = pocket_regions(open_top, g2.from_face(outline2d), band, p, g2.union([f for f, _ in above]))
    faces = g2.to_faces([r for _, r in levels] + [f for f, _ in above])
    zs = [z for z, _ in levels] + [p.top + 1]
    tools = [prism(f, zs[k], zs[k + 1]) for k in range(len(levels)) for f in faces[k]]
    tools += [prism(f, p.top, z1) for (_, z1), fs in zip(above, faces[len(levels):]) for f in fs]
    tools += [prism(f, z0, z1) for f, z0, z1 in cuts if z1 < p.top]
    return tools


def build_bottomplate(board: Board, p: BottomplateParams = BottomplateParams()):
    if p.min_feature <= 0 or p.layer <= 0:
        raise ValueError("bottomplate: min_feature and layer must be > 0")
    flip = board.mirrored              # right half, built as a mirrored left (build.py)
    cuts = []                     # (face, or its polygon (geom2d), z0, z1)

    def cut(face, z0, z1=None):
        # every pocket that doesn't go through keeps at least floor_skin under it,
        # so no floor is a single printed layer (see floor_skin)
        if z0 > p.bottom:
            # down onto the layer grid: no floor step thinner than a layer (a 0.1 mm
            # step prints as a stray line), and deeper only ever adds clearance
            z0 = max(p.bottom + math.floor((z0 - p.bottom) / p.layer + 1e-6) * p.layer,
                     p.bottom + p.floor_skin)
        cuts.append((face, z0, p.top + 1 if z1 is None else z1))

    H = p.housing
    # plate, and the wall round the XIAO corner
    outline2d = rule_outline(board, p)
    part = prism(outline2d, p.bottom, p.top)
    zx0, zx1, zy0, zy1 = H.xiao_zone
    c = p.xiao_clearance
    wall = (box(H.x_min, zx1 - c, zy0 + c, H.y_max)
            - box(min(o[0] for o in board.outline) - c, zx1 - c, zy0 + c, max(o[1] for o in board.outline) + c))
    part += prism(wall, p.top, p.wall_top)
    lock_band = None
    if p.lip_lock:
        part -= ThumbWall(p.switchplate).lip_cutter(board, p.lip_clearance)
        # its footprint takes part in the thin-wall rules below, as a neighbour
        lock_band = ThumbWall(p.switchplate).lip_band(board, p.lip_clearance)

    # keys: socket pocket with a through hole under the body (socket_body_span), switch
    # post recess + relief
    socket = path_face(p.socket_pocket)
    span = socket_body_span(p)
    for hs in board.of_kind(SOCKET):
        pocket = g2.from_face(place(hs, socket, flip))
        cut(pocket, p.bottom + p.floor_skin)
        cut(g2.inter(pocket, g2.from_face(place(hs, span, flip))), p.bottom - 1)
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

    tools = pocket_tools(cuts, outline2d, lock_band, p)
    # screws at the PCB mounting holes, from below (screw_hole)
    hole = screw_hole(p)
    tools += [Pos(h.x, h.y) * hole for h in map(board.by_ref, p.screw_holes)]
    ya, yb, za, zb = H.usb_slot
    slot = Plane.YZ * Pos((ya + yb) / 2, (za + zb) / 2) * RectangleRounded(yb - ya, zb - za, H.usb_slot_r)
    tools.append(Pos(H.x_min - 1, 0, 0) * extrude(slot, amount=3, dir=(1, 0, 0)))

    part = fuzzy_cut(part, tools)
    if len(part.solids()) > 1:
        # pieces cut loose from the plate (a corner between a through pocket and the edge,
        # at HS17) would just fall out of the print: keep the plate itself, say what went
        solids = sorted(part.solids(), key=lambda x: x.volume)
        for so in solids[:-1]:
            c = so.bounding_box().center()
            print(f"  bottomplate: dropped a loose piece of {so.volume:.1f} mm3 at ({c.X:.1f}, {c.Y:.1f})")
        part = solids[-1]
    return part
