{ pkgs, ... }:

let
  # keymap-drawer (caksoylar) — parses config/old_prototype.keymap and renders the
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
    uv # provides `uvx`, used by the kicad-mcp wrapper below
  ];

  # MCP server: kicad-mcp-pro, run via uvx. See .mcp.json (project-scoped so
  # Claude Code only offers it inside this repo).
  #
  # Why the LD_LIBRARY_PATH: the package's telemetry module pulls in grpcio,
  # whose prebuilt manylinux wheel dynamically links libstdc++.so.6. On NixOS
  # that library isn't on the default loader path, so the C extension fails to
  # load. We inject the matching 64-bit libstdc++ from the same nixpkgs.
  # OTEL_SDK_DISABLED keeps the telemetry SDK from exporting anything.
  scripts.kicad-mcp.exec = ''
    export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export OTEL_SDK_DISABLED=true
    exec uvx kicad-mcp-pro@3.25.0 --transport stdio "$@"
  '';

  # One-shot commands — runnable from any subshell once devenv is active.
  scripts.render.exec = ''
    cd "''${DEVENV_ROOT}"
    mkdir -p renders
    for half in left right; do
      for side in top bottom; do
        kicad-cli pcb render --side $side --width 1600 --height 1100 --quality high \
          --output renders/atlas-$half-$side.png pcb/atlas-$half.kicad_pcb
      done
    done
    echo "→ renders/atlas-{left,right}-{top,bottom}.png"
  '';

  # PCBWay panel: both halves joined by two rails + the thumb-cluster bridge, with
  # mouse bites -> pcb/atlas-panel.kicad_pcb (+ .kicad_pro/.kicad_dru). Stdlib Python,
  # no pcbnew. See pcb/tools/panelize.py; args pass through (--pcb-dir, --out).
  scripts.panel.exec = ''
    cd "''${DEVENV_ROOT}"
    python3 pcb/tools/panelize.py "$@"
  '';

  # Printable PCB test-fit models (top side flat, hotswap pin holes drilled
  # through) for checking real switches and the case. See pcb/testfit.py.
  scripts.testfit.exec = ''
    cd "''${DEVENV_ROOT}"
    ${pkgs.freecad}/bin/FreeCADCmd pcb/testfit.py
  '';

  # Case pipeline (cad/tools): build123d models generated from the KiCad
  # boards, exported to cad/out/ and checked against the reference STEPs.
  # Halves build in parallel; unchanged parts come from cad/out/.cache.
  # build123d's OCP wheels are manylinux builds: they need these libraries on
  # the loader path on NixOS. Python comes from nixpkgs, uv only manages deps.
  scripts.cad.exec = ''
    cd "''${DEVENV_ROOT}"
    export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath (with pkgs; [ stdenv.cc.cc.lib libGL xorg.libX11 zlib expat fontconfig freetype ])}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export UV_PYTHON="${pkgs.python312}/bin/python3.12" UV_PYTHON_DOWNLOADS=never
    unset PYTHONPATH  # the devenv shell exports nixpkgs python3.13 packages (keymap-drawer)
    case "''${1:-}" in
      view)  shift; exec uv run --project cad/tools python cad/tools/view.py "$@" ;;
      print) shift; exec uv run --project cad/tools python cad/tools/build.py --print "$@" ;;
      *)     exec uv run --project cad/tools python cad/tools/build.py "$@" ;;
    esac
  '';

  # Regenerate images/keymap.svg (the layer diagram in readme.org) from the
  # active keymap via keymap-drawer. Named draw-keymap to avoid shadowing
  # keymap-drawer's own `keymap` console script (which this calls).
  scripts.draw-keymap.exec = ''
    cd "''${DEVENV_ROOT}"
    keymap -c nix/keymap-drawer.yaml parse \
      -z firmware/ble/config/old_prototype.keymap -c 10 -o nix/keymap.yaml
    keymap -c nix/keymap-drawer.yaml draw nix/keymap.yaml \
      -n "33333+2 2+33333" -o images/keymap.svg
    echo "→ images/keymap.svg"
  '';

  # Build both firmware halves with the strata layer reporter (zmk-strata, pulled
  # via west.yml) baked into the central. Logic lives in build-firmware.sh to avoid Nix
  # string-escaping; it drops into the zmk-nix dev shell for west + the SDK.
  scripts.firmware.exec = ''
    exec bash "''${DEVENV_ROOT}/firmware/ble/build-firmware.sh"
  '';

  enterShell = ''
    echo ""
    echo "  atlas — KiCad PCB, case STEP files + ZMK firmware"
    echo "  render            render pcb/atlas-{left,right}.kicad_pcb to PNGs"
    echo "  panel             build pcb/atlas-panel.kicad_pcb (PCBWay panel) from the halves"
    echo "  testfit           printable PCB test-fit models -> pcb/testfit/ (Bambu 3MF)"
    echo "  cad               build the case from the boards -> cad/out/, all checks (cached)"
    echo "  cad --fast        quick iteration: no reference compare, no thin-wall scan"
    echo "  cad view          case viewer at http://localhost:8737, fast rebuild after each change"
    echo "  cad print         build + cad/out/atlas-case.3mf for Bambu (one part per plate)"
    echo "  draw-keymap       regenerate images/keymap.svg from the keymap"
    echo "  firmware          build old_prototype_{left,right}.uf2 (left = + strata)"
    echo ""
  '';
}
