#!/usr/bin/env bash
# ZMK Trackpoint Debug Logger - Auto-detect port, capture from boot

LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# Filter noisy output
FILTER='(kscan_matrix|split_peripheral_listener|zmk_physical_layouts_kscan|zmk_usb_get_conn_state|bvd_sample_fetch|Setting BAS GATT|<dbg>.*ps2_uart|<dbg>.*data_queue|split_svc_pos_state|split_input_events_ccc|security_changed|<dbg>.*zmk_mouse_ps2_activity|^\s*$|^$)'

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
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

    # T+G triggered reset
    tg_reset=$(grep -m1 "RESET PIN HIGH" "$log" 2>/dev/null)
    if [ -n "$tg_reset" ]; then
        time=$(echo "$tg_reset" | grep -oE '\[([0-9:.,]+)\]' | head -1)
        echo -e "${YELLOW}T+G Reset triggered:${NC} $time"
    fi

    # Count issues
    resets=$(grep -c "RESET PIN HIGH\|RESET TOGGLE COMPLETE" "$log" 2>/dev/null || echo 0)
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
    echo -e "\nLog saved: $log"
}

# --- Step 1: Detect port by disconnect/reconnect ---
echo -e "${CYAN}=== ZMK Debug Logger ===${NC}"
echo ""

# Get initial port count
initial_ports=$(ls /dev/ttyACM* 2>/dev/null | sort)
initial_count=$(echo "$initial_ports" | grep -c . 2>/dev/null || echo 0)

echo -e "${YELLOW}Disconnect${NC} the keyboard USB cable..."
echo -n "Waiting"

# Wait for port count to decrease (device unplugged)
while true; do
    current_ports=$(ls /dev/ttyACM* 2>/dev/null | sort)
    current_count=$(echo "$current_ports" | grep -c . 2>/dev/null || echo 0)
    if [ "$current_count" -lt "$initial_count" ] || [ -z "$current_ports" ]; then
        break
    fi
    echo -n "."
    sleep 0.3
done
echo " unplugged!"

# Remember state after unplug
unplugged_ports=$(ls /dev/ttyACM* 2>/dev/null | sort)

echo -e "${GREEN}Reconnect${NC} the keyboard USB cable..."
echo -n "Waiting"

# Wait for a NEW port to appear
while true; do
    current_ports=$(ls /dev/ttyACM* 2>/dev/null | sort)
    # Find port that wasn't there when unplugged
    for port in $current_ports; do
        if ! echo "$unplugged_ports" | grep -q "^${port}$"; then
            DEVICE="$port"
            break 2
        fi
    done
    echo -n "."
    sleep 0.3
done
echo " connected!"
echo -e "${GREEN}Detected:${NC} $DEVICE"
echo ""

# --- Step 2: Wait for reset to capture boot ---
echo -e "${YELLOW}Press the RESET button${NC} on the XIAO BLE to capture boot messages..."
echo -n "Waiting for device to disappear"

# Wait for device to disappear (reset pressed)
while [ -e "$DEVICE" ]; do
    echo -n "."
    sleep 0.2
done
echo " reset detected!"

# Wait for device to reappear
echo -n "Waiting for boot"
while [ ! -e "$DEVICE" ]; do
    echo -n "."
    sleep 0.1
done
echo ""
echo -e "${GREEN}Device ready!${NC}"
echo ""

# --- Step 3: Start logging ---
cleanup() {
    echo ""
    echo "=== $(date): Stopped ===" >> "$LOG"
    show_timing_report "$LOG"
    exit 0
}
trap cleanup INT TERM

echo -e "Logging to ${CYAN}$LOG${NC}"
echo -e "Press ${RED}Ctrl+C${NC} to stop"
echo ""

echo "=== $(date): Boot capture started ===" >> "$LOG"

# Keep logging forever, reconnect if device drops
while true; do
    if [ -e "$DEVICE" ]; then
        while IFS= read -r line; do
            # Skip filtered lines
            if echo "$line" | grep -qE "$FILTER"; then
                continue
            fi
            # Log and display
            echo "$line" | tee -a "$LOG"
            # Highlight important events
            if echo "$line" | grep -q "Crash recovery:"; then
                echo -e "${RED}>>> CRASH RECOVERY <<<${NC}"
            elif echo "$line" | grep -q "RESET PIN HIGH"; then
                echo -e "${RED}>>> T+G RESET TRIGGERED <<<${NC}"
            elif echo "$line" | grep -q "RESET TOGGLE COMPLETE"; then
                echo -e "${GREEN}>>> RESET COMPLETE <<<${NC}"
            elif echo "$line" | grep -qE "0xaa|self.test"; then
                echo -e "${YELLOW}^^^ Self-test ^^^${NC}"
            fi
        done < "$DEVICE" 2>/dev/null
        echo -e "${YELLOW}--- Device disconnected, waiting... ---${NC}"
        echo "=== $(date): Reconnecting... ===" >> "$LOG"
    fi
    sleep 0.5
done
