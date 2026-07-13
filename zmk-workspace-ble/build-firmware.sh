#!/usr/bin/env bash
# Build atlas firmware for both halves, with the strata layer reporter
# (zmk-strata/) baked into the central. Run from the atlas devenv via
# `firmware`, or directly: ./build-firmware.sh
#
# zmk-raw-hid is pulled by config/west.yml; zmk-strata is a local module here,
# so it's added via ZMK_EXTRA_MODULES. Uses the zmk-nix dev shell (west + SDK).
set -euo pipefail
cd "$(dirname "$0")"

nix develop -c bash -euo pipefail -c '
  export ZEPHYR_BASE="$PWD/zephyr"
  west update                       # fetch zmk, zmk-raw-hid, trackpoint driver
  west zephyr-export
  mkdir -p firmware
  for part in left right; do
    west build -p -s zmk/app -b "xiao_ble//zmk" -d "build/$part" -- \
      -DSHIELD="atlas_$part" \
      -DZMK_CONFIG="$PWD/config" \
      -DZMK_EXTRA_MODULES="$PWD/zmk-strata"
    cp "build/$part/zephyr/zmk.uf2" "firmware/atlas_$part.uf2"
  done
'
echo "→ firmware/atlas_{left,right}.uf2  (left carries the strata layer reporter)"
