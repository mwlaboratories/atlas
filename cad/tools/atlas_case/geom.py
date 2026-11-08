"""Small geometry helpers shared by the case parts (case frame, mm, z up)."""
from __future__ import annotations

import math

from build123d import Compound, Edge, Face, GeomType, Kind, Polygon, Pos, Rectangle, Wire, extrude, offset
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.Geom import Geom_TrimmedCurve
from OCP.GeomAbs import GeomAbs_C2
from OCP.GeomConvert import GeomConvert_ApproxCurve
from OCP.TopExp import TopExp


def prism(face, z0: float, z1: float):
    """Extrude a flat XY face from z0 to z1, whatever way its normal happens to point."""
    return Pos(0, 0, z0) * extrude(face, amount=z1 - z0, dir=(0, 0, 1))


def box(x0: float, x1: float, y0: float, y1: float):
    """Axis-aligned rectangle face from its edges."""
    return Pos((x0 + x1) / 2, (y0 + y1) / 2) * Rectangle(x1 - x0, y1 - y0)


def poly(*pts):
    """Polygon face through absolute points (no re-centring)."""
    return Polygon(*pts, align=None)


def no_offset_curves(face: Face, tol: float = 1e-5) -> Face:
    """The face with every offset-curve edge replaced by a B-spline within `tol`.

    Offsetting a face with spline edges gives offset curves; extruded, they become
    surfaces that the STEP writer stores in a form that reads back as loose shells
    instead of a solid (seen in Bambu and OCP alike). B-splines round-trip cleanly."""
    def fix(w):
        edges = []
        for e in w.edges():
            if e.geom_type == GeomType.OFFSET:
                t0, t1 = BRep_Tool.Range_s(e.wrapped)
                c = BRep_Tool.Curve_s(e.wrapped, 0.0, 1.0)
                bs = GeomConvert_ApproxCurve(Geom_TrimmedCurve(c, t0, t1), tol, GeomAbs_C2, 50, 8).Curve()
                # on the original end vertices (curve direction) and with the original
                # orientation, so the wire stays connected and oriented
                mk = BRepBuilderAPI_MakeEdge(bs, TopExp.FirstVertex_s(e.wrapped, False),
                                             TopExp.LastVertex_s(e.wrapped, False))
                if not mk.IsDone():
                    raise RuntimeError("could not rebuild an offset edge as a B-spline")
                ne = mk.Edge()
                ne.Orientation(e.wrapped.Orientation())
                e = Edge(ne)
            edges.append(e)
        return Wire(edges)
    if not any(e.geom_type == GeomType.OFFSET for e in face.edges()):
        return face
    return Face(fix(face.outer_wire()), [fix(w) for w in face.inner_wires()])


def fuzzy_cut(part, tools, fuzzy: float = 1e-3):
    """part minus tools with OCC's fuzzy boolean: boundaries closer than `fuzzy` are
    treated as coincident, so tools whose walls line up to rounding don't leave hairline
    faces between them."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    op = BRepAlgoAPI_Cut()
    op.SetFuzzyValue(fuzzy)
    op.SetRunParallel(True)
    res = part._bool_op((part,), tuple(tools), op)     # build123d's boolean driver
    res = reread(res.clean())
    solids = res.solids()
    return solids[0] if len(solids) == 1 else res


def reread(shape):
    """The shape written to BREP and read back (in memory). The boolean's result as it
    comes sections into broken (unclosed) wires once mirrored (the right half), which
    crashes the section rasteriser; read back from BREP the same geometry sections fine."""
    import io
    from build123d import Shape
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape
    buf = io.BytesIO()
    BRepTools.Write_s(shape.wrapped, buf)
    buf.seek(0)
    out = TopoDS_Shape()
    BRepTools.Read_s(out, buf, BRep_Builder())
    return Shape.cast(out)


def tidy(face: Face, tol: float = 0.01) -> Face:
    """The face with edges shorter than tol dropped, their neighbours meeting at the dropped
    edge's midpoint (micron-long arcs and lines are left where offsets/booleans meet almost
    tangentially; in the solid they become sliver faces). Lines and arcs only; any other
    edge type, or a result that isn't a valid face of the same area, returns the face as is."""
    from build123d import Line, ThreePointArc
    if all(e.length >= tol for e in face.edges()):
        return face

    def fix(w):
        es = w.order_edges()
        keep = [e for e in es if e.length >= tol]
        if len(keep) < 2 or any(e.geom_type not in (GeomType.LINE, GeomType.CIRCLE) for e in keep):
            raise ValueError("unsupported")
        n = len(es)
        # each kept edge's start/end, moved onto the midpoint of any dropped edge beside it
        segs = []
        for k, e in enumerate(es):
            if e.length < tol:
                continue
            a, b = e.position_at(0), e.position_at(1)
            prev, nxt = es[(k - 1) % n], es[(k + 1) % n]
            if prev.length < tol:
                a = prev.position_at(0.5)
            if nxt.length < tol:
                b = nxt.position_at(0.5)
            segs.append((e, a, b))
        new = []
        for e, a, b in segs:
            if e.geom_type == GeomType.LINE:
                new.append(Line(a, b))
            else:
                new.append(ThreePointArc(a, e.position_at(0.5), b))
        return Wire(new)
    try:
        nf = Face(fix(face.outer_wire()), [fix(w) for w in face.inner_wires()])
        if nf.is_valid and abs(nf.area - face.area) < 0.01 * max(face.area, 1.0):
            return nf
    except Exception:
        pass
    return face


def _grow(f, d):
    """f offset by d with round corners, as a list of faces ([] if OCC fails)."""
    try:
        g = offset(no_offset_curves(f), d, kind=Kind.ARC)
    except Exception:
        return []
    return [no_offset_curves(x) for x in g.faces()]


def _union(faces):
    u = None
    for f in faces:
        u = f if u is None else u + f
    return u


def _closing(faces, r, fallback=True):
    """Morphological closing of the union of `faces`: grown by r, shrunk by r, round corners.
    Fills every gap narrower than 2r between them, with fillets of radius r. Where OCC
    can't shrink the merged shape, falls back to the neck construction: the points within
    r of every face (the middle of each too-thin wall), grown by r, which spans the wall
    with round ends."""
    grown = [_grow(f, r) for f in faces]
    if any(not g for g in grown):
        return []
    u = _union([g for gs in grown for g in gs])
    u = u.clean() if hasattr(u, "clean") else u
    out = []
    for f in u.faces():
        shrunk = _grow(f, -r)
        if not shrunk:
            break
        out += shrunk
    else:
        return out
    if not fallback:
        return []
    neck = _union(grown[0])
    for gs in grown[1:]:
        neck = neck & _union(gs)
    return [g for n in (neck.faces() if neck else []) for g in _grow(n, r)]


def _touches(piece, f, tol=1e-3) -> bool:
    return piece.distance_to(f) < tol


def bridges(faces: list, w: float, outline=None) -> list[tuple[object, list[int]]]:
    """Walls thinner than w between two faces (pockets, holes), or between a face and the
    part's edge: the region to cut so no such wall is left. Returns (bridge, [i, j]), with
    j = -1 for the edge (`outline` is the part's 2D outline).

    Per pair: the closing (grow by w/2, shrink by w/2, round corners) of the two faces,
    minus the faces themselves, keeping only pieces that touch both. That is exactly the
    material in walls narrower than w, as one smooth piece with radius w/2 fillets (an
    earlier sampled construction left saw-tooth outlines of hundreds of tiny facets).
    The edge side uses the region beyond the outline near the face."""
    r = w / 2
    boxes = [f.bounding_box() for f in faces]
    out = []

    def pieces(a, b, keep=None):
        res = []
        for c in _closing([a, b], r):
            for pc in (c - a - b).faces():
                if keep is not None:
                    pcs = (pc & keep).faces()
                else:
                    pcs = [pc]
                for q in pcs:
                    if q.area > 1e-3 and _touches(q, a) and _touches(q, b):
                        res.append(q)
        return res

    # clusters: faces closer than w to each other, transitively. Each cluster is closed as
    # a whole (a tip bounded by three cuts isn't covered by any one pair's closing); the
    # pieces are what the closing adds, and each is assigned the faces within w of it.
    # If OCC can't close a cluster, its pairs are closed one by one instead.
    near = {i: set() for i in range(len(faces))}
    for i in range(len(faces)):
        for j in range(i + 1, len(faces)):
            bi, bj = boxes[i], boxes[j]
            if (bi.min.X > bj.max.X + w or bj.min.X > bi.max.X + w
                    or bi.min.Y > bj.max.Y + w or bj.min.Y > bi.max.Y + w):
                continue
            if faces[i].distance_to(faces[j]) < w:
                near[i].add(j)
                near[j].add(i)
    seen = set()
    for i in range(len(faces)):
        if i in seen or not near[i]:
            continue
        cluster, todo = [], [i]
        while todo:
            k = todo.pop()
            if k in seen:
                continue
            seen.add(k)
            cluster.append(k)
            todo += near[k] - seen
        closed = _closing([faces[k] for k in cluster], r, fallback=False)
        if closed:
            union = _union([faces[k] for k in cluster]).clean()
            for c in closed:
                for q in (c - union).faces():
                    if q.area < 1e-3:
                        continue
                    by = [k for k in cluster if q.distance_to(faces[k]) < 1e-3]
                    if len(by) >= 2:
                        out.append((q, by))
            continue
        for a in cluster:
            for b in near[a]:
                if a < b:
                    out += [(q, [a, b]) for q in pieces(faces[a], faces[b])]
    if outline is not None:
        edge = outline.outer_wire()
        for i, f in enumerate(faces):
            if f.distance_to(edge) >= w:
                continue
            bb = f.bounding_box()
            local = box(bb.min.X - 2 * w, bb.max.X + 2 * w, bb.min.Y - 2 * w, bb.max.Y + 2 * w) - outline
            for beyond in local.faces():
                out += [(q, [i, -1]) for q in pieces(f, beyond, keep=outline)]
    return out


