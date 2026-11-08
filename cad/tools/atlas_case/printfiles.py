"""Bambu Studio project with every part on its own plate, in print orientation.

Same plate layout as pcb/testfit.py (one object per plate, centred; Bambu's plate pitch is
bed x 1.2). Orientation per part:
  bottomplate   as modelled: underside on the bed (the 0.4 mm floors make layer 1 solid,
                the lip-lock bevel is a 45 deg overhang)
  switchplate   flipped: the switch face on the bed, the thumb wall pointing up
  housing       as modelled
"""
from __future__ import annotations

import math
import zipfile
from pathlib import Path

from build123d import Rot

BED_MM = 180.0                     # Bambu Lab A1 mini
FLIP = {"switchplate": True}


def oriented(name: str, shape):
    return Rot(180, 0, 0) * shape if FLIP.get(name) else shape


def write_3mf(path: Path, parts: list[tuple[str, object]], tol: float = 0.005) -> list[str]:
    """parts: (name, shape). Returns warnings (e.g. a part larger than the bed)."""
    warnings = []
    cols = math.isqrt(len(parts))
    cols += cols * cols < len(parts)
    stride = BED_MM * 1.2
    objects, items, cfg_obj, cfg_plate = [], [], [], []
    for i, (name, shape) in enumerate(parts, 1):
        shape = oriented(name.split("-")[-1], shape)
        verts, tris = shape.tessellate(tol, 0.03)
        bb = shape.bounding_box()
        if bb.size.X > BED_MM or bb.size.Y > BED_MM:
            warnings.append(f"{name}: {bb.size.X:.0f} x {bb.size.Y:.0f} mm is larger than the {BED_MM:.0f} mm bed")
        r, c = divmod(i - 1, cols)
        ox = c * stride + BED_MM / 2 - bb.center().X
        oy = -r * stride + BED_MM / 2 - bb.center().Y
        oz = -bb.min.Z
        vs = "".join(f'<vertex x="{v.X + ox:.4f}" y="{v.Y + oy:.4f}" z="{v.Z + oz:.4f}"/>' for v in verts)
        ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{d}"/>' for a, b, d in tris)
        objects.append(f'<object id="{i}" type="model"><mesh><vertices>{vs}</vertices>'
                       f'<triangles>{ts}</triangles></mesh></object>')
        items.append(f'<item objectid="{i}" printable="1"/>')
        cfg_obj.append(
            f'  <object id="{i}">\n    <metadata key="name" value="{name}"/>\n    <metadata key="extruder" value="1"/>\n'
            f'    <part id="1" subtype="normal_part">\n      <metadata key="name" value="{name}"/>\n    </part>\n  </object>\n')
        cfg_plate.append(
            f'  <plate>\n    <metadata key="plater_id" value="{i}"/>\n    <metadata key="plater_name" value="{name}"/>\n'
            f'    <metadata key="locked" value="false"/>\n    <model_instance>\n      <metadata key="object_id" value="{i}"/>\n'
            f'      <metadata key="instance_id" value="0"/>\n      <metadata key="identify_id" value="{100 + i}"/>\n'
            f'    </model_instance>\n  </plate>\n')
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">'
             '<metadata name="Application">BambuStudio-02.05.00.66</metadata>'
             '<metadata name="BambuStudio:3mfVersion">1</metadata>'
             f'<resources>{"".join(objects)}</resources><build>{"".join(items)}</build></model>')
    config = '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n' + "".join(cfg_obj) + "".join(cfg_plate) + "</config>\n"
    types = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
             '<Default Extension="config" ContentType="text/xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel-1" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", config)
    return warnings
