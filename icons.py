#!/usr/bin/env python3
"""PWA icon generation, stdlib-only (no Pillow).

Design: a bold safety-orange barbell glyph on a near-black background,
drawn on a 512 grid — bar, three plates per side, collars. All shapes are
rectangles, rasterized per pixel and encoded as PNG with zlib.

The app calls ensure_icons() at startup so the icons exist even though
they are not committed to git (the GitHub snapshot push is text-only).
"""
import os
import struct
import zlib

BG = (11, 11, 11)
ORANGE = (255, 106, 0)
ICONS = [("icon-192.png", 192), ("icon-512.png", 512),
         ("apple-touch-icon.png", 180)]


def _rects():
    """(x0, y0, x1, y1) rectangles on the 512 design grid."""
    cy = 256
    rects = [(56, cy - 13, 456, cy + 13)]                     # bar
    for cx, w, h in [(96, 26, 190), (134, 22, 150), (168, 18, 112)]:
        rects.append((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
        rects.append((512 - cx - w / 2, cy - h / 2,
                      512 - cx + w / 2, cy + h / 2))          # mirrored
    for cx in (196, 316):                                     # collars
        rects.append((cx - 7, cy - 34, cx + 7, cy + 34))
    return rects


def _png_bytes(size):
    rects = _rects()
    scale = size / 512.0
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter: none
        dy = y / scale
        for x in range(size):
            dx = x / scale
            px = BG
            for x0, y0, x1, y1 in rects:
                if x0 <= dx < x1 and y0 <= dy < y1:
                    px = ORANGE
                    break
            raw += bytes(px)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def ensure_icons(out_dir, force=False):
    """Write any missing icon PNGs into out_dir. Returns names written."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, size in ICONS:
        path = os.path.join(out_dir, name)
        if force or not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(_png_bytes(size))
            written.append(name)
    return written


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "static", "icons")
    print("wrote:", ensure_icons(here, force=True))
