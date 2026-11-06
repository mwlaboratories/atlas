# Atlas v4.1 review notes

v4.1 makes two changes to v4 (`a2bec11`, PCB content from `9942619`):

- It moves every via out of SMD pads that have paste.
- It replaces the LiPo solder pads `BT1`/`BT101` with a board-mounted
  JST-PH 2.00 mm 2-pin male header. The female plug stays on the cell.

All three boards were saved as `(version 20260206) (generator "pcbnew")
(generator_version "10.0")`.

## Battery connector: decision (final)

Gomar approved this on 2026-11-06, including the 2.5 mm overhang into the
battery bay and the bottomplate change it requires.

| item | choice |
|---|---|
| Footprint | `Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal` (KiCad 10 library) |
| Part | JST **S2B-PH-K-S**, PH 2.00 mm, 2-pin |
| Mounting | **THT**, body on **B.Cu** (bottom side) |
| Orientation | **Side entry**: the plug enters parallel to the board, from the battery bay |
| Pins | pad 1 = BAT+, pad 2 = GND; the footprint has no MP pads |
| Value | `S2B-PH-K-S`. The `LiPo 3.7V` B.SilkS text is kept. |

### Placements

The footprint origin is pad 1.

| board / ref | pad 1 (x, y) mm | rot | pad 1 | pad 2 at | plug enters from |
|---|---|---|---|---|---|
| left `BT1` | (53.25, 135.75) | −90° | BAT+ | (53.25, 137.75) GND | +x |
| right `BT1` | (138.75, 137.75) | 90° | BAT+ | (138.75, 135.75) GND | −x |
| panel `BT1` | (53.25, 135.75) | −90° | BAT+ | (53.25, 137.75) GND | +x |
| panel `BT101` | (258.75, 137.75) | 90° | R_BAT+ | (258.75, 135.75) R_GND | −x |

A part cannot be mirrored. On the right half the plug enters from the other
side, so pin 1 is the lower pin. The B.SilkS labels `BAT+` and `BAT-` were
moved next to the correct pins:

- left: x = 49.8
- right: x = 142.3
- panel right half: x = 262.3

## How it was measured

The case CAD (`cad/atlas-{left,right}-{housing,bottomplate,switchplate}.step`)
is in cm and has its own origin. Its M2 holes and the three switchplate holes
line up exactly with PCB holes H2–H7 when:

- left: `x_kicad = X_cad + 120`, `y_kicad = 120 − Y_cad`
- right: `x_kicad = X_cad`, `y_kicad = 120 − Y_cad`
- Z: the PCB bottom face is at Z_cad = −3.805 mm. The bottomplate top rests
  there and the switchplate underside is at the PCB top.

Blender 5.1 here has no STEP importer. Each CAD STEP was attached as a 3D
model to a scratch footprint at the registered offset, the scratch board was
exported with `kicad-cli pcb export glb`, and the result was measured in
headless Blender. Measurements are exact vertical ray-casts on the meshes.

## Clearances

These are the left half; the right half is a mirror and measures the same.
z = 0 is the PCB bottom face.

| quantity | value |
|---|---|
| Bottomplate below the PCB, total thickness | 2.40 mm |
| Existing pocket under BT1 (x 49.5–57, y 134–140.5), depth | 1.70 mm |
| Battery bay (the PCB notch, x 57–103), floor below PCB plane | 2.15 mm |
| Top side: only free window near BT1 | x 52.0–57.0, y ≥ 134.5, 2.20 mm high |
| H3 screw boss (bottomplate counterbore r = 2.2 mm at (54, 131)) | reaches y = 133.2 |
| Header body depth below the PCB | 4.89 mm |
| Header body vs pocket floor / plate outer face | 3.19 mm / 2.49 mm interference |
| Pin tail above PCB top (untrimmed) | 1.71 mm (z +3.31) |
| Pin tail to housing underside (z +3.80) | 0.49 mm |
| Pads to switchplate rib (plate rests on the PCB for x < 52) | 0.375 mm |
| Body to H3 boss / to board edge (y) | 0.6 mm / 0.3 mm |
| Body front past board edge | 2.5 mm (left x = 59.5, right x = 132.5) |

## Why S2B-PH-K

No JST-PH header fits the existing bottomplate. The body heights are:

- S2B-PH-K: 4.85 mm (measured from the KiCad model)
- S2B-PH-SM4-TB: about 5.5 mm (distributor figure)
- B2B-PH-K, vertical: 6.0 mm

