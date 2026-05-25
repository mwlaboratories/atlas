{ pkgs, ... }:

{
  packages = with pkgs; [
    just
    python3
    wl-clipboard
    nodejs_22
    kicad
  ];

  # Long-running services — start with `devenv up`.
  processes.server.exec = "cd ${"\${DEVENV_ROOT}"} && node tools/server.mjs";

  # One-shot commands — runnable from any subshell once devenv is active.
  scripts.ergogen.exec = ''
    cd "''${DEVENV_ROOT}"
    npx ergogen tools/ergogen -o tools/ergogen/output "$@"
  '';

  scripts.render.exec = ''
    cd "''${DEVENV_ROOT}"
    mkdir -p tools/renders
    kicad-cli pcb render --side top --width 1600 --height 1100 --quality high \
      --output tools/renders/atlas-top.png pcb/kicad/keyboard.kicad_pcb
    kicad-cli pcb render --side bottom --width 1600 --height 1100 --quality high \
      --output tools/renders/atlas-bottom.png pcb/kicad/keyboard.kicad_pcb
    echo "→ tools/renders/atlas-{top,bottom}.png"
  '';

  # Fetch 3D STEP models for components — used by kicad-cli to render a
  # realistic preview. SMD parts come from JLCPCB/LCSC via easyeda2kicad;
  # switch body comes from the kiswitch community library. Run once after
  # cloning the repo.
  scripts.fetch-3d-models.exec = ''
    cd "''${DEVENV_ROOT}"
    if [ ! -x tools/.venv/bin/easyeda2kicad ]; then
      echo "→ creating venv + installing easyeda2kicad"
      python3 -m venv tools/.venv
      tools/.venv/bin/pip install -q easyeda2kicad
    fi
    mkdir -p tools/3d-models
    cd tools/3d-models
    echo "→ Choc hotswap (C5333465)"
    ../.venv/bin/easyeda2kicad --lcsc_id C5333465 --3d --output kailh_hotswap --overwrite
    echo "→ SK6812MINI-E (C5149201)"
    ../.venv/bin/easyeda2kicad --lcsc_id C5149201 --3d --output sk6812mini-e --overwrite
    echo "→ 1N4148W SOD-123 (C81598)"
    ../.venv/bin/easyeda2kicad --lcsc_id C81598 --3d --output diode_sod123 --overwrite
    echo "→ Choc V1 switch body (kiswitch)"
    mkdir -p kiswitch.3dshapes
    curl -sLo kiswitch.3dshapes/SW_Kailh_Choc_V1.stp \
      "https://raw.githubusercontent.com/kiswitch/kiswitch/main/library/3dmodels/3d-library.3dshapes/SW_Kailh_Choc_V1.stp"
    echo "done. models in tools/3d-models/"
  '';

  enterShell = ''
    echo ""
    echo "  atlas — ergogen pipeline"
    echo "  devenv up    start webtool + ergogen + render server (foreground)"
    echo "               open http://localhost:8000, click 'build PCB'"
    echo "  ergogen      one-shot: run ergogen on tools/ergogen/config.yaml"
    echo "  render       one-shot: render pcb/kicad/keyboard.kicad_pcb to PNGs"
    echo ""
  '';
}
