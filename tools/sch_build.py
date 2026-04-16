#!/usr/bin/env python3
"""Atlas schematic generator — emits tools/build/atlas.kicad_sch.

Reads the placed footprints from tools/build/atlas.kicad_pcb and the net spec
from keyboard.yaml, then produces a schematic with stub-wire+label connectivity
via kicad-sch-api.

The schematic is for ERC + tooling consumption (kicad-happy chain), not for
visual design review. Auto-layout produces a functional but ugly arrangement;
each net becomes a global label adjacent to its component, so KiCad's
connectivity engine treats identically-named labels as the same net.

Run inside `nix develop` (provides pcb-python with kicad-sch-api).

Usage:
    pcb-python tools/sch_build.py
"""
import argparse
import re
import sys
from pathlib import Path

import kicad_sch_api as ksa
import yaml

# Relax kicad-sch-api's strict reference-format validator so our descriptive
# refs like U_ADC_L / R_REF_L1 / TP_L_pad_x are accepted. KiCad itself doesn't
# enforce this regex — it's an annotator-helper rule in kicad-sch-api 0.5.6
# (see utils/validation.py:_valid_reference_pattern).
from kicad_sch_api.utils.validation import SchematicValidator as _KSAValidator
_KSAValidator.validate_reference = lambda self, ref: bool(ref)

ksa.use_grid_units(True)

TOOLS_DIR = Path(__file__).resolve().parent
BUILD_DIR = TOOLS_DIR / "build"
SYMBOLS_DIR = TOOLS_DIR / "kicad" / "symbols"

# Register project-local symbol libraries (Seeed XIAO Series, etc.) with the
# kicad-sch-api symbol cache so lookups like
# Seeed_Studio_XIAO_Series:XIAO-nRF52840_Plus_SMD resolve.
_cache = ksa.get_symbol_cache()
for lib_file in SYMBOLS_DIR.glob("*.kicad_sym"):
    _cache.add_library_path(lib_file)


def read_pcb_footprints(pcb_path: Path) -> dict:
    """Return {ref: {footprint, value}} for every placed footprint in the PCB."""
    text = pcb_path.read_text()
    out = {}
    for chunk in re.split(r"(?=\(footprint\b)", text):
        if not chunk.startswith("(footprint"):
            continue
        fp_match = re.match(r'\(footprint\s+"([^"]*)"', chunk)
        ref_match = re.search(r'"Reference"\s+"([^"]+)"', chunk)
        val_match = re.search(r'"Value"\s+"([^"]+)"', chunk)
        if not (fp_match and ref_match):
            continue
        out[ref_match.group(1)] = {
            "footprint": fp_match.group(1),
            "value": val_match.group(1) if val_match else "",
        }
    return out


def lookup_symbol(ref: str, layout: dict) -> str | None:
    """Resolve a footprint reference to a schematic symbol id, or None to skip."""
    # Mechanical-only refs (no electrical connection) — no symbol needed
    if ref.startswith("TP_") and ("_stick" in ref or "_screw" in ref):
        return None

    if ref.startswith("U_ADC_"):
        return layout.get("trackpoint", {}).get("adc", {}).get("symbol", "Analog_ADC:ADS1220xPW")
    if ref.startswith("SW_PWR_"):
        return layout.get("power_switch", {}).get("symbol", "Switch:SW_SP3T")
    if ref.startswith("U_"):
        return layout.get("controller", {}).get("symbol", "Connector_Generic:Conn_01x14")
    if ref.startswith("R_"):
        return "Device:R"
    if ref.startswith("C_"):
        return "Device:C"
    if ref.startswith("D") and ref[1:].isdigit():
        return "Device:D"
    if ref.startswith("TP_") and "_pad_" in ref:
        return "Connector_Generic:Conn_01x01"
    if ref.startswith("SW") and ref[2:].isdigit():
        return layout.get("switch", {}).get("symbol", "Switch:SW_Push")
    return None


