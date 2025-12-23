#!/usr/bin/env bash
# ZMK Trackpoint Debug Logger - Interactive

DEVICE="/dev/ttyACM0"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# Filter for clean output
FILTER='(kscan_matrix|split_peripheral_listener|zmk_physical_layouts_kscan|zmk_usb_get_conn_state|bvd_sample_fetch|Setting BAS GATT|<dbg>.*ps2_uart|<dbg>.*data_queue|split_svc_pos_state|split_input_events_ccc|security_changed|<dbg>.*zmk_mouse_ps2_activity|^\s*$|^$)'

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

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
}

wait_for_device() {
    if [ -e "$DEVICE" ]; then
        echo "Device connected. Press reset on left half..."
        while [ -e "$DEVICE" ]; do sleep 0.1; done
        echo "Disconnected, waiting for boot..."
    else
        echo "Waiting for $DEVICE..."
    fi
    while [ ! -e "$DEVICE" ]; do sleep 0.1; done
    echo "Device found!"
    sleep 0.3
}

# Interactive menu
echo "ZMK Trackpoint Debugger"
echo "======================="
echo ""
echo "1) Boot capture (60s, filtered)"
echo "2) Crash monitor (continuous, auto-reconnect)"
echo "3) Full verbose (60s, everything)"
echo "4) View latest log + timing report"
echo "5) Analyze a log file"
echo ""
read -p "Choice [1-5]: " choice

case "$choice" in
    1)
        LOG="$LOG_DIR/boot-$(date +%Y%m%d-%H%M%S).log"
        echo "Boot capture to $LOG (60s, survives resets)"
        echo "Press Ctrl+C when done."
        trap "show_timing_report '$LOG'; exit 0" INT
        END=$((SECONDS + 60))
        while [ $SECONDS -lt $END ]; do
            if [ -e "$DEVICE" ]; then
                echo "=== $(date +%H:%M:%S): Connected ===" | tee -a "$LOG"
                timeout $((END - SECONDS)) cat "$DEVICE" 2>/dev/null | grep --line-buffered -vE "$FILTER" | cat -s | tee -a "$LOG"
                [ $SECONDS -lt $END ] && echo "=== $(date +%H:%M:%S): Reset detected ===" | tee -a "$LOG"
            fi
            sleep 0.3
        done
        show_timing_report "$LOG"
        ;;
    2)
        LOG="$LOG_DIR/crash-$(date +%Y%m%d-%H%M%S).log"
        echo "Crash monitoring to $LOG (Ctrl+C to stop)"
        echo "Will show timing report on exit."
        trap "show_timing_report '$LOG'; exit 0" INT
        while true; do
            if [ -e "$DEVICE" ]; then
                echo "=== $(date): Connected ===" | tee -a "$LOG"
                cat "$DEVICE" | grep --line-buffered -vE "$FILTER" | cat -s | tee -a "$LOG"
                echo "=== $(date): CRASHED/DISCONNECTED ===" | tee -a "$LOG"
            fi
            sleep 1
        done
        ;;
    3)
        LOG="$LOG_DIR/verbose-$(date +%Y%m%d-%H%M%S).log"
        wait_for_device
        echo "Capturing 60s verbose to $LOG..."
        timeout 60 cat "$DEVICE" | tee "$LOG"
        show_timing_report "$LOG"
        ;;
    4)
        LATEST=$(ls -t "$LOG_DIR"/*.log 2>/dev/null | head -1)
        if [ -z "$LATEST" ]; then
            echo "No logs found"
            exit 1
        fi
        show_timing_report "$LATEST"
        echo -e "\nPress Enter to view log, or Ctrl+C to exit"
        read
        less "$LATEST"
        ;;
    5)
        echo "Available logs:"
        ls -t "$LOG_DIR"/*.log 2>/dev/null | head -10 | nl
        read -p "Enter number: " num
        LOG=$(ls -t "$LOG_DIR"/*.log 2>/dev/null | sed -n "${num}p")
        if [ -n "$LOG" ]; then
            show_timing_report "$LOG"
            echo -e "\nPress Enter to view log"
            read
            less "$LOG"
        else
            echo "Invalid selection"
        fi
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac
