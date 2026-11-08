"""Small geometry helpers shared by the case parts (case frame, mm, z up).

Flat region work (offsets, closings, unions) is done on polygons in geom2d; what is here
is exact OpenCascade geometry: prisms, rectangles, the final boolean, and the switch
plate's thin-wall bridges."""
from __future__ import annotations

from build123d import Compound, Edge, Face, GeomType, Kind, Polygon, Pos, Rectangle, Wire, offset
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.Geom import Geom_TrimmedCurve
from OCP.GeomAbs import GeomAbs_C2
from OCP.GeomConvert import GeomConvert_ApproxCurve
from OCP.TopExp import TopExp


def prism(face, z0: float, z1: float):
    """Extrude a flat XY face (or several) from z0 to z1, whatever way its normal happens
    to point. Straight OCC (BRepPrimAPI_MakePrism): ten times faster than build123d's
    extrude, which matters for the bottom plate's hundred-odd slabs."""
    from build123d import Solid
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec
    solids = []
    for f in face.faces():
        if f.normal_at().Z < 0:
            f = -f
        f = f.moved(Pos(0, 0, z0)) if z0 else f
        solids.append(Solid(BRepPrimAPI_MakePrism(f.wrapped, gp_Vec(0, 0, z1 - z0)).Shape()))
    return solids[0] if len(solids) == 1 else Compound(solids)


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
