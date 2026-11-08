{
  description = "ATLAS split keyboard ZMK firmware (ESB split + USB dongle)";

  # Dev shell only (west + Zephyr SDK from zmk-nix); build-firmware.sh drives
  # west directly because the ESB stack (sdk-nrf, nrfxlib) is pulled by west.
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    zmk-nix = {
      url = "github:lilyinstarlight/zmk-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { nixpkgs, zmk-nix, ... }: let
    forAllSystems = nixpkgs.lib.genAttrs (nixpkgs.lib.attrNames zmk-nix.packages);
  in {
    devShells = forAllSystems (system: {
      default = zmk-nix.devShells.${system}.default;
    });
  };
}
