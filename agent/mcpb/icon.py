"""
The extension's icon: a white house on a teal rounded tile (the UCC Resolver page's
accent colour), drawn in plain Python so the build needs no image library.

    venv/bin/python3 -m agent.mcpb.icon agent/mcpb/icon.png
"""
from __future__ import annotations

import struct
import sys
import zlib

SIZE = 512
SS = 3                                   # supersamples per axis, for smooth edges
TEAL = (11, 110, 110)
WHITE = (255, 255, 255)


def _inside_tile(x: float, y: float, r: float = 0.18) -> bool:
    dx, dy = max(r - x, 0, x - (1 - r)), max(r - y, 0, y - (1 - r))
    return dx * dx + dy * dy <= r * r


def _inside_house(x: float, y: float) -> bool:
    roof = 0.22 <= y <= 0.48 and abs(x - 0.5) <= (y - 0.22) / 0.26 * 0.32
    body = 0.30 <= x <= 0.70 and 0.47 <= y <= 0.78
    door = 0.45 <= x <= 0.55 and 0.62 <= y <= 0.78
    return (roof or body) and not door


def pixels() -> list:
    rows = []
    for py in range(SIZE):
        row = bytearray([0])                 # PNG filter byte: none
        for px in range(SIZE):
            tile = house = 0
            for sy in range(SS):
                for sx in range(SS):
                    x, y = (px + (sx + 0.5) / SS) / SIZE, (py + (sy + 0.5) / SS) / SIZE
                    if _inside_tile(x, y):
                        tile += 1
                        house += _inside_house(x, y)
            n = SS * SS
            c = WHITE if house * 2 > tile else TEAL
            if 0 < house < tile:             # blend the house edge into the tile
                c = tuple(round(t + (w - t) * house / tile) for t, w in zip(TEAL, WHITE))
            row += bytes(c) + bytes([round(255 * tile / n)])
        rows.append(bytes(row))
    return rows


def png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)    # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"".join(pixels()), 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    with open(sys.argv[1], "wb") as f:
        f.write(png())
