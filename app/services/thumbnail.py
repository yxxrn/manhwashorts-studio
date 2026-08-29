"""Automatic upload-ready thumbnail generation for successful final renders.

The generator deliberately rebuilds a clean still from the persisted source panel
instead of screenshotting the burned-in final video. This keeps karaoke subtitles
out of the thumbnail while preserving the exact scene/panel lineage chosen by the
editorial pipeline.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat

from app.config import settings
from app.services import visual_scoring

THUMBNAIL_CONTRACT_VERSION = "auto-thumbnail-v1"
TARGET_SIZE = (1080, 1920)
MAX_HEADLINE_WORDS = 7
MAX_HEADLINE_CHARS = 38


class ThumbnailError(RuntimeError):
    """Stable error raised when an upload-ready thumbnail cannot be produced."""


@dataclass(frozen=True)
class HeadlineCandidate:
    text: str
    source_section: str = ""
    style: str = "clickbait"
    anchor_terms: tuple[str, ...] = ()
    strength: float = 1.0


@dataclass(frozen=True)
class VisualCandidate:
    scene_index: int
    section: str
    score: float
    visual_score: float
    alignment_score: float
    placement: str
    text_safe_score: float
    face_boxes: tuple[tuple[float, float, float, float], ...]
    image_jpeg: bytes
    source_asset_id: str
    source_family: str
    roi_label: str
    focus_y: float


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _script_sections(script: object) -> tuple[dict[str, str], str]:
    rows = getattr(script, "sections", None) or []
    sections: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        section = str(row.get("section") or "").strip().lower()
        text = str(row.get("text") or "").strip()
        if section and text:
            sections[section] = text
    hooks = list(getattr(script, "hook_options", None) or [])
    selected = int(getattr(script, "selected_hook", 0) or 0)
    if hooks and 0 <= selected < len(hooks) and str(hooks[selected]).strip():
        sections["hook"] = str(hooks[selected]).strip()
    joined = " ".join(sections.get(key, "") for key in ("hook", "setup", "conflict", "twist", "cta"))
    return sections, joined.strip()


def _language(story: str) -> str:
    tokens = set(re.findall(r"[a-zA-Z]+", story.lower()))
    markers = {"yang", "dan", "dengan", "dia", "ternyata", "akhirnya", "seorang", "ketika", "mereka"}
    return "id" if len(tokens & markers) >= 2 else "en"


def _clean_headline(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip()).strip("\"'` ")
    text = re.sub(r"[^A-Za-zÀ-ÿ0-9?!' -]+", "", text)
    words = text.split()[:MAX_HEADLINE_WORDS]
    while words and len(" ".join(words)) > MAX_HEADLINE_CHARS:
        words.pop()
    return " ".join(words).upper().strip()


def _fallback_headlines(sections: Mapping[str, str], language: str) -> list[HeadlineCandidate]:
    story = " ".join(sections.values()).lower()
    rows: list[HeadlineCandidate] = []

    def add(text: str, section: str, *anchors: str, strength: float = 1.0) -> None:
        clean = _clean_headline(text)
        if clean:
            rows.append(HeadlineCandidate(clean, section, "clickbait", tuple(anchors), strength))

    if language == "id":
        triggers = (
            (("hood", "hooded", "berkerudung", "berjubah"), "SIAPA SOSOK MISTERIUS INI?", "twist", 2.1),
            (("sword", "pedang", "blade", "bilah"), "PEDANG INI MENGUBAH SEGALANYA", "hook", 2.0),
            (("photo", "photograph", "foto"), "FOTO INI MEMBONGKAR SEMUANYA", "cta", 1.9),
            (("smile", "smiling", "senyum", "tersenyum"), "SENYUMNYA MENYIMPAN RAHASIA", "conflict", 2.0),
            (("surprise", "surprised", "shock", "kaget", "terkejut"), "DIA GAK NYANGKA INI TERJADI", "conflict", 2.1),
            (("secret", "truth", "rahasia", "kebenaran"), "RAHASIANYA AKHIRNYA TERBONGKAR", "twist", 2.2),
            (("power", "energy", "kekuatan", "energi"), "KEKUATAN INI GAK MASUK AKAL", "hook", 2.0),
            (("monster", "demon", "iblis", "monster"), "TERNYATA DIA BUKAN MANUSIA", "twist", 2.2),
        )
    else:
        triggers = (
            (("hood", "hooded"), "WHO IS THIS MYSTERIOUS FIGURE?", "twist", 2.1),
            (("sword", "blade"), "THIS SWORD CHANGED EVERYTHING", "hook", 2.0),
            (("photo", "photograph", "picture"), "THESE PHOTOS EXPOSE EVERYTHING", "cta", 1.9),
            (("smile", "smiling"), "SOMETHING ABOUT THIS FEELS WRONG", "conflict", 2.0),
            (("surprise", "surprised", "shock", "shocked"), "NO ONE SAW THIS COMING", "conflict", 2.1),
            (("secret", "truth"), "THE TRUTH FINALLY CAME OUT", "twist", 2.2),
            (("power", "energy"), "THIS POWER SHOULD NOT EXIST", "hook", 2.0),
            (("monster", "demon"), "THAT THING WASN'T HUMAN", "twist", 2.2),
        )
    for needles, text, section, strength in triggers:
        if any(needle in story for needle in needles):
            add(text, section, *needles, strength=strength)

    if language == "id":
        add("TERNYATA INI YANG TERJADI", "twist", "ternyata", strength=1.7)
        add("DIA GAK PERNAH MENYANGKA INI", "conflict", "surprise", strength=1.6)
        add("SATU MOMEN INI MENGUBAH SEMUANYA", "conflict", "change", strength=1.5)
        add("AKHIRNYA SEMUA TERUNGKAP", "twist", "akhirnya", strength=1.6)
    else:
        add("THIS CHANGED EVERYTHING", "conflict", "change", strength=1.7)
        add("THEY NEVER SAW THIS COMING", "conflict", "surprise", strength=1.6)
        add("THE TRUTH WAS RIGHT THERE", "twist", "truth", strength=1.6)
        add("WHAT HAPPENED NEXT?!", "twist", "next", strength=1.5)

    unique: list[HeadlineCandidate] = []
    seen: set[str] = set()
    for row in rows:
        if row.text not in seen:
            unique.append(row)
            seen.add(row.text)
    return unique


def _llm_headlines(sections: Mapping[str, str], language: str) -> list[HeadlineCandidate]:
    if settings.llm_provider != "openai_compatible" or not settings.llm_base_url or not settings.llm_api_key:
        return []
    story = json.dumps(dict(sections), ensure_ascii=False)
    system = (
        "You write extremely clickable short-form manhwa thumbnail headlines. "
        "Stay grounded in the supplied story, but curiosity-gap and mild exaggeration are allowed. "
        "Each headline must be 3-7 words, at most 38 characters, visually punchy, and usable in two lines. "
        "Return strict JSON: {\"headlines\":[{\"text\":str,\"section\":\"hook|setup|conflict|twist|cta\"}]}"
    )
    user = (
        f"Language: {'Indonesian' if language == 'id' else 'English'}\n"
        "Generate six distinct thumbnail headlines. Prioritize conflict, reveal, shock, mystery, or emotional stakes.\n"
        f"Story sections: {story[:5000]}"
    )
    try:
        import httpx

        response = httpx.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.llm_model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.8,
                "response_format": {"type": "json_object"},
            },
            timeout=min(30, settings.llm_timeout),
        )
        response.raise_for_status()
        payload = json.loads(response.json()["choices"][0]["message"]["content"])
    except Exception:
        return []
    rows: list[HeadlineCandidate] = []
    for item in payload.get("headlines", [])[:8] if isinstance(payload, dict) else []:
        if not isinstance(item, Mapping):
            continue
        text = _clean_headline(item.get("text"))
        section = str(item.get("section") or "").lower()
        if text and section in {"hook", "setup", "conflict", "twist", "cta"}:
            rows.append(HeadlineCandidate(text, section, "llm_clickbait", (), 2.25))
    return rows


def generate_headlines(script: object) -> tuple[list[HeadlineCandidate], dict[str, str], str]:
    sections, story = _script_sections(script)
    language = _language(story)
    rows = _llm_headlines(sections, language) + _fallback_headlines(sections, language)
    unique: list[HeadlineCandidate] = []
    seen: set[str] = set()
    for row in rows:
        if row.text and row.text not in seen:
            unique.append(row)
            seen.add(row.text)
    if not unique:
        fallback = "TERNYATA INI YANG TERJADI" if language == "id" else "THIS CHANGED EVERYTHING"
        unique.append(HeadlineCandidate(fallback, "conflict", "fallback", (), 1.0))
    return unique[:10], sections, language


def _accepted_roi(scene: object) -> tuple[int, int, int, int] | None:
    ledger = getattr(scene, "rejected_candidates", None) or []
    for entry in ledger:
        if not isinstance(entry, Mapping) or entry.get("accepted") is not True:
            continue
        telemetry = entry.get("telemetry")
        if not isinstance(telemetry, Mapping):
            continue
        selected = telemetry.get("selected_roi")
        if not isinstance(selected, Mapping):
            continue
        raw = selected.get("crop_box")
        if isinstance(raw, (list, tuple)) and len(raw) == 4:
            try:
                values = tuple(int(v) for v in raw)
            except (TypeError, ValueError):
                continue
            if values[2] > values[0] and values[3] > values[1]:
                return values
    return None


def _panel_bounds(scene: object, width: int, height: int) -> tuple[int, int, int, int] | None:
    raw = getattr(scene, "panel_bounds_json", None)
    if not isinstance(raw, Mapping):
        return None
    try:
        x = int(raw["x"])
        y = int(raw["y"])
        w = int(raw["width"])
        h = int(raw["height"])
    except (KeyError, TypeError, ValueError):
        return None
    box = (max(0, x), max(0, y), min(width, x + w), min(height, y + h))
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _encode_jpeg(image: Image.Image, quality: int = 94) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _clean_scene_image(scene: object, source_path: Path) -> bytes:
    with Image.open(source_path) as source:
        source.load()
        image = source.convert("RGB")
    panel_box = _panel_bounds(scene, image.width, image.height)
    if panel_box is not None:
        image = image.crop(panel_box)
    roi = _accepted_roi(scene)
    if roi is not None:
        x1, y1, x2, y2 = roi
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(image.width, x2), min(image.height, y2)
        if x2 > x1 and y2 > y1:
            image = image.crop((x1, y1, x2, y2))
    focus_x = max(0.05, min(0.95, float(getattr(scene, "focus_x", 0.5) or 0.5)))
    focus_y = max(0.05, min(0.95, float(getattr(scene, "focus_y", 0.4) or 0.4)))
    image = ImageOps.fit(
        image,
        TARGET_SIZE,
        method=Image.Resampling.LANCZOS,
        centering=(focus_x, focus_y),
    )
    image = ImageEnhance.Contrast(image).enhance(1.08)
    image = ImageEnhance.Sharpness(image).enhance(1.12)
    image = ImageEnhance.Color(image).enhance(1.03)
    return _encode_jpeg(image)


def _edge_energy(image: Image.Image, top: float, bottom: float) -> float:
    y1 = int(image.height * top)
    y2 = max(y1 + 1, int(image.height * bottom))
    crop = image.convert("L").crop((0, y1, image.width, y2)).resize((96, 64))
    edges = crop.filter(ImageFilter.FIND_EDGES)
    return min(1.0, ImageStat.Stat(edges).mean[0] / 55.0)


def _box_overlap_y(box: tuple[float, float, float, float], top: float, bottom: float) -> float:
    y1 = max(box[1], top)
    y2 = min(box[3], bottom)
    if y2 <= y1:
        return 0.0
    return min(1.0, (y2 - y1) / max(1e-6, box[3] - box[1]))


def _safe_text_placement(
    image: Image.Image,
    face_boxes: Sequence[tuple[float, float, float, float]],
    focus_y: float,
) -> tuple[str, float, float]:
    regions = {"top": (0.05, 0.34), "bottom": (0.66, 0.95)}
    ranked: list[tuple[float, str, float]] = []
    for name, (top, bottom) in regions.items():
        overlap = max((_box_overlap_y(box, top, bottom) for box in face_boxes), default=0.0)
        edge = _edge_energy(image, top, bottom)
        focus_penalty = 0.45 if top <= focus_y <= bottom else 0.0
        score = 1.5 * (1.0 - overlap) + 0.65 * (1.0 - edge) - focus_penalty
        ranked.append((score, name, overlap))
    ranked.sort(reverse=True)
    best = ranked[0]
    return best[1], round(best[0], 4), round(best[2], 4)


def _largest_face_area(face_boxes: Sequence[tuple[float, float, float, float]]) -> float:
    return max(((x2 - x1) * (y2 - y1) for x1, y1, x2, y2 in face_boxes), default=0.0)


def _face_edge_penalty(face_boxes: Sequence[tuple[float, float, float, float]]) -> float:
    penalty = 0.0
    for x1, y1, x2, y2 in face_boxes:
        if x1 < 0.018 or y1 < 0.018 or x2 > 0.982 or y2 > 0.982:
            penalty += 1.6
    largest = _largest_face_area(face_boxes)
    if largest > 0.46:
        penalty += min(2.3, (largest - 0.46) * 7.0)
    return penalty


def build_visual_candidates(
    scenes: Sequence[object],
    resolve_asset_path: Callable[[str], Path | None],
) -> list[VisualCandidate]:
    candidates: list[VisualCandidate] = []
    section_bonus = {"twist": 2.4, "conflict": 1.9, "hook": 1.5, "setup": 0.6, "cta": -0.8}
    for scene_index, scene in enumerate(scenes):
        asset_id = str(getattr(scene, "asset_id", "") or "")
        source_path = resolve_asset_path(asset_id) if asset_id else None
        if source_path is None or not source_path.is_file():
            continue
        try:
            image_jpeg = _clean_scene_image(scene, source_path)
            analyzed = visual_scoring.analyze_panel(image_jpeg, asset_id, scene_index)
        except (OSError, ValueError, visual_scoring.VisualEvidenceError):
            continue
        features = analyzed.features
        with Image.open(io.BytesIO(image_jpeg)) as decoded:
            frame = decoded.convert("RGB")
            placement, text_safe, _overlap = _safe_text_placement(
                frame, features.face_boxes, float(getattr(scene, "focus_y", 0.4) or 0.4)
            )
        section = str(getattr(scene, "section", "") or "").lower()
        alignment = max(0.0, min(1.0, float(getattr(scene, "alignment_score", 0.0) or 0.0)))
        score = (
            float(analyzed.visual_score)
            + 1.8 * features.face_visibility
            + 1.35 * features.facial_expression
            + 1.15 * features.action_pose
            + 1.25 * features.impact_frame
            + 1.15 * features.dramatic_composition
            + 0.75 * alignment
            + text_safe
            + section_bonus.get(section, 0.0)
            - 1.6 * features.scenery_only
            - 1.8 * features.transition
            - _face_edge_penalty(features.face_boxes)
        )
        candidates.append(
            VisualCandidate(
                scene_index=scene_index,
                section=section,
                score=round(score, 4),
                visual_score=float(analyzed.visual_score),
                alignment_score=alignment,
                placement=placement,
                text_safe_score=text_safe,
                face_boxes=tuple(features.face_boxes),
                image_jpeg=image_jpeg,
                source_asset_id=asset_id,
                source_family=str(getattr(scene, "source_family", "") or ""),
                roi_label=str(getattr(scene, "roi_label", "") or ""),
                focus_y=float(getattr(scene, "focus_y", 0.4) or 0.4),
            )
        )
    candidates.sort(key=lambda item: (item.score, item.visual_score, item.alignment_score), reverse=True)
    return candidates


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-zA-ZÀ-ÿ]+", value.lower()))


def rank_pairs(
    visuals: Sequence[VisualCandidate],
    headlines: Sequence[HeadlineCandidate],
    section_texts: Mapping[str, str],
) -> list[tuple[float, VisualCandidate, HeadlineCandidate]]:
    ranked: list[tuple[float, VisualCandidate, HeadlineCandidate]] = []
    for visual in visuals:
        narration_tokens = _tokens(section_texts.get(visual.section, ""))
        for headline in headlines:
            section_match = 1.9 if headline.source_section == visual.section else 0.0
            anchor_overlap = len(narration_tokens & set(headline.anchor_terms))
            cross_match = 0.5 if visual.section in {"conflict", "twist"} and headline.source_section in {"conflict", "twist"} else 0.0
            pair_score = visual.score + headline.strength + section_match + cross_match + 0.55 * anchor_overlap
            ranked.append((round(pair_score, 4), visual, headline))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def _font_candidates() -> tuple[Path, ...]:
    configured = Path(getattr(settings, "thumbnail_font", "") or "")
    subtitle = Path(getattr(settings, "subtitle_font", "") or "")
    return (
        configured,
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        subtitle,
    )


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _font_candidates():
        if str(path) and path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _balanced_lines(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    if len(words) <= 1:
        return [text]
    probe = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(probe)
    best: tuple[float, list[str]] | None = None
    for split in range(1, len(words)):
        lines = [" ".join(words[:split]), " ".join(words[split:])]
        widths = [draw.textbbox((0, 0), line, font=font, stroke_width=6)[2] for line in lines]
        if max(widths) > max_width:
            continue
        balance = abs(widths[0] - widths[1]) + max(widths) * 0.08
        if best is None or balance < best[0]:
            best = (balance, lines)
    if best is not None:
        return best[1]
    return [text]


def _fit_headline(text: str, max_width: int) -> tuple[ImageFont.ImageFont, list[str]]:
    for size in range(148, 75, -4):
        font = _font(size)
        lines = _balanced_lines(text, font, max_width)
        draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        widths = [draw.textbbox((0, 0), line, font=font, stroke_width=7)[2] for line in lines]
        if len(lines) <= 2 and max(widths, default=0) <= max_width:
            return font, lines
    font = _font(76)
    return font, _balanced_lines(text, font, max_width)[:2]


def _rect_face_overlap(
    rect: tuple[int, int, int, int],
    face_boxes: Sequence[tuple[float, float, float, float]],
    size: tuple[int, int],
) -> float:
    width, height = size
    rx1, ry1, rx2, ry2 = rect
    worst = 0.0
    for x1, y1, x2, y2 in face_boxes:
        fx1, fy1, fx2, fy2 = x1 * width, y1 * height, x2 * width, y2 * height
        ix1, iy1 = max(rx1, fx1), max(ry1, fy1)
        ix2, iy2 = min(rx2, fx2), min(ry2, fy2)
        if ix2 <= ix1 or iy2 <= iy1:
            continue
        overlap = (ix2 - ix1) * (iy2 - iy1)
        face_area = max(1.0, (fx2 - fx1) * (fy2 - fy1))
        worst = max(worst, overlap / face_area)
    return min(1.0, worst)


def _render_variant(
    visual: VisualCandidate,
    headline: HeadlineCandidate,
    destination: Path,
) -> dict[str, Any]:
    with Image.open(io.BytesIO(visual.image_jpeg)) as decoded:
        base = decoded.convert("RGB").resize(TARGET_SIZE, Image.Resampling.LANCZOS)
    canvas = base.convert("RGBA")
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    max_width = int(TARGET_SIZE[0] * 0.88)
    font, lines = _fit_headline(headline.text, max_width)
    stroke = max(5, int(getattr(font, "size", 80) * 0.055))
    line_gap = max(8, int(getattr(font, "size", 80) * 0.08))
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    boxes = [measure.textbbox((0, 0), line, font=font, stroke_width=stroke) for line in lines]
    widths = [box[2] - box[0] for box in boxes]
    heights = [box[3] - box[1] for box in boxes]
    block_h = sum(heights) + line_gap * max(0, len(lines) - 1)
    top_y = 105 if visual.placement == "top" else TARGET_SIZE[1] - block_h - 145
    block_x1 = max(36, int((TARGET_SIZE[0] - max(widths, default=0)) / 2) - 34)
    block_x2 = min(TARGET_SIZE[0] - 36, TARGET_SIZE[0] - block_x1)
    block_rect = (block_x1, max(40, top_y - 30), block_x2, min(TARGET_SIZE[1] - 40, top_y + block_h + 34))
    draw.rounded_rectangle(block_rect, radius=28, fill=(0, 0, 0, 118))
    y = top_y
    line_rects: list[tuple[int, int, int, int]] = []
    for index, (line, width, height) in enumerate(zip(lines, widths, heights, strict=True)):
        x = int((TARGET_SIZE[0] - width) / 2)
        fill = (255, 255, 255, 255) if index == 0 else (255, 222, 54, 255)
        shadow = max(3, stroke // 2)
        draw.text(
            (x + shadow, y + shadow), line, font=font, fill=(0, 0, 0, 210), stroke_width=stroke, stroke_fill=(0, 0, 0, 210)
        )
        draw.text(
            (x, y), line, font=font, fill=fill, stroke_width=stroke, stroke_fill=(8, 8, 8, 255)
        )
        line_rects.append((x, y, x + width, y + height))
        y += height + line_gap
    composed = Image.alpha_composite(canvas, overlay).convert("RGB")
    destination.parent.mkdir(parents=True, exist_ok=True)
    composed.save(destination, "JPEG", quality=94, optimize=True)

    gray = composed.convert("L").resize((128, 128))
    stats = ImageStat.Stat(gray)
    mean = float(stats.mean[0]) / 255.0
    variance = float(stats.var[0]) / (255.0 * 255.0)
    overflow = any(x1 < 24 or y1 < 24 or x2 > TARGET_SIZE[0] - 24 or y2 > TARGET_SIZE[1] - 24 for x1, y1, x2, y2 in line_rects)
    face_overlap = _rect_face_overlap(block_rect, visual.face_boxes, TARGET_SIZE)
    qc_pass = (
        destination.is_file()
        and destination.stat().st_size >= 20_000
        and not overflow
        and 0.045 < mean < 0.96
        and variance > 0.002
        and face_overlap <= 0.22
        and 1 <= len(lines) <= 2
    )
    return {
        "qc_pass": qc_pass,
        "width": TARGET_SIZE[0],
        "height": TARGET_SIZE[1],
        "line_count": len(lines),
        "lines": lines,
        "text_overflow": overflow,
        "text_face_overlap": round(face_overlap, 4),
        "mean_luma": round(mean, 4),
        "luma_variance": round(variance, 6),
        "file_size": destination.stat().st_size if destination.is_file() else 0,
        "placement": visual.placement,
    }


def _story_hash(sections: Mapping[str, str]) -> str:
    payload = json.dumps(dict(sections), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _existing_manifest(
    output_dir: Path,
    *,
    video_checksum: str,
    story_hash: str,
) -> dict[str, Any] | None:
    meta_path = output_dir / "thumbnail_meta.json"
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    required = (output_dir / "thumbnail.jpg", output_dir / "thumbnail_clean.jpg")
    if (
        data.get("contract_version") == THUMBNAIL_CONTRACT_VERSION
        and data.get("video_checksum") == video_checksum
        and data.get("story_hash") == story_hash
        and data.get("qc_pass") is True
        and all(path.is_file() for path in required)
    ):
        return data
    return None


def generate_thumbnail_package(
    *,
    video_path: Path,
    output_dir: Path,
    script: object,
    scenes: Sequence[object],
    resolve_asset_path: Callable[[str], Path | None],
    variants: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Generate clean + text thumbnails and return their persisted manifest."""
    if not video_path.is_file():
        raise ThumbnailError("thumbnail.video_missing: final video is unavailable")
    output_dir.mkdir(parents=True, exist_ok=True)
    headlines, sections, language = generate_headlines(script)
    story_hash = _story_hash(sections)
    video_checksum = _sha256_file(video_path)
    if not force:
        existing = _existing_manifest(
            output_dir, video_checksum=video_checksum, story_hash=story_hash
        )
        if existing is not None:
            return existing

    visuals = build_visual_candidates(scenes, resolve_asset_path)
    if not visuals:
        raise ThumbnailError("thumbnail.no_visual_candidate: no clean source panel could be rebuilt")
    pairs = rank_pairs(visuals, headlines, sections)
    if not pairs:
        raise ThumbnailError("thumbnail.no_pair_candidate: no visual/headline pair could be ranked")

    clean_path = output_dir / "thumbnail_clean.jpg"
    with Image.open(io.BytesIO(visuals[0].image_jpeg)) as clean:
        clean.convert("RGB").save(clean_path, "JPEG", quality=94, optimize=True)

    target_variants = max(1, min(3, int(variants or getattr(settings, "thumbnail_variants", 3) or 3)))
    rendered: list[dict[str, Any]] = []
    used_headlines: set[str] = set()
    used_scenes: set[int] = set()
    for pair_score, visual, headline in pairs:
        if headline.text in used_headlines:
            continue
        unseen_scene_exists = any(v.scene_index not in used_scenes for v in visuals)
        if rendered and visual.scene_index in used_scenes and unseen_scene_exists:
            continue
        variant_index = len(rendered) + 1
        destination = output_dir / f"thumbnail_v{variant_index}.jpg"
        qc = _render_variant(visual, headline, destination)
        if not qc["qc_pass"]:
            destination.unlink(missing_ok=True)
            continue
        rendered.append(
            {
                "rank": variant_index,
                "path": str(destination),
                "headline": headline.text,
                "headline_source": headline.style,
                "source_section": headline.source_section,
                "scene_index": visual.scene_index,
                "scene_section": visual.section,
                "source_asset_id": visual.source_asset_id,
                "source_family": visual.source_family,
                "roi_label": visual.roi_label,
                "visual_score": visual.visual_score,
                "thumbnail_score": visual.score,
                "pair_score": pair_score,
                "alignment_score": visual.alignment_score,
                "text_safe_score": visual.text_safe_score,
                "qc": qc,
            }
        )
        used_headlines.add(headline.text)
        used_scenes.add(visual.scene_index)
        if len(rendered) >= target_variants:
            break

    if not rendered:
        raise ThumbnailError("thumbnail.qc_failed: every ranked thumbnail violated visual/text QC")
    selected_scene = int(rendered[0]["scene_index"])
    selected_visual = next(v for v in visuals if v.scene_index == selected_scene)
    with Image.open(io.BytesIO(selected_visual.image_jpeg)) as clean:
        clean.convert("RGB").save(clean_path, "JPEG", quality=94, optimize=True)
    thumbnail_path = output_dir / "thumbnail.jpg"
    shutil.copyfile(rendered[0]["path"], thumbnail_path)

    overall_qc = {
        "qc_pass": True,
        "selected_variant": 1,
        "selected_headline": rendered[0]["headline"],
        "selected_scene_index": selected_scene,
        "variant_count": len(rendered),
        "clean_thumbnail_exists": clean_path.is_file(),
        "publish_thumbnail_exists": thumbnail_path.is_file(),
        "selected_variant_qc": rendered[0]["qc"],
    }
    manifest: dict[str, Any] = {
        "contract_version": THUMBNAIL_CONTRACT_VERSION,
        "qc_pass": True,
        "video_path": str(video_path),
        "video_checksum": video_checksum,
        "story_hash": story_hash,
        "language": language,
        "headline_count": len(headlines),
        "visual_candidate_count": len(visuals),
        "thumbnail_path": str(thumbnail_path),
        "clean_thumbnail_path": str(clean_path),
        "headline": rendered[0]["headline"],
        "variants": rendered,
        "qc": overall_qc,
    }
    (output_dir / "thumbnail.qc.json").write_text(
        json.dumps(overall_qc, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "thumbnail_meta.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest
