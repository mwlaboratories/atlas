#!/usr/bin/env bash
# Build the ESB split firmware: a USB dongle (the central, runs the keymap,
# carries the strata layer + battery reporter) and two peripheral halves that
# reach it over Enhanced ShockBurst instead of BLE.
#
#   ./build-firmware.sh                  # old_prototype (the breakout PCB)
#   ./build-firmware.sh atlas            # the v4.x PCB
#   ADS1220=1 ./build-firmware.sh atlas  # v4.x PCB with the ADS1220s fitted
#
# Out: firmware/<keyboard>_{dongle,left,right}.uf2. Flash the dongle and both
# halves; the halves pair to the dongle by ESB address, no bonding step.
set -euo pipefail
cd "$(dirname "$0")"

kb="${1:-old_prototype}"
case "$kb" in old_prototype|atlas) ;; *) echo "unknown keyboard: $kb" >&2; exit 1 ;; esac

extra=""
if [ "${ADS1220:-0}" = 1 ]; then
  # ADS1220 strain-gauge pointing, per half, for the v4.x PCB. Not written yet:
  # the parts aren't fitted. The drivers are already in west.yml
  # (ads1220-zephyr-module, analog-axis-hires-input-zephyr-module); template:
  # badjeff/zmk-config@esb-shield-only donki36_tpoint0_ads1220.dtsi, pins from
  # U2 in pcb/atlas-*.kicad_pcb. Our own bench proof: tag bench/ads1220.
  # Put ads1220.conf + ads1220.overlay in config/boards/shields/atlas/.
  dir="config/boards/shields/atlas"
  if [ "$kb" != atlas ] || [ ! -f "$dir/ads1220.conf" ]; then
    echo "ADS1220=1: no ADS1220 support for '$kb' yet (needs $dir/ads1220.{conf,overlay})" >&2
    exit 1
  fi
  extra="-DEXTRA_CONF_FILE=$PWD/$dir/ads1220.conf -DEXTRA_DTC_OVERLAY_FILE=$PWD/$dir/ads1220.overlay"
fi

nix develop -c bash -euo pipefail -c '
  kb="$1"; extra="$2"
  export ZEPHYR_BASE="$PWD/zephyr"
  [ -d .west ] || west init -l config   # fresh clone: make this a west workspace
  west update --fetch-opt=--filter=blob:none
  west zephyr-export
  mkdir -p firmware
  for part in dongle left right; do
    # ADS1220 lives on the halves; the dongle never gets it.
    [ "$part" = dongle ] && x="" || x="$extra"
    west build -p -s zmk/app -b "xiao_ble//zmk" -d "build/${kb}_$part" -- \
      -DSHIELD="${kb}_$part" -DZMK_CONFIG="$PWD/config" $x
    cp "build/${kb}_$part/zephyr/zmk.uf2" "firmware/${kb}_$part.uf2"
  done
' _ "$kb" "$extra"
echo "→ firmware/${kb}_{dongle,left,right}.uf2"
