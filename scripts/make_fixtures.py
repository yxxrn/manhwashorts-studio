#!/usr/bin/env python3
"""Generate synthetic manhwa-style panels for local testing.

These are plain gradient frames with a label, not artwork. They exist so the
render pipeline can be exercised without shipping any copyrighted material.
"""

from __future__ import annotations

import argparse
import colorsys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PANEL_SIZES = [
    (1200, 1600),  # portrait page
    (1600, 900),   # wide establishing shot
    (1000, 1000),  # square
    (900, 1600),   # tall
]

LABELS = [
    "PANEL 01 - dungeon entrance",
    "PANEL 02 - the system appears",
    "PANEL 03 - brutal training",
    "PANEL 04 - punishment zone",
    "PANEL 05 - power surge",
    "PANEL 06 - the challenger",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def make_panel(index: int, dest: Path) -> Path:
    width, height = PANEL_SIZES[index % len(PANEL_SIZES)]
    hue = (index * 0.16) % 1.0
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # Vertical gradient background.
    for y in range(height):
        t = y / max(1, height - 1)
        r, g, b = colorsys.hsv_to_rgb(hue, 0.45, 0.25 + 0.45 * (1 - t))
        draw.line([(0, y), (width, y)], fill=(int(r * 255), int(g * 255), int(b * 255)))

    # A framed subject box so crop/focus behaviour is visible in output.
    box_w, box_h = int(width * 0.5), int(height * 0.32)
    box_x, box_y = (width - box_w) // 2, int(height * 0.24)
    draw.rounded_rectangle(
        [box_x, box_y, box_x + box_w, box_y + box_h],
        radius=24,
        outline=(240, 240, 250),
        width=6,
    )
    draw.text(
        (box_x + 24, box_y + box_h // 2 - 18),
        f"SUBJECT {index + 1}",
        font=_font(max(28, width // 26)),
        fill=(245, 245, 255),
    )

    label = LABELS[index % len(LABELS)]
    draw.text((32, height - 90), label, font=_font(max(22, width // 34)), fill=(230, 230, 240))
    draw.text(
        (32, 32),
        f"{width}x{height}",
        font=_font(max(20, width // 40)),
        fill=(200, 200, 215),
    )

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=92)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--out", type=Path, default=Path("data/fixtures/panels"))
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for i in range(args.count):
        path = make_panel(i, args.out / f"panel{i + 1:02d}.jpg")
        print(f"wrote {path} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
