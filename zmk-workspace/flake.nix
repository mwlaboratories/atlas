{
  # ========================================================================
  # NIX FLAKE: ZMK Firmware Development Environment
  # ========================================================================
  # 
  # This flake defines a reproducible development environment for building
  # ZMK (Zephyr-based keyboard firmware) for custom keyboard projects.
  #
  # ZMK is a modern, open-source firmware for mechanical keyboards that runs
  # on the Zephyr real-time operating system. This environment provides all
  # the tools, toolchains, and dependencies needed to compile ZMK firmware
  # without manually managing complex Python environments and cross-compilation
  # toolchains.
  #
  # Usage: nix develop   (enters the development shell)
  # ========================================================================

  inputs = {
    # ----------------------------------------------------------------------
    # INPUT: nixpkgs - The Nix Package Collection
    # ----------------------------------------------------------------------
    # nixpkgs is the largest software repository in the Nix ecosystem.
    # We pin to the nixos-24.05 release channel to keep a stable toolchain
    # (including clang-tools_17) that remains compatible with Zephyr/ZMK.
    # This is the source of all the build tools (cmake, ninja, gcc, etc.)
    # and provides nixpkgs.lib for utility functions.
    # ----------------------------------------------------------------------
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";

    # ----------------------------------------------------------------------
    # INPUT: zephyr - The Zephyr RTOS (ZMK Fork)
    # ----------------------------------------------------------------------
    # ZMK uses a modified version of the Zephyr real-time operating system.
    # This input pins the exact version of Zephyr that ZMK requires
    # (v3.5.0 with ZMK-specific fixes applied).
    #
    # `flake = false` means this isn't a flake itself, just a regular git
    # repository. We only need the source code from here.
    #
    # This repository provides the requirements.txt file for Python dependencies.
    # ----------------------------------------------------------------------
    zephyr.url = "github:zmkfirmware/zephyr/v3.5.0+zmk-fixes";
    zephyr.flake = false;

    # ----------------------------------------------------------------------
    # INPUT: zephyr-nix - Zephyr SDK and Python Environment Helper
    # ----------------------------------------------------------------------
    # This is a community-maintained Nix expression that simplifies working
    # with Zephyr in Nix environments. It provides:
    #   - pythonEnv: A complete Python environment with all Zephyr dependencies
    #   - sdk-0_16: The Zephyr SDK (cross-compilation toolchains)
    #
    # The `follows` declarations ensure version compatibility:
    #   - zephyr-nix uses the exact same zephyr version we specified
    #   - zephyr-nix uses the same nixpkgs version for consistency
    # ----------------------------------------------------------------------
    zephyr-nix.url = "github:urob/zephyr-nix";
    zephyr-nix.inputs.zephyr.follows = "zephyr";
    zephyr-nix.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { nixpkgs, zephyr-nix, ... }: let
    # ----------------------------------------------------------------------
    # MULTI-ARCHITECTURE SUPPORT
    # ----------------------------------------------------------------------
    # systems: List of CPU architectures and operating systems to support.
    # This makes the dev environment available on:
    #   - x86_64-linux: Intel/AMD 64-bit Linux
    #   - aarch64-linux: ARM 64-bit Linux (e.g., Raspberry Pi 4, Rockchip SBCs)
    #   - x86_64-darwin: Intel 64-bit macOS
    #   - aarch64-darwin: Apple Silicon macOS (M1, M2, etc.)
    #
    # forAllSystems: Helper function that generates attribute sets for each
    # architecture, avoiding repetitive code. It applies the given function
    # to each system in the list.
    # ----------------------------------------------------------------------
    systems = ["x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin"];
    forAllSystems = nixpkgs.lib.genAttrs systems;
  in {
    # ----------------------------------------------------------------------
    # OUTPUT: devShells - Development Environments
    # ----------------------------------------------------------------------
    # devShells are interactive shells with pre-installed packages and
    # environment variables. You enter them with `nix develop`.
    #
    # The default devShell will be activated when no specific shell is named.
    # ----------------------------------------------------------------------
    devShells = forAllSystems (
      system: let
        # ------------------------------------------------------------------
        # SYSTEM-SPECIFIC PACKAGE SETS
        # ------------------------------------------------------------------
        # pkgs: All available packages for this architecture/os from nixpkgs
        # zephyr: Zephyr-specific packages (pythonEnv, SDK, etc.) for this system
        # keymap_drawer: A custom package defined in ./nix/keymap-drawer.nix
        #   This tool visualizes keyboard layouts and key mappings
        # ------------------------------------------------------------------
        pkgs = nixpkgs.legacyPackages.${system};
        zephyr = zephyr-nix.packages.${system};
        keymap_drawer = pkgs.python3Packages.callPackage ./nix/keymap-drawer.nix {};
      in {
        # ------------------------------------------------------------------
        # DEFAULT DEVELOPMENT SHELL
        # ------------------------------------------------------------------
        # This is the main development environment. mkShellNoCC means we're
        # not compiling any Nix code, just creating a shell environment.
        # The "NoCC" version is faster and sufficient for dev shells.
        # ------------------------------------------------------------------
        default = pkgs.mkShellNoCC {
          # --------------------------------------------------------------
          # PACKAGES: All software available in the shell
          # --------------------------------------------------------------
          packages =
            [
              # ----------------------------------------------------------
              # ZEPHYR DEVELOPMENT TOOLS
              # ----------------------------------------------------------
              # zephyr.pythonEnv: Complete Python environment with all Zephyr
              # dependencies pre-installed. This includes West (Zephyr's
              # meta-tool), Python packages like PyYAML, and other tools.
              
              # zephyr.sdk-0_16: Zephyr SDK version 0.16
              # Contains cross-compilation toolchains for embedded targets.
              # - arm-zephyr-eabi: ARM embedded application binary interface
              #   toolchain (the ARM architecture used by most ZMK keyboards)
              zephyr.pythonEnv
              (zephyr.sdk-0_16.override {targets = ["arm-zephyr-eabi"];})

              # ----------------------------------------------------------
              # BUILD TOOLS
              # ----------------------------------------------------------
              # CMake: Cross-platform build system used by Zephyr/ZMK
              # DTC (Device Tree Compiler): Compiles .dwei device tree files
              #   into binary format used by Zephyr's device tree system
              # GCC: GNU Compiler Collection (C/C++ compiler)
              # Ninja: Fast build system, generates from CMake
              pkgs.cmake
              pkgs.dtc
              pkgs.gcc
              pkgs.ninja

              # ----------------------------------------------------------
              # DEVELOPMENT WORKFLOW TOOLS
              # ----------------------------------------------------------
              # just: A command runner (like make, but simpler syntax)
              #   Used to run common ZMK build commands and workflows
              # yq: A YAML parser written in Python (not the Go version)
              #   Used to manipulate YAML config files in ZMK builds
              pkgs.just
              pkgs.yq # Make sure yq resolves to python-yq.

              # keymap_drawer: Visualization tool for keyboard layouts
              #   Converts ZMK keymap definitions into visual diagrams
              keymap_drawer

              # ----------------------------------------------------------
              # OPTIONAL SYSTEM UTILITIES (currently commented out)
              # ----------------------------------------------------------
              # These are standard Unix utilities that are typically already
              # available on most systems. They're used by:
              #   - just_recipes: The justfile command recipes
              #   - west_commands: Zephyr's West tool commands
              # 
              # Uncomment these if you're on a minimal system or NixOS where
              # these might not be in the standard PATH.
              # -- Used by just_recipes and west_commands. Most systems already have them. --
              # pkgs.gawk
              # pkgs.unixtools.column
              # pkgs.coreutils # cp, cut, echo, mkdir, sort, tail, tee, uniq, wc
              # pkgs.diffutils
              # pkgs.findutils # find, xargs
              # pkgs.gnugrep
              # pkgs.gnused
            ];

          # --------------------------------------------------------------
          # SHELL HOOK: Commands run when entering the shell
          # --------------------------------------------------------------
          # These environment variables tell ZMK where to find important
          # directories. They're set automatically when you run `nix develop`.
          #
          # ZMK_BUILD_DIR: Where compiled firmware artifacts are stored
          # ZMK_SRC_DIR: Root directory of the ZMK application source code
          #   (typically your zmk fork in the zmk/app subdirectory)
          # --------------------------------------------------------------
          shellHook = ''
            export ZMK_BUILD_DIR=$(pwd)/.build;
            export ZMK_SRC_DIR=$(pwd)/zmk/app;
            if [ -n "\${PS1:-}" ]; then
              export PS1="\n\[\033[1;32m\][zmk-shell:\w]\$\[\033[0m\] ";
            fi
          '';
        };
      }
    );
  };
}