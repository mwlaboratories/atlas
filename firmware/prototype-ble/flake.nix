{
  description = "ATLAS split keyboard ZMK firmware (BLE, dual trackpoint)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

    zmk-nix = {
      url = "github:lilyinstarlight/zmk-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, zmk-nix }: let
    forAllSystems = nixpkgs.lib.genAttrs (nixpkgs.lib.attrNames zmk-nix.packages);
  in {
    packages = forAllSystems (system: rec {
      default = firmware;

      firmware = zmk-nix.legacyPackages.${system}.buildSplitKeyboard {
        name = "atlas-firmware";

        src = nixpkgs.lib.sourceFilesBySuffices self [
          ".board" ".cmake" ".conf" ".defconfig" ".dts" ".dtsi"
          ".json" ".keymap" ".overlay" ".shield" ".yml" "_defconfig"
        ];

        # Zephyr 4.1 / HWMv2 renamed the old `seeeduino_xiao_ble` alias.
        # Per ZMK's 2025-12-09 migration note: seeeduino_xiao_ble -> xiao_ble//zmk
        board = "xiao_ble//zmk";
        shield = "atlas_%PART%";
        # parts defaults to [ "left" "right" ]; central is the first ("left").

        zephyrDepsHash = "sha256-B1R8LGhftG5BKSrCGsJ5WuboQub3CEyf4pQFdh0kq00=";

        meta = {
          description = "ATLAS ZMK firmware";
          license = nixpkgs.lib.licenses.mit;
          platforms = nixpkgs.lib.platforms.all;
        };
      };

      # Flash this to BOTH halves to wipe stored BLE bonds (split + host),
      # then reflash `firmware`, to re-pair peripheral <-> central after a reflash.
      reset = zmk-nix.legacyPackages.${system}.buildKeyboard {
        name = "atlas-settings-reset";
        src = nixpkgs.lib.sourceFilesBySuffices self [
          ".board" ".cmake" ".conf" ".defconfig" ".dts" ".dtsi"
          ".json" ".keymap" ".overlay" ".shield" ".yml" "_defconfig"
        ];
        board = "xiao_ble//zmk";
        shield = "settings_reset";
        zephyrDepsHash = "sha256-B1R8LGhftG5BKSrCGsJ5WuboQub3CEyf4pQFdh0kq00=";
        meta = {
          description = "ATLAS settings-reset image";
          license = nixpkgs.lib.licenses.mit;
          platforms = nixpkgs.lib.platforms.all;
        };
      };

      flash = zmk-nix.packages.${system}.flash.override { inherit firmware; };
      update = zmk-nix.packages.${system}.update;
    });

    devShells = forAllSystems (system: {
      default = zmk-nix.devShells.${system}.default;
    });
  };
}
