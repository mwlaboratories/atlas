#!/usr/bin/env python3
"""Fetch Matterhorn elevation data from AWS Terrarium tiles and save as terrain.json."""
import json
import math
import struct
import urllib.request
import zlib
from pathlib import Path


def decode_png(path):
    """Minimal PNG decoder — extract RGB pixels."""
    with open(path, "rb") as f:
        sig = f.read(8)
        assert sig == b"\x89PNG\r\n\x1a\n", "Not a PNG"

        width = height = 0
        color_type = 0
        idat_chunks = []

        while True:
            chunk_len = struct.unpack(">I", f.read(4))[0]
            chunk_type = f.read(4)
            chunk_data = f.read(chunk_len)
            f.read(4)  # crc

            if chunk_type == b"IHDR":
                width = struct.unpack(">I", chunk_data[0:4])[0]
                height = struct.unpack(">I", chunk_data[4:8])[0]
                color_type = chunk_data[9]
            elif chunk_type == b"IDAT":
                idat_chunks.append(chunk_data)
            elif chunk_type == b"IEND":
                break

        raw = zlib.decompress(b"".join(idat_chunks))
        bpp = 4 if color_type == 6 else 3
        stride = width * bpp

        prev_row = [0] * stride
        pixels = []
        pos = 0

        for _y in range(height):
            filter_type = raw[pos]
            pos += 1
            row_data = list(raw[pos : pos + stride])

            if filter_type == 1:  # Sub
                for i in range(bpp, stride):
                    row_data[i] = (row_data[i] + row_data[i - bpp]) & 0xFF
            elif filter_type == 2:  # Up
                for i in range(stride):
                    row_data[i] = (row_data[i] + prev_row[i]) & 0xFF
            elif filter_type == 3:  # Average
                for i in range(stride):
                    a = row_data[i - bpp] if i >= bpp else 0
                    row_data[i] = (row_data[i] + (a + prev_row[i]) // 2) & 0xFF
            elif filter_type == 4:  # Paeth
                for i in range(stride):
                    a = row_data[i - bpp] if i >= bpp else 0
                    b = prev_row[i]
                    c = prev_row[i - bpp] if i >= bpp else 0
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    if pa <= pb and pa <= pc:
                        pr = a
                    elif pb <= pc:
                        pr = b
                    else:
                        pr = c
                    row_data[i] = (row_data[i] + pr) & 0xFF

            prev_row = row_data[:]
            pos += stride

            row_pixels = []
            for x in range(width):
                r = row_data[x * bpp]
                g = row_data[x * bpp + 1]
                b = row_data[x * bpp + 2]
                row_pixels.append((r, g, b))
            pixels.append(row_pixels)

    return width, height, pixels


def main():
    # Matterhorn: 45.9763N, 7.6586E
    zoom = 10
    lat, lon = 45.9763, 7.6586

    n = 2**zoom
    x_tile = int((lon + 180) / 360 * n)
    lat_rad = math.radians(lat)
    y_tile = int(
        (1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * n
    )

    url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zoom}/{x_tile}/{y_tile}.png"
    print(f"Fetching z={zoom} x={x_tile} y={y_tile} ...")

    tmp = "/tmp/terrain_tile.png"
    req = urllib.request.Request(url, headers={"User-Agent": "atlas-keyboard/1.0"})
    resp = urllib.request.urlopen(req, timeout=30)
    data = resp.read()
    with open(tmp, "wb") as f:
        f.write(data)
    print(f"  Downloaded {len(data)} bytes")

    print("Decoding...")
    w, h, pixels = decode_png(tmp)

    # Terrarium encoding: elevation = (R * 256 + G + B / 256) - 32768
    elevations = []
    for row in pixels:
        elev_row = []
        for r, g, b in row:
            elev = (r * 256 + g + b / 256) - 32768
            elev_row.append(round(elev, 1))
        elevations.append(elev_row)

    emin = min(min(r) for r in elevations)
    emax = max(max(r) for r in elevations)

    # Find peak
    peak_h = 0
    for y in range(h):
        for x in range(w):
            if elevations[y][x] > peak_h:
                peak_h = elevations[y][x]

    print(f"  {w}x{h}, elevation {emin:.0f}m – {emax:.0f}m, peak {peak_h:.0f}m")

    out = Path(__file__).parent / "terrain.json"
    out.write_text(json.dumps(elevations, separators=(",", ":")))
    size_kb = out.stat().st_size / 1024
    print(f"  -> {out} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
