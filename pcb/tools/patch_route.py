"""Route the remaining unconnected pairs of one net with a small grid A* (two layers + vias),
using KiCad's own geometry for obstacles.  For the few links Freerouting gives up on (it
misreads contact with the rotated hotswap pads, e.g. the thumb HS17).

    python3 pcb/tools/patch_route.py pcb/atlas-left.kicad_pcb COL4

Adds at most one link per run (start cluster -> nearest other cluster of the net) and saves;
loop it until DRC shows no unconnected item on that net.  Clearances: 0.15 mm copper,
0.3 mm copper-to-edge (the .kicad_dru), rule-area keepouts honoured.  Tracks 0.2 mm,
vias 0.6/0.3 mm, 0.05 mm grid.
"""
import heapq
import sys
import pcbnew
from pcbnew import FromMM, ToMM, VECTOR2I

path, netname = sys.argv[1:3]
b = pcbnew.LoadBoard(path)
b.BuildConnectivity()
conn = b.GetConnectivity()
net = b.FindNet(netname)
nc = net.GetNetCode()

G = FromMM(0.05)                      # grid pitch
TW, CLR, EDGE = FromMM(0.2), FromMM(0.15), FromMM(0.3)
VD, MARGIN = FromMM(0.6), FromMM(0.02)
HCLR = b.GetDesignSettings().m_HoleClearance     # 0.25: copper to any drilled hole
LAYERS = (pcbnew.F_Cu, pcbnew.B_Cu)

# ---- the net's copper, grouped into connected clusters ---------------------------------------
items = [t for t in b.GetTracks() if t.GetNetCode() == nc]
items += [p for f in b.GetFootprints() for p in f.Pads() if p.GetNetCode() == nc]
cluster = {}
cid = 0
for it in items:
    if it.m_Uuid.AsString() in cluster:
        continue
    for c in [it] + list(conn.GetConnectedItems(it)):
        if c.GetNetCode() == nc:
            cluster[c.m_Uuid.AsString()] = cid
    cid += 1
groups = {}
for it in items:
    groups.setdefault(cluster[it.m_Uuid.AsString()], []).append(it)
if len(groups) < 2:
    print('RESULT 0 connected'); sys.exit()


def pos_of(it):
    return it.GetPosition() if it.Type() != pcbnew.PCB_TRACE_T else (it.GetStart() + it.GetEnd()) / 2


# start from the smallest cluster (the stranded pad); goal = every other cluster
start_g = min(groups, key=lambda g: len(groups[g]))
src = groups[start_g]
dst = [it for g, its in groups.items() if g != start_g for it in its]
sp = pos_of(src[0])
dmin = min((pos_of(d) - sp).EuclideanNorm() for d in dst)
R = int(dmin + FromMM(12))
x0, y0 = sp.x - R, sp.y - R
N = 2 * R // G + 1


def cell(x, y):
    return (x - x0) // G, (y - y0) // G


def raster(poly, grid, li):
    """mark every grid point inside poly"""
    bb = poly.BBox()
    i0, j0 = cell(bb.GetX(), bb.GetY())
    i1, j1 = cell(bb.GetRight(), bb.GetBottom())
    for i in range(max(i0, 0), min(i1 + 2, N)):
        for j in range(max(j0, 0), min(j1 + 2, N)):
            if poly.Contains(VECTOR2I(x0 + i * G, y0 + j * G)):
                grid[li].add((i, j))


blocked = [set(), set()]     # track centreline forbidden
vblocked = [set(), set()]    # via centre forbidden
goal = [set(), set()]
srcset = [set(), set()]
win = pcbnew.BOX2I(VECTOR2I(x0, y0), VECTOR2I(2 * R, 2 * R))


def shape(it, li, grow):
    ps = pcbnew.SHAPE_POLY_SET()
    it.TransformShapeToPolygon(ps, LAYERS[li], int(grow), FromMM(0.005), pcbnew.ERROR_INSIDE)
    return ps


for li, L in enumerate(LAYERS):
    copper = [t for t in b.GetTracks() if t.IsOnLayer(L)]
    copper += [p for f in b.GetFootprints() for p in f.Pads() if p.IsOnLayer(L)]
    for it in copper:
        if not it.GetBoundingBox().Intersects(win):
            continue
        if it.GetNetCode() == nc:
            u = it.m_Uuid.AsString()
            if it.Type() == pcbnew.PCB_PAD_T:      # no via in (or touching) a pad, even our own:
                raster(shape(it, li, VD / 2 + MARGIN), vblocked, li)   # paste wicks into it
            if cluster.get(u) == start_g:
                raster(shape(it, li, 0), srcset, li)
            else:
                raster(shape(it, li, -FromMM(0.05)), goal, li)
            continue
        raster(shape(it, li, CLR + TW / 2 + MARGIN), blocked, li)
        raster(shape(it, li, CLR + VD / 2 + MARGIN), vblocked, li)
    # every drilled hole (NPTH switch pins, PTH of other nets, vias): hole clearance
    holes = [p for f in b.GetFootprints() for p in f.Pads() if p.HasHole() and p.GetNetCode() != nc]
    for p in holes:
        if not p.GetBoundingBox().Intersects(win):
            continue
        for grow, grid in ((HCLR + VD / 2 + MARGIN, vblocked), (HCLR + TW / 2 + MARGIN, blocked)):
            ps = pcbnew.SHAPE_POLY_SET()
            p.TransformHoleToPolygon(ps, int(grow), FromMM(0.005), pcbnew.ERROR_INSIDE)
            raster(ps, grid, li)
    for z in b.Zones():
        if z.GetIsRuleArea() and z.IsOnLayer(L) and z.GetBoundingBox().Intersects(win):
            ps = z.Outline().CloneDropTriangulation()
            if z.GetDoNotAllowTracks():
                ps.Inflate(int(TW / 2 + MARGIN), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, FromMM(0.005))
                raster(ps, blocked, li)
            if z.GetDoNotAllowVias():
                ps2 = z.Outline().CloneDropTriangulation()
                ps2.Inflate(int(VD / 2 + MARGIN), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, FromMM(0.005))
                raster(ps2, vblocked, li)

