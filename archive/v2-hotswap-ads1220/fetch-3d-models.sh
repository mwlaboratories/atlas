#!/usr/bin/env bash
# Archived copy of the devenv command `fetch-3d-models`.
# It is not registered in devenv.nix. Opening the dev shell does not run it.
# Models are written under this folder's pcb/kicad/3d-models/, for this board only.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x tools/.venv/bin/easyeda2kicad ]; then
  echo "→ creating venv + installing easyeda2kicad"
  python3 -m venv tools/.venv
  tools/.venv/bin/pip install -q easyeda2kicad
fi
e2k="$PWD/tools/.venv/bin/easyeda2kicad"
mkdir -p pcb/kicad/3d-models
cd pcb/kicad/3d-models
echo "→ Choc hotswap (C5333465)"
"$e2k" --lcsc_id C5333465 --3d --output kailh_hotswap --overwrite
echo "→ SK6812MINI-E (C5149201)"
"$e2k" --lcsc_id C5149201 --3d --output sk6812mini-e --overwrite
echo "→ 1N4148W SOD-123 (C81598)"
"$e2k" --lcsc_id C81598 --3d --output diode_sod123 --overwrite
echo "→ Choc V1 switch body (kiswitch)"
mkdir -p kiswitch.3dshapes
curl -sLo kiswitch.3dshapes/SW_Kailh_Choc_V1.stp \
  "https://raw.githubusercontent.com/kiswitch/kiswitch/main/library/3dmodels/3d-library.3dshapes/SW_Kailh_Choc_V1.stp"
echo "done. models in pcb/kicad/3d-models/"
