"""Flat (XY) region geometry on polygons: shapely 2 / GEOS, on a fixed precision grid.

The bottom plate's pockets are worked out here as 2D regions (closings, openings, unions,
differences) and only turned into OpenCascade faces at the end. Exact OCC curves made
these operations fragile: offsets of arcs and B-splines that meet almost tangentially
left micron edges, slivers, half-circle holes and offset failures. Polygons on a grid
can't: every vertex is a multiple of GRID, every result of an operation is valid, and
what is left to decide is by rule (widths, areas), not by floating point luck.

Conventions
  - Every operation's result is snapped to GRID (shapely.set_precision, or the overlay's
    grid_size), so equal inputs give equal vertices and shared boundaries stay shared.
  - Curves coming from OCC (arcs, circles, B-splines) are polylines with sag <= SAG; round
    offsets (buffer) get enough segments per quarter circle for the same sag at their radius.
  - Back to OCC (to_faces): the boundaries of all regions are noded together, cut into
    chains between nodes, and each chain is turned into lines and circle arcs ONCE, so a
    boundary shared by two regions (a pocket wall that carries on down through two floor
    levels) is the very same edges in both, and their faces merge after the boolean.
    Fitted arcs keep side faces whole (a faceted arc of 0.1 mm facets on a 0.2 mm step
    would be a row of sliver faces), and so does merging pieces under MIN_EDGE.
"""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

GRID = 1e-4          # mm: precision grid of every vertex
SAG = 0.005          # mm: max distance between a curve and the polyline standing in for it
SIMPLIFY = 0.002     # mm: vertices closer than this to the line through their neighbours go
MIN_SEG = 0.025      # mm: polyline segments shorter than this are merged before fitting (an
                     #     OCC edge under 0.02 mm is a micro-edge)
MIN_CHAIN = 0.15     # mm: a boundary piece between two nodes shorter than this is collapsed
                     #     into one node: its face through a 2.4 mm wall would be a sliver
MIN_EDGE = 0.25      # mm: fitted pieces shorter than this are merged into their neighbours:
                     #     extruded over a 0.2 mm step, an edge under 0.2 mm is a sliver face
                     #     (clean_geometry: mean width under 0.1)
TOUCH = 1e-3         # mm: regions this close count as touching
NODE_SNAP = 1e-3     # mm: outlines' vertices this close to each other are made one (to_faces)
EMPTY = Polygon()


# --- basics -----------------------------------------------------------------------------------

def snap(g: BaseGeometry) -> BaseGeometry:
    """g on the precision grid (valid output: collapsed parts dropped)."""
    return shapely.set_precision(g, GRID)


def quad_segs(r: float) -> int:
    """Segments per quarter circle so a polyline arc of radius r is within SAG of it."""
    if r <= SAG:
        return 1
    step = 2 * math.acos(1 - SAG / r)
    return max(1, math.ceil((math.pi / 2) / step))


def buffer(g: BaseGeometry, d: float) -> BaseGeometry:
    """g grown by d (d < 0: shrunk), round joins; on the grid."""
    if g.is_empty or d == 0:
        return g
    return snap(g.buffer(d, quad_segs=quad_segs(abs(d)), join_style="round"))


def _area(g: BaseGeometry) -> BaseGeometry:
    """g's polygons only: an overlay on the grid can leave collapsed bits (lines, points)
    in a GeometryCollection, which no further overlay accepts."""
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    return collect(parts(g))


def union(gs) -> BaseGeometry:
    gs = [g for g in gs if g is not None and not g.is_empty]
    if not gs:
        return EMPTY
    return _area(shapely.union_all(gs, grid_size=GRID))


def diff(a: BaseGeometry, b: BaseGeometry) -> BaseGeometry:
    if a.is_empty or b.is_empty:
        return a
    return _area(shapely.difference(a, b, grid_size=GRID))


def inter(a: BaseGeometry, b: BaseGeometry) -> BaseGeometry:
    if a.is_empty or b.is_empty:
        return EMPTY
    return _area(shapely.intersection(a, b, grid_size=GRID))


