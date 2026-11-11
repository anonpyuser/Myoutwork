#!/usr/bin/env python3
"""Regenerate the PWA icons. Stdlib-only — delegates to ../icons.py,
which the app also uses to generate icons at startup when missing."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import icons

if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "static", "icons")
    print("wrote:", icons.ensure_icons(out, force=True))
