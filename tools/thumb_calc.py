#!/usr/bin/env python3
"""Calculate thumb fan angle_step so keycap edge gap matches ortho grid gap.

Usage:
    python3 tools/thumb_calc.py -i tools/keyboard.yaml
    python3 tools/thumb_calc.py -i tools/keyboard.yaml --target-gap 0.5
"""

import argparse
import math
import sys

import yaml


def keycap_corners(cx, cy, w, h, angle_deg):
    """Return 4 corners of a keycap centered at (cx, cy), rotated by angle_deg CW."""
    a = math.radians(angle_deg)
    cos_a, sin_a = math.cos(a), math.sin(a)
    corners = []
    for dx, dy in [(-w/2, -h/2), (w/2, -h/2), (w/2, h/2), (-w/2, h/2)]:
        rx = cx + dx * cos_a - dy * sin_a
        ry = cy + dx * sin_a + dy * cos_a
        corners.append((rx, ry))
    return corners


def min_corner_distance(corners_a, corners_b):
    """Minimum distance between any corner of A and any corner of B."""
    min_d = float('inf')
    for ax, ay in corners_a:
        for bx, by in corners_b:
            d = math.sqrt((ax - bx)**2 + (ay - by)**2)
            min_d = min(min_d, d)
    return min_d


def min_edge_distance(corners_a, corners_b):
    """Minimum distance between edges of two convex polygons (corner-to-edge and corner-to-corner)."""
    def point_to_segment_dist(px, py, x1, y1, x2, y2):
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            return math.sqrt((px - x1)**2 + (py - y1)**2)
        t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        proj_x, proj_y = x1 + t * dx, y1 + t * dy
        return math.sqrt((px - proj_x)**2 + (py - proj_y)**2)

    min_d = float('inf')
    for corners, other in [(corners_a, corners_b), (corners_b, corners_a)]:
        n = len(other)
        for px, py in corners:
            for i in range(n):
                x1, y1 = other[i]
                x2, y2 = other[(i + 1) % n]
                d = point_to_segment_dist(px, py, x1, y1, x2, y2)
                min_d = min(min_d, d)
    return min_d


def compute_thumb_gap(pivot_x, pivot_y, radius, start_angle, angle_step, keycap_w, keycap_h):
    """Compute minimum edge gap between two adjacent thumb keys."""
    a0 = start_angle
    a1 = start_angle + angle_step

    # Key centers on the arc
    cx0 = pivot_x + radius * math.sin(math.radians(a0))
    cy0 = pivot_y - radius * math.cos(math.radians(a0))
    cx1 = pivot_x + radius * math.sin(math.radians(a1))
    cy1 = pivot_y - radius * math.cos(math.radians(a1))

    c0 = keycap_corners(cx0, cy0, keycap_w, keycap_h, a0)
    c1 = keycap_corners(cx1, cy1, keycap_w, keycap_h, a1)

    return min_edge_distance(c0, c1)


def find_angle_step(pivot_x, pivot_y, radius, start_angle, keycap_w, keycap_h, target_gap):
    """Binary search for angle_step that produces target_gap between keycap edges."""
    lo, hi = 0.1, 90.0

    for _ in range(100):
        mid = (lo + hi) / 2
        gap = compute_thumb_gap(pivot_x, pivot_y, radius, start_angle, mid, keycap_w, keycap_h)
        if gap < target_gap:
            lo = mid
        else:
            hi = mid

    return (lo + hi) / 2


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--input", default="tools/keyboard.yaml", help="keyboard.yaml path")
    parser.add_argument("--target-gap", type=float, default=None,
                        help="Override target gap (key units). Default: use ortho grid gap.")
    args = parser.parse_args()

    with open(args.input) as f:
        layout = yaml.safe_load(f)

    spacing_x, spacing_y = layout["switch"]["spacing"]
    keycap_w, keycap_h = layout["switch"]["keycap"]
    thumb = layout["thumb"]

    # Ortho grid gap = space between adjacent keycap edges
    grid_gap_x = spacing_x - keycap_w
    grid_gap_y = spacing_y - keycap_h
    grid_gap = min(grid_gap_x, grid_gap_y)

    target = args.target_gap if args.target_gap is not None else grid_gap

    # Use arbitrary pivot for the calculation (relative positions are what matter)
    anchor_x, anchor_y = 0, 0
    pivot_dx, pivot_dy = thumb["pivot"]
    pivot_x = anchor_x + pivot_dx
    pivot_y = anchor_y + pivot_dy
    radius = thumb["radius"]
    start_angle = thumb["start_angle"]
    current_step = thumb["angle_step"]

    # Current gap
    current_gap = compute_thumb_gap(pivot_x, pivot_y, radius, start_angle, current_step, keycap_w, keycap_h)

    # Find optimal step
    optimal_step = find_angle_step(pivot_x, pivot_y, radius, start_angle, keycap_w, keycap_h, target)
    optimal_gap = compute_thumb_gap(pivot_x, pivot_y, radius, start_angle, optimal_step, keycap_w, keycap_h)

    print(f"Ortho grid:")
    print(f"  spacing:      [{spacing_x}, {spacing_y}]")
    print(f"  keycap:       [{keycap_w}, {keycap_h}]")
    print(f"  gap x:        {grid_gap_x:.4f}u ({grid_gap_x * 19.05:.2f} mm)")
    print(f"  gap y:        {grid_gap_y:.4f}u ({grid_gap_y * 19.05:.2f} mm)")
    print()
    print(f"Target gap:     {target:.4f}u ({target * 19.05:.2f} mm)")
    print()
    print(f"Thumb fan:")
    print(f"  radius:       {radius}")
    print(f"  start_angle:  {start_angle}°")
    print()
    print(f"  Current:      angle_step = {current_step}°  →  gap = {current_gap:.4f}u ({current_gap * 19.05:.2f} mm)")
    print(f"  Optimal:      angle_step = {optimal_step:.2f}°  →  gap = {optimal_gap:.4f}u ({optimal_gap * 19.05:.2f} mm)")
    print()
    print(f"Suggested keyboard.yaml change:")
    print(f"  angle_step: {round(optimal_step, 2)}")


if __name__ == "__main__":
    main()
