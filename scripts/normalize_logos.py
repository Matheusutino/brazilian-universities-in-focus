"""Normalize university logos so they look the same size on the website.

Each logo is trimmed to its visible pixels and placed on a square transparent
canvas. Square and tall logos are scaled to the same visual area; wide logos
use the full width instead, because area alone would make them too thin.

    uv run --with pillow python scripts/normalize_logos.py SOURCE_DIR \
        src/university_news/site/static/logos

SOURCE_DIR holds one image per university named after its site key
(``usp.png``, ``ufc.jpg``…). Output files are ``<site>.png``.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageChops

CANVAS = 192
# Visible logo covers at most this share of the canvas side...
MAX_EXTENT = 0.92
# ...and at most this share of its area, so compact logos are not oversized.
TARGET_AREA = 0.56
SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def visible_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Bounding box of non-transparent, non-white pixels."""
    alpha = image.getchannel("A").point(lambda value: 255 if value > 16 else 0)
    white = Image.new("RGB", image.size, (255, 255, 255))
    difference = ImageChops.difference(image.convert("RGB"), white).convert("L")
    ink = difference.point(lambda value: 255 if value > 24 else 0)
    return ImageChops.multiply(alpha, ink).getbbox()


def normalize(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    box = visible_box(image)
    if box:
        image = image.crop(box)
    width, height = image.size
    contain = min(CANVAS * MAX_EXTENT / width, CANVAS * MAX_EXTENT / height)
    optical = math.sqrt(TARGET_AREA * CANVAS * CANVAS / (width * height))
    scale = min(contain, optical)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    resized = image.resize(size, Image.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (255, 255, 255, 0))
    canvas.paste(resized, ((CANVAS - size[0]) // 2, (CANVAS - size[1]) // 2), resized)
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for path in sorted(args.source.iterdir()):
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        target = args.output / f"{path.stem.lower()}.png"
        normalize(path).save(target, optimize=True)
        print(f"{path.name} -> {target.name}")


if __name__ == "__main__":
    main()