outline = pcbnew.SHAPE_POLY_SET()
b.GetBoardPolygonOutlines(outline, False)
inner_t = outline.CloneDropTriangulation()
inner_t.Deflate(int(EDGE + TW / 2 + MARGIN), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, FromMM(0.005))
inner_v = outline.CloneDropTriangulation()
inner_v.Deflate(int(EDGE + VD / 2 + MARGIN), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, FromMM(0.005))
okt, okv = [set()], [set()]
raster(inner_t, okt, 0)
raster(inner_v, okv, 0)
okt, okv = okt[0], okv[0]


def free(li, c):
    return c in okt and c not in blocked[li] or c in srcset[li] or c in goal[li]


def vfree(c):
    return c in okv and c not in vblocked[0] and c not in vblocked[1]


# ---- A*: state (layer, i, j); 8-neighbour moves, via = cost 40 cells -------------------------
gi = [(i, j) for li in (0, 1) for (i, j) in goal[li]]
gx = [g[0] for g in gi]; gy = [g[1] for g in gi]
gbox = (min(gx), max(gx), min(gy), max(gy))


def h(i, j):
    dx = max(gbox[0] - i, 0, i - gbox[1]); dy = max(gbox[2] - j, 0, j - gbox[3])
    return max(dx, dy) + 0.414 * min(dx, dy)


openq, came, cost = [], {}, {}
for li in (0, 1):
    for c in srcset[li]:
        s = (li,) + c
        cost[s] = 0
        heapq.heappush(openq, (h(*c), s))
DIRS = [(1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
end = None
while openq:
    f, s = heapq.heappop(openq)
    li, i, j = s
    if (i, j) in goal[li]:
        end = s; break
    base = cost[s]
    nbr = []
    for di, dj, w in DIRS:
        c = (i + di, j + dj)
        if free(li, c):
            # turn penalty keeps the path to few segments
            pd = came.get(s)
            turn = 0.3 if pd and (pd[1] - i, pd[2] - j) != (-di, -dj) else 0
            nbr.append(((li, c[0], c[1]), w + turn))
    if vfree((i, j)) and free(1 - li, (i, j)):
        nbr.append(((1 - li, i, j), 40))
    for n, w in nbr:
        nc_ = base + w
        if nc_ < cost.get(n, 1e18):
            cost[n] = nc_; came[n] = s
            heapq.heappush(openq, (nc_ + h(n[1], n[2]), n))
if end is None:
    print('RESULT FAIL no path'); sys.exit(1)

path_ = [end]
while path_[-1] in came:
    path_.append(came[path_[-1]])
path_.reverse()

# compress to straight runs; drop the parts inside source/goal copper except one cell
pts = [path_[0]]
for cur in path_[1:]:
    if len(pts) >= 2 and pts[-2][0] == pts[-1][0] == cur[0]:
        d1 = (pts[-1][1] - pts[-2][1], pts[-1][2] - pts[-2][2])
        d2 = (cur[1] - pts[-1][1], cur[2] - pts[-1][2])
        if d1[0] * d2[1] == d1[1] * d2[0] and d1[0] * d2[0] + d1[1] * d2[1] > 0:
            pts[-1] = cur
            continue
    pts.append(cur)
added = 0
for (la, ia, ja), (lb, ib, jb) in zip(pts, pts[1:]):
    A = VECTOR2I(x0 + ia * G, y0 + ja * G); B = VECTOR2I(x0 + ib * G, y0 + jb * G)
    if la != lb:
        v = pcbnew.PCB_VIA(b); v.SetPosition(A); v.SetWidth(VD); v.SetDrill(FromMM(0.3))
        v.SetNetCode(nc); b.Add(v); added += 1
        continue
    if A == B:
        continue
    t = pcbnew.PCB_TRACK(b); t.SetStart(A); t.SetEnd(B); t.SetWidth(TW)
    t.SetLayer(LAYERS[la]); t.SetNetCode(nc); b.Add(t); added += 1
pcbnew.SaveBoard(path, b)
L = sum(((pts[k + 1][1] - pts[k][1]) ** 2 + (pts[k + 1][2] - pts[k][2]) ** 2) ** 0.5 for k in range(len(pts) - 1)) * ToMM(G)
print(f'RESULT 1 {netname}: {added} items, {L:.1f} mm, from {ToMM(sp)}')
