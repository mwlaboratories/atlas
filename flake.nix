{
  description = "Atlas keyboard dev environment (firmware + PCB)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    zephyr.url = "github:zmkfirmware/zephyr/v4.1.0+zmk-fixes";
    zephyr.flake = false;
    zephyr-nix.url = "github:urob/zephyr-nix";
    zephyr-nix.inputs.zephyr.follows = "zephyr";
    zephyr-nix.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { nixpkgs, zephyr-nix, ... }: let
    systems = ["x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin"];
    forAllSystems = nixpkgs.lib.genAttrs systems;
  in {
    devShells = forAllSystems (system: let
      pkgs = nixpkgs.legacyPackages.${system};
      zephyr = zephyr-nix.packages.${system};
      keymap_drawer = pkgs.python312Packages.callPackage ./nix/keymap-drawer.nix {};
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
          (pkgs.python312.withPackages (ps: [ ps.pyyaml ps.jinja2 ]))
          pkgs.kicad
          pkgs.wl-clipboard
          pkgs.unzip
        ];

        shellHook = ''
          export IN_NIX_SHELL="atlas-dev"
          echo ""
          echo "  Atlas Keyboard — dev shell"
          echo ""
          echo "  Firmware (ZMK BLE, XIAO nRF52840):"
          echo "    just all           build both halves"
          echo "    just left/right    build one side"
          echo "    just keymap        generate keymap SVG"
          echo "    just init          initialize west (first time)"
          echo ""
          echo "  PCB generation (see tools/readme.org):"
          echo "    just pcb           full flow with instructions"
          echo "    just kle-clip      KLE JSON → clipboard"
          echo "    just pcb-enhance   patch kbplacer output"
          echo ""
        '';
      };
    });
  };
}