# --- morphology on flat faces: grow, shrink, closing --------------------------------------
# Built from single-loop offsets (BRepOffsetAPI_MakeOffset on a face with one wire), whose
# results are known without guessing: growing a simple region gives one outer loop plus
# the holes it closes off, shrinking one gives disjoint loops (it may split). A region
# with holes is then: grow = grown outer minus the shrunk holes, shrink = shrunk outer
# minus the grown holes. build123d's offset raises when a shrink splits a face, which
# is exactly what happens at a wall just under the threshold.

def _flat_face(wire) -> Face:
    if not wire.is_closed:
        raise RuntimeError("open wire")
    try:
        f = Face(wire)
    except Exception:
        # booleans leave zero-length arcs and 10 nm lines in a wire, which OCC won't make
        # a face from: drop them and re-join the rest within 1 um
        es = [e for e in wire.edges() if e.length > 1e-4]
        ws = [w for w in Wire.combine(es, tol=1e-3) if w.is_closed] if es else []
        if not ws:
            raise RuntimeError("degenerate wire")
        f = Face(max(ws, key=lambda w: w.length))
    if f.normal_at().Z < 0:
        f = Face(f.wrapped.Reversed())
    return f


def split_circles(face: Face) -> Face:
    """The face with every one-edge wire (a full circle) given as two half circles. OCC's
    2D offset misplaces or crashes on a one-edge wire (build123d splits it too), and a
    whole-circle wall extruded and cut can section into a broken wire."""
    def two(w):
        es = w.edges()
        return Wire([es[0].trim(0.0, 0.5), es[0].trim(0.5, 1.0)]) if len(es) == 1 else w
    ws = [face.outer_wire()] + face.inner_wires()
    if all(len(w.edges()) > 1 for w in ws):
        return face
    nf = _flat_face(two(face.outer_wire()))
    return Face(nf.outer_wire(), [two(w) for w in face.inner_wires()]) if face.inner_wires() else nf


def _closed_wire(w):
    """w, or, if it is a single open circle arc (booleans on a circle split into two halves
    sometimes keep only one half as a hole's wire), the full circle it belongs to."""
    if w.is_closed:
        return w
    es = w.edges()
    if len(es) == 1 and es[0].geom_type == GeomType.CIRCLE:
        from build123d import Circle
        c, r = es[0].arc_center, es[0].radius
        circ = (Pos(c.X, c.Y) * Circle(r)).faces()[0].outer_wire()
        return split_circles(Face(circ)).outer_wire()
    return w


def _holes(face: Face) -> list[Face]:
    """The face's holes as faces (booleans occasionally leave an empty inner wire, or a
    lone half circle of one, see _closed_wire)."""
    return [_flat_face(_closed_wire(w)) for w in face.inner_wires() if w.edges()]


SHORT = 0.02       # mm: edges shorter than this are merged away before offsetting


def _meet(a: Edge, b: Edge, near):
    """Where the curves of edges a and b (extended: lines and circles in full) cross or
    touch (closer than 1e-4: tangent within rounding), closest to `near`, or None."""
    from OCP.GeomAPI import GeomAPI_ExtremaCurveCurve
    ca = BRep_Tool.Curve_s(a.wrapped, 0.0, 1.0)
    cb = BRep_Tool.Curve_s(b.wrapped, 0.0, 1.0)
    from OCP.gp import gp_Pnt
    from build123d import Vector
    try:
        ex = GeomAPI_ExtremaCurveCurve(ca, cb)
        n = ex.NbExtrema()
    except Exception:
        return None
    best = None
    for i in range(1, n + 1):
        if ex.Distance(i) > 1e-4:
            continue
        p1, p2 = gp_Pnt(), gp_Pnt()
        ex.Points(i, p1, p2)
        q = Vector((p1.X() + p2.X()) / 2, (p1.Y() + p2.Y()) / 2, (p1.Z() + p2.Z()) / 2)
        if best is None or (q - near).length < (best - near).length:
            best = q
    return best


