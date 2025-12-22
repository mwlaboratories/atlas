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

### Current Issue (Dec 2025) - HARDWARE/SOLDER PROBLEM

**Diagnosis:** Bad solder joints on trackpoint causing signal integrity issues.

**Symptoms observed:**
- Self-test (0xAA) passes ✓
- Reset (0xFF) works ✓
- TP sensitivity (0xE2) commands work with retries ✓
- **0xF4 (enable data reporting) fails all 5 retries**
- All received bytes have **"Framing error"** in logs
- Trackpoint responds 0xFE (resend) - it detects corruption too
- When trackpoint touched after partial init: parity errors, resend loops, system crash

**Key log messages:**
```
<wrn> ps2_uart: UART RX detected error for byte 0xfe: Framing error (4)
<wrn> ps2_uart: Write of 0xf4 received error response: 0xfe
<err> zmk: Could not enable data reporting: 4
```

**Root cause:** Electrical signal from trackpoint → MCU is degraded due to poor solder joints. Writes succeed but responses are corrupted. Some commands pass (borderline), 0xF4 consistently fails.

**Solution:** Replace trackpoint with clean solder joints. Consider external 4.7kΩ-10kΩ pull-ups on Clock/Data if internal pull-ups insufficient.

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
