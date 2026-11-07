"""Small geometry helpers shared by the case parts (case frame, mm, z up)."""
from __future__ import annotations

from build123d import Polygon, Pos, Rectangle, extrude


def prism(face, z0: float, z1: float):
    """Extrude a flat XY face from z0 to z1, whatever way its normal happens to point."""
    return Pos(0, 0, z0) * extrude(face, amount=z1 - z0, dir=(0, 0, 1))


def box(x0: float, x1: float, y0: float, y1: float):
    """Axis-aligned rectangle face from its edges."""
    return Pos((x0 + x1) / 2, (y0 + y1) / 2) * Rectangle(x1 - x0, y1 - y0)


def poly(*pts):
    """Polygon face through absolute points (no re-centring)."""
    return Polygon(*pts, align=None)