class _Step:
    """An edge of a wire as traversed: its ends and end tangents in wire order."""
    __slots__ = ("e", "rev", "p0", "p1", "t0", "t1", "length")

    def point(self, t):
        return self.e.position_at(1 - t if self.rev else t)


def _walk(w: Wire, tol: float = 1e-5) -> list[_Step]:
    """The edges of a closed wire in traversal order, each with its ends and end tangents
    along the wire (straight from OCC: build123d's order_edges is slow and lists some
    wires backwards)."""
    from build123d import Vector
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.GCPnts import GCPnts_AbscissaPoint
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.TopAbs import TopAbs_REVERSED
    steps = []
    ex = BRepTools_WireExplorer(w.wrapped)
    while ex.More():
        e = ex.Current()
        ad = BRepAdaptor_Curve(e)
        u0, u1 = ad.FirstParameter(), ad.LastParameter()
        rev = e.Orientation() == TopAbs_REVERSED
        pa, pb, va, vb = gp_Pnt(), gp_Pnt(), gp_Vec(), gp_Vec()
        ad.D1(u0, pa, va)
        ad.D1(u1, pb, vb)
        st = _Step()
        st.e, st.rev = Edge(e), rev
        st.length = GCPnts_AbscissaPoint.Length_s(ad)
        P = lambda p: Vector(p.X(), p.Y(), p.Z())
        def T(v, sign):
            m = v.Magnitude()
            return Vector(v.X() * sign / m, v.Y() * sign / m, v.Z() * sign / m) if m > 1e-12 else Vector(0, 0, 0)
        if rev:
            st.p0, st.p1, st.t0, st.t1 = P(pb), P(pa), T(vb, -1), T(va, -1)
        else:
            st.p0, st.p1, st.t0, st.t1 = P(pa), P(pb), T(va, 1), T(vb, 1)
        steps.append(st)
        ex.Next()
    if not steps:
        raise ValueError("empty wire")
    for x, y in zip(steps, steps[1:] + steps[:1]):
        if (x.p1 - y.p0).length > tol:
            raise ValueError("wire edges don't chain")
    return steps


def _rebuild(st: _Step, start, end) -> Edge:
    """The step's edge from start to end: the same line or circle; any other curve
    re-interpolated through its own points (ends moved by at most the merge tolerance)."""
    from build123d import Line, Spline, ThreePointArc
    e = st.e
    if e.geom_type == GeomType.LINE:
        return Line(start, end)
    if e.geom_type == GeomType.CIRCLE:
        return ThreePointArc(start, e.position_at(0.5), end)
    pts = [start] + [st.point(t / 24) for t in range(1, 24)] + [end]
    return Spline(*pts)


def _same(st: _Step, start, end) -> bool:
    return (st.p0 - start).length < 1e-9 and (st.p1 - end).length < 1e-9


def _refaced(face: Face, fix) -> Face:
    nf = Face(fix(face.outer_wire()), [fix(w) for w in face.inner_wires()])
    return nf if nf.normal_at().Z * face.normal_at().Z > 0 else Face(nf.wrapped.Reversed())


def drop_short_edges(face: Face, tol: float) -> Face:
    """The face with every run of edges shorter than tol replaced by one corner: the
    neighbours are extended or trimmed along their own curves to where they cross (so
    lines stay on their lines, arcs on their circles), or meet at the run's midpoint if
    they don't cross nearby. Offsets and booleans leave such micron edges where
    boundaries meet almost tangentially; OCC's 2D offset fails on some, and in a solid
    they become sliver faces. Returns the face unchanged if it can't be rebuilt."""
    if all(e.length >= tol for e in face.edges()):
        return face

    def fix(w):
        st = _walk(w)
        n = len(st)
        short = [x.length < tol for x in st]
        if sum(not s for s in short) < 2:
            raise ValueError("too few edges")
        ends = {k: [st[k].p0, st[k].p1] for k in range(n) if not short[k]}
        for k in range(n):
            if short[k] or not short[(k + 1) % n]:
                continue
            j = (k + 1) % n                            # run of short edges after edge k
            while short[j]:
                j = (j + 1) % n
            m = (st[k].p1 + st[j].p0) * 0.5
            run = sum(st[(k + 1 + t) % n].length for t in range((j - k - 1) % n))
            p = _meet(st[k].e, st[j].e, m)
            if p is None or (p - m).length > 2 * run + tol:
                p = m
            ends[k][1] = p
            ends[j][0] = p
        return Wire([st[k].e if _same(st[k], *ends[k]) else _rebuild(st[k], *ends[k])
                     for k in range(n) if not short[k]])
    try:
        nf = _refaced(face, fix)
        if nf.is_valid and abs(nf.area - face.area) < 0.01 * max(face.area, 1.0):
            return nf
    except Exception:
        pass
    return face


