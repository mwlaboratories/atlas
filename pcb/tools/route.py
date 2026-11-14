"""Autoroute what is left unrouted on a half with Freerouting, keeping all existing copper.

    python3 pcb/tools/route.py export pcb/atlas-left.kicad_pcb out.dsn   # rip conflicts, lock, DSN
    freerouting --gui.enabled=false -de out.dsn -do out.ses -mp 20 -mt 1 # route (nixpkgs 2.2.4)
    python3 pcb/tools/route.py import pcb/atlas-left.kicad_pcb out.ses   # SES in, unlock

export: removes tracks/vias of other nets that touch a U1 pad (the Plus's new pads land on
copper routed for the old XIAO) plus dangling stubs and vias, locks every remaining track/via
so Freerouting treats it as fixed, writes the Specctra DSN.  import: reads the SES, unlocks.

Each removal pass runs in its own process: in KiCad 10's SWIG bindings, after BOARD.Remove()
every later proxy in the session (even a fresh LoadBoard) comes back as a raw SwigPyObject.
"""
import subprocess
import sys
import pcbnew

EDGE_KO = 'EDGE_KO'
mode, path = sys.argv[1:3]
other = sys.argv[3] if len(sys.argv) > 3 else None


def again(m):
    out = subprocess.run([sys.executable, __file__, m, path], check=True,
                         capture_output=True, text=True).stdout
    return [l[7:] for l in out.splitlines() if l.startswith('RESULT ')][-1]


def save_after(b, kill):
    for t in kill:
        b.Remove(t)
    pcbnew.SaveBoard(path, b)


if mode == 'rip':           # one process: conflicts with U1 pads
    b = pcbnew.LoadBoard(path)
    u1 = next(f for f in b.GetFootprints() if f.GetReference() == 'U1')
    margin = pcbnew.FromMM(0.25)
    boxes = []
    for p in u1.Pads():
        bb = p.GetBoundingBox(); bb.Inflate(margin)
        boxes.append((bb, p.GetNetCode()))
    kill = []
    for t in b.GetTracks():
        tb = t.GetBoundingBox()
        for bb, nc in boxes:
            if t.GetNetCode() != nc and bb.Intersects(tb):
                # bounding boxes of diagonal tracks are coarse: confirm on real geometry
                if t.HitTest(bb, False) or (t.Type() == pcbnew.PCB_VIA_T):
                    kill.append(t); break
    names = sorted({t.GetNetname() for t in kill})
    save_after(b, kill)
    print('RESULT', len(kill), names)
elif mode == 'stubs':       # one process: remove what KiCad's own DRC calls dangling
    import json, os, tempfile
    rep = os.path.join(tempfile.mkdtemp(), 'drc.json')
    subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '-o', rep, path],
                   check=True, capture_output=True)
    ids = {it['uuid'] for v in json.load(open(rep))['violations']
           if v['type'] in ('track_dangling', 'via_dangling') for it in v['items']}
    b = pcbnew.LoadBoard(path)
    kill = [t for t in b.GetTracks() if t.m_Uuid.AsString() in ids]
    names = sorted({t.GetNetname() for t in kill})
    save_after(b, kill)
    print('RESULT', len(kill), names)
elif mode == 'lockdsn':
    b = pcbnew.LoadBoard(path)
    # Freerouting keeps only the track clearance (0.15) to the board boundary; the .kicad_dru
    # wants 0.3 copper-to-edge.  Temporary keepout strips along every Edge.Cuts line carry
    # that into the DSN; import removes them again (zone name EDGE_KO).
    hw = pcbnew.FromMM(0.35)
    for d in b.GetDrawings():
        if d.GetLayer() != pcbnew.Edge_Cuts or d.GetShape() != pcbnew.SHAPE_T_SEGMENT:
            continue
        s, e = d.GetStart(), d.GetEnd()
        dx, dy = e.x - s.x, e.y - s.y
        ln = (dx * dx + dy * dy) ** 0.5
        if ln == 0:
            continue
        ux, uy = dx / ln * hw, dy / ln * hw          # along, extended by hw at both ends
        nx, ny = -uy, ux                              # normal
        z = pcbnew.ZONE(b)
        z.SetIsRuleArea(True); z.SetZoneName(EDGE_KO)
        z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(True)
        z.SetDoNotAllowPads(False); z.SetDoNotAllowZoneFills(False); z.SetDoNotAllowFootprints(False)
        ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu); ls.AddLayer(pcbnew.B_Cu); z.SetLayerSet(ls)
        o = z.Outline(); o.NewOutline()
        for px, py in ((s.x - ux + nx, s.y - uy + ny), (e.x + ux + nx, e.y + uy + ny),
                       (e.x + ux - nx, e.y + uy - ny), (s.x - ux - nx, s.y - uy - ny)):
            o.Append(int(px), int(py))
        b.Add(z)
    for t in b.GetTracks():
        t.SetLocked(True)
    pcbnew.SaveBoard(path, b)
    assert pcbnew.ExportSpecctraDSN(pcbnew.LoadBoard(path), other), 'DSN export failed'
elif mode == 'export':
    print('ripped (U1 conflicts):', again('rip'))
    for _ in range(10):
        r = again('stubs')
        if r.startswith('0 '):
            break
        print('removed dangling:', r)
    subprocess.run([sys.executable, __file__, 'lockdsn', path, other], check=True,
                   capture_output=True)
    print('DSN written:', other)
else:
    b = pcbnew.LoadBoard(path)
    assert pcbnew.ImportSpecctraSES(b, other), 'SES import failed'
    for t in b.GetTracks():
        t.SetLocked(False)
    ko = [z for z in b.Zones() if z.GetZoneName() == EDGE_KO]
    for z in ko:                      # last: Remove() invalidates later proxies
        b.Remove(z)
    pcbnew.SaveBoard(path, b)
    print('imported', other, '- removed', len(ko), 'edge keepouts')
