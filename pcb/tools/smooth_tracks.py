"""Pull-tight cleanup of autorouted copper: replace chains of segments by the shortest
octilinear path (one straight run, or a 45 degree dogleg) wherever that is clear.

    python3 pcb/tools/smooth_tracks.py pcb/atlas-left.kicad_pcb

Freerouting normally polishes its result with its optimizer, which it skips while it believes
the board is not fully routed (it misreads the hotswap pad contacts), leaving jogs and stubs.

Fixed nodes (never moved): chain ends at pads, vias, T-junctions, any point another item of
the net touches mid-segment.  Clearances checked here: 0.15 copper, board hole clearance,
0.3 copper-to-edge, rule-area keepouts; KiCad DRC has the last word afterwards.
"""
import math
import sys
import pcbnew
from pcbnew import FromMM, ToMM, VECTOR2I

path = sys.argv[1]
b = pcbnew.LoadBoard(path)
ds = b.GetDesignSettings()
CLR, HCLR, EDGE = FromMM(0.15), ds.m_HoleClearance, FromMM(0.3)
M = FromMM(0.01)
ERR = FromMM(0.005)
LAYERS = (pcbnew.F_Cu, pcbnew.B_Cu)


def poly_of(item, layer, grow):
    ps = pcbnew.SHAPE_POLY_SET()
    item.TransformShapeToPolygon(ps, layer, int(grow), ERR, pcbnew.ERROR_INSIDE)
    return ps


def hits(a, bset):
    c = a.CloneDropTriangulation()
    c.BooleanIntersection(bset)
    return c.OutlineCount() > 0


# ---- obstacles per layer: (netcode, bbox, poly) ---------------------------------------------
obst = {L: {} for L in LAYERS}           # key -> (net, bbox, poly)
tracks = [t for t in b.GetTracks()]
pads = [p for f in b.GetFootprints() for p in f.Pads()]
for L in LAYERS:
    for t in tracks:
        if t.IsOnLayer(L):
            pp = poly_of(t, L, CLR); obst[L][t.m_Uuid.AsString()] = (t.GetNetCode(), pp.BBox(), pp)
    for i, p in enumerate(pads):
        if p.IsOnLayer(L):
            pp = poly_of(p, L, CLR); obst[L][f'pad{i}'] = (p.GetNetCode(), pp.BBox(), pp)
        if p.HasHole():
            hp = pcbnew.SHAPE_POLY_SET()
            p.TransformHoleToPolygon(hp, int(HCLR), ERR, pcbnew.ERROR_INSIDE)
            obst[L][f'hole{i}'] = (-1, hp.BBox(), hp)        # -1: blocks every net
    for i, z in enumerate(b.Zones()):
        if z.GetIsRuleArea() and z.GetDoNotAllowTracks() and z.IsOnLayer(L):
            kp = z.Outline().CloneDropTriangulation(); obst[L][f'ko{i}'] = (-1, kp.BBox(), kp)
for t in tracks:                          # via drills: hole clearance for other nets
    if t.Type() == pcbnew.PCB_VIA_T:
        hp = pcbnew.SHAPE_POLY_SET()
        hp.NewOutline()
        r = t.GetDrillValue() // 2 + HCLR
        c = t.GetPosition()
        for k in range(24):
            a = 2 * math.pi * k / 24
            hp.Append(int(c.x + r * math.cos(a)), int(c.y + r * math.sin(a)))
        for L in LAYERS:
            obst[L]['vh' + t.m_Uuid.AsString()] = (t.GetNetCode(), hp.BBox(), hp)

outline = pcbnew.SHAPE_POLY_SET()
b.GetBoardPolygonOutlines(outline, False)
inside = outline.CloneDropTriangulation()
inside.Deflate(int(EDGE), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, ERR)


def seg_poly(a, c, w):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(a); t.SetEnd(c); t.SetWidth(w); t.SetLayer(pcbnew.F_Cu)
    return poly_of(t, pcbnew.F_Cu, 0)


def clear(a, c, w, L, net):
    sp = seg_poly(a, c, w)
    out = sp.CloneDropTriangulation(); out.BooleanSubtract(inside)
    if out.OutlineCount():
        return False
    bb = sp.BBox()
    for n, obb, op in obst[L].values():
        if n == net or not obb.Intersects(bb):
            continue
        if hits(sp, op):
            return False
    return True


def octi(a, c):
    dx, dy = c.x - a.x, c.y - a.y
    return dx == 0 or dy == 0 or abs(abs(dx) - abs(dy)) <= 2


def candidates(a, c):
    """straight if octilinear, else the two 45-degree doglegs"""
    if octi(a, c):
        return [[a, c]]
    dx, dy = c.x - a.x, c.y - a.y
    m = min(abs(dx), abs(dy))
    sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
    diag = VECTOR2I(sx * m, sy * m)
    return [[a, a + diag, c], [a, c - diag, c]]


# ---- chains ----------------------------------------------------------------------------------
def key(p, L):
    return (p.x, p.y, L)


segs = [t for t in tracks if t.Type() == pcbnew.PCB_TRACE_T and t.GetLength() > 0]
deg = {}
for t in segs:
    for p in (t.GetStart(), t.GetEnd()):
        deg.setdefault(key(p, t.GetLayer()), []).append(t)
