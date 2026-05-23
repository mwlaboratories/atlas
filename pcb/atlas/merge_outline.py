#!/usr/bin/env python3
"""Merge an SVG outline path into a KiCad .kicad_pcb file's Edge.Cuts layer.

Re-runnable: removes any existing Edge.Cuts gr_line/gr_arc/gr_poly/gr_rect/
gr_circle blocks first, then inserts new gr_line segments parsed from the SVG.

The SVG path must use only M / L / Z commands (no arcs). The keyboard-tools.xyz
plate export already gives us a polyline outline, so this is the common case.

Usage:
    python merge_outline.py [outline.svg] [keyboard.kicad_pcb]

Defaults to the files alongside this script.
"""
import re
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent


def parse_outline(svg_text: str) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Return list of (start, end) line segments from the outline path."""
    # Prefer a path with id="outline"; fall back to the last <path>.
    m = re.search(r'<path[^>]*\bid="outline"[^>]*\bd="([^"]+)"', svg_text)
    if not m:
        m = re.search(r'<path[^>]*\bd="([^"]+)"[^>]*\bid="outline"', svg_text)
    if not m:
        paths = re.findall(r'<path[^>]*\bd="([^"]+)"', svg_text)
        if not paths:
            raise SystemExit("no <path> found in SVG")
        d = paths[-1]
    else:
        d = m.group(1)

    tokens = re.findall(r"[A-Za-z]|-?\d+(?:\.\d+)?", d)
    segments = []
    i, cur, start = 0, None, None
    while i < len(tokens):
        cmd = tokens[i]
        if cmd in ("M", "L"):
            x = float(tokens[i + 1])
            y = float(tokens[i + 2])
            if cmd == "M":
                start = cur = (x, y)
            else:
                segments.append((cur, (x, y)))
                cur = (x, y)
            i += 3
        elif cmd in ("Z", "z"):
            if cur != start and cur is not None and start is not None:
                segments.append((cur, start))
            cur = start
            i += 1
        else:
            # Any other command means the script's assumptions don't hold —
            # bail loudly rather than silently produce a wrong outline.
            raise SystemExit(
                f"unsupported SVG path command {cmd!r} at token {i}; "
                f"only M/L/Z are handled"
            )
    return segments


def make_gr_line(start, end) -> str:
    sx, sy = start
    ex, ey = end
    return (
        f'\t(gr_line\n'
        f'\t\t(start {sx} {sy})\n'
        f'\t\t(end {ex} {ey})\n'
        f'\t\t(stroke\n'
        f'\t\t\t(width 0.05)\n'
        f'\t\t\t(type solid)\n'
        f'\t\t)\n'
        f'\t\t(layer "Edge.Cuts")\n'
        f'\t\t(uuid "{uuid.uuid4()}")\n'
        f'\t)\n'
    )


def strip_edge_cuts(pcb: str) -> str:
    """Drop top-level gr_line/arc/poly blocks on Edge.Cuts (the outline).

    Deliberately spares gr_circle so add_trackpoint_holes.py's drill-out
    circles survive a re-run of merge_outline.
    """
    pattern = re.compile(r"^\t\(gr_(?:line|arc|poly)\b", re.MULTILINE)
    out, i = [], 0
    while i < len(pcb):
        m = pattern.search(pcb, i)
        if not m:
            out.append(pcb[i:])
            break
        out.append(pcb[i:m.start()])
        # walk balanced parens
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
    return "".join(out)


def pcb_switch_centers(pcb_text: str) -> list[tuple[float, float]]:
    """Pull (at x y ...) from every SW_* footprint block."""
    positions, in_sw = [], False
    for line in pcb_text.splitlines():
        s = line.lstrip()
        if s.startswith('(footprint "SW_'):
            in_sw = True
        elif in_sw and s.startswith("(at "):
            m = re.match(r"\(at ([-0-9.]+) ([-0-9.]+)", s)
            if m:
                positions.append((float(m.group(1)), float(m.group(2))))
            in_sw = False
    return positions


def auto_offset(segments, pcb_text: str, body_margin: float = 10.0) -> tuple[float, float]:
    """Compute SVG→PCB offset by aligning outline-edge corners.

    keyboard-tools.xyz's "Tight" outline traces around each switch's body
    (14mm for Choc V1/V2, 15.6mm for MX) plus the user-chosen plate margin.
    For Choc V2 with the typical 3mm margin, the outline extends 10mm from
    each switch center (7mm body-half + 3mm). That's `body_margin`.

    SVG (0, 0) sits at the top-left of the outline's bbox, which equals
    (min_switch_x − body_margin, min_switch_y − body_margin). Align those.
    """
    sxs = [p for s, e in segments for p in (s[0], e[0])]
    sys_ = [p for s, e in segments for p in (s[1], e[1])]
    svg_min_x = min(sxs)
    svg_min_y = min(sys_)

    sw = pcb_switch_centers(pcb_text)
    if not sw:
        raise SystemExit("no SW_* footprints in PCB — cannot auto-align")
    pcb_min_x = min(p[0] for p in sw) - body_margin
    pcb_min_y = min(p[1] for p in sw) - body_margin

    return pcb_min_x - svg_min_x, pcb_min_y - svg_min_y


def merge(svg_path: Path, pcb_path: Path,
          dx: float | None = None, dy: float | None = None) -> None:
    svg_text = svg_path.read_text()
    pcb_text = pcb_path.read_text()

    segments = parse_outline(svg_text)

    if dx is None or dy is None:
        adx, ady = auto_offset(segments, pcb_text)
        if dx is None: dx = adx
        if dy is None: dy = ady
    print(f"applying offset ({dx:+.3f}, {dy:+.3f}) mm to SVG → PCB")

    segments = [((s[0] + dx, s[1] + dy), (e[0] + dx, e[1] + dy))
                for s, e in segments]

    pcb_text = strip_edge_cuts(pcb_text)
    new_blocks = "".join(make_gr_line(s, e) for s, e in segments)
    last = pcb_text.rfind(")")
    pcb_text = pcb_text[:last] + new_blocks + pcb_text[last:]

    pcb_path.write_text(pcb_text)
    print(f"wrote {len(segments)} Edge.Cuts segments → {pcb_path.name}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("svg", nargs="?", default=str(HERE / "keyboard-outline.svg"))
    ap.add_argument("pcb", nargs="?", default=str(HERE / "keyboard.kicad_pcb"))
    ap.add_argument("--dx", type=float, default=None,
                    help="x offset in mm (added to auto-detected). default: 0")
    ap.add_argument("--dy", type=float, default=None,
                    help="y offset in mm (added to auto-detected). default: 0")
    ap.add_argument("--abs-dx", type=float, default=None,
                    help="absolute x offset (mm). overrides auto-detect.")
    ap.add_argument("--abs-dy", type=float, default=None,
                    help="absolute y offset (mm). overrides auto-detect.")
    args = ap.parse_args()

    svg_path = Path(args.svg)
    pcb_path = Path(args.pcb)
    svg_text = svg_path.read_text()
    pcb_text = pcb_path.read_text()
    segments = parse_outline(svg_text)

    adx, ady = auto_offset(segments, pcb_text)
    dx = args.abs_dx if args.abs_dx is not None else (adx + (args.dx or 0))
    dy = args.abs_dy if args.abs_dy is not None else (ady + (args.dy or 0))

    merge(svg_path, pcb_path, dx, dy)
