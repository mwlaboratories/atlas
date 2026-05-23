#!/usr/bin/env python3
"""Mirror left-half work (Edge.Cuts + non-switch/non-LED footprints) to right.

Useful after you manually edit the outline on one side and add controller /
power / signal-conditioning footprints. Switches and SK6812 LEDs are already
placed on both halves by kbplacer + add_leds.py, so they're skipped.

Idempotent: strips right-half Edge.Cuts gr_lines and right-half "other"
footprints first, then mirrors the left-half versions across the centerline.

Mirror math: x' = 2·center − x, y' = y, rotation' = (180 − rotation) mod 360.
Layer stays unchanged — flip in KiCad afterwards (key shortcut F) if the
right half needs to be on B.Cu for your PCB-cutting plan.

Usage:
    python mirror_to_right.py [keyboard.kicad_pcb]
"""
import re
import sys
import uuid as uuid_lib
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Footprint prefixes that already exist on BOTH halves (don't mirror).
# Diodes are NOT skipped — they need to mirror, otherwise user-moved left-half
# diodes get out of sync with the right-half kbplacer-placed ones.
SKIP_PREFIXES = (
    "SW_",                              # switches (kbplacer)
    "LED_SMD:LED_SK6812MINI-E",         # LEDs (add_leds.py)
)


def find_blocks(pcb: str, regex_prefix: str) -> list[tuple[int, int]]:
    """Find every top-level (...) block whose first chars match regex_prefix.

    Matches `\\t(prefix` anywhere — *not* anchored to line start — because
    earlier buggy inserts produced blocks immediately after `\\t)` with no
    newline. Without that lenience we couldn't strip our own past mistakes.
    """
    out, i = [], 0
    pat = re.compile(rf'\t\({regex_prefix}')
    while True:
        m = pat.search(pcb, i)
        if not m:
            return out
        start = m.start()
        depth, j = 0, start
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
        out.append((start, j))
        i = j


def parse_switch_centers(pcb: str) -> list[tuple[float, float]]:
    centers = []
    for start, end in find_blocks(pcb, 'footprint "SW_'):
        m = re.search(r"^\t\t\(at ([-0-9.]+) ([-0-9.]+)", pcb[start:end], re.MULTILINE)
        if m:
            centers.append((float(m.group(1)), float(m.group(2))))
    return centers


def compute_centerline(pcb: str) -> float:
    sw = parse_switch_centers(pcb)
    if not sw:
        raise SystemExit("no SW_* footprints — can't determine centerline")
    xs = [p[0] for p in sw]
    return (min(xs) + max(xs)) / 2


# ─── Edge.Cuts gr_line mirroring ───────────────────────────────────────────

LINE_RE = re.compile(
    r'^\t\(gr_line\s*\n'
    r'\t\t\(start ([-0-9.]+) ([-0-9.]+)\)\s*\n'
    r'\t\t\(end ([-0-9.]+) ([-0-9.]+)\)\s*\n',
    re.MULTILINE,
)


def parse_gr_line_endpoints(block: str) -> tuple[float, float, float, float] | None:
    m = LINE_RE.match(block)
    if not m:
        return None
    return tuple(float(g) for g in m.groups())


def make_mirrored_gr_line(block: str, center: float) -> str:
    """Take a gr_line block, return a new block with x coords mirrored."""
    pts = parse_gr_line_endpoints(block)
    if pts is None:
        return block
    sx, sy, ex, ey = pts
    new_sx = 2 * center - sx
    new_ex = 2 * center - ex
    # Replace start/end coords and the uuid.
    block = re.sub(r'(\(start )[-0-9.]+( [-0-9.]+\))',
                   rf'\g<1>{new_sx}\g<2>', block, count=1)
    block = re.sub(r'(\(end )[-0-9.]+( [-0-9.]+\))',
                   rf'\g<1>{new_ex}\g<2>', block, count=1)
    block = re.sub(r'\(uuid "[^"]+"\)',
                   f'(uuid "{uuid_lib.uuid4()}")', block, count=1)
    return block


def mirror_edge_cuts(pcb: str, center: float) -> str:
    """Strip right-half Edge.Cuts gr_lines, then copy mirrored left-half lines."""
    # 1) Collect all Edge.Cuts gr_line blocks and split by side.
    left_blocks: list[str] = []
    right_ranges: list[tuple[int, int]] = []
    for start, end in find_blocks(pcb, 'gr_line'):
        block = pcb[start:end]
        if '(layer "Edge.Cuts")' not in block:
            continue
        pts = parse_gr_line_endpoints(block)
        if pts is None:
            continue
        sx, _, ex, _ = pts
        mid = (sx + ex) / 2
        if mid > center:
            right_ranges.append((start, end))
        else:
            left_blocks.append(block)

    # 2) Strip right-half ranges (reverse order so indices stay valid).
    for start, end in reversed(right_ranges):
        nl = 1 if end < len(pcb) and pcb[end] == "\n" else 0
        pcb = pcb[:start] + pcb[end + nl:]

    # 3) Generate mirrored copies of left-half blocks, insert before final ).
    if left_blocks:
        # Join with newlines so each block sits on its own line (and so the
        # script can find its own output next time).
        mirrored = "\n".join(make_mirrored_gr_line(b, center) for b in left_blocks) + "\n"
        last = pcb.rfind(")")
        # Ensure a newline before the inserted section too.
        sep = "" if pcb[:last].endswith("\n") else "\n"
        pcb = pcb[:last] + sep + mirrored + pcb[last:]

    return pcb


# ─── footprint mirroring ───────────────────────────────────────────────────

def block_first_at(block: str) -> tuple[float, float, float] | None:
    """First (at x y [rot]) inside a footprint block (the placement)."""
    m = re.search(r"^\t\t\(at ([-0-9.]+) ([-0-9.]+)(?: ([-0-9.]+))?",
                  block, re.MULTILINE)
    if not m:
        return None
    return (float(m.group(1)), float(m.group(2)), float(m.group(3) or 0))


def block_ref(block: str) -> str | None:
    m = re.search(r'\(property "Reference" "([^"]+)"', block)
    return m.group(1) if m else None


def block_fp_name(block: str) -> str:
    m = re.search(r'\(footprint "([^"]+)"', block)
    return m.group(1) if m else ""


def is_skippable(block: str) -> bool:
    name = block_fp_name(block)
    return any(name.startswith(p) or p in name for p in SKIP_PREFIXES)


def mirror_footprint_block(block: str, center: float) -> str:
    """Mirror a footprint block's placement across the vertical line at `center`."""
    place = block_first_at(block)
    if place is None:
        return block
    x, y, rot = place
    new_x = 2 * center - x
    new_y = y
    new_rot = (180 - rot) % 360

    # Replace the first (at ...) line. Use a marker by line index.
    def repl_at(m):
        return f"(at {new_x} {new_y} {new_rot:g})"

    # Only replace the FIRST (at) at footprint root depth (tab-tab indent).
    block = re.sub(
        r'(^\t\t)\(at [-0-9.]+ [-0-9.]+(?: [-0-9.]+)?\)',
        rf'\g<1>(at {new_x} {new_y} {new_rot:g})',
        block, count=1, flags=re.MULTILINE,
    )

    # Replace UUIDs (footprint + property + pad uuids). Generate new ones so
    # the mirrored instance doesn't clash with the original.
    def new_uuid(_m):
        return f'(uuid "{uuid_lib.uuid4()}")'
    block = re.sub(r'\(uuid "[^"]+"\)', new_uuid, block)

    # Bump the reference designator: e.g. "U1" → "U1_R". Crude but unique.
    ref = block_ref(block)
    if ref:
        new_ref = f"{ref}_R"
        block = block.replace(f'"Reference" "{ref}"', f'"Reference" "{new_ref}"', 1)

    return block


def mirror_footprints(pcb: str, center: float) -> str:
    """Strip right-half non-skip footprints, then mirror left-half copies."""
    left_blocks: list[str] = []
    right_ranges: list[tuple[int, int]] = []
    for start, end in find_blocks(pcb, 'footprint '):
        block = pcb[start:end]
        if is_skippable(block):
            continue
        place = block_first_at(block)
        if place is None:
            continue
        x, _, _ = place
        if x > center:
            right_ranges.append((start, end))
        else:
            left_blocks.append(block)

    for start, end in reversed(right_ranges):
        nl = 1 if end < len(pcb) and pcb[end] == "\n" else 0
        pcb = pcb[:start] + pcb[end + nl:]

    if left_blocks:
        mirrored = "\n".join(mirror_footprint_block(b, center) for b in left_blocks) + "\n"
        last = pcb.rfind(")")
        sep = "" if pcb[:last].endswith("\n") else "\n"
        pcb = pcb[:last] + sep + mirrored + pcb[last:]

    return pcb, len(left_blocks), len(right_ranges)


# ─── entry point ───────────────────────────────────────────────────────────

def main(pcb_path: Path) -> None:
    pcb = pcb_path.read_text()
    center = compute_centerline(pcb)
    print(f"centerline at x = {center:.2f} mm")

    pcb = mirror_edge_cuts(pcb, center)
    print("mirrored Edge.Cuts gr_lines (right-half stripped, left-half copied)")

    pcb, n_mirrored, n_stripped = mirror_footprints(pcb, center)
    print(f"mirrored {n_mirrored} footprint(s) (stripped {n_stripped} existing on right)")

    pcb_path.write_text(pcb)
    print(f"→ {pcb_path.name}")


if __name__ == "__main__":
    pcb = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "keyboard.kicad_pcb"
    main(pcb)
