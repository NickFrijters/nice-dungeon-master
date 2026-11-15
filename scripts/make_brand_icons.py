"""Draw the neutral d20 glyph used for the logo, app icons and favicon.

Pure standard library (no Pillow). Run from the repo root:
    python scripts/make_brand_icons.py
It overwrites the PNG/ICO files in skills/dnd/display/icons/.
"""
import math
import pathlib
import struct
import zlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "skills/dnd/display/icons"
GOLD = (217, 180, 91)
BG = (27, 20, 36)


def glyph_segments():
    """Unit-space (-1..1) line segments of a d20 seen from the front."""
    hexv = [(math.sin(math.radians(a)), -math.cos(math.radians(a)))
            for a in range(0, 360, 60)]
    r = 0.5
    tri = [(r * math.sin(math.radians(a)), -r * math.cos(math.radians(a)))
           for a in (0, 120, 240)]
    segs = [(hexv[i], hexv[(i + 1) % 6]) for i in range(6)]
    segs += [(tri[i], tri[(i + 1) % 3]) for i in range(3)]
    # each triangle corner reaches out to the two nearest hexagon corners
    for t, pts in zip(tri, ((0, 1, 5), (2, 3, 1), (4, 5, 3))):
        for h in pts[1:]:
            segs.append((t, hexv[h]))
        segs.append((t, hexv[pts[0]]))
    return segs


def dist(px, py, a, b):
    ax, ay = a; bx, by = b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def render(size, scale, background):
    segs = glyph_segments()
    half = size / 2
    w = max(1.2, size * 0.035)
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            ux = (x + 0.5 - half) / (half * scale)
            uy = (y + 0.5 - half) / (half * scale)
            d = min(dist(ux, uy, a, b) for a, b in segs) * half * scale
            cov = max(0.0, min(1.0, w / 2 - d + 0.5))
            if background:
                # rounded square background
                cx, cy = abs(x + 0.5 - half) - (half - size * 0.22), abs(y + 0.5 - half) - (half - size * 0.22)
                bd = math.hypot(max(cx, 0), max(cy, 0)) - size * 0.22
                bcov = max(0.0, min(1.0, 0.5 - bd))
                col = tuple(round(BG[i] * (1 - cov) + GOLD[i] * cov) for i in range(3))
                row += bytes(col) + bytes([round(255 * bcov)])
            else:
                row += bytes(GOLD) + bytes([round(255 * cov)])
        rows.append(bytes(row))
    return rows


def png(size, rows):
    raw = b"".join(b"\x00" + r for r in rows)
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    jobs = {"logo_primary_fullcolor.png": (512, 0.94, False),
            "app_icon_32.png": (32, 0.78, True),
            "app_icon_180.png": (180, 0.78, True),
            "app_icon_192.png": (192, 0.78, True)}
    for name, (s, sc, bg) in jobs.items():
        (OUT / name).write_bytes(png(s, render(s, sc, bg)))
    entries = [(s, png(s, render(s, 0.78, True))) for s in (16, 32)]
    head = struct.pack("<HHH", 0, 1, len(entries))
    off = 6 + 16 * len(entries)
    dirs, blobs = b"", b""
    for s, data in entries:
        dirs += struct.pack("<BBBBHHII", s, s, 0, 0, 1, 32, len(data), off + len(blobs))
        blobs += data
    (OUT / "favicon.ico").write_bytes(head + dirs + blobs)


if __name__ == "__main__":
    main()
