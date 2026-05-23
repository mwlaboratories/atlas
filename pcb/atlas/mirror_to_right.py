#!/usr/bin/env python3
"""Mirror left-half work (Edge.Cuts + non-switch/non-LED footprints) to right.

Useful after you manually edit the outline on one side and add controller /
power / signal-conditioning footprints. Switches and SK6812 LEDs are already
placed on both halves by kbplacer + add_leds.py, so they're skipped.

Three passes:

 1. **Centerline mirror** for Edge.Cuts and "free" footprints (MCU, regulators,
    connectors, etc.) that the user placed by hand on the left half:
        x' = 2·center − x, y' = y, rotation' = (180 − rotation) mod 360.
    Layer stays unchanged.

 2. **Switch-local re-pair** for diodes (and any other switch-paired part):
    each left diode is associated with its nearest left switch, its
    switch-local pose is extracted, and a copy is placed at the same local
    pose around the paired right switch. This is required because diodes on
    both halves often want to live on the SAME side of their switch in the
    switch's local frame (e.g. "to the right of the switch") — a centerline
    mirror would flip that to the wrong side.

 3. **Net-split** for the right half. Atlas is a wireless split — each half
    has its own controller and its own matrix, so they MUST NOT share nets.
    kbplacer ships a single shared matrix; this pass renames the right-half
    matrix nets:
      - `ROW{n}` → `R_ROW{n}`     (kbplacer already splits COL{n} per half)
      - `Net-(D{n}-A)` → `Net-(D{n}_R-A)` on every right-side diode
      - on every right-side switch, the dangling anode-net reference (which
        still points at a now-deleted kbplacer diode) is rewritten to its
        physically-paired right-half diode's new anode net

Idempotent — re-running strips the right-half versions of the things it
just mirrored, then re-creates them.

Usage:
    python mirror_to_right.py [keyboard.kicad_pcb]
"""
import math
import re
import sys
import uuid as uuid_lib
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Footprint name fragments handled by the *centerline mirror* pass.
# Things in SKIP_PREFIXES are left alone there; diodes are skipped here too
# because they get their own pairing pass (see PAIRED_PREFIXES below).
SKIP_PREFIXES = (
    "SW_",                              # switches (kbplacer, both halves)
    "LED_SMD:LED_SK6812MINI-E",         # LEDs (add_leds.py, both halves)
    "Diode_SMD:",                       # diodes — handled by pair-pass
    "D_SOD-323",                        # diodes — handled by pair-pass
)

