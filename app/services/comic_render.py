"""Comic-specific final-render transforms.

This module deliberately does not perform story analysis. It only transforms
already-selected render derivatives and selects one locked karaoke color.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from PIL import Image


class AdaptiveKaraokeError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


COMIC_TEXT_CLEANUP_VERSION = "comic-render-text-cleanup-v2"
ADAPTIVE_KARAOKE_CONTRAST_VERSION = "adaptive-karaoke-contrast-v2"
_ADAPTIVE_KARAOKE_COLORS: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("yellow", (255, 235, 40)),
    ("cyan", (40, 225, 255)),
    ("red", (255, 70, 70)),
)
_ADAPTIVE_KARAOKE_MIN_DISTANCE = 0.24
_ADAPTIVE_KARAOKE_MIN_LUMA_CONTRAST = 1.04


def _ass_bgr(rgb: tuple[int, int, int]) -> str:
    red, green, blue = rgb
    return f"{blue:02X}{green:02X}{red:02X}"


def _relative_luma(rgb: tuple[int, int, int]) -> float:
    red, green, blue = (component / 255.0 for component in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _sample_karaoke_background(image_path: Path, *, anchor: tuple[float, float] = (0.50, 0.56)) -> tuple[int, int, int]:
    with Image.open(image_path) as raw:
        image = raw.convert("RGB")
        width, height = image.size
        left = max(0, round(width * 0.14))
        right = min(width, round(width * 0.86))
        top = max(0, round(height * max(0.0, anchor[1] - 0.12)))
        bottom = min(height, round(height * min(1.0, anchor[1] + 0.12)))
        sample = image.crop((left, top, right, bottom)).resize((32, 32), Image.Resampling.BILINEAR)
        pixels = list(sample.getdata())
    return tuple(round(sum(pixel[i] for pixel in pixels) / max(1, len(pixels))) for i in range(3))


def select_locked_karaoke_color(image_paths: Sequence[Path]) -> dict[str, object]:
    started = time.perf_counter()
    samples = [_sample_karaoke_background(path) for path in image_paths]
    if not samples:
        raise AdaptiveKaraokeError("adaptive karaoke requires prepared frames", code="subtitle.adaptive_color_missing")
    metrics: list[dict[str, object]] = []
    for name, rgb in _ADAPTIVE_KARAOKE_COLORS:
        distances = [sum((float(a) - float(b)) ** 2 for a, b in zip(sample, rgb, strict=True)) ** 0.5 / 441.673 for sample in samples]
        candidate_luma = _relative_luma(rgb)
        contrasts = [(max(_relative_luma(sample), candidate_luma) + 0.05) / (min(_relative_luma(sample), candidate_luma) + 0.05) for sample in samples]
        metrics.append({"name": name, "rgb": rgb, "min_distance": min(distances), "avg_distance": sum(distances) / len(distances), "min_luma_contrast": min(contrasts), "avg_luma_contrast": sum(contrasts) / len(contrasts)})
    selected = next((row for row in metrics if float(row["min_distance"]) >= _ADAPTIVE_KARAOKE_MIN_DISTANCE and float(row["min_luma_contrast"]) >= _ADAPTIVE_KARAOKE_MIN_LUMA_CONTRAST), None)
    if selected is None:
        selected = max(metrics, key=lambda row: (float(row["min_distance"]) + 0.35 * float(row["min_luma_contrast"]), float(row["avg_distance"])))
    rgb = tuple(int(value) for value in selected["rgb"])
    return {"version": ADAPTIVE_KARAOKE_CONTRAST_VERSION, "mode": "per_video_locked", "name": selected["name"], "ass_color": _ass_bgr(rgb), "sample_count": len(samples), "samples_rgb": [list(sample) for sample in samples], "candidates": [{key: (round(value, 4) if isinstance(value, float) else value) for key, value in row.items() if key != "rgb"} for row in metrics], "wall_s": round(time.perf_counter() - started, 6)}

def cleanup_comic_frame_heuristic(image_path: Path) -> dict[str, object]:
    """Legacy fallback when local OCR is unavailable."""
    started = time.perf_counter()
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {"version": COMIC_TEXT_CLEANUP_VERSION, "applied": False, "reason": "opencv_unavailable", "wall_s": round(time.perf_counter() - started, 6)}
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        return {"version": COMIC_TEXT_CLEANUP_VERSION, "applied": False, "reason": "unreadable", "wall_s": round(time.perf_counter() - started, 6)}
    total_components = 0
    pass_ratios: list[float] = []
    applied_passes = 0
    union_mask = None
    for _pass in range(2):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        background = cv2.medianBlur(gray, 31)
        contrast = background.astype(np.int16) - gray.astype(np.int16)
        raw_mask = ((contrast >= 42) & (background >= 138) & (gray <= 175)).astype(np.uint8) * 255
        count, labels, stats, _ = cv2.connectedComponentsWithStats(raw_mask, 8)
        glyph_mask = np.zeros_like(raw_mask)
        kept = 0
        for label in range(1, count):
            _x, _y, width, height, area = (int(v) for v in stats[label])
            box_area = max(1, width * height)
            density = area / box_area
            if not (3 <= area <= 1250 and 2 <= width <= 90 and 3 <= height <= 58 and box_area <= 4200 and 0.055 <= density <= 0.94):
                continue
            glyph_mask[labels == label] = 255
            kept += 1
        if kept == 0:
            break
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.dilate(glyph_mask, kernel, iterations=1)
        mask_ratio = float(np.count_nonzero(mask)) / float(mask.size)
        if mask_ratio > 0.13:
            if applied_passes == 0:
                return {"version": COMIC_TEXT_CLEANUP_VERSION, "applied": False, "reason": "mask_safety_limit", "glyph_components": kept, "mask_ratio": round(mask_ratio, 6), "wall_s": round(time.perf_counter() - started, 6)}
            break
        image = cv2.inpaint(image, mask, 3.0, cv2.INPAINT_TELEA)
        union_mask = mask.copy() if union_mask is None else cv2.bitwise_or(union_mask, mask)
        total_components += kept
        pass_ratios.append(round(mask_ratio, 6))
        applied_passes += 1
    if applied_passes == 0:
        return {"version": COMIC_TEXT_CLEANUP_VERSION, "applied": False, "reason": "no_text_glyphs", "glyph_components": 0, "mask_ratio": 0.0, "wall_s": round(time.perf_counter() - started, 6)}
    if union_mask is not None:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        background = cv2.medianBlur(gray, 31)
        neighborhood = cv2.dilate(union_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)), iterations=1)
        residual = (((background.astype(np.int16) - gray.astype(np.int16)) >= 16) & (background >= 138) & (gray <= 215) & (neighborhood > 0)).astype(np.uint8) * 255
        residual = cv2.dilate(residual, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1)
        residual_ratio = float(np.count_nonzero(residual)) / float(residual.size)
        if 0.0 < residual_ratio <= 0.035:
            image = cv2.inpaint(image, residual, 2.0, cv2.INPAINT_TELEA)
        else:
            residual_ratio = 0.0
    else:
        residual_ratio = 0.0
    cv2.imwrite(str(image_path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 94])
    return {"version": COMIC_TEXT_CLEANUP_VERSION, "applied": True, "reason": "glyph_inpaint", "passes": applied_passes, "glyph_components": total_components, "mask_ratio": round(sum(pass_ratios), 6), "pass_mask_ratios": pass_ratios, "residual_mask_ratio": round(residual_ratio, 6), "wall_s": round(time.perf_counter() - started, 6)}


def cleanup_comic_frame(image_path: Path) -> dict[str, object]:
    """Remove text only from final comic render derivatives.

    Analysis assets remain untouched.  Tesseract is used only as a sparse
    locator for text on verified flat white/yellow containers; it is never used
    as story evidence.  When OCR is unavailable we fall back to the bounded v1
    heuristic instead of failing production.
    """
    started = time.perf_counter()
    if shutil.which("tesseract") is None:
        fallback = dict(cleanup_comic_frame_heuristic(image_path))
        fallback["version"] = COMIC_TEXT_CLEANUP_VERSION
        fallback["reason"] = f"ocr_unavailable:{fallback.get('reason', 'heuristic')}"
        fallback["ocr_used"] = False
        return fallback
    try:
        import csv
        import io

        import cv2
        import numpy as np
    except ImportError:
        fallback = dict(cleanup_comic_frame_heuristic(image_path))
        fallback["version"] = COMIC_TEXT_CLEANUP_VERSION
        fallback["reason"] = f"ocr_dependency_unavailable:{fallback.get('reason', 'heuristic')}"
        fallback["ocr_used"] = False
        return fallback

    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        return {"version": COMIC_TEXT_CLEANUP_VERSION, "applied": False, "reason": "unreadable", "ocr_used": False, "wall_s": round(time.perf_counter() - started, 6)}

    ocr_started = time.perf_counter()
    try:
        proc = subprocess.run(
            ["tesseract", str(image_path), "stdout", "--psm", "11", "tsv"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    ocr_wall_s = time.perf_counter() - ocr_started
    if proc is None or proc.returncode != 0:
        fallback = dict(cleanup_comic_frame_heuristic(image_path))
        fallback["version"] = COMIC_TEXT_CLEANUP_VERSION
        fallback["reason"] = f"ocr_failed:{fallback.get('reason', 'heuristic')}"
        fallback["ocr_used"] = False
        fallback["ocr_wall_s"] = round(ocr_wall_s, 6)
        return fallback

    groups: dict[tuple[int, int], list[tuple[int, int, int, int, float, str]]] = {}
    for row in csv.DictReader(io.StringIO(proc.stdout), delimiter="\t"):
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        try:
            confidence = float(row.get("conf") or -1)
            x, y = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
            key = (int(row["block_num"]), int(row["par_num"]))
        except (KeyError, TypeError, ValueError):
            continue
        if confidence < 5 or width < 2 or height < 3:
            continue
        groups.setdefault(key, []).append((x, y, width, height, confidence, text))

    def color_class(median: object) -> str | None:
        b, g, r = (float(value) for value in median)
        if min(b, g, r) >= 220 and max(b, g, r) - min(b, g, r) <= 28:
            return "white"
        if r >= 205 and g >= 180 and b <= 85 and r - g <= 85:
            return "yellow"
        return None

    records: list[dict[str, object]] = []
    for words in groups.values():
        if sum(len(word[5]) for word in words) < 4:
            continue
        x0 = min(word[0] for word in words)
        y0 = min(word[1] for word in words)
        x1 = max(word[0] + word[2] for word in words)
        y1 = max(word[1] + word[3] for word in words)
        patch = image[y0:y1, x0:x1]
        if patch.size == 0:
            continue
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        bright = patch[gray >= 145]
        if len(bright) < patch.shape[0] * patch.shape[1] * 0.42:
            continue
        median = np.median(bright, axis=0)
        mad = float(np.median(np.abs(bright - median), axis=0).mean())
        close_fraction = float(np.mean(np.linalg.norm(bright.astype(float) - median, axis=1) < 45))
        container_class = color_class(median)
        if container_class is None or mad > 15 or close_fraction < 0.62:
            continue
        records.append({"box": (x0, y0, x1, y1), "median": median, "weight": max(1, len(bright)), "class": container_class, "parts": 1})

    def interval_overlap(a0: int, a1: int, b0: int, b1: int) -> int:
        return max(0, min(a1, b1) - max(a0, b0))

    merged = [dict(record) for record in records]
    changed = True
    while changed:
        changed = False
        for left_index in range(len(merged)):
            if changed:
                break
            left = merged[left_index]
            ax0, ay0, ax1, ay1 = left["box"]
            for right_index in range(left_index + 1, len(merged)):
                right = merged[right_index]
                if left["class"] != right["class"]:
                    continue
                bx0, by0, bx1, by1 = right["box"]
                horizontal_overlap = interval_overlap(ax0, ax1, bx0, bx1) / max(1, min(ax1 - ax0, bx1 - bx0))
                vertical_overlap = interval_overlap(ay0, ay1, by0, by1) / max(1, min(ay1 - ay0, by1 - by0))
                vertical_gap = max(0, max(ay0, by0) - min(ay1, by1))
                horizontal_gap = max(0, max(ax0, bx0) - min(ax1, bx1))
                if not ((horizontal_overlap >= 0.18 and vertical_gap <= 70) or (vertical_overlap >= 0.30 and horizontal_gap <= 45)):
                    continue
                left_weight = int(left["weight"])
                right_weight = int(right["weight"])
                median = (np.asarray(left["median"]) * left_weight + np.asarray(right["median"]) * right_weight) / (left_weight + right_weight)
                merged[left_index] = {
                    "box": (min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1)),
                    "median": median,
                    "weight": left_weight + right_weight,
                    "class": left["class"],
                    "parts": int(left["parts"]) + int(right["parts"]),
                }
                merged.pop(right_index)
                changed = True
                break

    cleaned = image.copy()
    frame_height, frame_width = cleaned.shape[:2]
    for record in merged:
        x0, y0, x1, y1 = record["box"]
        pad = max(4, min(14, round(min(x1 - x0, y1 - y0) * 0.06)))
        x0 = max(0, x0 - pad)
        y0 = max(0, y0 - pad)
        x1 = min(frame_width, x1 + pad)
        y1 = min(frame_height, y1 + pad)
        cleaned[y0:y1, x0:x1] = np.asarray(record["median"], dtype=np.uint8)

    yellow_fallback_regions = 0
    # Rare classic-comic case: OCR can miss stylized yellow balloons entirely.
    # Only use this fallback when no verified flat OCR block exists.
    if not merged:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        hue, saturation, value = cv2.split(hsv)
        yellow = ((hue >= 18) & (hue <= 42) & (saturation >= 100) & (value >= 170)).astype(np.uint8) * 255
        closed = cv2.morphologyEx(yellow, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (31, 17)), iterations=2)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        frame_area = frame_height * frame_width
        for label in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[label])
            if y > frame_height * 0.45 or not frame_area * 0.006 <= area <= frame_area * 0.28 or width < 70 or height < 40:
                continue
            component = labels == label
            dark = ((gray < 145) & component).astype(np.uint8) * 255
            glyph_count, _, glyph_stats, _ = cv2.connectedComponentsWithStats(dark, 8)
            glyphs = 0
            for glyph_label in range(1, glyph_count):
                _gx, _gy, glyph_width, glyph_height, glyph_area = (int(value) for value in glyph_stats[glyph_label])
                density = glyph_area / max(1, glyph_width * glyph_height)
                if 3 <= glyph_area <= 1800 and 2 <= glyph_width <= 110 and 3 <= glyph_height <= 85 and glyph_width * glyph_height <= 6000 and 0.04 <= density <= 0.95:
                    glyphs += 1
            if glyphs < 20:
                continue
            component_mask = component.astype(np.uint8) * 255
            interior = cv2.erode(component_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=1)
            color_samples = image[component & (yellow > 0)]
            if len(color_samples) < 20:
                continue
            median = np.median(color_samples, axis=0).astype(np.uint8)
            cleaned[interior > 0] = median
            yellow_fallback_regions += 1

    applied = bool(merged or yellow_fallback_regions)
    if applied:
        cv2.imwrite(str(image_path), cleaned, [int(cv2.IMWRITE_JPEG_QUALITY), 94])
    return {
        "version": COMIC_TEXT_CLEANUP_VERSION,
        "applied": applied,
        "reason": "ocr_flat_container_cleanup" if applied else "no_verified_flat_text",
        "ocr_used": True,
        "ocr_word_groups": len(groups),
        "verified_blocks": len(records),
        "merged_blocks": len(merged),
        "yellow_fallback_regions": yellow_fallback_regions,
        "ocr_wall_s": round(ocr_wall_s, 6),
        "wall_s": round(time.perf_counter() - started, 6),
    }
