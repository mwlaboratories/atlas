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

### Trackpoint Status (Dec 2025) - WORKING

**Resolution:** All-in-one trackpoint module works. Reset pin is required for reliable initialization.

#### Critical Requirements
1. **Reset pin MUST be enabled** - without it, trackpoint only initializes when USB logs are actively being read (timing issue)
2. **GPIO_PULL_UP required** on both SCL and SDA lines
3. **Good solder joints** - framing errors indicate bad connections

#### Config Requirements
```dts
/* In mouse_ps2 node */
rst-gpios = <&gpio0 10 GPIO_ACTIVE_HIGH>;  /* NFC2 pin - REQUIRED */

/* In uart_ps2 node */
scl-gpios = <&gpio1 11 (GPIO_ACTIVE_HIGH | GPIO_PULL_UP)>;
sda-gpios = <&gpio1 12 (GPIO_ACTIVE_HIGH | GPIO_PULL_UP)>;
```

#### What Failed
- TP #1 and #2: Separate trackpoint modules with manual wiring
- Re-soldering on original XIAO BLE caused framing errors (board may have been damaged)
- Disabling reset pin caused init timing issues

#### What Works
- All-in-one integrated trackpoint module
- Reset pin on NFC2 (P0.10) for reliable initialization
- Fresh XIAO BLE board if original has issues

#### Debugging Framing Errors
If you see `Framing error (4)` on responses:
1. Check solder joints on CLK (D6) and DATA (D7)
2. Verify GPIO_PULL_UP flags are present
3. Ensure reset pin is enabled
4. Try a different XIAO BLE board

### Previous Hypotheses (ruled out)
- ~~SCL GPIO interrupts not firing~~ - writes succeed, problem is on receive
- ~~BT priority conflict~~ - priority settings didn't help
- ~~Blocking write mode~~ - already enabled, didn't help
- ~~Reset pin optional~~ - actually required for init timing

### Pinctrl Gotcha
UART "off" state must NOT use P0.28 (it's matrix row D2). Use P0.31 instead.

## USB Debugging

For trackpoint debugging, enable on **left half** (direct PS2 logs):
```
CONFIG_ZMK_USB_LOGGING=y
CONFIG_PS2_LOG_LEVEL_DBG=y
CONFIG_INPUT_LOG_LEVEL_DBG=y
CONFIG_UART_LOG_LEVEL_DBG=y
```

Capture boot sequence (reset left half after starting):
```bash
while [ ! -e /dev/ttyACM0 ]; do sleep 0.1; done; timeout 30 cat /dev/ttyACM0 | tee ps2-boot.log
```

Filter for PS2 messages:
```bash
grep -iE "(ps2|mouse|0xf4|0xaa|framing|error)" ps2-boot.log
```

NixOS: Requires `dialout` group + full reboot.