def _on_edge(p, e: Edge, tol=1e-6) -> bool:
    from build123d import Vertex
    return e.distance_to(Vertex(p.X, p.Y, p.Z)) < tol


def _spiky(face: Face) -> bool:
    return any(st[k].t1.dot(st[(k + 1) % len(st)].t0) < -0.99
               for w in [face.outer_wire()] + face.inner_wires() for st in [_walk(w)]
               for k in range(len(st)))


def _despike(face: Face) -> Face:
    """The face without hairline spikes: places where the outline runs out and straight
    back (a 180 degree turn at a vertex). OCC's 2D offset leaves one where an edge that
    should vanish meets a tangent arc (the socket pocket grown by 0.3..0.6 mm: its 0.29 mm
    line beside the 1.19 mm arc comes out reversed). The edge running back is dropped and
    its other neighbour extended to where it meets the edge that carries on."""
    def fix(w):
        st = _walk(w)
        edges = [[x, x.p0, x.p1] for x in st]          # step, start, end (as rebuilt)
        for _ in range(len(st)):
            n = len(edges)
            for k in range(n):
                a, b = edges[k][0], edges[(k + 1) % n][0]
                if a.t1.dot(b.t0) > -0.99:
                    continue
                # a runs back along b (drop a, extend the edge before a onto b), or b back
                # along a (drop b, extend the edge after b onto a)
                prv, nxt = edges[(k - 1) % n], edges[(k + 2) % n]
                x = _meet(prv[0].e, b.e, a.p0)
                if x is not None and _on_edge(x, b.e):
                    prv[2] = x
                    edges[(k + 1) % n][1] = x
                    del edges[k]
                    break
                x = _meet(a.e, nxt[0].e, b.p1)
                if x is not None and _on_edge(x, a.e):
                    edges[k][2] = x
                    nxt[1] = x
                    del edges[(k + 1) % n]
                    break
                raise ValueError("spike not repaired")
            else:
                return Wire([x.e if _same(x, s0, s1) else _rebuild(x, s0, s1) for x, s0, s1 in edges])
        raise ValueError("spike not repaired")
    if not _spiky(face):
        return face
    try:
        return _refaced(face, fix)
    except Exception:              # ValueError, or OCC failing on the rebuilt wire
        raise RuntimeError("spike not repaired")


def _offset_loops(face: Face, d: float) -> list[Face]:
    """The single-wire `face` offset by d (> 0 outward), round corners, one face per loop,
    largest first. [] if it vanishes."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from OCP.GeomAbs import GeomAbs_Arc
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    def loops(f):
        mo = BRepOffsetAPI_MakeOffset(f.wrapped, GeomAbs_Arc)
        mo.Perform(d)
        if not mo.IsDone():
            raise ValueError("not done")
        out = []
        ex = TopExp_Explorer(mo.Shape(), TopAbs_WIRE)
        while ex.More():
            w = Wire(TopoDS.Wire(ex.Current()))
            ex.Next()
            g = _despike(no_offset_curves(_flat_face(w)))
            _walk(g.outer_wire())                    # a closed chain, else it's broken
            if g.area > 1e-9:
                out.append(g)
        return sorted(out, key=lambda g: -g.area)

    # as it is, else with its micron edges merged away (OCC fails on some of those, and
    # on some of the near-tangent corners that merging them leaves: either may work)
    face = split_circles(no_offset_curves(face))
    for f in (face, drop_short_edges(face, SHORT)):
        try:
            return loops(f)
        except Exception:          # OCC raises its own types on broken results
            pass
    raise RuntimeError(f"offset by {d} failed")


def _up(f: Face) -> Face:
    """The flat face with its normal up (+z). Placed or mirrored faces can come with it
    down; booleans then keep faces of opposite orientation apart instead of merging them."""
    return f if f.normal_at().Z > 0 else Face(f.wrapped.Reversed())


def _faces(shape) -> list[Face]:
    return [_up(f) for f in shape.faces() if f.area > 1e-9] if shape is not None else []


def fuse(faces: list) -> list[Face]:
    """Union of flat faces, one face per connected region."""
    faces = [_up(g) for f in faces if f is not None for g in f.faces()]
    if not faces:
        return []
    if len(faces) == 1:
        return _faces(faces[0])
    return _faces(faces[0].fuse(*faces[1:]).clean())


def subtract(faces: list, tools: list, fuzzy: float = 0.0) -> list[Face]:
    """Flat faces (not overlapping each other) minus tools; with `fuzzy`, boundaries closer
    than that count as the same."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    faces = [_up(g) for f in faces for g in f.faces() if g.area > 1e-9]
    tools = [_up(g) for t in tools for g in t.faces()]
    if not faces or not tools:
        return faces
    op = BRepAlgoAPI_Cut()
    if fuzzy:
        op.SetFuzzyValue(fuzzy)
    base = faces[0] if len(faces) == 1 else Compound(faces)
    return _faces(base._bool_op((base,), tuple(tools), op).clean())


