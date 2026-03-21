{
  description = "Atlas keyboard dev environment (firmware + PCB)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";
    zephyr.url = "github:zmkfirmware/zephyr/v4.1.0+zmk-fixes";
    zephyr.flake = false;
    zephyr-nix.url = "github:urob/zephyr-nix";
    zephyr-nix.inputs.zephyr.follows = "zephyr";
    zephyr-nix.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { nixpkgs, nixpkgs-unstable, zephyr-nix, ... }: let
    systems = ["x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin"];
    forAllSystems = nixpkgs.lib.genAttrs systems;
  in {
    devShells = forAllSystems (system: let
      pkgs = nixpkgs.legacyPackages.${system};
      unstable = nixpkgs-unstable.legacyPackages.${system};
      zephyr = zephyr-nix.packages.${system};
      keymap_drawer = pkgs.python312Packages.callPackage ./keymapdrawer-nix/keymap-drawer.nix {};
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
          unstable.kicad              # KiCad 9 (PCB format 20241229)
          pkgs.wl-clipboard
          pkgs.unzip
          pkgs.curl
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
          echo "  PCB generation:"
          echo "    just pcb           full auto: YAML → KLE → kbplacer → enhance → KiCad"
          echo "    just pcb-setup     bootstrap kbplacer venv (first time)"
          echo "    just pcb-enhance   re-run enhancements only"
          echo "    just pcb-open      open PCB in KiCad"
          echo ""
        '';
      };
    });
  };
}
