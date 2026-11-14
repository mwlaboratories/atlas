"""v4.3: XIAO nRF52840 Plus footprint + plain 4x5 diode matrix (replaces the charlieplex).

    python3 pcb/tools/xiao_plus.py pcb/atlas-left.kicad_pcb left
    python3 pcb/tools/xiao_plus.py pcb/atlas-right.kicad_pcb right

Run once per half on the v4.2 boards (needs the pcbnew module from kicad-base).

Footprint (U1, local frame = Seeed's host footprint frame, pad 1 = D0 at +x/-y):
  * pads 15..23: the Plus's extra castellations, between the main pads at half pitch,
    0.95 x 1.232 mm, outer end flush with the main pads (local y = -/+8.655).
    15 P0.15  16 P0.19  17 P1.01  18 P0.09/NFC1  19 P0.10/NFC2  20 P0.31 (battery divider,
    never use)  21 P1.03  22 P1.05  23 P1.07   (Seeed XIAO nRF52840 Plus schematic v1.1)
  * BAT and GND move to the Plus's underside pads, Seeed's land pattern: 2.5 x 1.1 mm at
    local (-5.518, -0.994) / (-5.518, 1.006).  Old pad 28 stays BAT; old pad 20 becomes 24 = GND (20 is now the P0.31 castellation).
  * underside RST (Plus TP2) and GND test point (TP1, no net): old pads 17/18, renumbered 25/26.

Matrix (col2row, existing diodes and K-nets unchanged): a switch's line pad -> COLc from its
CxRy label, the diode cathode -> ROWr; thumbs T0/T1 -> ROW3 x COL3/COL4.  The BAW56s (U10..U14)
and every track/via on the old P*/C*/INT nets are removed; the matrix nets are left unrouted.
"""
import sys
import pcbnew
from pcbnew import VECTOR2I, FromMM

path, half = sys.argv[1], sys.argv[2]
b = pcbnew.LoadBoard(path)

# XIAO pad -> matrix net.  Left: matrix side is D0..D5 (+ 15,16,17 between them);
# right: D5..D10 (+ 21,22,23 between D7..D10).
PINS = {
    'left':  {'6': 'COL0', '5': 'COL1', '4': 'COL2', '17': 'COL3', '3': 'COL4',
              '16': 'ROW0', '2': 'ROW1', '15': 'ROW2', '1': 'ROW3'},
    'right': {'11': 'COL0', '23': 'COL1', '10': 'COL2', '22': 'COL3', '9': 'COL4',
              '21': 'ROW0', '8': 'ROW1', '7': 'ROW2', '6': 'ROW3'},
}[half]
OLD = {f'P{i}' for i in range(5)} | {f'C{i}' for i in range(5)} | {'INT'}

def net(name):
    n = b.FindNet(name)
    if n is None:
        n = pcbnew.NETINFO_ITEM(b, name); b.Add(n)
    return n

fps = {f.GetReference(): f for f in b.GetFootprints()}

BAW = ('U10', 'U11', 'U12', 'U13', 'U14')
for r in BAW:
    assert fps[r].GetValue() == 'BAW56', r

# --- 1. XIAO Plus footprint -----------------------------------------------------------------
u1 = fps['U1']
pads = {p.GetNumber(): p for p in u1.Pads()}
ref = pads['1']
pads['17'].SetNumber('25')      # underside RST (Plus TP2): 17/18 are castellation numbers now
pads['18'].SetNumber('26')      # underside GND test point (Plus TP1), no net
extra_x = {'15': 6.35, '16': 3.81, '17': 1.27, '18': -1.27, '19': -3.81, '20x': -6.35,
           '21': -6.35, '22': -3.81, '23': -1.27}
for num, x in extra_x.items():
    p = pcbnew.PAD(ref)
    p.SetNumber('20' if num == '20x' else num)
    y = -8.655 if num in ('15', '16', '17', '18', '19', '20x') else 8.655
    p.SetFPRelativePosition(VECTOR2I(FromMM(x), FromMM(y)))
    p.SetSize(pcbnew.PADSTACK.ALL_LAYERS, VECTOR2I(FromMM(1.232), FromMM(0.95)))
    p.SetNetCode(0)
    u1.Add(p)
# the edge castellation pad 20 (P0.31) gets the number 20; the old underside pad 20 becomes GND
old20, old28 = pads['20'], pads['28']
for p, (x, y), netname, num in ((old28, (-5.518, -0.994), 'BAT_SW', '28'),
                                (old20, (-5.518, 1.006), 'GND', '24')):
    p.SetFPRelativePosition(VECTOR2I(FromMM(x), FromMM(y)))
    p.SetSize(pcbnew.PADSTACK.ALL_LAYERS, VECTOR2I(FromMM(2.5), FromMM(1.1)))
    p.SetOrientationDegrees(u1.GetOrientationDegrees())   # long side along local x
    p.SetShape(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.PAD_SHAPE_ROUNDRECT)
    p.SetNumber(num)
    p.SetNet(net(netname))
u1.SetValue('XIAO nRF52840 Plus')
u1.SetLibDescription('Seeed XIAO nRF52840 Plus: 14 edge + 9 half-pitch castellations, '
                     'RST/GND test points, BAT/GND underside pads (Seeed land pattern)')

for p in u1.Pads():
    if p.GetNetname() in OLD:
        p.SetNetCode(0)
for num, name in PINS.items():
    hits = [p for p in u1.Pads() if p.GetNumber() == num]
    assert len(hits) == 1, (num, len(hits))
    hits[0].SetNet(net(name))

# --- 2. re-net the matrix ---------------------------------------------------------------------
THUMB = {'T0': (3, 3), 'T1': (3, 4)}          # label -> (row, col)
for r, f in fps.items():
    if not (r.startswith('SW') and r[2:].isdigit()):
        continue
    n = r[2:]
    lab = f.GetValue()
    row, col = THUMB.get(lab) or (int(lab[3]), int(lab[1]))
    hs, d = fps['HS' + n], fps['D' + n]
    hs_line = [p for p in hs.Pads() if p.GetNetname() in OLD]
    assert hs_line and all(p.GetNetname().startswith('P') for p in hs_line), r
    for p in hs_line:
        p.SetNet(net(f'COL{col}'))
    cath = [p for p in d.Pads() if p.GetNetname() in OLD]
    assert cath and all(p.GetNetname().startswith('C') for p in cath), r
    for p in cath:
        p.SetNet(net(f'ROW{row}'))

left = [f'{f.GetReference()}:{p.GetNumber()}' for f in b.GetFootprints() for p in f.Pads()
        if p.GetNetname() in OLD and f.GetReference() not in BAW]
assert not left, left

# --- 3. remove the BAW56s and the old charlieplex copper (last: removing items invalidates
#        the SWIG pad proxies of this session) -------------------------------------------------
for r in BAW:
    b.Remove(fps.pop(r))
for t in list(b.GetTracks()):
    if t.GetNetname() in OLD:
        b.Remove(t)
b.BuildConnectivity()
pcbnew.SaveBoard(path, b)
print(half, 'ok')
