{
  description = "Atlas keyboard ZMK firmware dev environment";

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
        ];

        shellHook = ''
          export PS1="\n\[\033[1;32m\][atlas:\w]\$\[\033[0m\] "
          echo "Atlas ZMK dev shell. Run 'just' to see available commands."
        '';
      };
    });
  };
}
