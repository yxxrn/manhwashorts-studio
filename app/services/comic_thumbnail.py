"""Comic-specific thumbnail transforms and palette policy."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from PIL import Image, ImageStat

RGBA = tuple[int, int, int, int]


def cleanup_yellow_dialogue(image_path: Path) -> None:
    """Clear text inside large flat yellow comic balloons on thumbnail derivatives."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        return
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    yellow = ((hue >= 18) & (hue <= 42) & (saturation >= 100) & (value >= 170)).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (31, 17))
    closed = cv2.morphologyEx(yellow, cv2.MORPH_CLOSE, kernel, iterations=2)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    frame_height, frame_width = image.shape[:2]
    frame_area = frame_height * frame_width
    changed = False
    for label in range(1, count):
        _x, y, width, height, area = (int(v) for v in stats[label])
        if y > frame_height * 0.45 or not frame_area * 0.006 <= area <= frame_area * 0.36:
            continue
        if width < 70 or height < 40:
            continue
        component = labels == label
        dark = ((gray < 145) & component).astype(np.uint8) * 255
        glyph_count, _, glyph_stats, _ = cv2.connectedComponentsWithStats(dark, 8)
        glyphs = 0
        for glyph_label in range(1, glyph_count):
            _gx, _gy, gw, gh, ga = (int(v) for v in glyph_stats[glyph_label])
            density = ga / max(1, gw * gh)
            if (
                3 <= ga <= 1800
                and 2 <= gw <= 110
                and 3 <= gh <= 85
                and gw * gh <= 6000
                and 0.04 <= density <= 0.95
            ):
                glyphs += 1
        if glyphs < 20:
            continue
        erode_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        interior = cv2.erode(component.astype(np.uint8) * 255, erode_kernel, iterations=1)
        color_samples = image[component & (yellow > 0)]
        if len(color_samples) < 20:
            continue
        image[interior > 0] = np.median(color_samples, axis=0).astype(np.uint8)
        changed = True
    if changed:
        cv2.imwrite(str(image_path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 95])


def dual_contrast_palette(
    image: Image.Image,
    placement: str,
    colors: Mapping[str, RGBA],
) -> tuple[tuple[str, RGBA], tuple[str, RGBA]]:
    """Choose two non-white comic headline colors against the placement region."""
    regions = {"top": (0.05, 0.32), "middle": (0.35, 0.65), "bottom": (0.68, 0.95)}
    top, bottom = regions.get(placement, regions["top"])
    sample = image.crop((0, int(image.height * top), image.width, int(image.height * bottom)))
    sample = sample.resize((32, 32))
    bg = tuple(float(value) for value in ImageStat.Stat(sample.convert("RGB")).mean[:3])
    def score(item: tuple[str, RGBA]) -> float:
        fg = item[1][:3]
        distance = sum((float(fg[i]) - bg[i]) ** 2 for i in range(3)) ** 0.5 / 441.7
        fg_luma = (0.2126 * fg[0] + 0.7152 * fg[1] + 0.0722 * fg[2]) / 255.0
        bg_luma = (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) / 255.0
        return abs(fg_luma - bg_luma) + 0.75 * distance

    choices = [(name, colors[name]) for name in ("yellow", "red", "cyan")]
    choices.sort(key=score, reverse=True)
    return choices[0], choices[1]
