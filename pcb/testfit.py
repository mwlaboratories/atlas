"""Printable PCB test-fit models: real switches click in, real hotswap sockets show their fit.

Run through FreeCAD's headless interpreter (the devenv `testfit` script does this):

    FreeCADCmd pcb/testfit.py

Input:  pcb/atlas-{left,right}.step, the KiCad 3D exports with component models.
        Re-export them after changing the board (KiCad: File > Export > STEP, with
        the 3D models), otherwise this builds the old board.
Output: pcb/testfit/ (gitignored)
        atlas-{left,right}-pcb-testfit.{step,stl}
        atlas-pcb-testfit.3mf, one half per plate, for Bambu Studio

What it does per half:
  - drops everything on the top side (the XIAO and the top copper/mask/silk layers),
    so the top face lies flat on the bed
  - thickens the board body to the real 1.6 mm (KiCad's STEP board is 1.51 mm dielectric)
  - keeps the bottom side: hotswap sockets, diodes, BAW56s, ADS1220, switches, 0402s
  - cuts every 3.0 mm hotswap pin hole straight through board and socket, so the
    printer doesn't try the tiny receptacle slits and real switch legs drop in
  - flips the part top-face-down
"""
import math
import os
import traceback
import zipfile

import FreeCAD as App
import MeshPart
import Part

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "testfit")
BOARD_MM = 1.6
HOLE_R = 1.5          # hotswap pin hole, 3.0 mm drill
BED_MM = 180.0        # Bambu Lab A1 mini; plates are laid out on this grid


def build(half):
    shape = Part.Shape()
    shape.read(os.path.join(HERE, f"atlas-{half}.step"))
    solids = shape.Solids
    board = max(solids, key=lambda s: s.BoundBox.XLength * s.BoundBox.YLength)
    top, bot = board.BoundBox.ZMax, board.BoundBox.ZMin
    rest = [s for s in solids if not s.isSame(board)]
    keep = [s for s in rest if s.BoundBox.ZMin < top - 0.05]   # bottom side + leads through the board
    drop = len(rest) - len(keep)

    # Hotswap pin holes, read from the board body itself: vertical cylindrical walls of r = 1.5 mm.
    centres = {}
    for f in board.Faces:
        s = f.Surface
        if isinstance(s, Part.Cylinder) and abs(s.Radius - HOLE_R) < 0.01 and abs(abs(s.Axis.z) - 1) < 1e-6:
            centres[(round(s.Center.x, 3), round(s.Center.y, 3))] = None
    holes = list(centres)

    t = BOARD_MM - (top - bot)
    if t > 0.001:
        board = board.fuse(board.translated(App.Vector(0, 0, t))).removeSplitter()
    zlo = min(s.BoundBox.ZMin for s in keep) - 1
    zhi = top + t + 1
    cyls = [Part.makeCylinder(HOLE_R, zhi - zlo, App.Vector(x, y, zlo)) for x, y in holes]

    parts, ncut = [], 0
    for s in [board] + keep:
        hit = [c for c in cyls if s.BoundBox.intersect(c.BoundBox) and s.common(c).Volume > 1e-6]
        if hit:
            parts += s.cut(hit).Solids
            ncut += 1
        else:
            parts.append(s)
    body = Part.makeCompound(parts)
    body.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), 180)
    body.translate(App.Vector(0, 0, -body.BoundBox.ZMin))

    bb = body.BoundBox
    print(f"{half}: removed {drop} top-side solids, kept {len(keep)}, board {BOARD_MM} mm, "
          f"{len(holes)} pin holes through {ncut} solids, size {bb.XLength:.1f} x {bb.YLength:.1f} x {bb.ZLength:.2f} mm")
    if bb.XLength > BED_MM or bb.YLength > BED_MM:
        print(f"  warning: larger than the {BED_MM:.0f} mm bed")
    name = f"atlas-{half}-pcb-testfit"
    body.exportStep(os.path.join(OUT, name + ".step"))
    mesh = MeshPart.meshFromShape(Shape=body, LinearDeflection=0.02, AngularDeflection=0.2)
    mesh.write(os.path.join(OUT, name + ".stl"))
    return name, mesh


def write_3mf(path, meshes):
    """Bambu Studio project: one object per plate, each centred on its plate."""
    cols = math.isqrt(len(meshes))
    cols += cols * cols < len(meshes)
    stride = BED_MM * 1.2                      # Bambu's plate pitch: bed + 1/5 gap
    objects, items, cfg_obj, cfg_plate = [], [], [], []
    for i, (name, mesh) in enumerate(meshes, 1):
        pts, faces = mesh.Topology
        bb = mesh.BoundBox
        r, c = divmod(i - 1, cols)
        ox = c * stride + BED_MM / 2 - bb.Center.x
        oy = -r * stride + BED_MM / 2 - bb.Center.y
        oz = -bb.ZMin
        vs = "".join(f'<vertex x="{p.x + ox:.4f}" y="{p.y + oy:.4f}" z="{p.z + oz:.4f}"/>' for p in pts)
        ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{d}"/>' for a, b, d in faces)
        objects.append(f'<object id="{i}" type="model"><mesh><vertices>{vs}</vertices><triangles>{ts}</triangles></mesh></object>')
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


try:
    os.makedirs(OUT, exist_ok=True)
    meshes = [build(h) for h in ("left", "right")]
    write_3mf(os.path.join(OUT, "atlas-pcb-testfit.3mf"), meshes)
    print(f"-> {OUT}/atlas-pcb-testfit.3mf (plate 1 left, plate 2 right)")
except Exception:
    traceback.print_exc()
    raise SystemExit(1)