def _sausage(e: Edge, d: float) -> list[Face]:
    """The points within d of edge e (lines and circle arcs): a band along it with round
    ends."""
    from build123d import Circle, Line, ThreePointArc, Vector
    a, b = e.position_at(0), e.position_at(1)
    ends = [Pos(p.X, p.Y) * Circle(d) for p in (a, b)]
    if e.geom_type == GeomType.LINE:
        u = (b - a).normalized()
        n = Vector(-u.Y, u.X, 0) * d
        band = poly(*[(v.X, v.Y) for v in (a + n, b + n, b - n, a - n)])
    elif e.geom_type == GeomType.CIRCLE and (e.position_at(0.5) - (a + b) * 0.5).length < 1e-4 \
            and (a - b).length > 1e-7:
        # an arc this flat (sag < 0.1 um) is a line for this purpose; ThreePointArc
        # rejects its nearly collinear points
        return _sausage(Edge.make_line(a, b), d)
    elif e.geom_type == GeomType.CIRCLE:
        c, rho, m = e.arc_center, e.radius, e.position_at(0.5)
        ro, ri = rho + d, rho - d
        if (a - b).length < 1e-7:                     # full circle
            band = Pos(c.X, c.Y) * Circle(ro)
            if ri > 1e-6:
                band = band - Pos(c.X, c.Y) * Circle(ri)
        else:
            on = lambda p, r: c + (p - c) * (r / (p - c).length)
            outer = ThreePointArc(on(a, ro), on(m, ro), on(b, ro))
            if ri > 1e-6:
                w = Wire([outer, Line(on(b, ro), on(b, ri)), ThreePointArc(on(b, ri), on(m, ri), on(a, ri)),
                          Line(on(a, ri), on(a, ro))])
            else:
                w = Wire([outer, Line(on(b, ro), c), Line(c, on(a, ro))])
            band = Face(w)
    else:
        # any other curve (B-splines: lip relief, run-outs): converted to circle arcs and
        # lines within 5 um (OCC's ConvertWire), each of which gets its exact band
        from OCP.BRepAlgo import BRepAlgo
        support = Rectangle(1e4, 1e4).faces()[0]
        w = Wire(BRepAlgo.ConvertWire_s(Wire([e]).wrapped, 0.005, support.wrapped))
        subs = [x for x in w.edges() if x.length > 1e-4 and x.geom_type in (GeomType.LINE, GeomType.CIRCLE)]
        if not subs:
            raise RuntimeError(f"no band for a {e.geom_type.name} edge")
        return fuse([f for x in subs for f in _sausage(x, d)])
    return [_up(f) for x in [band] + ends for f in x.faces()]


def _grow1(face: Face, d: float) -> list[Face]:
    """A face with one wire grown by d > 0. Where OCC's offset breaks (it does on the
    socket pocket for some d): the face and a band of width d along each edge, fused."""
    try:
        loops = _offset_loops(face, d)
        return subtract([loops[0]], loops[1:]) if len(loops) > 1 else loops[:1]
    except RuntimeError:
        return fuse([face] + [b for e in face.edges() for b in _sausage(e, d)])


