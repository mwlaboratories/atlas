"""Read what the case needs straight from a .kicad_pcb file: outline, footprints, pads.

No pcbnew dependency: KiCad's board file is an s-expression, parsed here directly,
so the case pipeline runs in its own Python (build123d) without KiCad's bindings.

Coordinates are returned in the CASE frame of that half (mm, y up), see `Frame`.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def parse_sexpr(text: str):
    stack, cur = [], []
    for m in _TOKEN.finditer(text):
        if m.group(1):
            stack.append(cur)
            cur = []
        elif m.group(2):
            done, cur = cur, stack.pop()
            cur.append(done)
        elif m.group(3) is not None:
            cur.append(Str(m.group(3)))
        elif m.group(4):
            cur.append(m.group(4))
    return cur[0]


class Str(str):
    """A quoted string token (kept apart from bare atoms)."""


def find(node, key):
    return next((c for c in node if isinstance(c, list) and c and c[0] == key), None)


def find_all(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def xy(node):
    return float(node[1]), float(node[2])


@dataclass(frozen=True)
class Frame:
    """KiCad board coordinates (mm, y down) -> case coordinates (mm, y up).

    The Fusion case files put each half at its own origin; measured from the
    screw holes in both: left x = X - 120, right x = X; both y = 120 - Y.
    """
    dx: float
    dy: float = 120.0

    def p(self, X: float, Y: float) -> tuple[float, float]:
        return X + self.dx, self.dy - Y

    def angle(self, kicad_deg: float) -> float:
        # KiCad angles are counter-clockwise on screen (y down); flipping y keeps them CCW.
        return kicad_deg


FRAMES = {"left": Frame(dx=-120.0), "right": Frame(dx=0.0)}


@dataclass
class Pad:
    number: str
    x: float          # case frame
    y: float
    size: tuple[float, float]
    drill: float | None
    net: str | None


@dataclass
class Footprint:
    ref: str
    lib: str          # e.g. "atlas20:kailh_choc_hotswap_CPG135001S30"
    value: str
    side: str         # "F" or "B"
    x: float          # case frame
    y: float
    rot: float        # degrees, CCW, case frame
    pads: list[Pad] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.lib.split(":")[-1]


@dataclass
class Board:
    half: str
    outline: list[tuple[float, float]]           # closed polygon, case frame, CCW
    footprints: list[Footprint]
    thickness: float

    def by_ref(self, ref: str) -> Footprint:
        return next(f for f in self.footprints if f.ref == ref)

    def of_kind(self, name_part: str) -> list[Footprint]:
        return [f for f in self.footprints if name_part in f.lib]


def _chain(segments, tol=0.01):
    """Order loose outline segments into one closed loop (KiCad leaves tiny gaps)."""
    segs = list(segments)
    loop = list(segs.pop(0))
    while segs:
        end = loop[-1]
        i, rev = next(((i, False) for i, s in enumerate(segs) if math.dist(s[0], end) < tol),
                      next(((i, True) for i, s in enumerate(segs) if math.dist(s[1], end) < tol), (None, None)))
        if i is None:
            raise ValueError(f"outline is not closed near {end}")
        s = segs.pop(i)
        loop.append(s[0] if rev else s[1])
    if math.dist(loop[0], loop[-1]) < tol:
        loop.pop()
    return loop


def _area(poly):
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]))


def load_board(path: str | Path, half: str) -> Board:
    fr = FRAMES[half]
    root = parse_sexpr(Path(path).read_text())
    thickness = float(find(find(root, "general"), "thickness")[1])

    segs = []
    for g in find_all(root, "gr_line"):
        if find(g, "layer")[1] == "Edge.Cuts":
            segs.append((fr.p(*xy(find(g, "start"))), fr.p(*xy(find(g, "end")))))
    if any(find(g, "layer")[1] == "Edge.Cuts" for k in ("gr_arc", "gr_circle", "gr_rect", "gr_poly") for g in find_all(root, k)):
        raise NotImplementedError("board outline uses arcs/rects/polys; extend kicad.load_board")
    outline = _chain(segs)
    if _area(outline) < 0:
        outline.reverse()

    fps = []
    for f in find_all(root, "footprint"):
        at = find(f, "at")
        X, Y = float(at[1]), float(at[2])
        rot = float(at[3]) if len(at) > 3 else 0.0
        props = {p[1]: p[2] for p in find_all(f, "property")}
        x, y = fr.p(X, Y)
        fp = Footprint(ref=str(props.get("Reference", "")), lib=str(f[1]), value=str(props.get("Value", "")),
                       side="F" if find(f, "layer")[1] == "F.Cu" else "B", x=x, y=y, rot=fr.angle(rot))
        a = math.radians(rot)
        for pd in find_all(f, "pad"):
            pat = find(pd, "at")
            px, py = float(pat[1]), float(pat[2])
            # pad position in board coords: KiCad rotates footprint-local points by -rot (y down)
            PX = X + px * math.cos(a) + py * math.sin(a)
            PY = Y - px * math.sin(a) + py * math.cos(a)
            dr = find(pd, "drill")
            net = find(pd, "net")
            size = find(pd, "size")
            fp.pads.append(Pad(number=str(pd[1]), x=fr.p(PX, PY)[0], y=fr.p(PX, PY)[1],
                               size=(float(size[1]), float(size[2])) if size else (0, 0),
                               drill=float(dr[1]) if dr and len(dr) > 1 and dr[1] != "oval" else None,
                               net=str(net[2]) if net and len(net) > 2 else (str(net[1]) if net and len(net) > 1 else None)))
        fps.append(fp)
    return Board(half=half, outline=outline, footprints=fps, thickness=thickness)


# The right case parts are the left ones mirrored about x = MIRROR_X / 2 (case frame).
MIRROR_X = 72.0


def mirrored(board: Board, axis2: float = MIRROR_X) -> Board:
    """The board mirrored x -> axis2 - x, so a right half can be built with left-half code."""
    def mp(x, y):
        return axis2 - x, y
    outline = [mp(x, y) for x, y in reversed(board.outline)]
    fps = []
    for f in board.footprints:
        x, y = mp(f.x, f.y)
        g = Footprint(ref=f.ref, lib=f.lib, value=f.value, side=f.side, x=x, y=y, rot=(-f.rot) % 360)
        g.pads = [Pad(p.number, *mp(p.x, p.y), p.size, p.drill, p.net) for p in f.pads]
        fps.append(g)
    return Board(half=board.half, outline=outline, footprints=fps, thickness=board.thickness)
