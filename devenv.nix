{ pkgs, ... }:

{
  packages = with pkgs; [
    just
    python3
    wl-clipboard
    nodejs_22
    kicad
  ];

  scripts.serve.exec = ''
    cd "''${DEVENV_ROOT}/tools/layout"
    python3 -m http.server "''${1:-8000}"
  '';

  scripts.ergogen.exec = ''
    cd "''${DEVENV_ROOT}"
    npx ergogen "$@"
  '';

  scripts.render.exec = ''
    cd "''${DEVENV_ROOT}"
    mkdir -p tools/renders
    kicad-cli pcb render --side bottom --width 2000 --height 1400 --quality high \
      --output tools/renders/bottom.png pcb/kicad/keyboard.kicad_pcb
    echo "→ tools/renders/bottom.png"
  '';

  enterShell = ''
    echo ""
    echo "  atlas — fresh"
    echo "  serve     start layout webtool on http://localhost:8000"
    echo "  ergogen   run ergogen on a yaml config"
    echo "  render    render PCB bottom view to tools/renders/bottom.png"
    echo ""
  '';
}
