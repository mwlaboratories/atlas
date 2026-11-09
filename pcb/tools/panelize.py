"""Build the PCBWay panel (pcb/atlas-panel.kicad_pcb) from the two halves.

    python3 pcb/tools/panelize.py [--pcb-dir pcb] [--out pcb/atlas-panel.kicad_pcb]

Plain Python 3 (stdlib only, no pcbnew). Writes the board plus its project files next to it:
<out>.kicad_pcb, <out stem>.kicad_pro and <out stem>.kicad_dru. Re-running is deterministic.

How the panel is made (reverse-engineered from the hand-made v4.1 panel, which this script
reproduces; see the rules below):

  * Left half: copied as is (same coordinates, references, nets, uuids).
  * Right half: translated by (+DX, 0), no rotation or mirroring. References get +100
    (D1 -> D101, U1 -> U101); references without a number get "_R" (SW_PWR -> SW_PWR_R).
    Nets get the prefix "R_" (GND -> R_GND), so the halves stay two separate circuits.
    uuids are kept (the halves share none; a collision would be remapped deterministically).
  * Header (general, paper, title_block, layers, setup with stackup and plot settings):
    taken from the left board. The right board's header must match it.
  * Edge.Cuts: the two outlines, joined across the gap between the halves by three solid
    bands (BANDS): two straight rails between the inner edges (x 131.8 / 180.2) and the
    bridge between the thumb clusters' far sides. For each band, the facing edge of each half
    inside the band is cut out and the band's top and bottom lines are drawn from one half's
    edge to the other's, so the panel is one outline with two inner cut-outs. Everything is
    gr_line at EDGE_WIDTH. Only gr_line edges are supported (the case pipeline uses the same).
  * Mouse bites: one board-only footprint MB1 with 0.5 mm NPTH holes at 0.8 mm pitch along
    every joint (where a band meets a board edge), the holes tangent to the board edge on the
    band side, centred on the joint, as many as fit with 0.25 mm of material left at each end.
  * Design rules: the left .kicad_dru with every memberOfFootprint('X') widened to
    (memberOfFootprint('X') || memberOfFootprint('X101')); .kicad_pro = the left's, renamed.

No fiducials, tooling holes, frame or panel text: the panel is just the two boards and the
three bands (PCBWay adds its own rails if it wants them).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import uuid
from decimal import Decimal
from pathlib import Path

DX = Decimal("120")          # right half offset: halves mirror-symmetric about x = 156
# (y0, y1) of the bands that join the halves (KiCad y, down). Rails 5.04 mm, bridge 4.04 mm.
BANDS = [(Decimal("74.98"), Decimal("80.02")),      # top rail
         (Decimal("111.98"), Decimal("117.02")),    # middle rail
         (Decimal("144.98"), Decimal("149.02"))]    # thumb-cluster bridge (far sides)
EDGE_WIDTH = "0.05"
MB_DRILL = 0.5               # mouse-bite hole diameter
MB_PITCH = 0.8
MB_END = 0.25                # material between the outermost hole and the band's edge
REF_OFFSET = 100
NET_PREFIX = "R_"
UUID_NS = uuid.UUID("3d0b8f7e-5a41-4c3e-9d6a-0f1e2b7c9a15")
HEADER = ("version", "generator", "generator_version", "general", "paper", "title_block",
          "layers", "setup")
EPS = 1e-6


# --- s-expressions with source positions ---------------------------------------------------

TOK = re.compile(r'\s*(?:(\()|(\))|("(?:[^"\\]|\\.)*")|([^\s()"]+))')


class Node(list):
    span = (0, 0)


class Atom(str):
    s = e = 0


def parse(text):
    stack, starts, pos = [Node()], [], 0
    while True:
        m = TOK.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group(1):
            n = Node()
            stack[-1].append(n)
            stack.append(n)
            starts.append(m.start(1))
        elif m.group(2):
            stack.pop().span = (starts.pop(), m.end(2))
        else:
            g = m.group(3) or m.group(4)
            a = Atom(g)
            a.s = m.start(3) if m.group(3) else m.start(4)
            a.e = a.s + len(g)
            stack[-1].append(a)
    if len(stack) != 1 or len(stack[0]) != 1:
        raise ValueError("unbalanced s-expression")
    return stack[0][0]


def child(node, key):
    for c in node:
        if isinstance(c, list) and c and c[0] == key:
            return c
    return None


def unq(a):
    return a[1:-1] if a.startswith('"') else a


def fmt(d):
    """KiCad number style: no exponent, no trailing zeros, at most 6 decimals (1 nm)."""
    d = Decimal(d).quantize(Decimal("0.000001")) if not isinstance(d, Decimal) else d
    s = format(d.normalize(), "f")
    return "0" if s in ("-0", "") else s


def q6(x):
    return Decimal(repr(round(float(x), 6))).quantize(Decimal("0.000001"))


# --- board -----------------------------------------------------------------------------------

class Board:
    def __init__(self, path):
        self.path = Path(path)
        self.text = self.path.read_text()
        self.tree = parse(self.text)
        if self.tree[0] != "kicad_pcb":
            raise ValueError(f"{path}: not a kicad_pcb")
        self.items = [c for c in self.tree[1:] if isinstance(c, list)]

    def src(self, node):
        return self.text[node.span[0]:node.span[1]]

    def is_edge(self, it):
        lay = child(it, "layer")
        return it[0].startswith("gr_") and lay is not None and unq(lay[1]) == "Edge.Cuts"

    def edges(self, dx=Decimal(0)):
        """Edge.Cuts gr_lines as ((x, y), (x, y)) Decimal pairs, shifted by dx."""
        out = []
        for it in self.items:
            if not self.is_edge(it):
                continue
            if it[0] != "gr_line":
                raise SystemExit(f"{self.path}: Edge.Cuts {it[0]} not supported (gr_line only)")
            s, e = child(it, "start"), child(it, "end")
            out.append(((Decimal(s[1]) + dx, Decimal(s[2])), (Decimal(e[1]) + dx, Decimal(e[2]))))
        return out

    def refs(self):
        out = []
        for it in self.items:
            if it[0] == "footprint":
                for p in it:
                    if isinstance(p, list) and p[:2] == ["property", '"Reference"']:
                        out.append(unq(p[2]))
        return out


def right_ref(ref):
    m = re.fullmatch(r"(.*?)(\d+)", ref)
    return f"{m.group(1)}{int(m.group(2)) + REF_OFFSET}" if m else ref + "_R"


COORD_KEYS = {"at", "start", "end", "mid", "center", "xy"}


def right_item(board, it, uuid_map):
    """Source text of a right-half item, translated by DX, references and nets renamed."""
    edits = []

    def num(a, d):
        edits.append((a.s, a.e, fmt(Decimal(a) + d)))

    def walk(n, in_fp):
        if not n:
            return
        h = n[0]
        if h in COORD_KEYS and not in_fp and len(n) >= 3:
            num(n[1], DX)
        if h == "net" and len(n) == 2 and n[1] != '""':
            edits.append((n[1].s, n[1].e, '"%s%s"' % (NET_PREFIX, unq(n[1]))))
        if h == "property" and len(n) >= 3 and n[1] == '"Reference"':
            edits.append((n[2].s, n[2].e, '"%s"' % right_ref(unq(n[2]))))
        for c in n[1:]:
            if isinstance(c, list):
                # a footprint's own (at) is absolute; everything inside it is relative
                walk(c, (in_fp or h == "footprint") and not (h == "footprint" and c[0] == "at"))
            elif c.startswith('"') and unq(c) in uuid_map:
                edits.append((c.s, c.e, '"%s"' % uuid_map[unq(c)]))

    walk(it, False)
    s0, s1 = it.span
    out, pos = [], s0
    for a, b, t in sorted(edits):
        out.append(board.text[pos:a])
        out.append(t)
        pos = b
    out.append(board.text[pos:s1])
    return "".join(out)


# --- outline ---------------------------------------------------------------------------------

def crossings(segs, y):
    """x where the non-horizontal segments cross the horizontal line y."""
    xs = []
    for (x1, y1), (x2, y2) in segs:
        if y1 == y2 or not (min(y1, y2) <= y <= max(y1, y2)):
            continue
        xs.append(float(x1) + (float(y) - float(y1)) * (float(x2) - float(x1)) / (float(y2) - float(y1)))
    return xs


def split_at(segs, y, xt):
    """Split the segment that strictly crosses the line y at x = xt at the crossing point."""
    out = []
    for a, b in segs:
        (x1, y1), (x2, y2) = a, b
        x = None
        if min(y1, y2) < y < max(y1, y2):
            x = float(x1) + (float(y) - float(y1)) * (float(x2) - float(x1)) / (float(y2) - float(y1))
        if x is not None and abs(x - xt) < 1e-4:
            out += [(a, (q6(x), y)), ((q6(x), y), b)]
        else:
            out.append((a, b))
    return out


def join(left, right):
    """Panel outline (list of segments) and the joints [(side, p0, p1)] for the mouse bites."""
    xc = (max(float(p[0]) for s in left for p in s) + min(float(p[0]) for s in right for p in s)) / 2
    joints, bands = [], []
    for y0, y1 in BANDS:
        inner = {}
        for side, segs, pick in (("L", left, max), ("R", right, min)):
            def xin(y):
                xs = [x for x in crossings(segs, y) if (x < xc if side == "L" else x > xc)]
                if not xs:
                    raise SystemExit(f"band {y0}..{y1}: the {side} half has no edge at y={y}")
                return pick(xs)
            segs[:] = split_at(split_at(segs, y0, xin(y0)), y1, xin(y1))
            keep, cut = [], []
            for a, b in segs:
                ym = (float(a[1]) + float(b[1])) / 2
                in_band = float(y0) <= float(a[1]) <= float(y1) and float(y0) <= float(b[1]) <= float(y1)
                if in_band and a[1] != b[1] and abs((float(a[0]) + float(b[0])) / 2 - xin(ym)) < 1e-4:
                    cut.append((a, b))
                else:
                    keep.append((a, b))
            segs[:] = keep
            p0, p1 = (q6(xin(y0)), y0), (q6(xin(y1)), y1)
            # the cut-out edge must be one straight piece from p0 to p1
            length = sum(math.dist(tuple(map(float, a)), tuple(map(float, b))) for a, b in cut)
            if not cut or abs(length - math.dist(tuple(map(float, p0)), tuple(map(float, p1)))) > 1e-4:
                raise SystemExit(f"band {y0}..{y1}: {side} facing edge is not a straight joint")
            inner[side] = (p0, p1)
            joints.append((side, p0, p1))
        for k in (0, 1):
            bands.append((inner["L"][k], inner["R"][k]))
    segs = left + right + bands
    check_closed(segs)
    return segs, joints


def check_closed(segs, tol=2e-3):
    """Every vertex must join exactly two segments (within tol); returns the loop count."""
    pts = []

    def vid(p):
        for i, q in enumerate(pts):
            if abs(float(p[0]) - float(q[0])) < tol and abs(float(p[1]) - float(q[1])) < tol:
                return i
        pts.append(p)
        return len(pts) - 1
    adj = {}
    for a, b in segs:
        i, j = vid(a), vid(b)
        adj.setdefault(i, []).append(j)
        adj.setdefault(j, []).append(i)
    bad = [pts[i] for i, n in adj.items() if len(n) != 2]
    if bad:
        raise SystemExit(f"panel outline is not closed at {[(fmt(x), fmt(y)) for x, y in bad[:6]]}")
    seen, loops = set(), 0
    for i in adj:
        if i in seen:
            continue
        loops += 1
        stack = [i]
        while stack:
            k = stack.pop()
            if k not in seen:
                seen.add(k)
                stack += adj[k]
    return loops


def mouse_bites(joints):
    r = MB_DRILL / 2
    holes = []
    for side, p0, p1 in joints:
        (x0, y0), (x1, y1) = map(lambda p: (float(p[0]), float(p[1])), (p0, p1))
        L = math.hypot(x1 - x0, y1 - y0)
        u = ((x1 - x0) / L, (y1 - y0) / L)
        n = (u[1], -u[0])
        if (n[0] > 0) != (side == "L"):          # normal into the band (towards the gap)
            n = (-n[0], -n[1])
        count = int(math.floor((L - 2 * (r + MB_END)) / MB_PITCH + 1e-9)) + 1
        mx, my = (x0 + x1) / 2 + n[0] * r, (y0 + y1) / 2 + n[1] * r
        for i in range(count):
            t = (i - (count - 1) / 2) * MB_PITCH
            holes.append((q6(mx + u[0] * t), q6(my + u[1] * t)))
    return sorted(holes)


# --- panel-only items ------------------------------------------------------------------------

def uid(*key):
    return str(uuid.uuid5(UUID_NS, "|".join(map(str, key))))


def edge_item(a, b):
    return (f'(gr_line\n\t\t(start {fmt(a[0])} {fmt(a[1])})\n\t\t(end {fmt(b[0])} {fmt(b[1])})\n'
            f'\t\t(stroke\n\t\t\t(width {EDGE_WIDTH})\n\t\t\t(type default)\n\t\t)\n'
            f'\t\t(layer "Edge.Cuts")\n\t\t(uuid "{uid("edge", *a, *b)}")\n\t)')


def mb_item(holes):
    def prop(name, value, layer):
        return (f'\t\t(property "{name}" "{value}"\n\t\t\t(at 0 0 0)\n\t\t\t(layer "{layer}")\n'
                f'\t\t\t(hide yes)\n\t\t\t(uuid "{uid("mb", name)}")\n\t\t\t(effects\n\t\t\t\t(font\n'
                f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n')
    d = fmt(Decimal(str(MB_DRILL)))
    pads = "".join(f'\t\t(pad "" np_thru_hole circle\n\t\t\t(at {fmt(x)} {fmt(y)})\n\t\t\t(size {d} {d})\n'
                   f'\t\t\t(drill {d})\n\t\t\t(layers "*.Mask")\n\t\t\t(uuid "{uid("mb", x, y)}")\n\t\t)\n'
                   for x, y in holes)
    return ('(footprint ""\n\t\t(layer "F.Cu")\n\t\t(uuid "%s")\n\t\t(at 0 0)\n' % uid("mb")
            + prop("Reference", "MB1", "F.SilkS") + prop("Value", "mousebites", "F.Fab")
            + prop("Datasheet", "", "F.Fab") + prop("Description", "", "F.Fab")
            + "\t\t(attr board_only exclude_from_pos_files exclude_from_bom)\n"
            + "\t\t(duplicate_pad_numbers_are_jumpers no)\n" + pads + "\t\t(embedded_fonts no)\n\t)")


def panel_dru(left_dru: str, mapping) -> str:
    def widen(m):
        side, ref = m.group(1), m.group(2)
        return (f"({side}.memberOfFootprint('{ref}') || "
                f"{side}.memberOfFootprint('{mapping.get(ref, right_ref(ref))}'))")
    body = re.sub(r"\b([AB])\.memberOfFootprint\('([^']+)'\)", widen, left_dru)
    lines = body.splitlines(keepends=True)
    head = "# generated by pcb/tools/panelize.py from atlas-left.kicad_dru (right-half refs = +100)\n"
    return lines[0] + head + "".join(lines[1:]) if lines and lines[0].startswith("(version") else head + body


# --- main ------------------------------------------------------------------------------------

def build(pcb_dir: Path, out: Path):
    lb, rb = Board(pcb_dir / "atlas-left.kicad_pcb"), Board(pcb_dir / "atlas-right.kicad_pcb")

    for key in ("layers", "setup", "general"):
        a, b = child(lb.tree, key), child(rb.tree, key)
        if (a is None) != (b is None) or (a is not None and lb.src(a) != rb.src(b)):
            print(f"warning: ({key}) differs between the halves; the panel uses the left's", file=sys.stderr)

    lrefs, rrefs = lb.refs(), rb.refs()
    mapping = {r: right_ref(r) for r in rrefs}
    clash = set(lrefs) & set(mapping.values())
    if clash or len(set(mapping.values())) != len(mapping):
        raise SystemExit(f"reference clash after renaming the right half: {sorted(clash)}")

    luu = set(re.findall(r'\(uuid "([^"]+)"\)', lb.text))
    uuid_map = {u: uid("right", u) for u in re.findall(r'\(uuid "([^"]+)"\)', rb.text) if u in luu}

    outline, joints = join(lb.edges(), rb.edges(DX))
    loops = check_closed(outline)
    holes = mouse_bites(joints)

    parts = ["(kicad_pcb"]
    for it in lb.items:
        if it[0] in HEADER:
            parts.append("\t" + lb.src(it))
    for it in lb.items:
        if it[0] not in HEADER and it[0] != "embedded_fonts" and not lb.is_edge(it):
            parts.append("\t" + lb.src(it))
    for it in rb.items:
        if it[0] not in HEADER and it[0] != "embedded_fonts" and not rb.is_edge(it):
            parts.append("\t" + right_item(rb, it, uuid_map))
    parts += ["\t" + edge_item(a, b) for a, b in outline]
    parts.append("\t" + mb_item(holes))
    ef = child(lb.tree, "embedded_fonts")
    if ef is not None:
        parts.append("\t" + lb.src(ef))
    parts.append(")\n")
    text = "\n".join(parts)
    parse(text)                                   # sanity: balanced

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    stem = out.with_suffix("")
    lpro, ldru = pcb_dir / "atlas-left.kicad_pro", pcb_dir / "atlas-left.kicad_dru"
    if lpro.exists():
        pro, name = lpro.read_text(), f'"filename": "{lpro.name}"'
        if name in pro:                                   # keep KiCad's own formatting
            pro = pro.replace(name, f'"filename": "{stem.name}.kicad_pro"', 1)
        else:
            d = json.loads(pro)
            d.setdefault("meta", {})["filename"] = stem.name + ".kicad_pro"
            pro = json.dumps(d, indent=2)
        stem.with_suffix(".kicad_pro").write_text(pro)
    if ldru.exists():
        stem.with_suffix(".kicad_dru").write_text(panel_dru(ldru.read_text(), mapping))

    xs = [float(p[0]) for s in outline for p in s]
    ys = [float(p[1]) for s in outline for p in s]
    print(f"{out}: {len(lrefs)}+{len(rrefs)} footprints, {len(outline)} edge lines in {loops} loops, "
          f"{len(holes)} mouse-bite holes, panel {max(xs) - min(xs):.2f} x {max(ys) - min(ys):.2f} mm")
    for side, p0, p1 in joints:
        print(f"  joint {side} ({fmt(p0[0])}, {fmt(p0[1])}) - ({fmt(p1[0])}, {fmt(p1[1])})")


def main():
    repo = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pcb-dir", default=str(repo / "pcb"), help="dir with atlas-{left,right}.kicad_pcb")
    ap.add_argument("--out", default=None, help="panel board (default: <pcb-dir>/atlas-panel.kicad_pcb)")
    a = ap.parse_args()
    pcb_dir = Path(a.pcb_dir)
    build(pcb_dir, Path(a.out) if a.out else pcb_dir / "atlas-panel.kicad_pcb")


if __name__ == "__main__":
    main()
