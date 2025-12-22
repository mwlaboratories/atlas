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

**Resolution:** All-in-one trackpoint module works immediately. Previous failures were hardware/wiring issues.

#### What Failed
- TP #1 and #2: Separate trackpoint modules with manual wiring
- Symptoms: Framing errors on every byte, 0xF4 failed all retries
- Cause: Bad solder joints / wiring between TP and MCU

#### What Works
- All-in-one integrated trackpoint module (22 Dec 2025)
- No framing errors, initialization succeeds, movement data flows

### Previous Hypotheses (ruled out)
- ~~SCL GPIO interrupts not firing~~ - writes succeed, problem is on receive
- ~~BT priority conflict~~ - priority settings didn't help
- ~~Blocking write mode~~ - already enabled, didn't help

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
