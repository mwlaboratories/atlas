#!/usr/bin/env python3
"""Convert Atlas layout.yaml to KLE-NG JSON for editor.keyboard-tools.xyz"""

import argparse
import json
import sys
from dataclasses import dataclass
from itertools import groupby
from typing import List

import yaml


@dataclass
class KleKey:
    x: float
    y: float
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
                keys.append(KleKey(x=x, y=y, label=f"{row},{col}",
                                   r=r, rx=x + 0.5, ry=y - 0.5))
            else:
                keys.append(KleKey(x=x, y=y, label=f"{row},{col}"))

    # Thumb keys
    thumb = layout["thumb"]
    # Anchor: inner column (last col), bottom row (row = rows - 1)
    anchor_col = cols - 1
    anchor_x = anchor_col * spacing_x
    anchor_y = (rows - 1) * spacing_y + stagger[anchor_col] - min_stagger

    origin_dx, origin_dy = thumb["origin_offset"]
    spread_dx, spread_dy = thumb["spread"]
    rot_start = thumb["rotation_start"]
    rot_step = thumb["rotation_step"]

    for i in range(thumb["keys"]):
        tx = anchor_x + origin_dx + i * spread_dx
        ty = anchor_y + origin_dy + i * spread_dy
        r = rot_start + i * rot_step
        thumb_col = cols - thumb["keys"] + i
        # rx/ry must be the key center in the grid's coordinate frame.
        # Grid keys are serialized with a -1 y shift (KLE cursor model),
        # so thumb ry also needs -1: ry = ty + 0.5 - 1 = ty - 0.5.
        # This ensures rotation_reference == key_center in kbplacer,
        # preventing rotation from translating the key.
        keys.append(KleKey(x=tx, y=ty, label=f"3,{thumb_col}", r=r, rx=tx + 0.5, ry=ty - 0.5))

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
        mx = grid_max_x + 1 + gap + (grid_max_x - k.x)
        r = -k.r
        # rx/ry = key center; mirrored center = mirrored top-left + 0.5
        mrx = mx + 0.5 if k.r != 0 else 0
        mry = k.ry

        right_keys.append(
            KleKey(x=mx, y=k.y, label=f"{row_str},{right_col}",
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

    for y_val, group in groupby(grid_keys, key=y_group_key):
        row_keys = sorted(group, key=lambda k: k.x)
        kle_row = []
        cursor_x = 0.0

        for i, key in enumerate(row_keys):
            props = {}

            if i == 0:
                # First key in row: set y offset relative to implicit +1 advance
                y_offset = key.y - cursor_y - 1
                if abs(y_offset) > 0.001:
                    props["y"] = _round(y_offset)
                if abs(key.x) > 0.001:
                    props["x"] = _round(key.x)
            else:
                # Subsequent keys: x offset from cursor (already advanced by prev key width)
                x_gap = key.x - cursor_x
                if abs(x_gap) > 0.001:
                    props["x"] = _round(x_gap)

            if props:
                kle_row.append(props)
            kle_row.append(key.label)
            cursor_x = key.x + 1  # key occupies 1u

        kle_rows.append(kle_row)
        cursor_y = row_keys[0].y

    # Thumb keys: each has unique rx/ry (key center), so KLE resets cursor
    # each time. After reset, cursor is at (rx, ry). kbplacer's parse_kle
    # does NOT add implicit +1 per row. We need the key center at (rx, ry),
    # so key top-left at (rx-0.5, ry-0.5) => x=-0.5, y=-0.5.
    thumb_keys.sort(key=lambda k: (k.rx, k.r))

    for key in thumb_keys:
        props = {
            "r": _round(key.r),
            "rx": _round(key.rx),
            "ry": _round(key.ry),
            "x": -0.5,
            "y": -0.5,
        }
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
        description="Convert layout.yaml to KLE-NG JSON"
    )
    parser.add_argument(
        "-i", "--input", default="layout.yaml", help="Input layout.yaml path"
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
