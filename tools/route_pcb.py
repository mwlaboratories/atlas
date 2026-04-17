#!/usr/bin/env python3
"""PCB autorouting helper — DSN export / SES import via pcbnew API.

Freerouting uses the Specctra DSN format. This script bridges KiCad's
pcbnew API to freerouting's CLI:

  1. --export-dsn: Load .kicad_pcb → write .dsn (Specctra Design)
  2. --import-ses: Load .kicad_pcb + .ses → merge routed traces back

Usage:
    pcb-python tools/route_pcb.py --export-dsn --pcb board.kicad_pcb --dsn board.dsn
    pcb-python tools/route_pcb.py --import-ses --pcb board.kicad_pcb --ses board.ses
"""
import argparse
import sys
from pathlib import Path

import pcbnew


def export_dsn(pcb_path: Path, dsn_path: Path) -> None:
    """Export a KiCad PCB to Specctra DSN format."""
    board = pcbnew.LoadBoard(str(pcb_path))

    # Fill zones before export so copper pours are included
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())

    ok = pcbnew.ExportSpecctraDSN(board, str(dsn_path))
    if not ok:
        print(f"Error: ExportSpecctraDSN failed for {pcb_path}", file=sys.stderr)
        sys.exit(1)
    print(f"  Exported: {dsn_path} ({dsn_path.stat().st_size:,} bytes)")


def import_ses(pcb_path: Path, ses_path: Path) -> None:
    """Import freerouting's SES result back into the KiCad PCB."""
    board = pcbnew.LoadBoard(str(pcb_path))

    ok = pcbnew.ImportSpecctraSES(board, str(ses_path))
    if not ok:
        print(f"Error: ImportSpecctraSES failed for {ses_path}", file=sys.stderr)
        sys.exit(1)

    # Save the board with routed traces
    board.Save(str(pcb_path))
    print(f"  Imported routes into: {pcb_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export-dsn", action="store_true", help="Export PCB → DSN")
    group.add_argument("--import-ses", action="store_true", help="Import SES → PCB")
    parser.add_argument("--pcb", type=Path, required=True, help="KiCad PCB file")
    parser.add_argument("--dsn", type=Path, help="Specctra DSN output file")
    parser.add_argument("--ses", type=Path, help="Specctra SES input file")
    args = parser.parse_args()

    if args.export_dsn:
        if not args.dsn:
            parser.error("--dsn is required with --export-dsn")
        export_dsn(args.pcb, args.dsn)
    elif args.import_ses:
        if not args.ses:
            parser.error("--ses is required with --import-ses")
        import_ses(args.pcb, args.ses)


if __name__ == "__main__":
    main()