The free depth is 1.70 mm in the pocket and 2.40 mm through the whole plate.
Since the plate has to change in every case, the choice came down to area and
the top side:

- **Side entry rather than vertical.** It is the lowest body. The lead stays
  in the PCB plane and runs straight out of the battery bay. A vertical
  header with its mated plug needs about 8 mm below the board.
- **THT rather than SMD.** The SMD version (S2B-PH-SM4-TB) is 8.2 mm wide
  across its mounting tabs. Between the H3 screw boss and the board edge there
  is only 6.8 mm. The THT body is 5.9 mm wide.
- **Where the THT pins go.** The pin tails come out on the top side, and the
  only place with room for them is the x 52–57 window. That fixes the pins at
  x = 53.25 (left) and 138.75 (right), so the body front overhangs the board
  edge by 2.5 mm into the battery bay. The mated plug sits fully in the bay.

## For Gomar: case changes

- **Bottomplate depth.** Under the header, provide at least **5.2 mm** below
  the PCB bottom face (4.89 mm body plus about 0.3 mm). That is about
  3.5 mm deeper than the current 1.70 mm pocket and about 2.8 mm past the
  plate's current outer face.
  - Header outline: left x 51.9–59.5, right x 132.5–140.1, both
    y 133.8–139.7.
  - Leave room in front of the header for the PHR-2 plug (about 4.5 mm) and
    the lead bend, in the battery bay.
- **Pin tails.** Trim them to about 1.0 mm above the PCB after soldering.
  Untrimmed they clear the housing by only 0.49 mm.

## BOM and assembly

- **BOM:** the connector only: JST **S2B-PH-K-S** (`BT1`, `BT101` on the
  panel). No LiPo cell and no trackpoint.
- **Assembly:** it is a THT part, so it needs PCBWay THT assembly or hand
  soldering.

## Via-in-pad fix per board

The checker (`check_via_in_pad.py`) flags a via when its drill circle
intersects an SMD pad that has paste. Counts at a2bec11 → v4.1:

| board | base hits | v4.1 hits | what changed |
|---|---|---|---|
| left | 53 | 0 | See below |
| right | 45 | 0 | See below |
| panel | 98 | 0 | Both halves replicated from the final half boards |

**Left.**

- 52 vias moved beside their pads. This includes XIAO `U1` edge pads 3, 4, 5,
  6, 10, 11, 12 and 13 (Kelly's markers), plus the hotswap sockets, SOT-23s,
  0402s, diodes and the ribbon connector.
- R3 pad 1 (SCLK) was rerouted on B.Cu. That removed 2 vias, deleted 6
  segments and added 2.
- No footprints were moved.

**Right.**

- 45 vias moved beside their pads, including `U1` edge pads 4, 5 and 13.
- `R11` (0402) was shifted by (−0.10, −0.35) mm to make room for its pad 2
  GND via. Three B.Cu track ends followed it.

**Panel.**

- The left half is at offset 0. The right half is shifted +120 mm in x, with
  refs +100 and nets prefixed `R_` (`R11` → `R111`, `BT1` → `BT101`).
- The change lists were derived from base vs final half. They match the agents'
  move tables item for item:
  - left: −122 / +170 items
  - right: −94 / +142 items
- Each panel half was checked for an exact match against its half board:
  tracks and vias down to the nm, and footprints including FPID, position,
  rotation, layer and pad nets.

**DRC** (kicad-cli 10.0.4, `--refill-zones`). The panel gives 268 violations
vs 268 at base, of the same types, with none new or gone and 0 unconnected.
The violations are pre-existing: library footprint issues, NPTH inside
courtyard, and courtyard overlaps. There are no copper zones; only the
keepout areas around the holes exist.

## Screenshots (in this folder)

z = 0 is the PCB bottom face in each image.

- `v41-fit-left-current-BT1-section.png`: v4 as it is, with the 1.70 mm pocket
  under the old pads
- `v41-fit-left-S2B-PH-K-section-pin1.png`: final left, through pin 1. Shows
  the header against the bottomplate, the pin tail against the housing, and
  the switchplate rib.
- `v41-fit-left-S2B-PH-K-section-x53.25.png`: final left, across both pins
- `v41-fit-right-S2B-PH-K-section-pin1.png`: final right, through pin 1
- `v41-fit-right-S2B-PH-K-section-x138.75.png`: final right, across both pins
