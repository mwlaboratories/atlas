#!/usr/bin/env python3
"""Add trackpoint hole + screw holes to Edge.Cuts on each half.

The trackpoint sits at the centroid of 4 keys: cols 3-4 (index, inner) ×
rows 0-1 (top, middle). On each half a circle is added for the stick
(4 mm dia) and two more for M2 screws at ±9.5 mm Y from center.

Idempotent: re-running strips existing Edge.Cuts gr_circle blocks first.
merge_outline.py is configured to leave these alone when it re-paints
the outline, so the order of operations is:

    1. add_leds.py
    2. merge_outline.py
    3. add_trackpoint_holes.py     ← run last, survives re-paints

Usage:
    python add_trackpoint_holes.py [keyboard.kicad_pcb]
"""
import re
import sys
import uuid as uuid_lib
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Trackpoint config — matches the atlas preset in tools/layout/index.html.
STICK_DIA       = 4.0
SCREW_DIA       = 2.2
SCREW_OFFSET_Y  = 9.5     # mm from center
# Cols are indexed left-to-right within each half. The right half is mirrored,
# so its index+inner cols sit on the LEFT of the half (indices 0, 1) while the
# left half's are on the RIGHT (indices 3, 4).
LEFT_COLS       = (3, 4)
RIGHT_COLS      = (0, 1)
ROWS            = (0, 1)  # rows flanking it (top, middle) — same on both halves


def parse_switches(pcb: str) -> list[tuple[float, float]]:
    """Pull (x, y) from every SW_* footprint's first (at ...) line."""
    out, in_sw = [], False
    for line in pcb.splitlines():
        s = line.lstrip()
        if s.startswith('(footprint "SW_'):
            in_sw = True
        elif in_sw and s.startswith("(at "):
            m = re.match(r"\(at ([-0-9.]+) ([-0-9.]+)", s)
            if m:
                out.append((float(m.group(1)), float(m.group(2))))
            in_sw = False
    return out


def split_halves(positions: list) -> tuple[list, list]:
    """Split switches into left/right by finding the largest x gap."""
    xs = sorted({round(p[0], 1) for p in positions})
    if len(xs) < 2:
        return positions, []
    gaps = [(xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)]
    _, idx = max(gaps)
    split_x = (xs[idx] + xs[idx + 1]) / 2
    left  = [p for p in positions if p[0] < split_x]
    right = [p for p in positions if p[0] >= split_x]
    return left, right


def trackpoint_centroid(half: list, cols, rows=ROWS) -> tuple[float, float] | None:
    """Centroid of switches at the intersection of the given cols × rows.

    Filters out thumb clusters (cols with <3 switches) so the col indices
    correctly refer to the 5 ortho cols on each half.
    """
    if not half:
        return None
    # Cluster switches into columns by x position (5 mm threshold groups
    # same-col keys even with column splay).
    half_sorted = sorted(half, key=lambda p: p[0])
    clusters: list[list] = []  # each entry: list of switches in that cluster
    for p in half_sorted:
        if clusters and abs(p[0] - clusters[-1][-1][0]) <= 5:
            clusters[-1].append(p)
        else:
            clusters.append([p])

    # Keep only ortho cols (≥ 3 switches per col). Thumbs typically have 1-2.
    ortho_cols = [c for c in clusters if len(c) >= 3]
    if len(ortho_cols) < max(cols) + 1:
        return None

    target = []
    for c in cols:
        col_sw = sorted(ortho_cols[c], key=lambda p: p[1])
        for r in rows:
            if r < len(col_sw):
                target.append(col_sw[r])
    if not target:
        return None
    return (sum(p[0] for p in target) / len(target),
            sum(p[1] for p in target) / len(target))


def gr_circle(cx: float, cy: float, dia: float) -> str:
    r = dia / 2
    return (
        f'\t(gr_circle\n'
        f'\t\t(center {cx} {cy})\n'
        f'\t\t(end {cx + r} {cy})\n'
        f'\t\t(stroke\n\t\t\t(width 0.05)\n\t\t\t(type solid)\n\t\t)\n'
        f'\t\t(fill no)\n'
        f'\t\t(layer "Edge.Cuts")\n'
        f'\t\t(uuid "{uuid_lib.uuid4()}")\n'
        f'\t)\n'
    )


def strip_existing_circles(pcb: str) -> str:
    """Drop all top-level gr_circle on Edge.Cuts (these are our drill-outs)."""
    pat = re.compile(r"^\t\(gr_circle\b", re.MULTILINE)
    out, i = [], 0
    while True:
        m = pat.search(pcb, i)
        if not m:
            out.append(pcb[i:])
            return "".join(out)
        out.append(pcb[i:m.start()])
        depth, j = 0, m.start()
        while j < len(pcb):
            c = pcb[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        block = pcb[m.start():j]
        if '(layer "Edge.Cuts")' in block:
            i = j + (1 if j < len(pcb) and pcb[j] == "\n" else 0)
        else:
            out.append(block)
            i = j


def add_trackpoint_holes(pcb_path: Path) -> None:
    pcb = pcb_path.read_text()
    pcb = strip_existing_circles(pcb)

    positions = parse_switches(pcb)
    if not positions:
        raise SystemExit("no SW_* footprints — nothing to anchor trackpoint to")

    left, right = split_halves(positions)

    blocks = []
    for label, half, cols in [("left", left, LEFT_COLS),
                              ("right", right, RIGHT_COLS)]:
        centroid = trackpoint_centroid(half, cols)
        if centroid is None:
            print(f"  skipped {label} half (couldn't locate cols/rows)")
            continue
        cx, cy = centroid
        print(f"  {label} trackpoint center at ({cx:.2f}, {cy:.2f}) mm")
        blocks.append(gr_circle(cx, cy, STICK_DIA))
        blocks.append(gr_circle(cx, cy - SCREW_OFFSET_Y, SCREW_DIA))
        blocks.append(gr_circle(cx, cy + SCREW_OFFSET_Y, SCREW_DIA))

    if blocks:
        last = pcb.rfind(")")
        pcb = pcb[:last] + "".join(blocks) + pcb[last:]

    pcb_path.write_text(pcb)
    print(f"added {len(blocks)} gr_circle(s) on Edge.Cuts → {pcb_path.name}")


if __name__ == "__main__":
    pcb = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "keyboard.kicad_pcb"
    add_trackpoint_holes(pcb)
