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

The solder pads are exactly as they were in `a2bec11`:

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

The battery routing around the pads is unchanged from v4.

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
