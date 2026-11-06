# Atlas v4.1 review notes

v4.1 changes one thing compared with v4 (`a2bec11`, PCB content from
`9942619`): every via that sat in an SMD pad with paste now sits beside the
pad. PCBWay's CAM review flagged vias in pads because paste can wick into
them; v4.1 fixes the design instead of relying on via-in-pad plugging.

The LiPo connection is unchanged from v4. `BT1` (left and right files) and
`BT1`/`BT101` (panel) are the original solder pads.

All three boards are saved as `(version 20260206) (generator "pcbnew")
(generator_version "10.0")`.

## Battery connection: solder pads, as in v4

A JST-PH header was tried and dropped. Gomar decided after seeing the
S2B-PH-K-S fitment screenshots (below): it does not fit the case. The v4
solder pads are back exactly as they were in `a2bec11`:

- **Footprint:** `atlas20:battery_pads`, value `LiPo 371030`, on B.Cu
- **Pads:** SMD roundrect 2.0 × 3.0 mm, 3.5 mm pitch
- **Silkscreen:** B.SilkS labels `BAT+` / `BAT-` in their v4 positions;
  `LiPo 3.7V` is unchanged

| board / ref | position (mm) | rot | pad 1 | pad 2 |
|---|---|---|---|---|
| left `BT1` | (52.75, 134.849999) | −90° | BAT+ | GND |
| right `BT1` | (139.250001, 134.849999) | −90° | BAT+ | GND |
| panel `BT1` | (52.75, 134.849999) | −90° | BAT+ | GND |
| panel `BT101` | (259.250001, 134.849999) | −90° | R_BAT+ | R_GND |

The battery routing around the pads is also back to v4.

**BOM:** no connector, no LiPo cell and no trackpoint. The cell is wired to
the solder pads by hand.

## Via-in-pad fix per board

The checker (`check_via_in_pad.py`, KiCad 10.0.4 bindings) flags a via when
its drill circle intersects an SMD pad that has paste.

| board | a2bec11 | v4.1 |
|---|---|---|
| left | 53 | 0 |
| right | 45 | 0 |
| panel | 98 | 0 |

**Left.**

- 52 vias moved beside their pads. This includes XIAO `U1` edge pads 3, 4, 5,
  6, 10, 11, 12 and 13 (the pads marked in PCBWay's CAM review), plus the
  hotswap sockets, SOT-23s, 0402s, diodes and the ribbon connector.
- R3 pad 1 (SCLK) was rerouted on B.Cu. That removed 2 vias, deleted 6
  segments and added 2.
- No footprints were moved.

**Right.**

- 45 vias moved beside their pads, including `U1` edge pads 4, 5 and 13.
- `R11` (0402) was shifted by (−0.10, −0.35) mm to make room for its pad 2
  GND via. Three B.Cu track ends followed it.

**Panel.**

- The panel is the left half at offset 0 plus the right half shifted
  +120 mm in x, with refs +100 and nets prefixed `R_` (`R11` → `R111`).
- Each half's changes were derived from the base vs final half board and
  applied to the panel.
- Each panel half was then checked for an exact match against its half
  board: tracks and vias down to the nm, and footprints including FPID,
  position, rotation, layer and pad nets.

**DRC** (kicad-cli 10.0.4, `--refill-zones`): the same violation set as at
`a2bec11`, with 0 unconnected. The existing violations are library footprint
issues, NPTH inside courtyard, and courtyard overlaps. There are no copper
zones; only the keepout areas around the holes exist.

## Record: why the JST-PH connector was dropped (historical)

This section records the measurements from the trial. **None of it applies
to v4.1 as built.**

**The trial part.** It was `Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal`
(JST S2B-PH-K-S): THT, side entry, body on B.Cu, with the plug entering from
the battery bay.

**How the case CAD was measured.**

- The case CAD (`cad/atlas-*-{housing,bottomplate,switchplate}.step`) is in
  cm and has its own origin. Its M2 holes line up exactly with PCB holes
  H2–H7 when:
  - left: `x_kicad = X_cad + 120`, `y_kicad = 120 − Y_cad`
  - right: `x_kicad = X_cad`, `y_kicad = 120 − Y_cad`
  - Z: the PCB bottom face is at Z_cad = −3.805 mm.
- The STEP files were converted to meshes through `kicad-cli pcb export glb`
  and measured by ray-casting in headless Blender 5.1.

**Measured.** z = 0 is the PCB bottom face.

| quantity | value |
|---|---|
| Bottomplate below the PCB, total thickness | 2.40 mm |
| Pocket under the BT1 pads, depth | 1.70 mm |
| Top side: only free window near BT1 | x 52–57, y ≥ 134.5, 2.20 mm high |
| S2B-PH-K-S body depth below the PCB | 4.89 mm (3.19 mm into the pocket floor, 2.49 mm through the plate's outer face) |
| Pin tail to housing underside (untrimmed) | 0.49 mm |
| Body front past board edge | 2.5 mm into the battery bay |

**The other JST-PH variants.**

- They are taller: about 5.5 mm (SMD side entry) and 6.0 mm (vertical).
- The SMD side-entry part is also 8.2 mm wide, against 6.8 mm free between
  the H3 screw boss and the board edge.
- So no JST-PH header fits the case as designed.

**Fitment screenshots** (in this folder; z = 0 is the PCB bottom face):

- `v41-fit-left-current-BT1-section.png`: v4 as built, with the 1.70 mm pocket
  under the solder pads
- `v41-fit-left-S2B-PH-K-section-pin1.png`: trial header on the left, through
  pin 1. Shows the header through the bottomplate and the pin tail against
  the housing.
- `v41-fit-left-S2B-PH-K-section-x53.25.png`: trial header on the left,
  across both pins
- `v41-fit-right-S2B-PH-K-section-pin1.png`: trial header on the right,
  through pin 1
- `v41-fit-right-S2B-PH-K-section-x138.75.png`: trial header on the right,
  across both pins
