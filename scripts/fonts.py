"""Cross-platform font resolution for the render scripts.

Override any slot via FONT_SANS/FONT_SANS_BOLD/FONT_MONO, or set FONT_DIR to a folder of .ttf files.
"""

import os
import sys
from pathlib import Path


def _windows_dir():
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def _candidates(*fs):
    dirs = [
        os.environ.get("FONT_DIR"),
        "/System/Library/Fonts",
        "/System/Library/Fonts/Supplemental",
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype/liberation",
        str(_windows_dir()),
    ]
    for d in dirs:
        if not d:
            continue
        d = Path(d)
        if not d.is_dir():
            continue
        for f in fs:
            p = d / f
            if p.is_file():
                return str(p)
    return None


_FONTS = {
    "sans": (
        os.environ.get(
            "FONT_SANS",
            _candidates("Arial.ttf", "Arial Regular.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf")
            or _candidates("arial.ttf"),
        ),
        os.environ.get(
            "FONT_SANS_BOLD",
            _candidates(
                "Arial Bold.ttf", "Arial-Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"
            ),
        ),
    ),
    "mono": (
        os.environ.get(
            "FONT_MONO",
            _candidates("Menlo.ttc", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf", "consola.ttf"),
        ),
    ),
}


def resolve(name):
    """Return the resolved .ttf/.ttc path for a font slot, or fail with a usable hint."""
    path = _FONTS[name][0]
    if path:
        return path
    raise SystemExit(
        f"No usable {name} font found on {sys.platform}. "
        "Install DejaVu/Liberation fonts or point FONT_DIR (or FONT_SANS/FONT_MONO) at a folder of .ttf files."
    )