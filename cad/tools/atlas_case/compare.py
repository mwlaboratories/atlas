"""Exactness check: how far a generated part is from a reference STEP.

Two independent measures:

1. Booleans: `extra` = volume the generated part has and the reference doesn't,
   `missing` = the other way round. Both ~0 means the same solid. The cuts run
   with OpenCascade's fuzzy value = `FUZZY` (0.01 mm, the same as `dev_tol`):
   exact booleans misclassify near-coincident faces (the Fusion STEPs carry
   edge tolerances up to 0.008 mm, and the mirrored right halves trip it too),
   returning the whole part although the shapes coincide.
2. Boundary deviation: every vertex and edge midpoint of each shape must lie on
   the other's surface. The largest distance is reported, both ways.

A part passes when both hold. Total volumes are printed for information only:
OpenCascade's volume integration is off by a few mm3 on spline faces, so they
are not compared.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from build123d import Compound, Part, import_step
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

FUZZY = 0.01  # mm


def fuzzy_cut(a, b, fuzzy: float = FUZZY):
    """a - b with OpenCascade's fuzzy tolerance; None when nothing is left."""
    op = BRepAlgoAPI_Cut()
    op.SetFuzzyValue(fuzzy)
    try:
        r = a._bool_op((a,), (b,), op)
    except ValueError:          # build123d raises on a null result: nothing left over
        return None
    return r if r is not None and r.solids() else None


@dataclass
class Diff:
    name: str
    ref_volume: float
    volume: float
    extra: float
    missing: float
    deviation: float                  # mm, worst of both directions
    worst_at: tuple
    extra_pieces: list = field(default_factory=list)
    missing_pieces: list = field(default_factory=list)
    vol_tol: float = 0.05
    dev_tol: float = 0.01

    def ok(self) -> bool:
        return self.deviation <= self.dev_tol and self.extra < self.vol_tol and self.missing < self.vol_tol

    def __str__(self) -> str:
        s = (f"{self.name}: volume {self.volume:.3f} vs reference {self.ref_volume:.3f} mm3 | "
             f"extra {self.extra:.3f}, missing {self.missing:.3f} mm3 | "
             f"max surface deviation {self.deviation:.4f} mm at {self.worst_at}  "
             f"{'OK' if self.ok() else 'DIFFERS'}")
        for label, pieces in (("extra", self.extra_pieces), ("missing", self.missing_pieces)):
            for v, bb in pieces[:12]:
                if v < 1e-3:
                    continue
                s += (f"\n    {label:7} {v:9.3f} mm3 at x[{bb.min.X:.2f},{bb.max.X:.2f}] "
                      f"y[{bb.min.Y:.2f},{bb.max.Y:.2f}] z[{bb.min.Z:.2f},{bb.max.Z:.2f}]")
        return s


def _pieces(shape, min_vol=1e-4):
    if shape is None:
        return []
    out = [(s.volume, s.bounding_box()) for s in shape.solids() if s.volume > min_vol]
    return sorted(out, key=lambda t: -t[0])


def _probe_points(shape):
    return [v.center() for v in shape.vertices()] + [e.position_at(0.5) for e in shape.edges()]


def surface_deviation(a, b):
    """Largest distance from a's vertices/edge midpoints to b's surface."""
    skin = Compound(b.faces())
    worst, where = 0.0, None
    for p in _probe_points(a):
        d = skin.distance_to(p)
        if d > worst:
            worst, where = d, p
    return worst, where


def compare(name: str, part: Part, reference_step: str) -> Diff:
    ref = import_step(reference_step)
    extra = fuzzy_cut(part, ref)
    missing = fuzzy_cut(ref, part)
    d1, w1 = surface_deviation(part, ref)
    d2, w2 = surface_deviation(ref, part)
    dev, where = (d1, w1) if d1 >= d2 else (d2, w2)
    return Diff(name=name, ref_volume=ref.volume, volume=part.volume,
                extra=extra.volume if extra else 0.0, missing=missing.volume if missing else 0.0,
                deviation=dev, worst_at=tuple(round(c, 3) for c in where) if where else (),
                extra_pieces=_pieces(extra), missing_pieces=_pieces(missing))
