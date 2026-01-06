#!/usr/bin/env bash
# ZMK Trackpoint Debug Logger - Continuous, auto-reconnect

DEVICE="${1:-/dev/ttyACM1}"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# Filter noisy output
FILTER='(kscan_matrix|split_peripheral_listener|zmk_physical_layouts_kscan|zmk_usb_get_conn_state|bvd_sample_fetch|Setting BAS GATT|<dbg>.*ps2_uart|<dbg>.*data_queue|split_svc_pos_state|split_input_events_ccc|security_changed|<dbg>.*zmk_mouse_ps2_activity|^\s*$|^$)'

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

LOG="$LOG_DIR/debug-$(date +%Y%m%d-%H%M%S).log"

show_timing_report() {
    local log="$1"
    echo -e "\n${GREEN}=== Timing Report ===${NC}"

    # First error
    first_err=$(grep -m1 "<err>" "$log" 2>/dev/null)
    if [ -n "$first_err" ]; then
        time=$(echo "$first_err" | grep -oE '\[([0-9:.,]+)\]' | head -1)
        echo -e "${RED}First error:${NC} $time"
    fi

    # Self-test
    selftest=$(grep -m1 "0xaa" "$log" 2>/dev/null)
    if [ -n "$selftest" ]; then
        time=$(echo "$selftest" | grep -oE '\[([0-9:.,]+)\]' | head -1)
        echo -e "${GREEN}Self-test pass:${NC} $time"
    fi

    # Data reporting enabled
    datarep=$(grep -m1 "Successfully activated ps2 callback" "$log" 2>/dev/null)
    if [ -n "$datarep" ]; then
        time=$(echo "$datarep" | grep -oE '\[([0-9:.,]+)\]' | head -1)
        echo -e "${GREEN}Data reporting:${NC} $time"
    fi

    # First mouse movement
    firstmove=$(grep -m1 "mouse activity" "$log" 2>/dev/null)
    if [ -n "$firstmove" ]; then
        time=$(echo "$firstmove" | grep -oE '\[([0-9:.,]+)\]' | head -1)
        echo -e "${GREEN}First movement:${NC} $time"
    fi

    # T+G triggered reset (looks for "RESET PIN HIGH" from debug_reset_toggle.c)
    tg_reset=$(grep -m1 "RESET PIN HIGH" "$log" 2>/dev/null)
    if [ -n "$tg_reset" ]; then
        time=$(echo "$tg_reset" | grep -oE '\[([0-9:.,]+)\]' | head -1)
        echo -e "${YELLOW}T+G Reset triggered:${NC} $time"
    fi

    # Count T+G reset events
    resets=$(grep -c "RESET PIN HIGH\|RESET TOGGLE COMPLETE" "$log" 2>/dev/null || echo 0)

    # Count issues
    errs=$(grep -c "<err>" "$log" 2>/dev/null || echo 0)
    warns=$(grep -c "<wrn>" "$log" 2>/dev/null || echo 0)
    framing=$(grep -c "Framing error" "$log" 2>/dev/null || echo 0)
    dropped=$(grep -c "dropped" "$log" 2>/dev/null || echo 0)
    queue_full=$(grep -c "queue full" "$log" 2>/dev/null || echo 0)
    misalign=$(grep -c "out of aligment" "$log" 2>/dev/null || echo 0)

    echo -e "\n${YELLOW}Issues:${NC}"
    echo "  Errors: $errs | Warnings: $warns"
    echo "  Framing errors: $framing | Dropped: $dropped"
    echo "  Queue full: $queue_full | Misaligned: $misalign"
    echo "  TP Resets detected: $resets"
}

trap "show_timing_report '$LOG'; exit 0" INT TERM

echo "Logging to $LOG (Ctrl+C to stop)"
echo "Auto-reconnects on reset. Watching for T+G reset combo..."
echo ""

while true; do
    if [ -e "$DEVICE" ]; then
        echo "=== $(date): Connected ===" | tee -a "$LOG"
        cat "$DEVICE" 2>/dev/null | grep --line-buffered -vE "$FILTER" | while read line; do
            echo "$line" | tee -a "$LOG"
            # Check for T+G reset toggle events
            if echo "$line" | grep -q "RESET PIN HIGH"; then
                echo -e "${RED}>>> T+G RESET TRIGGERED <<<${NC}"
            elif echo "$line" | grep -q "RESET TOGGLE COMPLETE"; then
                echo -e "${GREEN}>>> RESET COMPLETE <<<${NC}"
            # Check for trackpoint self-test response
            elif echo "$line" | grep -qE "0xaa|self.test"; then
                echo -e "${YELLOW}^^^ Self-test ^^^${NC}"
            fi
        done
        echo "=== $(date): Disconnected ===" | tee -a "$LOG"
    fi
    sleep 0.5
done
