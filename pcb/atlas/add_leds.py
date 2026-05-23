#!/usr/bin/env python3
"""Drop a reverse-mount SK6812MINI-E LED under every switch.

Uses KiCad's `pcbnew` Python API so the parent/child transform system does
its job:
  - Footprint has (position, rotation); pads/silkscreen are children with
    LOCAL coords; KiCad applies the footprint transform when rendering.
  - `Flip()` properly mirrors children for B.Cu placement.

Sequence is the one from kbplacer's `set_side()` — battle-tested for KiCad 9+:

    1. SetPosition(target)             # footprint anchor in PCB coords
    2. SetOrientation(switch_rotation)
    3. board.Add(led)
    4. Flip(position, False)           # to B.Cu (mirrors layers + pad geom)
    5. Rotate(position, 180°)          # KiCad 9+ compensation

Idempotent: removes existing SK6812MINI-E footprints first.

Usage:
    nix-shell -p kicad python3 --run '
        KICAD_PY=$(find /nix/store -name "pcbnew.py" 2>/dev/null | grep "10\\." | head -1)
        PYTHONPATH="$(dirname $KICAD_PY)" python3 pcb/atlas/add_leds.py
    '
"""
import math
import sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent

LED_LIB_PATH = (
    "/nix/store/9gfz6b3g3cnz7r38k7bik6xsyj3izvll-kicad-footprints-ab2f97eaa2"
    "/share/kicad/footprints/LED_SMD.pretty"
)
LED_FP_NAME = "LED_SK6812MINI-E_3.2x2.8mm_P1.5mm_ReverseMount"

# Offset from switch center, in the SWITCH's local frame (mm).
# Choc V2 datasheet: LED window is 4.93 mm along +Y in the switch's local
# frame. The offset is rotated into PCB-global coords below using the
# switch's orientation, so it stays aligned for rotated thumb keys.
DEFAULT_OFFSET_X = 0.0
DEFAULT_OFFSET_Y = 4.93


def is_switch(fp) -> bool:
    return fp.GetFPID().GetLibItemName().c_str().startswith("SW_")


def is_our_led(fp) -> bool:
    return "LED_SK6812MINI-E" in fp.GetFPID().GetLibItemName().c_str()


def add_leds(pcb_path: Path,
             offset_x: float = DEFAULT_OFFSET_X,
             offset_y: float = DEFAULT_OFFSET_Y) -> None:
    board = pcbnew.LoadBoard(str(pcb_path))

    # 1) Idempotent: remove pre-existing LED footprints (collect then remove
    #    so we don't mutate while iterating).
    to_remove = [fp for fp in board.GetFootprints() if is_our_led(fp)]
    for fp in to_remove:
        board.Remove(fp)

    # 2) Snapshot the switch list BEFORE we start adding LEDs.
    switches = [fp for fp in board.GetFootprints() if is_switch(fp)]
    if not switches:
        raise SystemExit("no SW_* footprints — nothing to anchor LEDs to")

    for i, sw in enumerate(switches, start=1):
        sw_pos = sw.GetPosition()
        sw_orient = sw.GetOrientation()
        sw_deg = sw_orient.AsDegrees()

        # LED target = switch position + (offset rotated by switch rotation).
        # KiCad rotation is CCW viewed from F.Cu (top), which is math CW with
        # +Y pointing DOWN in PCB coords. So we use the math-CW formula
        # (flip the sin signs vs. a standard math-CCW matrix). For 180°
        # rotation both formulas give the same answer — that's why the bug
        # was only visible on rotated thumb keys.
        a = math.radians(sw_deg)
        dx_mm = offset_x * math.cos(a) + offset_y * math.sin(a)
        dy_mm = -offset_x * math.sin(a) + offset_y * math.cos(a)
        led_pos = pcbnew.VECTOR2I(
            sw_pos.x + pcbnew.FromMM(dx_mm),
            sw_pos.y + pcbnew.FromMM(dy_mm),
        )

        # Load a fresh copy of the LED footprint from its library.
        led = pcbnew.FootprintLoad(LED_LIB_PATH, LED_FP_NAME)
        if led is None:
            raise SystemExit(f"could not load {LED_FP_NAME}")
        led.SetReference(f"LED{i}")

        # Add to board first.
        led.SetPosition(led_pos)
        board.Add(led)

        # Flip to B.Cu — handles layer/pad mirroring and 3D model rotation.
        led.Flip(led_pos, False)

        # NOW set the final orientation (overrides whatever Flip did to it).
        # KiCad applies this to all children via the parent/child transform.
        led.SetOrientation(sw_orient)

        # Hide silkscreen labels (clutter on a 34-LED keyboard PCB).
        led.Reference().SetVisible(False)
        led.Value().SetVisible(False)

    board.Save(str(pcb_path))
    print(f"placed {len(switches)} LED footprints (offset {offset_x:+.2f} {offset_y:+.2f} mm switch-local) → {pcb_path.name}")


if __name__ == "__main__":
    pcb = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "keyboard.kicad_pcb"
    if not pcb.exists():
        raise SystemExit(f"no PCB at {pcb}")
    add_leds(pcb)