def _shrink1(face: Face, d: float) -> list[Face]:
    """A face with one wire shrunk by d > 0; where OCC's offset breaks, the face minus a
    band of width d along each edge."""
    bb = face.bounding_box()
    if face.area < math.pi * d * d or min(bb.size.X, bb.size.Y) < 2 * d:
        return []                                      # no disc of radius d fits
    bands = lambda: subtract([face], [b for e in face.edges() for b in _sausage(e, d)])
    if len(face.edges()) <= 12:
        # small faces the bands way: OCC's offset crashes (segfault) on some that
        # shrink to nothing, like a sliver between a circle and a line
        return bands()
    try:
        return _offset_loops(face, -d)
    except RuntimeError:
        return bands()


def grow(face: Face, d: float) -> list[Face]:
    """Points within d of `face` (round corners)."""
    outer = _grow1(_flat_face(face.outer_wire()), d)
    holes = [g for h in _holes(face) for g in _shrink1(h, d)]
    return subtract(outer, holes) if holes and outer else outer


def shrink(face: Face, d: float) -> list[Face]:
    """Points of `face` at least d from its boundary (its convex corners come out sharp,
    concave ones round)."""
    parts = _shrink1(_flat_face(face.outer_wire()), d)
    islands = fuse([g for h in _holes(face) for g in _grow1(h, d)])
    # two islands that only touch would leave holes meeting at a point, which OCC can't
    # represent (the face comes out invalid and later booleans go wrong): the smaller is
    # a stick of material at most a point wide there, so it goes (is cut away)
    islands.sort(key=lambda f: -f.area)
    keep = []
    for g in islands:
        if not any(_near(g, k, 1e-6) for k in keep):
            keep.append(g)
    return subtract(parts, keep) if keep and parts else parts


def closing(faces: list, r: float) -> list[Face]:
    """Morphological closing of the union of `faces`: grown by r, shrunk by r. Fills every
    gap narrower than 2r and rounds every corner of the material between them (every
    concave corner of the union) with radius r."""
    grown = fuse([g for f in faces for g in grow(f, r)])
    out = []
    for f in grown:
        try:
            out += shrink(f, r)
        except RuntimeError:
            # left as it was (unclosed) rather than failing the build; say where
            import warnings
            c = f.center()
            warnings.warn(f"closing: could not shrink the region at ({c.X:.1f}, {c.Y:.1f})", stacklevel=2)
            out += fuse([g for g in faces if g.distance_to(f) < 1e-6])
    return out


def pinches(face: Face, tol: float = 1e-6) -> list:
    """Points where two of the face's wires touch at a point (the face is pinched there;
    OCC calls it invalid and booleans on it go wrong). Wires sharing edges don't count."""
    from build123d import Vertex
    from OCP.BRepCheck import BRepCheck_Face, BRepCheck_NoError
    if BRepCheck_Face(face.wrapped).IntersectWires() == BRepCheck_NoError:
        return []
    ws = [face.outer_wire()] + face.inner_wires()
    out = []
    for i, a in enumerate(ws):
        for b in ws:
            if b is a or a.distance_to(b) > tol:
                continue
            st = _walk(a)
            for x, y in zip(st, st[1:] + st[:1]):          # vertex between edges x and y
                v = Vertex(x.p1.X, x.p1.Y, x.p1.Z)
                if (b.distance_to(v) < tol and b.distance_to(Vertex(*x.point(0.5).to_tuple())) > tol
                        and b.distance_to(Vertex(*y.point(0.5).to_tuple())) > tol):
                    out.append(x.p1)
    return out


def opening(faces: list, r: float) -> list[Face]:
    """Morphological opening: the union of the discs of radius r inside the faces (parts
    narrower than 2r dropped, convex corners rounded)."""
    return fuse([g for f in faces for s_ in shrink(f, r) for g in grow(s_, r)])


def mean_width(f) -> float:
    """2 area / perimeter: about the width of a long thin face."""
    per = sum(e.length for e in f.edges())
    return 2 * f.area / per if per else 0.0


def gaps(faces: list, r: float) -> list[Face]:
    """What the closing by r adds to the union of `faces`: the material between them
    (and in their own notches) that a disc of radius r can't reach, as separate pieces."""
    return [q for q in subtract(closing(faces, r), faces) if mean_width(q) > 1e-3]


def _overlap(a: Face, b: Face) -> bool:
    ba, bb = a.bounding_box(), b.bounding_box()
    if ba.min.X > bb.max.X or bb.min.X > ba.max.X or ba.min.Y > bb.max.Y or bb.min.Y > ba.max.Y:
        return False
    common = a & b
    return common is not None and any(f.area > 1e-9 for f in common.faces())


