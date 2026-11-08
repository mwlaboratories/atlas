"""Faster stand-ins for build123d calls the fit checks make many times, same results.

build123d's Solid/Compound.is_inside builds a new BRepClass3d_SolidClassifier, which
indexes every face of the shape, for each point it classifies. switch_legs_clear asks
~2400 points of the bottom plate (30 s); with one classifier per shape, loaded once and
asked each point (Perform, exactly what is_inside does after loading), it takes 3 s, and
switches_centred / screws_inside drop from 7 s to under 1 s. Verified on both halves: the
check results are identical.

build.py applies this in its check workers only (the part builds keep plain build123d).
"""
from __future__ import annotations

from contextlib import contextmanager


@contextmanager
def classifier_per_shape():
    """Within the block, is_inside reuses one solid classifier per shape."""
    from build123d import Vector
    from build123d.topology.three_d import Mixin3D
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    plain = Mixin3D.is_inside
    loaded = {}           # TopoDS_Shape (same TShape, location, orientation) -> classifier

    def is_inside(self, point, tolerance: float = 1.0e-6) -> bool:
        c = loaded.get(self.wrapped)
        if c is None:
            c = loaded[self.wrapped] = BRepClass3d_SolidClassifier(self.wrapped)
        c.Perform(gp_Pnt(*Vector(point)), tolerance)
        return c.State() == TopAbs_IN or c.IsOnAFace()
    is_inside.__doc__ = plain.__doc__
    Mixin3D.is_inside = is_inside
    try:
        yield
    finally:
        Mixin3D.is_inside = plain
