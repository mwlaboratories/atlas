{
  description = "Atlas keyboard dev environment (firmware + PCB + case)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";
    zephyr.url = "github:zmkfirmware/zephyr/v4.1.0+zmk-fixes";
    zephyr.flake = false;
    zephyr-nix.url = "github:urob/zephyr-nix";
    zephyr-nix.inputs.zephyr.follows = "zephyr";
    zephyr-nix.inputs.nixpkgs.follows = "nixpkgs";
    cq-flake.url = "github:vinszent/cq-flake";
  };

  outputs = { nixpkgs, nixpkgs-unstable, zephyr-nix, cq-flake, ... }: let
    systems = ["x86_64-linux" "aarch64-linux"];
    forAllSystems = nixpkgs.lib.genAttrs systems;
  in {
    devShells = forAllSystems (system: let
      pkgs = nixpkgs.legacyPackages.${system};
      unstable = nixpkgs-unstable.legacyPackages.${system};
      zephyr = zephyr-nix.packages.${system};
      cq = cq-flake.packages.${system};
      keymap_drawer = pkgs.python312Packages.callPackage ./nix/keymap-drawer.nix {};
      kiswitch = pkgs.callPackage ./nix/kiswitch.nix {};

      # PCB Python — must match KiCad's Python (3.13 from unstable).
      # pcbnew is added via PYTHONPATH in shellHook.
      kbplacer = unstable.python313Packages.callPackage ./nix/kbplacer.nix {};
      kicad-sch-api = unstable.python313Packages.callPackage ./nix/kicad-sch-api.nix {};
      pcbPython = unstable.python313.withPackages (ps: [
        ps.pyyaml
        ps.contourpy
        ps.numpy
        kbplacer
        kicad-sch-api
      ]);

      # CadQuery Python — must use cq-flake's own Python (3.12.9) since cadquery's
      # native extensions are compiled against it. Uses cq.cadquery.pythonModule
      # to get the matching interpreter, then wraps it with cadquery + pyyaml.
      cqPython = cq.cadquery.pythonModule.withPackages (ps: [
        ps.pyyaml
        cq.cadquery
      ]);

      # Wrapper: pcb-python — python3.13 with pcbnew + kbplacer + pyyaml
      # Sources all KiCad env vars (PYTHONPATH, KICAD9_FOOTPRINT_DIR, etc.)
      pcb-python = pkgs.writeShellScriptBin "pcb-python" ''
        eval "$(grep '^export ' "$(command -v pcbnew)" | head -30)"
        exec ${pcbPython}/bin/python3 "$@"
      '';

      # Wrapper: cq-python — python3.12 with cadquery + pyyaml
      cq-python = pkgs.writeShellScriptBin "cq-python" ''
        exec ${cqPython}/bin/python3 "$@"
      '';
    in {
      default = pkgs.mkShellNoCC {
        packages = [
          zephyr.pythonEnv
          (zephyr.sdk-0_17.override { targets = ["arm-zephyr-eabi"]; })
          pkgs.cmake
          pkgs.dtc
          pkgs.gcc
          pkgs.ninja
          pkgs.just
          pkgs.yq
          keymap_drawer
          pkgs.librsvg
          pcb-python                    # pcb-python: python3.13 + pcbnew + kbplacer
          cq-python                     # cq-python:  python3.12 + cadquery
          unstable.kicad                # KiCad 9 (kicad-cli, pcbnew)
          pkgs.f3d                      # lightweight 3D viewer (STEP/STL/OBJ)
          pkgs.wl-clipboard
          pkgs.freerouting              # PCB autorouter (DSN → SES)
        ];

        KISWITCH_DIR = "${kiswitch}/footprints";

        shellHook = ''
          export IN_NIX_SHELL="atlas-dev"
          echo ""
          echo "  Atlas Keyboard — dev shell"
          echo ""
          echo "  just build-fw       build firmware (.uf2) for both halves"
          echo "  just build-keymap   render firmware/keymap.svg from atlas.keymap"
          echo "  just init-west      initialize west workspace (one-time)"
          echo "  just gen-kicad      YAML → tools/build/{atlas.kicad_pcb,.kicad_sch,.step}"
          echo "  just open-step      open tools/build/atlas.step in f3d viewer"
          echo ""
        '';
      };
    });
  };
}
