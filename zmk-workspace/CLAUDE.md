# CLAUDE.md

## Project Overview

ZMK firmware workspace for **Atlas** - 34-key (3x5+2) split keyboard with dongle, using XIAO BLE controllers.

## Development

```bash
nix develop              # Enter dev shell (required)
just build all           # Build all targets
just build "left"        # Build specific target
```

## Trackpoint Setup (Left Side)

### Pin Mapping
| Pin | nRF | Use |
|-----|-----|-----|
| D6 | P1.11 | PS/2 Clock (SCL) |
| D7 | P1.12 | PS/2 Data (SDA) - UART RX |

**Important**: D7 must be Data (not Clock) because UART RX samples the Data line.

### Driver
Uses `badjeff/kb_zmk_ps2_mouse_trackpoint_driver` with UART driver (`uart-ps2`).

### Key Config (atlas_left.conf)
```
CONFIG_ZMK_POINTING=y
CONFIG_UART_INTERRUPT_DRIVEN=y
CONFIG_PS2_UART_WRITE_MODE_BLOCKING=y
```

### Current Issue (Dec 2025)
- Trackpoint detected and passes self-test (0xAA)
- **Problem**: "Could not enable data reporting" - 0xF4 command fails with "scl timeout"
- All 5 write retry attempts fail (normal is 1-2 failures then success)
- BT priority settings added but didn't resolve
- No mouse movement data reaching dongle via input-split

### Forum Post Summary
```
Hardware: XIAO BLE (nRF52840), Lenovo trackpoint, 3-piece split (dongle + left + right)
Driver: badjeff/kb_zmk_ps2_mouse_trackpoint_driver (uart-ps2)
Pins: D6=Clock(P1.11), D7=Data(P1.12)

Working: Trackpoint passes self-test (0xAA), device ID read succeeds
Failing: "Could not enable data reporting" - 0xF4 write fails all retries with "scl timeout"

Tried: BT priority shifting, blocking write mode, interrupt priority overrides in DT
Hypothesis: SCL GPIO interrupts not firing in time during write phase
```

### Pinctrl Gotcha
UART "off" state must NOT use P0.28 (it's matrix row D2). Use P0.31 instead.

## USB Debugging

Enable on **dongle only** (avoids multiple /dev/ttyACM devices):
```
CONFIG_ZMK_USB_LOGGING=y
```

Capture logs:
```bash
timeout 15 cat /dev/ttyACM0 | grep -iE "(ps2|mouse|error|input)"
```

NixOS: Requires `dialout` group + full reboot.
