{ pkgs, ... }:

{
  packages = with pkgs; [
    just
    python3
    wl-clipboard
    nodejs_22
    kicad
  ];

  scripts.dev.exec = ''
    cd "''${DEVENV_ROOT}"
    node tools/server.mjs
  '';

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

  enterShell = ''
    echo ""
    echo "  atlas — ergogen pipeline"
    echo "  dev       start full webtool + ergogen + render server"
    echo "            open http://localhost:8000, click 'build PCB'"
    echo "  ergogen   run ergogen once on tools/ergogen/config.yaml"
    echo "  render    render current pcb/kicad/keyboard.kicad_pcb to PNGs"
    echo ""
  '';
}
