#!/usr/bin/env python3
"""Convert Atlas keyboard.yaml to KLE-NG JSON for editor.keyboard-tools.xyz"""

import argparse
import json
import math
import sys
from dataclasses import dataclass
from itertools import groupby
from typing import List

import yaml


@dataclass
class KleKey:
    x: float
    y: float
    w: float
    h: float
    label: str
    r: float = 0
    rx: float = 0
    ry: float = 0


def load_layout(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def compute_half_keys(layout: dict) -> List[KleKey]:
    """Compute KLE positions for one half (grid + thumb). All in key units, Y-down."""
    spacing_x, spacing_y = layout["switch"]["spacing"]
    keycap_w, keycap_h = layout["switch"].get("keycap", [1, 1])
    cols = layout["grid"]["cols"]
    rows = layout["grid"]["rows"]
    stagger = layout["grid"]["stagger"]

    min_stagger = min(stagger)
    keys = []

    # Build per-key rotation lookup from rotated_keys config
    rot_map = {}
    for entry in layout.get("rotated_keys", []):
        rot_map[(entry["row"], entry["col"])] = entry["rotation"]

    # Grid keys: row 0=top, 2=bottom. In Y-down KLE, top row has smallest y.
    for row in range(rows):
        for col in range(cols):
            x = col * spacing_x
            y = row * spacing_y + stagger[col] - min_stagger
            r = rot_map.get((row, col), 0)
            if r != 0:
                # Rotated around own center; rx/ry uses KLE's -1 y cursor shift
                keys.append(KleKey(x=x, y=y, w=keycap_w, h=keycap_h, label=f"{row},{col}",
                                   r=r, rx=x + keycap_w/2, ry=y - 1 + keycap_h/2))
            else:
                keys.append(KleKey(x=x, y=y, w=keycap_w, h=keycap_h, label=f"{row},{col}"))

    # Thumb keys — chain placement model
    # First key's top-left corner aligns with anchor's bottom-right corner + gap.
    # Subsequent keys chain from the previous key, maintaining the same gap.
    thumb = layout["thumb"]
    anchor_col = cols - 1
    anchor_x = anchor_col * spacing_x
    anchor_y = (rows - 1) * spacing_y + stagger[anchor_col] - min_stagger

    # Grid gap (space between adjacent keycap edges)
    gap = min(spacing_x - keycap_w, spacing_y - keycap_h)

    angle_start = thumb["angle"]
    angle_step = thumb["angle_step"]
    offset_dx, offset_dy = thumb.get("offset", [0, 0])

    # Anchor key's bottom-left corner (unrotated grid key)
    anchor_bl_x = anchor_x - keycap_w / 2
    anchor_bl_y = anchor_y + keycap_h / 2

    # First thumb key: top-left corner at anchor's bottom-left + gap downward
    first_tl_x = anchor_bl_x
    first_tl_y = anchor_bl_y + gap

    prev_corners = None
    for i in range(thumb["keys"]):
        r = angle_start + i * angle_step
        r_rad = math.radians(r)
        cos_r, sin_r = math.cos(r_rad), math.sin(r_rad)
        thumb_col = cols - thumb["keys"] + i

        if i == 0:
            # Place center so that the rotated top-left corner lands at first_tl
            # Rotated top-left offset from center: rotate (-w/2, -h/2) by r
            tl_dx = (-keycap_w/2) * cos_r - (-keycap_h/2) * sin_r
            tl_dy = (-keycap_w/2) * sin_r + (-keycap_h/2) * cos_r
            tx = first_tl_x - tl_dx + offset_dx
            ty = first_tl_y - tl_dy + offset_dy
        else:
            # Chain from previous key's bottom-right corner to this key's bottom-left corner
            prev_br_x, prev_br_y = prev_corners[2]  # index 2 = bottom-right

            # This key's bottom-left corner (rotated offset from center)
            bl_dx = (-keycap_w/2) * cos_r - (keycap_h/2) * sin_r
            bl_dy = (-keycap_w/2) * sin_r + (keycap_h/2) * cos_r

            # Gap direction: perpendicular to the average bottom edge of both keys
            avg_angle = math.radians(r - angle_step / 2)
            gap_dx = gap * math.cos(avg_angle)
            gap_dy = gap * math.sin(avg_angle)

            tx = prev_br_x + gap_dx - bl_dx
            ty = prev_br_y + gap_dy - bl_dy

        # Compute corners for this key (for chaining to next)
        corners = []
        for dx, dy in [(-keycap_w/2, -keycap_h/2),   # top-left
                        (keycap_w/2, -keycap_h/2),    # top-right
                        (keycap_w/2, keycap_h/2),     # bottom-right
                        (-keycap_w/2, keycap_h/2)]:   # bottom-left
            cx = tx + dx * cos_r - dy * sin_r
            cy = ty + dx * sin_r + dy * cos_r
            corners.append((cx, cy))
        prev_corners = corners

        keys.append(KleKey(x=tx, y=ty, w=keycap_w, h=keycap_h,
                           label=f"3,{thumb_col}", r=r,
                           rx=tx + keycap_w/2, ry=ty - 1 + keycap_h/2))

    return keys


def mirror_keys(
    left_keys: List[KleKey], gap: float, grid_cols: int
) -> List[KleKey]:
    """Mirror left-half keys horizontally to produce right-half keys."""
    # Mirror axis based on grid keys only (thumb keys extend beyond grid)
    grid_max_x = max(k.x for k in left_keys if not k.label.startswith("3,"))

    right_keys = []
    for k in left_keys:
        # Mirror label columns (works for both grid and thumb keys)
        row_str, col_str = k.label.split(",")
        left_col = int(col_str)
        right_col = grid_cols + (grid_cols - 1 - left_col)

        # Mirror x around grid extent, negate rotation
        mx = grid_max_x + k.w + gap + (grid_max_x - k.x)
        r = -k.r
        # rx/ry = key center; mirrored center = mirrored top-left + w/2
        mrx = mx + k.w/2 if k.r != 0 else 0
        mry = k.ry

        right_keys.append(
            KleKey(x=mx, y=k.y, w=k.w, h=k.h, label=f"{row_str},{right_col}",
                   r=r, rx=mrx, ry=mry)
        )

    return right_keys


def keys_to_kle(keys: List[KleKey]) -> list:
    """Serialize keys into KLE-NG JSON array format."""
    # Separate grid (no rotation) and thumb (with rotation) keys
    grid_keys = [k for k in keys if k.r == 0]
    thumb_keys = [k for k in keys if k.r != 0]

    # Sort grid keys by y, then x
    grid_keys.sort(key=lambda k: (round(k.y, 3), k.x))

    # Group grid keys by y-value (within tolerance)
    def y_group_key(k):
        return round(k.y, 2)

    kle_rows = []
    cursor_y = 0.0  # implicit y cursor (advances +1 per KLE row)
    key_w = grid_keys[0].w if grid_keys else 1
    key_h = grid_keys[0].h if grid_keys else 1

    for y_val, group in groupby(grid_keys, key=y_group_key):
        row_keys = sorted(group, key=lambda k: k.x)
        kle_row = []
        cursor_x = 0.0

        for i, key in enumerate(row_keys):
            props = {}

            # Set key size (only needed once per row if all same, but KLE
            # requires it on every key if non-default)
            if abs(key.w - 1) > 0.001:
                props["w"] = _round(key.w)
            if abs(key.h - 1) > 0.001:
                props["h"] = _round(key.h)

            if i == 0:
                # First key in row: set y offset relative to implicit +1 advance
                y_offset = key.y - cursor_y - 1
                if abs(y_offset) > 0.001:
                    props["y"] = _round(y_offset)
                if abs(key.x) > 0.001:
                    props["x"] = _round(key.x)
            else:
                # Subsequent keys: x offset from cursor (advanced by prev key width)
                x_gap = key.x - cursor_x
                if abs(x_gap) > 0.001:
                    props["x"] = _round(x_gap)

            if props:
                kle_row.append(props)
            kle_row.append(key.label)
            cursor_x = key.x + key.w  # key occupies its width

        kle_rows.append(kle_row)
        cursor_y = row_keys[0].y

    # Thumb keys: each has unique rx/ry (key center), so KLE resets cursor
    # each time. After reset, cursor is at (rx, ry). We need the key center
    # at (rx, ry), so key top-left at (rx - w/2, ry - h/2).
    thumb_keys.sort(key=lambda k: (k.rx, k.r))

    for key in thumb_keys:
        props = {
            "r": _round(key.r),
            "rx": _round(key.rx),
            "ry": _round(key.ry),
            "x": _round(-key.w / 2),
            "y": _round(-key.h / 2),
        }
        if abs(key.w - 1) > 0.001:
            props["w"] = _round(key.w)
        if abs(key.h - 1) > 0.001:
            props["h"] = _round(key.h)
        kle_rows.append([props, key.label])

    return kle_rows


def _round(v: float) -> float:
    """Round to 2 decimal places, return int if whole number."""
    r = round(v, 2)
    if r == int(r):
        return int(r)
    return r


def main():
    parser = argparse.ArgumentParser(
        description="Convert keyboard.yaml to KLE-NG JSON"
    )
    parser.add_argument(
        "-i", "--input", default="keyboard.yaml", help="Input keyboard.yaml path"
    )
    parser.add_argument(
        "-o", "--output", default=None, help="Output JSON file (default: stdout)"
    )
    parser.add_argument(
        "--gap", type=float, default=4.0, help="Gap between halves in key units"
    )
    args = parser.parse_args()

    layout = load_layout(args.input)
    left_keys = compute_half_keys(layout)
    right_keys = mirror_keys(
        left_keys,
        gap=args.gap,
        grid_cols=layout["grid"]["cols"],
    )

    all_keys = left_keys + right_keys
    kle_json = keys_to_kle(all_keys)

    output = json.dumps(kle_json)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output + "\n")
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