def parts(g: BaseGeometry) -> list[Polygon]:
    """The polygons of g (any geometry; lines and points are dropped)."""
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    if hasattr(g, "geoms"):
        return [p for x in g.geoms for p in parts(x)]
    return []


def collect(ps: list) -> BaseGeometry:
    """Disjoint polygons (parts of one geometry) as one geometry, without an overlay."""
    ps = [p for p in ps if not p.is_empty]
    if not ps:
        return EMPTY
    return ps[0] if len(ps) == 1 else MultiPolygon(ps)


def touching(qs: list, gs: list, tol: float = TOUCH) -> list[int]:
    """For each of qs, how many of gs it touches (comes within tol of)."""
    if not qs or not gs:
        return [0] * len(qs)
    tree = shapely.STRtree(gs)
    hits = tree.query(qs, predicate="dwithin", distance=tol)
    return list(np.bincount(hits[0], minlength=len(qs)))


def drop_specks(g: BaseGeometry, d: float) -> BaseGeometry:
    """g without parts and holes that no disc of diameter d fits in (specks)."""
    out = []
    for p in parts(g):
        if buffer(p, -d / 2).is_empty:
            continue
        keep = [r for r in p.interiors if not buffer(Polygon(r), -d / 2).is_empty]
        out.append(Polygon(p.exterior, keep) if len(keep) < len(p.interiors) else p)
    return collect(out)


def mean_width(p: BaseGeometry) -> float:
    """2 area / perimeter: about the width of a long thin region."""
    return 2 * p.area / p.length if p.length else 0.0


def closing(g: BaseGeometry, r: float) -> BaseGeometry:
    """Grown by r, shrunk by r: gaps narrower than 2r filled, concave corners rounded r."""
    return buffer(buffer(g, r), -r)


def opening(g: BaseGeometry, r: float) -> BaseGeometry:
    """Shrunk by r, grown by r: the union of the discs of radius r inside g (parts narrower
    than 2r dropped, convex corners rounded r)."""
    return buffer(buffer(g, -r), r)


def clean(g: BaseGeometry, d: float = 0.05) -> BaseGeometry:
    """g without hairline cracks of material and hairline spikes of region (narrower than
    2d), corners kept sharp: a closing then an opening with mitred offsets. Overlays of
    almost coincident edges leave such hairlines (valid, but no solid can be made of them)."""
    if g.is_empty:
        return g
    m = dict(join_style="mitre", mitre_limit=10.0)   # sharp corners down to 11 deg kept
    g = snap(snap(g.buffer(d, **m)).buffer(-d, **m))
    return snap(snap(g.buffer(-d, **m)).buffer(d, **m))


def gaps(g: BaseGeometry, r: float) -> list[Polygon]:
    """What the closing by r adds to g: the material between its parts (and in its own
    notches) that a disc of radius r can't reach, as separate pieces. Hairlines (grid
    crumbs along edges the closing restores) are left out; a hairline crack of material
    between two cuts is filled by clean()."""
    return [q for q in parts(diff(closing(g, r), g)) if mean_width(q) > 1e-3]


def overlaps(a: BaseGeometry, b: BaseGeometry) -> bool:
    """a and b share area (not just a boundary)."""
    return a.intersects(b) and inter(a, b).area > 1e-8


def disc(x: float, y: float, r: float) -> Polygon:
    return snap(shapely.Point(x, y).buffer(r, quad_segs=quad_segs(r)))


# --- from OCC ---------------------------------------------------------------------------------