fixed = set()
vias = [t for t in tracks if t.Type() == pcbnew.PCB_VIA_T]
for k, lst in deg.items():
    x, y, L = k
    pt = VECTOR2I(x, y)
    if len(lst) != 2 or lst[0].GetNetCode() != lst[1].GetNetCode():
        fixed.add(k); continue
    net = lst[0].GetNetCode()
    if any(v.GetNetCode() == net and v.HitTest(pt) for v in vias) or \
       any(p.GetNetCode() == net and p.IsOnLayer(L) and p.HitTest(pt) for p in pads):
        fixed.add(k)
# an endpoint/via/pad of the net landing mid-segment pins that segment's ends
for t in segs:
    net, L = t.GetNetCode(), t.GetLayer()
    probes = [VECTOR2I(x, y) for (x, y, l2), lst in deg.items() if l2 == L and lst[0].GetNetCode() == net
              and lst[0] is not t and (len(lst) < 2 or lst[1] is not t)]
    probes += [v.GetPosition() for v in vias if v.GetNetCode() == net]
    for q in probes:
        if q != t.GetStart() and q != t.GetEnd() and pcbnew.SEG(t.GetStart(), t.GetEnd()).Distance(q) <= 2:
            fixed.add(key(t.GetStart(), L)); fixed.add(key(t.GetEnd(), L))
            break

seen = set()
chains = []
for t in segs:
    if t.m_Uuid.AsString() in seen:
        continue
    # walk back to a fixed node
    L = t.GetLayer()
    cur, node = t, key(t.GetStart(), L)
    guard = 0
    while node not in fixed and guard < 10000:
        nxt = [s for s in deg[node] if s is not cur][0]
        cur = nxt
        node = key(cur.GetEnd(), L) if key(cur.GetStart(), L) == node else key(cur.GetStart(), L)
        guard += 1
        if cur is t:
            break
    # walk forward from that node
    chain, pts = [], [VECTOR2I(node[0], node[1])]
    seg = cur
    while True:
        chain.append(seg); seen.add(seg.m_Uuid.AsString())
        nxt_node = key(seg.GetEnd(), L) if key(seg.GetStart(), L) == node else key(seg.GetStart(), L)
        pts.append(VECTOR2I(nxt_node[0], nxt_node[1]))
        node = nxt_node
        if node in fixed:
            break
        nx = [s for s in deg[node] if s is not seg]
        if not nx or nx[0].m_Uuid.AsString() in seen:
            break
        seg = nx[0]
    chains.append((chain, pts))

# ---- pull tight --------------------------------------------------------------------------------
adds, kills = [], []
before = after = 0
for chain, pts in chains:
    # chain[k] runs pts[k] -> pts[k+1]; widths may mix (Freerouting necks down to 0.15):
    # new runs take the widest, kept originals keep their own
    L, net = chain[0].GetLayer(), chain[0].GetNetCode()
    w = max(s.GetWidth() for s in chain)
    if len(chain) < 2 and octi(pts[0], pts[-1]):
        continue
    for s in chain:                       # own chain is same net anyway; drop it as obstacle
        obst[L].pop(s.m_Uuid.AsString(), None)
    out = [(pts[0], None)]                # (point, width of the run ending there)
    i, n = 0, len(pts) - 1
    while i < n:
        done = False
        for j in range(n, i, -1):
            if j == i + 1 and octi(pts[i], pts[j]):
                out.append((pts[j], chain[i].GetWidth())); i = j; done = True; break
            for cand in candidates(pts[i], pts[j]):
                if all(clear(p, q, w, L, net) for p, q in zip(cand, cand[1:]) if p != q):
                    out += [(p, w) for p in cand[1:]]
                    i = j; done = True; break
            if done:
                break
        if not done:                      # keep the original segment
            out.append((pts[i + 1], chain[i].GetWidth())); i += 1
    # drop zero-length / merge collinear runs of equal width
    clean = [out[0]]
    for p, wd in out[1:]:
        if p == clean[-1][0]:
            continue
        if len(clean) >= 2 and clean[-1][1] == wd:
            a, c = clean[-2][0], clean[-1][0]
            if (c.x - a.x) * (p.y - c.y) == (c.y - a.y) * (p.x - c.x) and \
               (c.x - a.x) * (p.x - c.x) + (c.y - a.y) * (p.y - c.y) >= 0:
                clean[-1] = (p, wd); continue
        clean.append((p, wd))
    before += len(chain); after += len(clean) - 1
    for (p, _), (q, wd) in zip(clean, clean[1:]):
        nt = pcbnew.PCB_TRACK(b); nt.SetStart(p); nt.SetEnd(q); nt.SetWidth(wd); nt.SetLayer(L); nt.SetNetCode(net)
        adds.append(nt)
        pp = poly_of(nt, L, CLR); obst[L][f'new{len(adds)}'] = (net, pp.BBox(), pp)
    kills += chain

kills += [t for t in tracks if t.Type() == pcbnew.PCB_TRACE_T and t.GetLength() == 0]   # junk
for t in adds:
    b.Add(t)
for t in kills:                            # last: Remove() invalidates later proxies
    b.Remove(t)
pcbnew.SaveBoard(path, b)
print(f'RESULT {len(chains)} chains: {before} -> {after} segments')