def materialize_nets(nets_spec: dict) -> dict:
    """Apply the L/R mirror rule: a net ending in _L auto-emits an _R counterpart."""
    materialized = {}
    for net_name, fp_map in nets_spec.items():
        materialized[net_name] = dict(fp_map)
        if not net_name.endswith("_L"):
            continue
        mirror_name = net_name[:-2] + "_R"
        if mirror_name in nets_spec:
            continue
        mirrored_map = {}
        for ref, pads in fp_map.items():
            new_ref = ref[:-2] + "_R" if ref.endswith("_L") else ref.replace("_L", "_R")
            mirrored_map[new_ref] = list(pads)
        materialized[mirror_name] = mirrored_map
    return materialized


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-l", "--layout", type=Path, default=TOOLS_DIR / "keyboard.yaml")
    parser.add_argument("--pcb", type=Path, default=BUILD_DIR / "atlas.kicad_pcb")
    parser.add_argument("-o", "--output", type=Path, default=BUILD_DIR / "atlas.kicad_sch")
    args = parser.parse_args()

    if not args.pcb.exists():
        print(f"Error: {args.pcb} not found. Run 'just gen-kicad' first.", file=sys.stderr)
        sys.exit(1)

    layout = yaml.safe_load(args.layout.read_text())
    placed = read_pcb_footprints(args.pcb)

    sch = ksa.create_schematic("atlas")

    # Group refs by type so the auto-layout clusters related parts together.
    groups: dict[str, list[str]] = {
        "controller": [],
        "adc": [],
        "passive": [],
        "diode": [],
        "switch": [],
        "sensor_pad": [],
        "power_switch": [],
    }
    skipped = 0
    for ref in sorted(placed.keys()):
        sym_id = lookup_symbol(ref, layout)
        if not sym_id:
            skipped += 1
            continue
        if ref.startswith("U_ADC_"):
            groups["adc"].append(ref)
        elif ref.startswith("SW_PWR_"):
            groups["power_switch"].append(ref)
        elif ref.startswith("U_"):
            groups["controller"].append(ref)
        elif ref.startswith(("R_", "C_")):
            groups["passive"].append(ref)
        elif ref.startswith("D") and ref[1:].isdigit():
            groups["diode"].append(ref)
        elif ref.startswith("SW") and ref[2:].isdigit():
            groups["switch"].append(ref)
        elif ref.startswith("TP_") and "_pad_" in ref:
            groups["sensor_pad"].append(ref)

    placed_components: dict[str, tuple[str, tuple[int, int]]] = {}

    # Auto-layout: each group gets a row; refs flow left-to-right within the row.
    # Grid units (1 unit = 1.27mm); ksa.use_grid_units(True) handles the scaling.
    row_y = 5
    cell_w = 12
    for group_name, refs in groups.items():
        if not refs:
            continue
        # Group label position
        x = 5
        for ref in refs:
            sym_id = lookup_symbol(ref, layout)
            info = placed[ref]
            try:
                sch.components.add(
                    lib_id=sym_id,
                    reference=ref,
                    value=info["value"] or ref,
                    position=(x, row_y),
                    footprint=info["footprint"],
                )
                placed_components[ref] = (sym_id, (x, row_y))
                x += cell_w
            except Exception as e:
                print(f"  Warning: failed to add {ref} ({sym_id}): {e}", file=sys.stderr)
        row_y += 20  # next group row

    print(f"  Symbols: {len(placed_components)} placed, {skipped} non-electrical refs skipped")

    # Power flag symbols — KiCad's ERC needs an Output Power pin to "drive" each
    # power net (otherwise sees Input Power pins as floating).
    pwr_y = 1
    for power_net, power_lib_id in [("VCC", "power:VCC"), ("GND", "power:GND")]:
        for half in ("L", "R"):
            ref = f"#PWR_{power_net}_{half}"
            x = 1 if half == "L" else 50
            try:
                sch.components.add(
                    lib_id=power_lib_id,
                    reference=ref.replace("_L", "01").replace("_R", "02"),
                    value=power_net,
                    position=(x, pwr_y),
                )
                # Place a coincident label so KiCad's connectivity engine ties
                # the power flag to the same net our component pads use.
                sch.add_label(power_net, position=(x, pwr_y))
            except Exception as e:
                print(f"  Warning: failed to add power flag {ref}: {e}", file=sys.stderr)
        pwr_y += 3

    # Net labels — for each pad in keyboard.yaml's nets block, drop a label
    # adjacent to the symbol that holds that pad. KiCad treats identically-named
    # labels as the same net, so positional accuracy matters less than name match.
    nets_spec = layout.get("nets") or {}
    materialized = materialize_nets(nets_spec)

    label_count = 0
    skipped_labels = 0
    pin_lookup_failures = 0
    GRID_MM = 1.27  # 1 grid unit = 1.27 mm in KiCad schematic space
    for net_name, fp_map in sorted(materialized.items()):
        for fp_ref, pads in fp_map.items():
            if fp_ref not in placed_components:
                skipped_labels += len(pads)
                continue
            for pad in pads:
                # Query the symbol's pin position; convert mm → grid units and snap
                try:
                    pin_pos = sch.get_component_pin_position(fp_ref, str(pad))
                except Exception:
                    pin_pos = None
                if pin_pos is None:
                    pin_lookup_failures += 1
                    continue
                lx = round(pin_pos.x / GRID_MM)
                ly = round(pin_pos.y / GRID_MM)
                try:
                    sch.add_label(net_name, position=(lx, ly))
                    label_count += 1
                except Exception as e:
                    print(f"  Warning: label '{net_name}' on {fp_ref}.{pad}: {e}", file=sys.stderr)

    print(f"  Net labels: {label_count} placed (pin-precise), {skipped_labels} on missing footprints skipped, {pin_lookup_failures} pin lookups failed")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sch.save(str(args.output))
    print(f"  → {args.output} ({args.output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