def _wire_points(wire) -> list[tuple]:
    """The wire as a closed polyline (first point not repeated): lines by their ends, any
    other curve sampled within SAG, edges in wire order and direction."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.TopAbs import TopAbs_REVERSED
    pts = []
    ex = BRepTools_WireExplorer(wire.wrapped)
    while ex.More():
        e = ex.Current()
        ad = BRepAdaptor_Curve(e)
        u0, u1 = ad.FirstParameter(), ad.LastParameter()
        if ad.GetType() == GeomAbs_Line:
            q = [ad.Value(u0), ad.Value(u1)]
        else:
            d = GCPnts_QuasiUniformDeflection(ad, SAG, u0, u1)
            if not d.IsDone() or d.NbPoints() < 2:
                raise RuntimeError("could not sample an edge")
            q = [d.Value(i) for i in range(1, d.NbPoints() + 1)]
        q = [(p.X(), p.Y()) for p in q]
        if e.Orientation() == TopAbs_REVERSED:
            q.reverse()
        pts += q if not pts else q[1:]
        ex.Next()
    return pts


def from_face(face) -> BaseGeometry:
    """An OCC/build123d flat face (or several: Compound, Sketch) as a polygon on the grid."""
    out = []
    for f in face.faces():
        shell = Polygon(_wire_points(f.outer_wire()))
        holes = [Polygon(_wire_points(w)) for w in f.inner_wires()]
        out.append(diff(shapely.make_valid(shell), union([shapely.make_valid(h) for h in holes])))
    return snap(union(out))


# --- to OCC -----------------------------------------------------------------------------------

def _turn(a, b, c) -> float:
    """Turning angle (rad) at b of the path a -> b -> c."""
    u, v = b - a, c - b
    nu, nv = np.hypot(*u), np.hypot(*v)
    if nu < 1e-12 or nv < 1e-12:
        return 0.0
    return abs(math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1]))


CORNER = math.radians(35)   # a polyline vertex turning more than this is a corner, not a curve
FIT_TOL = 0.002             # mm: vertices within this of a fitted line or arc
MERGE_TOL = 0.006           # mm: the same for a line or arc a short piece is merged into
MERGE_TOL_MAX = 0.03        # mm: ... and where that is not enough
STRAIGHT = -math.cos(math.radians(0.5))   # two chains this close to straight through a node
MAX_R = 2000.0              # mm: flatter "arcs" are lines
SPIKE_TURN = math.radians(150)   # a vertex turning more than this ...
SPIKE_LEN = 0.05                 # ... next to a segment shorter than this is a spike


def _circle(a, b, c):
    """Centre and radius of the circle through a, b, c (None if about collinear)."""
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        return None
    sa, sb, sc = a @ a, b @ b, c @ c
    ux = (sa * (b[1] - c[1]) + sb * (c[1] - a[1]) + sc * (a[1] - b[1])) / d
    uy = (sa * (c[0] - b[0]) + sb * (a[0] - c[0]) + sc * (b[0] - a[0])) / d
    centre = np.array([ux, uy])
    return centre, float(np.hypot(*(a - centre)))


def _fits_line(q) -> bool:
    a, b = q[0], q[-1]
    L = np.hypot(*(b - a))
    if L < 1e-9:
        return False
    n = np.array([-(b - a)[1], (b - a)[0]]) / L
    return bool(np.all(np.abs((q[1:-1] - a) @ n) <= FIT_TOL))


def _fits_arc(q, tol: float = FIT_TOL):
    """(centre, radius) if the polyline q is a circle arc: the circle through its ends and
    middle vertex has every vertex within tol and every segment's midpoint within the
    sampling sag, the arc turns one way and by at most 180 deg."""
    m = len(q) // 2
    c = _circle(q[0], q[m], q[-1])
    if c is None:
        return None
    centre, r = c
    if r > MAX_R:
        return None
    if np.max(np.abs(np.hypot(*(q - centre).T) - r)) > tol:
        return None
    mids = (q[1:] + q[:-1]) / 2
    if np.max(np.abs(np.hypot(*(mids - centre).T) - r)) > SAG + tol:
        return None
    v = q - centre
    ang = np.unwrap(np.arctan2(v[:, 1], v[:, 0]))
    da = np.diff(ang)
    if not (np.all(da > 0) or np.all(da < 0)) or abs(ang[-1] - ang[0]) > math.pi:
        return None
    return c


def _run_pieces(q) -> list[tuple]:
    """A run of polyline vertices (no corners inside) as ('line' | 'arc', vertices)
    pieces, greedily as long as each fits."""
    out, i, n = [], 0, len(q)
    while i < n - 1:
        best = (i + 1, "line")
        j = i + 2
        while j < n:
            seg = q[i:j + 1]
            if _fits_line(seg):
                best = (j, "line")
            elif _fits_arc(seg) is not None:
                best = (j, "arc")
            else:
                break
            j += 1
        j, kind = best
        out.append((kind, q[i:j + 1]))
        i = j
    return out


def _piece_length(pc) -> float:
    return float(np.sum(np.hypot(*np.diff(pc[1], axis=0).T)))


def _fit(q, tol: float):
    """The vertices q (a smooth run) as one piece, a line or an arc with every vertex
    within tol of it; None if neither fits."""
    if len(q) == 2:
        return ("line", q)
    a, b = q[0], q[-1]
    L = np.hypot(*(b - a))
    if L > 1e-9:
        n = np.array([-(b - a)[1], (b - a)[0]]) / L
        if np.all(np.abs((q[1:-1] - a) @ n) <= tol):
            return ("line", q)
    if _fits_arc(q, tol) is not None:
        return ("arc", q)
    return None


def _merge_short(pieces: list) -> list:
    """Pieces of a run shorter than MIN_EDGE merged into their neighbours as one line or
    arc through all their vertices (_fit), keeping the merged pieces' outer ends: first
    with one neighbour within MERGE_TOL, then with one or both within MERGE_TOL_MAX. No
    end vertex moves, so nothing moves by more than the tolerance (an extruded edge under
    MIN_EDGE would be a sliver face). A piece that fits with neither stays."""
    pieces = list(pieces)
    for tol, spans in ((MERGE_TOL, (1,)), (MERGE_TOL_MAX, (1, 2))):
        stuck = set()
        while len(pieces) > 1:
            lens = [_piece_length(pc) for pc in pieces]
            todo = [i for i in range(len(pieces)) if lens[i] < MIN_EDGE and id(pieces[i]) not in stuck]
            if not todo:
                break
            k = min(todo, key=lambda i: lens[i])
            options = []
            for span in spans:
                for a in range(k - span, k + 1):
                    b = a + span
                    if 0 <= a and b < len(pieces):
                        options.append((sum(lens[a:b + 1]), a, b))
            for _, a, b in sorted(options):
                pc = _fit(np.vstack([pieces[a][1]] + [p[1][1:] for p in pieces[a + 1:b + 1]]), tol)
                if pc is not None:
                    pieces[a:b + 1] = [pc]
                    break
            else:
                stuck.add(id(pieces[k]))
    # what is still short is a feature under MIN_EDGE (a truncated tip, a tiny step): it
    # becomes a corner at its middle, where its neighbours now meet (at the chain's end,
    # the neighbour runs on to the end); this moves the outline by at most MIN_EDGE / 2
    while len(pieces) > 1:
        lens = [_piece_length(pc) for pc in pieces]
        k = min(range(len(pieces)), key=lambda i: lens[i])
        if lens[k] >= MIN_EDGE:
            break
        q = pieces.pop(k)[1]
        if k == 0:
            pieces[0] = _moved(pieces[0], 0, q[0])
        elif k == len(pieces):
            pieces[-1] = _moved(pieces[-1], -1, q[-1])
        else:
            m = (q[0] + q[-1]) / 2 if len(q) == 2 else q[len(q) // 2]
            pieces[k - 1] = _moved(pieces[k - 1], -1, m)
            pieces[k] = _moved(pieces[k], 0, m)
    return pieces


def _moved(pc, end: int, p):
    """The piece with its first (end 0) or last (end -1) vertex moved to p."""
    q = pc[1].copy()
    q[end] = p
    if pc[0] == "arc" and _circle(q[0], q[len(q) // 2], q[-1]) is None:
        return ("line", q)
    return (pc[0], q)


def _chain_pieces(pts) -> list[tuple]:
    """An open chain of vertices (ends fixed) as lines and arcs: spikes and segments
    shorter than MIN_SEG merged away, split at corners, each run fitted (_run_pieces),
    short pieces merged (_merge_short)."""
    q = [np.asarray(p, float) for p in pts]
    # spikes: a vertex where the outline runs out and almost straight back over a short
    # distance (overlays of polylines crossing at a grazing angle leave such zigzags)
    k = 1
    while k < len(q) - 1:
        if (_turn(q[k - 1], q[k], q[k + 1]) > SPIKE_TURN
                and min(np.hypot(*(q[k] - q[k - 1])), np.hypot(*(q[k + 1] - q[k]))) < SPIKE_LEN):
            del q[k]
            k = max(1, k - 1)
        else:
            k += 1
    # merge short segments: drop the inner vertex of each (ends stay where they are)
    k = 1
    while len(q) > 2 and k < len(q):
        if np.hypot(*(q[k] - q[k - 1])) < MIN_SEG:
            del q[k if k < len(q) - 1 else k - 1]
            k = max(1, k - 1)
        else:
            k += 1
    q = np.array(q)
    if len(q) == 2:
        return [("line", q)]
    cuts = [0] + [k for k in range(1, len(q) - 1) if _turn(q[k - 1], q[k], q[k + 1]) > CORNER] + [len(q) - 1]
    out = []
    for a, b in zip(cuts, cuts[1:]):
        out += _run_pieces(q[a:b + 1])
    # short pieces merged, across corners too where they fit (a kink of a few microns
    # between two almost coincident curves is a "corner")
    return _merge_short(out)


def _occ_edge(piece):
    from build123d import Edge, Vector
    V = lambda p: Vector(float(p[0]), float(p[1]), 0.0)
    kind, q = piece
    if kind == "line":
        return Edge.make_line(V(q[0]), V(q[-1]))
    return Edge.make_three_point_arc(V(q[0]), V(q[len(q) // 2]), V(q[-1]))


def _key(p) -> tuple:
    return (round(float(p[0]), 6), round(float(p[1]), 6))


def _geoms(g) -> list:
    return [x for x in getattr(g, "geoms", [g]) if not x.is_empty]


def to_faces(regions: list[BaseGeometry]) -> list[list]:
    """OCC faces (build123d) of each region in `regions`, built so that boundaries the
    regions share are identical edges:
      1. all boundaries are noded together (union of the rings on the grid) and merged
         into chains between nodes;
      2. the chains cut the plane into cells; each region is rebuilt as the union of the
         cells inside it, so its rings run exactly along chains, and each ring is read as
         a sequence of chains;
      3. chains shorter than MIN_CHAIN are collapsed into one node (their ends' midpoint),
         the rest simplified (SIMPLIFY, ends fixed) and turned into lines and arcs, once
         per chain; every ring's wire is made of its chains' edges."""
    from build123d import Face, Wire
    regions = [union([g]) for g in regions]
    # every outline onto the others' vertices within NODE_SNAP: a vertex of one lying on
    # another's segment (a little off the grid line) becomes a node of both, so two
    # outlines that run together share that piece instead of overlapping a micron apart
    ref = shapely.multipoints(np.vstack([shapely.get_coordinates(g) for g in regions if not g.is_empty]))
    regions = [_area(snap(shapely.make_valid(shapely.snap(g, ref, NODE_SNAP)))) if not g.is_empty else g
               for g in regions]
    rings = [r for g in regions for p in parts(g) for r in (p.exterior, *p.interiors)]
    if not rings:
        return [[] for _ in regions]
    merged = _geoms(shapely.line_merge(shapely.union_all([LineString(r.coords) for r in rings], grid_size=GRID)))
    chains = [np.asarray(c.coords, float) for c in merged]
    seg = {}                       # directed segment -> (chain, index of its start, direction)
    for i, c in enumerate(chains):
        for j in range(len(c) - 1):
            a, b = _key(c[j]), _key(c[j + 1])
            seg[(a, b)] = (i, j, 1)
            seg[(b, a)] = (i, j + 1, -1)
    cells = _geoms(shapely.polygonize(merged))
    probes = shapely.point_on_surface(cells)

    def loop(ring) -> list[tuple]:
        """The ring as [(chain, direction)]."""
        K = [_key(p) for p in ring.coords[:-1]]
        n = len(K)
        at_node = [j for j in range(n) if (lambda i, idx, d: (idx == 0 and d == 1) or
                                           (idx == len(chains[i]) - 1 and d == -1))(*seg[(K[j], K[(j + 1) % n])])]
        if not at_node:                                  # one closed chain, no nodes on it
            i, _, d = seg[(K[0], K[1])]
            return [(i, d)]
        out, j, done = [], at_node[0], 0
        while done < n:
            i, idx, d = seg[(K[j % n], K[(j + 1) % n])]
            if not ((idx == 0 and d == 1) or (idx == len(chains[i]) - 1 and d == -1)):
                raise RuntimeError(f"to_faces: ring leaves a chain midway near {K[j % n]}")
            out.append((i, d))
            j += len(chains[i]) - 1
            done += len(chains[i]) - 1
        return out

    polys = []                                           # per region: [[outer loop, hole loops...]]
    for g in regions:
        inside = [c for c, q in zip(cells, probes) if g.contains(q)]
        rebuilt = parts(shapely.union_all(inside, grid_size=GRID)) if inside else []
        polys.append([[loop(p.exterior)] + [loop(r) for r in p.interiors] for p in rebuilt])

    # micro chains: both ends become one node (union-find over node keys)
    parent = {}

    def find(k):
        while parent.get(k, k) != k:
            k = parent[k]
        return k
    micro = set()
    loops = [lp for ps in polys for poly in ps for lp in poly]

    def end_node(i, d):
        return _key(chains[i][-1] if d == 1 else chains[i][0])

    def pinches_a_ring(group: set, extra: int) -> bool:
        """Would merging the nodes in `group` (with chain `extra` gone) make some ring
        pass through the merged node twice? (Then the chain stays.)"""
        for lp in loops:
            runs, prev_in = 0, False
            for i, d in lp:
                gone = i in micro or i == extra
                inside = find(end_node(i, d)) in group or end_node(i, d) in group
                if inside and not (gone and prev_in):
                    runs += 1
                prev_in = inside
            if lp and runs > 1:
                # the first and last junction may be one run around the loop's start
                i0, d0 = lp[0]
                if not ((i0 in micro or i0 == extra) and prev_in and runs == 2):
                    return True
        return False
    for i, c in enumerate(chains):
        if _key(c[0]) != _key(c[-1]) and LineString(c).length < MIN_CHAIN:
            a, b = find(_key(c[0])), find(_key(c[-1]))
            group = {k for k in set(parent) | {a, b} | {_key(c[0]), _key(c[-1])} if find(k) in (a, b)}
            if pinches_a_ring(group, i):
                continue
            micro.add(i)
            if a != b:
                parent[b] = a
    groups = {}
    for k in list(parent):
        groups.setdefault(find(k), []).append(k)
    where = {}
    for root, ks in groups.items():
        ks = set(ks) | {root}
        m = (sum(k[0] for k in ks) / len(ks), sum(k[1] for k in ks) / len(ks))
        for k in ks:
            where[k] = m
    # each chain with its ends moved as above, simplified (ends fixed)
    simple = []
    for c in chains:
        c = c.copy()
        closed = _key(c[0]) == _key(c[-1])
        c[0] = where.get(_key(c[0]), c[0])
        c[-1] = where.get(_key(c[-1]), c[-1])
        sc = np.asarray(shapely.simplify(LineString(c), SIMPLIFY, preserve_topology=False).coords)
        simple.append(sc if len(sc) >= (4 if closed else 2) else c)

    # straight through a node: where two chains carry on in a straight line through a node,
    # the node is put exactly on the line between their next vertices (it is off by grid
    # rounding), so the two pieces are one line and their faces merge into one (a short
    # piece left on its own, between the node and a corner, would be a sliver face)
    ends = {}
    for i, c in enumerate(simple):
        if _key(c[0]) != _key(c[-1]):
            ends.setdefault(_key(c[0]), []).append((i, 0, 1))
            ends.setdefault(_key(c[-1]), []).append((i, -1, -2))
    # (repeated until nothing moves: a straight run of short chains through several nodes
    # settles on one line)
    for _ in range(100):
        moved = 0.0
        for k, es in ends.items():
            best = None
            p = simple[es[0][0]][es[0][1]]
            for x in range(len(es)):
                for y in range(x + 1, len(es)):
                    va, vb = simple[es[x][0]][es[x][2]], simple[es[y][0]][es[y][2]]
                    da, db = va - p, vb - p
                    na, nb = np.hypot(*da), np.hypot(*db)
                    if na < 1e-9 or nb < 1e-9:
                        continue
                    dot = float(da @ db) / (na * nb)
                    if dot < STRAIGHT and (best is None or dot < best[0]):
                        best = (dot, va, vb)
            if best is not None:
                _, va, vb = best
                u = (vb - va) / np.hypot(*(vb - va))
                q = va + u * float((p - va) @ u)
                moved = max(moved, float(np.hypot(*(q - p))))
                for i, j, _ in es:
                    simple[i][j] = q
        if moved < 1e-12:
            break
    edges_of = {}

    def chain_edges(i):
        if i not in edges_of:
            c = simple[i]
            if _key(c[0]) == _key(c[-1]):              # a closed chain: split in two at least
                m = len(c) // 2
                pieces = _chain_pieces(c[:m + 1]) + _chain_pieces(c[m:])
            else:
                pieces = _chain_pieces(c)
            # (a piece whose ends a collapse has brought together is no edge)
            edges_of[i] = [_occ_edge(p) for p in pieces if np.hypot(*(p[1][-1] - p[1][0])) > 1e-7]
        return edges_of[i]

    def wire(lp):
        es = [e for i, _ in lp if i not in micro for e in chain_edges(i)]
        if len(es) < 2 or sum(e.length for e in es) < 3 * MIN_CHAIN:
            return None                                  # collapsed with its short chains
        return Wire(es)

    out = []
    for loops in polys:
        faces = []
        for lps in loops:
            outer = wire(lps[0])
            if outer is None:
                continue
            holes = [w for w in map(wire, lps[1:]) if w is not None]
            try:
                f = Face(outer, holes)
            except Exception as e:
                c = outer.bounding_box().center()
                raise RuntimeError(f"to_faces: no face from the ring near ({c.X:.2f}, {c.Y:.2f})") from e
            if f.normal_at().Z < 0:
                f = -f
            faces.append(f)
        out.append(faces)
    return out


# --- thin walls -----------------------------------------------------------------------------------

def open_thin_walls(pockets: list, keep_out: BaseGeometry, w: float, w_open: float, r: float,
                    hold: BaseGeometry = EMPTY, soft: BaseGeometry = EMPTY,
                    soft_d: float = 0.1, r_join: float = 0.0) -> BaseGeometry:
    """The region to cut so that no material narrower than w is left between `pockets`
    (cut from the same height), or between them and `keep_out` (the part's outside, other
    features that must not be cut themselves), with smooth outlines:
      1. thin walls: the gaps (closing minus union) at radius w/2 that touch two separate
         regions (connected pockets, connected parts of keep_out): the material between
         them a disc of diameter w can't pass. (A gap touching one region only is a
         corner, rule 4.)
      2. a wall that is opened is opened to w_open > w: each gap at radius w_open/2 that
         contains a thin wall is cut. The wall's remaining ends are round (radius w_open/2)
         and well apart, where a closing right at the wall's thickness would leave a
         hairline channel between two ends almost touching.
      3. necks: a passage of the cut region narrower than w joining two wider parts is
         widened to w, so a small cut overlapping a bigger one a little merges into it as
         one shape rather than two joined by a slit.
      4. corners: every corner of material left between pockets and opened walls (or
         between them and keep_out) rounded with radius r: the gaps at radius r touching
         a pocket; tips and sticks narrower than 2r go with them. Where cuts join (a
         corner within one connected region) with radius r_join (> r) instead, so the
         joined cuts read as one smooth pocket. keep_out's own corners stay as they are.
      5. nubs: void the rules added that no disc of radius r fits in (ears where a
         rounded corner meets a small cut nearby) is taken back; the cuts as drawn stay
         exactly; the corners this leaves are rounded again (rule 4).
    `hold` (part of keep_out) is outside where the edge is a visible wall: nothing is
    added within w_open/2 of it, so walls to it stay as drawn. keep_out is taken off the
    result. `soft` (part of the pockets: deeper cuts whose outline must not change) is
    taken soft_d smaller for rule 4, so a corner rounded against its wall crosses that
    wall at an angle (about 30 deg for soft_d = r / 6) instead of running into it tangentially."""
    pk = parts(union(pockets))
    ko = parts(union([keep_out]))
    regions = pk + ko
    base = union(regions)
    protect = buffer(hold, w_open / 2)

    def added(qs):                 # additions to the cut (disjoint pieces), off the protected band
        return collect(parts(diff(collect([p for q in qs for p in parts(q)]), protect)))

    gs = gaps(base, w / 2)
    thin = [q for q, n in zip(gs, touching(gs, regions)) if n >= 2]
    opened = [q for q in gaps(base, w_open / 2) if any(overlaps(q, t) for t in thin)] if thin else []
    core = union([base, added(opened)])
    bodies = parts(opening(core, w / 2))
    ns = [q for q in parts(diff(core, collect(bodies))) if mean_width(q) > 1e-3]
    necks = [q for q, n in zip(ns, touching(ns, bodies)) if n >= 2]
    if necks:
        core = union([core, added([buffer(collect(necks), w / 2)])])

    def corners(g, near=None):
        """Rule 4 on g: what to add, the corner gaps touching a pocket (and `near`, if
        given) off the protected band (added), at radius
        r where they are tips or sticks (narrower than 2r: what a disc of radius r can't
        reach), at radius r_join inside one connected region (a corner where two cuts
        join, rounded generously, so they read as one pocket; between two regions a gap
        at r_join is a wall, not a corner)."""
        cc = union([diff(g, soft), inter(buffer(soft, -soft_d), g)]) if not soft.is_empty else g
        gs = gaps(cc, r)
        sets = []
        if r_join > r:
            # a gap at r inside one region lies in one at r_join (the closing grows with
            # the radius): only those between regions are kept at r
            comps = parts(cc)
            gj = gaps(cc, r_join)
            sets.append([q for q, n, m in zip(gs, touching(gs, pk), touching(gs, comps)) if n >= 1 and m >= 2])
            sets.append([q for q, n, m in zip(gj, touching(gj, pk), touching(gj, comps)) if n >= 1 and m == 1])
        else:
            sets.append([q for q, n in zip(gs, touching(gs, pk)) if n >= 1])
        # each set is disjoint pieces (one closing's), the sets may overlap
        return [union([added([q for q in qs if near is None or q.intersects(near)]) for qs in sets])]
    region = union([core] + corners(core))
    # 5. nubs: what the rules added where no disc of radius r fits (an ear where a rounded
    #    corner meets a small cut nearby) goes again; the cuts as drawn stay exactly. The
    #    corners that leaves are rounded once more (rule 4). Worked out near the additions
    #    only (3r around them, enough for every disc that matters): the same result, faster.
    more = diff(region, base)
    if not more.is_empty:
        nubs = [q for q in parts(diff(more, opening(inter(region, buffer(more, 3 * r)), r))) if q.area > 1e-6]
        if nubs:
            region = diff(region, collect(nubs))
            near = buffer(collect(nubs), 2 * r)
            reach = 3 * max(r, r_join)
            region = union([region] + corners(inter(region, buffer(near, reach)), near))
    region = diff(region, union(ko))
    return collect([q for q in parts(region) if mean_width(q) > 1e-3])


# --- pinches ------------------------------------------------------------------------------------

def pinch_points(g: BaseGeometry, tol: float = 0.02) -> list[tuple]:
    """Where g's boundary comes within tol of itself between two separate rings or
    parts: a hole touching (or almost) the outer ring or another hole, two parts touching.
    There the region (or the material around it) is pinched to a point or a hairline,
    which OCC can't make a valid face of. Returns one point per such place."""
    ps = parts(g)
    pts = []

    def near(a, b):
        if shapely.dwithin(a, b, tol):
            ln = shapely.shortest_line(a, b)
            (x0, y0), (x1, y1) = ln.coords
            pts.append(((x0 + x1) / 2, (y0 + y1) / 2))
    for p in ps:
        rs = [p.exterior, *p.interiors]
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                near(rs[i], rs[j])
    for i in range(len(ps)):
        for j in range(i + 1, len(ps)):
            near(ps[i].boundary, ps[j].boundary)
    return pts
