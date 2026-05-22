{ pkgs, ... }:

{
  packages = with pkgs; [
    just
    python3
    wl-clipboard
  ];

  scripts.serve.exec = ''
    cd "''${DEVENV_ROOT}/tools/layout"
    python3 -m http.server "''${1:-8000}"
  '';

  enterShell = ''
    echo ""
    echo "  atlas — fresh"
    echo "  serve   start layout webtool on http://localhost:8000"
    echo ""
  '';
}
