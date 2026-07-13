#!/usr/bin/env bash
# Build atlas firmware for both halves, with the strata layer reporter baked
# into the central. Run from the atlas devenv via `firmware`, or directly:
# ./build-firmware.sh
#
# All modules — zmk-raw-hid and zmk-strata included — are pulled by
# config/west.yml, so our builds use the same published zmk-strata every
# zmk-config does. Uses the zmk-nix dev shell (west + SDK).
#
# Hacking on the module itself? Point the build at a local checkout instead of
# the west-cloned one by adding to the west build flags below:
#   -DZMK_EXTRA_MODULES="$HOME/Documents/repos/zmk-strata"
# (and push to GitHub when done — west.yml is what ships).
set -euo pipefail
cd "$(dirname "$0")"

nix develop -c bash -euo pipefail -c '
  export ZEPHYR_BASE="$PWD/zephyr"
  west update    # fetch zmk, zmk-raw-hid, zmk-strata, trackpoint driver
  west zephyr-export
  mkdir -p firmware
  for part in left right; do
    west build -p -s zmk/app -b "xiao_ble//zmk" -d "build/$part" -- \
      -DSHIELD="atlas_$part" \
      -DZMK_CONFIG="$PWD/config"
    cp "build/$part/zephyr/zmk.uf2" "firmware/atlas_$part.uf2"
  done
'
echo "→ firmware/atlas_{left,right}.uf2  (left carries the strata layer reporter)"
