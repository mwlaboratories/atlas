#!/usr/bin/env bash
# Build the ESB split firmware: a USB dongle (the central, runs the keymap,
# carries the strata layer + battery reporter) and two peripheral halves that
# reach it over Enhanced ShockBurst instead of BLE.
#
#   ./build-firmware.sh                  # old_prototype (the breakout PCB)
#   ./build-firmware.sh atlas            # the v4.x PCB
#   ADS1220=1 ./build-firmware.sh atlas  # v4.x PCB with the ADS1220 trackpoints
#
# Out: firmware/<keyboard>_{dongle,left,right}.uf2. Flash the dongle and both
# halves; the halves pair to the dongle by ESB address, no bonding step.
set -euo pipefail
cd "$(dirname "$0")"

kb="${1:-old_prototype}"
case "$kb" in old_prototype|atlas) ;; *) echo "unknown keyboard: $kb" >&2; exit 1 ;; esac

ads1220="${ADS1220:-0}"
if [ "$ads1220" = 1 ]; then
  # ADS1220 strain-gauge trackpoint, one per half, for the v4.x PCB. Each half
  # gets ads1220.conf + its own ads1220_<half>.overlay (the pins differ per
  # half; the shared part is ads1220.dtsi) from config/boards/shields/atlas/.
  # Drivers: ads1220-zephyr-module + analog-axis-hires-input-zephyr-module in
  # west.yml. Register values: our bench proof, tag bench/ads1220.
  if [ "$kb" != atlas ]; then
    echo "ADS1220=1: only the atlas PCB carries an ADS1220, not '$kb'" >&2
    exit 1
  fi
fi

nix develop -c bash -euo pipefail -c '
  kb="$1"; ads1220="$2"
  export ZEPHYR_BASE="$PWD/zephyr"
  [ -d .west ] || west init -l config   # fresh clone: make this a west workspace
  west update --fetch-opt=--filter=blob:none
  west zephyr-export
  mkdir -p firmware
  dir="$PWD/config/boards/shields/$kb"
  for part in dongle left right; do
    # The ADS1220s sit on the halves. The dongle is the same build either way:
    # it always listens for their pointer events.
    extra=()
    if [ "$ads1220" = 1 ] && [ "$part" != dongle ]; then
      extra=("-DEXTRA_CONF_FILE=$dir/ads1220.conf"
             "-DEXTRA_DTC_OVERLAY_FILE=$dir/ads1220_$part.overlay")
    fi
    west build -p -s zmk/app -b "xiao_ble//zmk" -d "build/${kb}_$part" -- \
      -DSHIELD="${kb}_$part" -DZMK_CONFIG="$PWD/config" "${extra[@]}"
    cp "build/${kb}_$part/zephyr/zmk.uf2" "firmware/${kb}_$part.uf2"
  done
' _ "$kb" "$ads1220"
echo "→ firmware/${kb}_{dongle,left,right}.uf2"
