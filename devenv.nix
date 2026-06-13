{ pkgs, ... }:

{
  packages = with pkgs; [
    just
    python3
    wl-clipboard
    kicad
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

  enterShell = ''
    echo ""
    echo "  atlas — hand-laid KiCad PCB"
    echo "  render            render pcb/kicad/keyboard.kicad_pcb to PNGs"
    echo "  fetch-3d-models   download component 3D models (run once)"
    echo ""
  '';
}