# Footprint name fragments handled by the *switch-local re-pair* pass.
# These get stripped from the right half and re-placed using the left half's
# switch-local pose.
PAIRED_PREFIXES = (
    "Diode_SMD:",
    "D_SOD-323",
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


# ─── switch-local pairing pass (for diodes) ────────────────────────────────

def parse_all_switches(pcb: str) -> list[dict]:
    """All SW_* footprints with placement."""
    out = []
    for s, e in find_blocks(pcb, 'footprint "SW_'):
        block = pcb[s:e]
        p = block_first_at(block)
        if p is None:
            continue
        out.append({"x": p[0], "y": p[1], "rot": p[2], "ref": block_ref(block)})
    return out


def _is_paired(block: str) -> bool:
    name = block_fp_name(block)
    return any(p in name for p in PAIRED_PREFIXES)


def _rot_global_to_local(dx: float, dy: float, rot_deg: float) -> tuple[float, float]:
    """Express a global-frame offset in the switch's local frame.

    KiCad rotation is CCW from F.Cu (top); with +Y pointing DOWN that's math
    CW. Going global → local means rotating by −rot.
    """
    a = -math.radians(rot_deg)
    # math-CW rotation matrix for +Y-down PCB coords:
    lx =  dx * math.cos(a) + dy * math.sin(a)
    ly = -dx * math.sin(a) + dy * math.cos(a)
    return lx, ly


def _rot_local_to_global(lx: float, ly: float, rot_deg: float) -> tuple[float, float]:
    """Inverse: local → global."""
    a = math.radians(rot_deg)
    dx =  lx * math.cos(a) + ly * math.sin(a)
    dy = -lx * math.sin(a) + ly * math.cos(a)
    return dx, dy


def pair_diodes(pcb: str, center: float) -> tuple[str, int, int]:
    """Strip right-half PAIRED_PREFIXES footprints, then re-place from left.

    Each left diode is anchored to its nearest left switch (in global coords),
    its switch-local pose is extracted, and a copy is placed at the same
    local pose relative to the corresponding right-half switch (the one
    whose position mirrors the left switch's across the centerline).
    """
    switches = parse_all_switches(pcb)
    left_sw  = [s for s in switches if s["x"] < center]
    right_sw = [s for s in switches if s["x"] >= center]
    if not left_sw or not right_sw:
        return pcb, 0, 0

    # Pair: for each left switch, find right switch closest to its mirror.
    pairs: dict[str, dict] = {}
    for ls in left_sw:
        target_x = 2 * center - ls["x"]
        target_y = ls["y"]
        rs = min(right_sw, key=lambda r: (r["x"]-target_x)**2 + (r["y"]-target_y)**2)
        pairs[ls["ref"]] = rs

    # Walk all paired-prefix footprint blocks; classify by half.
    left_diodes: list[dict] = []
    right_ranges: list[tuple[int, int]] = []
    for s, e in find_blocks(pcb, 'footprint "'):
        block = pcb[s:e]
        if not _is_paired(block):
            continue
        place = block_first_at(block)
        if place is None:
            continue
        x, y, rot = place
        if x >= center:
            right_ranges.append((s, e))
            continue
        # Left diode → find parent switch + extract switch-local pose
        parent = min(left_sw, key=lambda sw: (sw["x"]-x)**2 + (sw["y"]-y)**2)
        local_x, local_y = _rot_global_to_local(x - parent["x"], y - parent["y"], parent["rot"])
        rot_delta = (rot - parent["rot"]) % 360
        left_diodes.append({
            "block": block,
            "parent_ref": parent["ref"],
            "local_x": local_x,
            "local_y": local_y,
            "rot_delta": rot_delta,
        })

    # Strip right-half paired footprints first (reverse order keeps offsets valid).
    for s, e in reversed(right_ranges):
        nl = 1 if e < len(pcb) and pcb[e] == "\n" else 0
        pcb = pcb[:s] + pcb[e + nl:]

    # Build new right-half blocks.
    new_blocks: list[str] = []
    for ld in left_diodes:
        rs = pairs.get(ld["parent_ref"])
        if rs is None:
            continue
        dx, dy = _rot_local_to_global(ld["local_x"], ld["local_y"], rs["rot"])
        new_x = rs["x"] + dx
        new_y = rs["y"] + dy
        new_rot = (rs["rot"] + ld["rot_delta"]) % 360

        block = ld["block"]
        block = re.sub(
            r'(^\t\t)\(at [-0-9.]+ [-0-9.]+(?: [-0-9.]+)?\)',
            rf'\g<1>(at {new_x:.4f} {new_y:.4f} {new_rot:g})',
            block, count=1, flags=re.MULTILINE,
        )
        block = re.sub(r'\(uuid "[^"]+"\)',
                       lambda _: f'(uuid "{uuid_lib.uuid4()}")', block)
        ref = block_ref(block)
        if ref:
            block = block.replace(f'"Reference" "{ref}"',
                                  f'"Reference" "{ref}_R"', 1)
        new_blocks.append(block)

    if new_blocks:
        mirrored = "\n".join(new_blocks) + "\n"
        last = pcb.rfind(")")
        sep = "" if pcb[:last].endswith("\n") else "\n"
        pcb = pcb[:last] + sep + mirrored + pcb[last:]

    return pcb, len(new_blocks), len(right_ranges)


# ─── net-split pass for the right half (wireless split, no shared nets) ────

def repair_right_half_nets(pcb: str, center: float) -> tuple[str, int, int]:
    """Rename right-half ROW nets + re-wire each right switch to its diode.

    Returns (pcb, n_row_renames, n_switch_rewires).
    """
    # 1. Collect right-half diode positions + refs (post pair_diodes).
    right_diodes = []
    for s, e in find_blocks(pcb, 'footprint "'):
        block = pcb[s:e]
        if not _is_paired(block):
            continue
        place = block_first_at(block)
        if place is None or place[0] < center:
            continue
        right_diodes.append({"ref": block_ref(block), "x": place[0], "y": place[1]})

    if not right_diodes:
        return pcb, 0, 0

    # 2. Pair each right switch with its physically-closest right diode.
    sw_to_diode: dict[str, str] = {}
    for sw in parse_all_switches(pcb):
        if sw["x"] < center:
            continue
        d = min(right_diodes, key=lambda d: (d["x"]-sw["x"])**2 + (d["y"]-sw["y"])**2)
        sw_to_diode[sw["ref"]] = d["ref"]

    # 3. Walk all right-half footprints in REVERSE so splices don't invalidate
    #    earlier indices. Rewrite ROW + anode nets.
    blocks = []
    for s, e in find_blocks(pcb, 'footprint "'):
        block = pcb[s:e]
        place = block_first_at(block)
        if place is None or place[0] < center:
            continue
        blocks.append((s, e, block))
    blocks.sort(key=lambda x: x[0], reverse=True)

    n_row = n_sw = 0
    for start, end, block in blocks:
        ref = block_ref(block)
        name = block_fp_name(block)

        new_block, n = re.subn(r'\(net "ROW(\d+)"\)', r'(net "R_ROW\1")', block)
        n_row += n
        block = new_block

        if "Diode" in name or "D_SOD" in name:
            # Diode: anode pad should reference its OWN refdes, not the left
            # half's. (We're at the right half here, so ref ends in "_R".)
            block = re.sub(
                r'\(net "Net-\(D[^)]+-A\)"\)',
                f'(net "Net-({ref}-A)")',
                block,
            )
        elif name.startswith("SW_") or "SW_Hotswap" in name:
            paired = sw_to_diode.get(ref)
            if paired:
                block, n = re.subn(
                    r'\(net "Net-\(D[^)]+-A\)"\)',
                    f'(net "Net-({paired}-A)")',
                    block,
                )
                if n:
                    n_sw += 1

        pcb = pcb[:start] + block + pcb[end:]

    return pcb, n_row, n_sw


# ─── entry point ───────────────────────────────────────────────────────────

def main(pcb_path: Path) -> None:
    pcb = pcb_path.read_text()
    center = compute_centerline(pcb)
    print(f"centerline at x = {center:.2f} mm")

    pcb = mirror_edge_cuts(pcb, center)
    print("mirrored Edge.Cuts gr_lines (right-half stripped, left-half copied)")

    pcb, n_mirrored, n_stripped = mirror_footprints(pcb, center)
    print(f"centerline-mirrored {n_mirrored} footprint(s) (stripped {n_stripped} existing on right)")

    pcb, n_paired, n_paired_stripped = pair_diodes(pcb, center)
    print(f"switch-local re-paired {n_paired} diode(s) (stripped {n_paired_stripped} existing on right)")

    pcb, n_row, n_sw = repair_right_half_nets(pcb, center)
    print(f"split right-half nets: {n_row} ROW renames, {n_sw} switch anode rewires")

    pcb_path.write_text(pcb)
    print(f"→ {pcb_path.name}")


if __name__ == "__main__":
    pcb = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "keyboard.kicad_pcb"
    main(pcb)
