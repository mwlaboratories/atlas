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
  # Renders the built prototype board (soldered Choc V1). The README picture
  # is the committed images/atlas-render.png, not this output.
  scripts.render.exec = ''
    cd "''${DEVENV_ROOT}"
    mkdir -p tools/renders
    kicad-cli pcb render --side top --width 1600 --height 1100 --quality high \
      --output tools/renders/atlas-top.png pcb/atlas/atlas.kicad_pcb
    kicad-cli pcb render --side bottom --width 1600 --height 1100 --quality high \
      --output tools/renders/atlas-bottom.png pcb/atlas/atlas.kicad_pcb
    echo "→ tools/renders/atlas-{top,bottom}.png"
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

  # Build both firmware halves with the strata layer reporter (zmk-strata, pulled
  # via west.yml) baked into the central. Logic lives in build-firmware.sh to avoid Nix
  # string-escaping; it drops into the zmk-nix dev shell for west + the SDK.
  scripts.firmware.exec = ''
    exec bash "''${DEVENV_ROOT}/zmk-workspace-ble/build-firmware.sh"
  '';

  enterShell = ''
    echo ""
    echo "  atlas — built Choc V1 prototype + ZMK firmware"
    echo "  render            render pcb/atlas/atlas.kicad_pcb to PNGs"
    echo "  draw-keymap       regenerate images/keymap.svg from the keymap"
    echo "  firmware          build atlas_{left,right}.uf2 (left = + strata)"
    echo ""
  '';
}
