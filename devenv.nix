{ pkgs, ... }:

let
  # keymap-drawer (caksoylar) — parses config/atlas.keymap and renders the
  # layer diagram in images/keymap.svg. Built from ./nix/ because the version
  # we pin (0.22.1) needs tree-sitter 0.24.0 + a devicetree grammar.
  keymap-drawer = pkgs.python3Packages.callPackage ./nix/keymap-drawer.nix { };
in
{
  packages = with pkgs; [
    just
    python3
    wl-clipboard
    kicad
    keymap-drawer
  ];

  # One-shot commands — runnable from any subshell once devenv is active.
  scripts.render.exec = ''
    cd "''${DEVENV_ROOT}"
    mkdir -p tools/renders
    kicad-cli pcb render --side top --width 1600 --height 1100 --quality high \
      --output tools/renders/atlas-top.png pcb/kicad/keyboard.kicad_pcb
    kicad-cli pcb render --side bottom --width 1600 --height 1100 --quality high \
      --output tools/renders/atlas-bottom.png pcb/kicad/keyboard.kicad_pcb
    echo "→ tools/renders/atlas-{top,bottom}.png"
  '';

  # (Re)generate the component 3D models tracked under pcb/kicad/3d-models/.
  # SMD parts come from JLCPCB/LCSC via easyeda2kicad; the switch body comes
  # from the kiswitch community library. The models are committed, so this is
  # only needed to refresh them — not a required setup step.
  scripts.fetch-3d-models.exec = ''
    cd "''${DEVENV_ROOT}"
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
  '';

  # Regenerate images/keymap.svg (the layer diagram in readme.org) from the
  # active keymap via keymap-drawer. Named draw-keymap to avoid shadowing
  # keymap-drawer's own `keymap` console script (which this calls).
  scripts.draw-keymap.exec = ''
    cd "''${DEVENV_ROOT}"
    keymap -c nix/keymap-drawer.yaml parse \
      -z zmk-workspace-ble/config/atlas.keymap -c 10 -o nix/keymap.yaml
    keymap -c nix/keymap-drawer.yaml draw nix/keymap.yaml \
      -n "33333+2 2+33333" -o images/keymap.svg
    echo "→ images/keymap.svg"
  '';

  # Build both firmware halves with the laymap layer reporter (zmk-laymap/)
  # baked into the central. Logic lives in build-firmware.sh to avoid Nix
  # string-escaping; it drops into the zmk-nix dev shell for west + the SDK.
  scripts.firmware.exec = ''
    exec bash "''${DEVENV_ROOT}/zmk-workspace-ble/build-firmware.sh"
  '';

  enterShell = ''
    echo ""
    echo "  atlas — hand-laid KiCad PCB + ZMK firmware"
    echo "  render            render pcb/kicad/keyboard.kicad_pcb to PNGs"
    echo "  fetch-3d-models   download component 3D models (run once)"
    echo "  draw-keymap       regenerate images/keymap.svg from the keymap"
    echo "  firmware          build atlas_{left,right}.uf2 (left = + laymap)"
    echo ""
  '';
}