def arcs_and_lines(face: Face, tol: float = 2e-5) -> Face:
    """The face with every other curve (B-splines) replaced by circle arcs and lines within
    tol. OCC offsets B-splines into offset curves, which then multiply into micron pieces
    and break later offsets; arcs and lines offset into arcs and lines."""
    from OCP.BRepAlgo import BRepAlgo
    if all(e.geom_type in (GeomType.LINE, GeomType.CIRCLE) for e in face.edges()):
        return face

    def conv(w):
        if all(e.geom_type in (GeomType.LINE, GeomType.CIRCLE) for e in w.edges()):
            return w
        return Wire(BRepAlgo.ConvertWire_s(w.wrapped, tol, face.wrapped))
    try:
        nf = _up(Face(conv(face.outer_wire()), [conv(w) for w in face.inner_wires()]))
        if nf.is_valid and abs(nf.area - face.area) < 1e-3 * max(face.area, 1.0):
            return nf
    except Exception:
        pass
    return face


def _near(a: Face, b: Face, tol: float = 1e-6) -> bool:
    ba, bb = a.bounding_box(), b.bounding_box()
    if (ba.min.X > bb.max.X + tol or bb.min.X > ba.max.X + tol
            or ba.min.Y > bb.max.Y + tol or bb.min.Y > ba.max.Y + tol):
        return False
    return a.distance_to(b) < tol


def open_thin_walls(faces: list, keep_out: list, w: float, w_open: float, r: float,
                    hold: list = (), seal: list | None = None) -> list[Face]:
    """The region to cut so that no material narrower than w is left between `faces`
    (pockets cut from the same height), or between them and `keep_out` (the part's
    outside, other features that must not be cut themselves), with smooth outlines:
      1. thin walls: the gaps (closing minus union) at radius w/2 that touch two separate
         regions (pockets, the outside): the material between them a disc of diameter w
         can't pass. (A gap touching one region only is a corner, rule 4.)
      2. a wall that is opened is opened to w_open > w: each gap at radius w_open/2 that
         contains a thin wall is cut. The wall's remaining ends are round (radius w_open/2)
         and well apart, where a closing right at the wall's thickness would leave a
         hairline channel between two ends almost touching (micron-wide faces).
      3. necks: a passage of the cut region narrower than w joining two wider parts is
         widened to w, so a small cut overlapping a bigger one a little merges into it as
         one shape rather than two joined by a slit.
      4. corners: every corner of material left between pockets and opened walls (or
         between them and keep_out) rounded with radius r: the gaps at radius r touching
         a pocket; tips and sticks narrower than 2r go with them. The outline's own
         corners stay as they are.
    `hold` (part of keep_out) is outside where the edge is a visible wall: nothing is
    added within w_open/2 of it, so walls to it stay as drawn. keep_out is taken off the
    result. Curves other than lines and arcs are approximated by arcs (arcs_and_lines).
    `seal`: the part of keep_out that is the plate's true outside (default: all of it);
    where the cut reaches it, the cut is run on into it before the exact subtraction."""
    exact_keep_out = keep_out
    keep_out = [arcs_and_lines(f) for f in fuse(keep_out)]
    pockets = [arcs_and_lines(f) for f in fuse(faces)]
    regions = pockets + keep_out
    base = fuse(regions)
    protect = fuse([g for h in hold for g in grow(arcs_and_lines(h), w_open / 2)]) if hold else []

    def added(qs):                         # additions to the cut, off the protected band
        return subtract(qs, protect) if protect and qs else qs

    thin = [q for q in gaps(base, w / 2) if sum(_near(q, g) for g in regions) >= 2]
    opened = [q for q in gaps(base, w_open / 2) if any(_overlap(q, t) for t in thin)] if thin else []
    core = fuse(base + added(opened))
    bodies = fuse([g for f in core for s_ in shrink(f, w / 2) for g in grow(s_, w / 2)])
    necks = [q for q in subtract(core, bodies) if mean_width(q) > 1e-3
             and sum(_near(q, b) for b in bodies) >= 2]
    if necks:
        core = fuse(core + added([g for q in necks for g in grow(q, w / 2)]))
    corners = [q for q in gaps(core, r) if any(_near(q, g) for g in pockets)]
    region = fuse(core + added(corners))
    if keep_out:
        # the exact outside: its curves were approximated (within 2e-5) for the morphology.
        # Fuzzy 1 um: where the cut reaches the outside, approximate and exact edge count as
        # one, so no zero-width strip of material is left along the edge (a non-manifold
        # line that slicers warn about, at HS17); 1 um is far below anything printable
        region = subtract(region, exact_keep_out, fuzzy=1e-3)
    return [f for f in region if mean_width(f) > 1e-3]
