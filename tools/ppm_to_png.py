#!/usr/bin/env python3
"""Convert the PPM preview frames produced by tools/render_face_preview.cpp
into PNG images using only the Python standard library."""

import binascii
import glob
import os
import struct
import sys
import zlib


def chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(
        ">I", binascii.crc32(tag + data) & 0xFFFFFFFF
    )


def write_png(path: str, width: int, height: int, rgb: bytes) -> None:
    raw = b"".join(
        b"\x00" + rgb[y * width * 3 : (y + 1) * width * 3] for y in range(height)
    )
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as handle:
        handle.write(png)


def main() -> int:
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} <ppm_dir>")
        return 1
    ppm_dir = sys.argv[1]
    count = 0
    frames_by_expr: dict[str, list[tuple[str, int, int, bytes]]] = {}
    for ppm in sorted(glob.glob(os.path.join(ppm_dir, "*.ppm"))):
        with open(ppm, "rb") as handle:
            assert handle.readline().strip() == b"P6"
            width, height = (int(v) for v in handle.readline().split())
            assert int(handle.readline()) == 255
            rgb = handle.read(width * height * 3)
        png = ppm[: -len(".ppm")] + ".png"
        write_png(png, width, height, rgb)
        expr = os.path.basename(ppm).split("_t")[0]
        frames_by_expr.setdefault(expr, []).append((ppm, width, height, rgb))
        count += 1

    for expr, frames in frames_by_expr.items():
        if len(frames) < 2:
            continue
        cols = 4
        rows = (len(frames) + cols - 1) // cols
        width, height = frames[0][1], frames[0][2]
        canvas = bytearray(width * cols * height * rows * 3)
        for index, (_, _, _, rgb) in enumerate(frames):
            cx, cy = index % cols, index // cols
            for row in range(height):
                src = rgb[row * width * 3 : (row + 1) * width * 3]
                dst = (cy * height + row) * width * cols * 3 + cx * width * 3
                canvas[dst : dst + len(src)] = src
        write_png(os.path.join(ppm_dir, f"{expr}_grid.png"), width * cols,
                  height * rows, bytes(canvas))
    print(f"wrote {count} png(s) to {ppm_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
