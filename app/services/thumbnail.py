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
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat

from app.config import settings
from app.services import visual_scoring
from app.services.file_integrity import sha256_file

THUMBNAIL_CONTRACT_VERSION = "auto-thumbnail-v5"
TARGET_SIZE = (1080, 1920)
MAX_HEADLINE_WORDS = 7
MAX_HEADLINE_CHARS = 38



_HEADLINE_HISTORY_VERSION = "thumbnail-headline-history-v1"
_HEADLINE_NEAR_DUPLICATE_THRESHOLD = 0.72
_THUMBNAIL_ACCENT_COLORS = {
    "yellow": (255, 224, 48, 255),
    "red": (255, 72, 72, 255),
    "blue": (72, 164, 255, 255),
    "green": (72, 228, 120, 255),
}
_THUMBNAIL_MAIN_TEXT_COLOR = (255, 255, 255, 255)
_ACCENT_STOPWORDS = frozenset({
    "a", "an", "and", "are", "at", "behind", "did", "do", "does", "for",
    "from", "in", "is", "it", "of", "on", "that", "the", "these", "this",
    "to", "was", "were", "what", "when", "where", "which", "who", "why",
    "yang", "dan", "di", "ini", "itu", "apa", "siapa", "kenapa", "saat",
})
_ACCENT_HOOK_WORDS = frozenset({
    "awaken", "awakened", "betrayed", "cursed", "danger", "dangerous", "demon",
    "door", "forbidden", "heaven", "hell", "hidden", "hiding", "human", "late",
    "lurks", "monster", "never", "power", "secret", "truth", "scam", "sword",
    "bangkit", "dikhianati", "iblis", "kekuatan", "pedang", "rahasia",
    "terkutuk", "terlambat", "tersembunyi", "bahaya", "manusia", "kebenaran",
})

def _headline_history_path() -> Path:
    return Path(settings.data_dir) / "thumbnail-headline-history.json"

def _normalized_headline(value: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))

def _headline_similarity(left: str, right: str) -> float:
    a = _normalized_headline(left)
    b = _normalized_headline(right)
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / max(1, len(ta | tb))
    return max(seq, jac)

def _headline_is_novel(value: str, history: Sequence[str]) -> bool:
    norm = _normalized_headline(value)
    if not norm:
        return False
    return all(
        norm != _normalized_headline(old)
        and _headline_similarity(value, old) < _HEADLINE_NEAR_DUPLICATE_THRESHOLD
        for old in history
    )

def _load_headline_history() -> list[str]:
    history: list[str] = []
    path = _headline_history_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, Mapping) and data.get("version") == _HEADLINE_HISTORY_VERSION:
                history.extend(
                    str(row.get("headline"))
                    for row in data.get("entries", ())
                    if isinstance(row, Mapping) and str(row.get("headline") or "").strip()
                )
        except (OSError, json.JSONDecodeError):
            pass
    output_root = Path(settings.output_dir)
    if output_root.is_dir():
        for meta_path in sorted(output_root.glob("*/thumbnail_meta.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            headline = str(meta.get("headline") or "").strip() if isinstance(meta, Mapping) else ""
            if headline:
                history.append(headline)
    unique: list[str] = []
    seen: set[str] = set()
    for headline in history:
        key = _normalized_headline(headline)
        if key and key not in seen:
            unique.append(headline)
            seen.add(key)
    return unique

def _record_headline_history(headline: str, *, story_hash: str, video_checksum: str) -> None:
    path = _headline_history_path()
    entries: list[dict[str, str]] = []
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(current, Mapping) and current.get("version") == _HEADLINE_HISTORY_VERSION:
                entries = [dict(row) for row in current.get("entries", ()) if isinstance(row, Mapping)]
        except (OSError, json.JSONDecodeError):
            entries = []
    entries = [row for row in entries if _normalized_headline(row.get("headline")) != _normalized_headline(headline)]
    entries.append({"headline": headline, "story_hash": story_hash, "video_checksum": video_checksum})
    payload = {"version": _HEADLINE_HISTORY_VERSION, "entries": entries[-500:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


class ThumbnailError(RuntimeError):
    """Stable error raised when an upload-ready thumbnail cannot be produced."""


@dataclass(frozen=True)
class HeadlineCandidate:
    text: str
    source_section: str = ""
    style: str = "clickbait"
    anchor_terms: tuple[str, ...] = ()
    strength: float = 1.0
    accent_words: tuple[str, ...] = ()


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


def _normalized_word(value: object) -> str:
    return "".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _clean_accent_words(value: object, headline: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    headline_words = headline.split()
    normalized = [_normalized_word(word) for word in headline_words]
    selected: list[str] = []
    for item in value:
        target = _normalized_word(item)
        if not target or target not in normalized:
            continue
        source_word = headline_words[normalized.index(target)]
        if source_word not in selected:
            selected.append(source_word)
        if len(selected) >= 2:
            break
    return tuple(selected)


def _accent_word_indexes(headline: HeadlineCandidate) -> tuple[int, ...]:
    words = headline.text.split()
    if len(words) <= 1:
        return ()
    normalized = [_normalized_word(word) for word in words]
    max_accent = 1 if len(words) <= 4 else 2
    explicit = {_normalized_word(word) for word in headline.accent_words if _normalized_word(word)}
    if explicit:
        indexes = [index for index, token in enumerate(normalized) if token in explicit]
        return tuple(indexes[:max_accent])
    anchors = {_normalized_word(term) for term in headline.anchor_terms if _normalized_word(term)}
    ranked: list[tuple[float, int]] = []
    for index, (raw, token) in enumerate(zip(words, normalized, strict=True)):
        if not token:
            continue
        score = 0.0
        if token in _ACCENT_HOOK_WORDS:
            score += 4.0
        if any(anchor and (anchor in token or token in anchor) for anchor in anchors):
            score += 5.0
        if token not in _ACCENT_STOPWORDS and len(token) >= 5:
            score += 0.8
        if raw.endswith(("?", "!")):
            score += 0.3
        if token in _ACCENT_STOPWORDS:
            score -= 2.0
        ranked.append((score, index))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    chosen = [index for score, index in ranked if score > 0][:max_accent]
    if not chosen:
        content = [
            (len(token), index) for index, token in enumerate(normalized)
            if token and token not in _ACCENT_STOPWORDS
        ]
        if content:
            chosen = [max(content)[1]]
    return tuple(sorted(chosen))


def _fallback_headlines(sections: Mapping[str, str], language: str) -> list[HeadlineCandidate]:
    story = " ".join(sections.values()).lower()
    rows: list[HeadlineCandidate] = []

    def add(text: str, section: str, *anchors: str, strength: float = 1.0) -> None:
        clean = _clean_headline(text)
        if clean:
            rows.append(HeadlineCandidate(clean, section, "clickbait_v2", tuple(anchors), strength))

    if language == "id":
        triggers = (
            (("hood", "hooded", "berkerudung", "berjubah"), "SIAPA SEBENARNYA SOSOK INI?!", "twist", 3.15),
            (("sword", "pedang", "blade", "bilah"), "APA YANG BARUSAN DIBANGKITKAN PEDANG INI?!", "hook", 3.35),
            (("photo", "photograph", "foto"), "APA YANG DISEMBUNYIKAN FOTO INI?!", "cta", 3.05),
            (("smile", "smiling", "senyum", "tersenyum"), "KENAPA DIA MALAH TERSENYUM?!", "conflict", 3.2),
            (("surprise", "surprised", "shock", "kaget", "terkejut"), "DIA SADAR SAAT SUDAH TERLAMBAT", "conflict", 3.1),
            (("secret", "truth", "rahasia", "kebenaran"), "MEREKA GAK BOLEH TAHU INI", "twist", 3.25),
            (("power", "energy", "kekuatan", "energi"), "KEKUATAN APA YANG BARUSAN BANGKIT?!", "hook", 3.25),
            (("monster", "demon", "iblis", "monster"), "ITU BENERAN BUKAN MANUSIA?!", "twist", 3.35),
        )
    else:
        triggers = (
            (("hood", "hooded"), "WHO IS REALLY UNDER THAT HOOD?!", "twist", 3.15),
            (("sword", "blade"), "WHAT DID THAT SWORD JUST AWAKEN?!", "hook", 3.35),
            (("photo", "photograph", "picture"), "WHAT ARE THESE PHOTOS HIDING?!", "cta", 3.05),
            (("smile", "smiling"), "WHY SMILE AT A TIME LIKE THIS?!", "conflict", 3.2),
            (("surprise", "surprised", "shock", "shocked"), "THE TRUTH CAME WAY TOO LATE", "conflict", 3.1),
            (("secret", "truth"), "THEY WERE NEVER MEANT TO KNOW", "twist", 3.25),
            (("power", "energy"), "WHAT POWER JUST AWAKENED?!", "hook", 3.25),
            (("monster", "demon"), "THAT THING ISN'T EVEN HUMAN?!", "twist", 3.35),
        )
    for needles, text, section, strength in triggers:
        if any(needle in story for needle in needles):
            add(text, section, *needles, strength=strength)

    if language == "id":
        add("KENAPA SEMUA ORANG DIAM SOAL INI?!", "twist", strength=2.35)
        add("DIA BARU SADAR SAAT TERLAMBAT", "conflict", strength=2.4)
        add("APA YANG SEBENARNYA TERJADI DI SINI?!", "conflict", strength=2.25)
        add("MEREKA GAK SIAP MELIHAT INI", "twist", strength=2.2)
    else:
        add("WHY IS NOBODY TALKING ABOUT THIS?!", "twist", strength=2.35)
        add("THEY REALIZED IT TOO LATE", "conflict", strength=2.4)
        add("WHAT ACTUALLY HAPPENED HERE?!", "conflict", strength=2.25)
        add("THEY WEREN'T READY TO SEE THIS", "twist", strength=2.2)

    unique: list[HeadlineCandidate] = []
    seen: set[str] = set()
    for row in rows:
        if row.text not in seen:
            unique.append(row)
            seen.add(row.text)
    return unique


def _llm_headlines(sections: Mapping[str, str], language: str, avoid_headlines: Sequence[str] = ()) -> list[HeadlineCandidate]:
    if settings.llm_provider != "openai_compatible" or not settings.llm_base_url or not settings.llm_api_key:
        return []
    story = json.dumps(dict(sections), ensure_ascii=False)
    system = (
        "You write high-CTR short-form manhwa thumbnail headlines, not summaries. "
        "Create an unresolved curiosity gap around one concrete story object, action, reaction, mystery, or danger. "
        "Aggressive clickbait and provocative inference are allowed, but do not invent a completed outcome absent from the story. "
        "Avoid generic cliches such as THIS CHANGED EVERYTHING, NO ONE SAW THIS COMING, or WHAT HAPPENED NEXT. "
        "Prefer WHY/WHAT/WHO questions, TOO LATE, hidden motives, forbidden-looking power, suspicious reactions, or dangerous discoveries. "
        "Each headline must be 3-7 words, at most 38 characters, visually punchy, and usable in two lines. "
        "Return strict JSON: {\"headlines\":[{\"text\":str,\"section\":\"hook|setup|conflict|twist|cta\",\"accent_words\":[str]}]}. accent_words must contain only 1-2 exact words copied from the headline that carry the strongest curiosity hook; never accent the whole headline."
    )
    user = (
        f"Language: {'Indonesian' if language == 'id' else 'English'}\n"
        "Generate eight distinct thumbnail headlines. Make every option feel impossible to ignore and specific to this story.\n"
        f"Avoid prior production headlines and close paraphrases: {json.dumps(list(avoid_headlines)[-80:], ensure_ascii=False)[:4000]}\n"
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
            rows.append(HeadlineCandidate(text, section, "llm_clickbait", (), 2.25, _clean_accent_words(item.get("accent_words"), text)))
    return rows


def _headline_pronouns_are_grounded(text: str, story: str, language: str) -> bool:
    """Reject English headline pronouns unsupported by the approved narration."""
    if language != "en":
        return True
    headline_tokens = set(re.findall(r"[A-Za-z']+", text.lower()))
    story_tokens = set(re.findall(r"[A-Za-z']+", story.lower()))
    for group in ({"she", "her", "hers"}, {"he", "him", "his"}):
        if headline_tokens & group and not story_tokens & group:
            return False
    return True


def generate_headlines(script: object, headline_history: Sequence[str] = ()) -> tuple[list[HeadlineCandidate], dict[str, str], str]:
    sections, story = _script_sections(script)
    language = _language(story)
    llm_rows = _llm_headlines(sections, language, headline_history)
    fallback_rows = _fallback_headlines(sections, language)
    # When the provider produced grounded story-specific options, keep only
    # deterministic fallbacks that are tied to concrete story anchors. Generic
    # emergency clickbait remains available when the headline provider fails,
    # but it cannot outrank fresh story-specific production copy.
    rows = llm_rows + (
        [row for row in fallback_rows if row.anchor_terms]
        if llm_rows
        else fallback_rows
    )
    unique: list[HeadlineCandidate] = []
    seen: set[str] = set()
    for row in rows:
        if (
            row.text
            and _headline_pronouns_are_grounded(row.text, story, language)
            and row.text not in seen
            and _headline_is_novel(row.text, headline_history)
        ):
            unique.append(row)
            seen.add(row.text)
    if not unique:
        fallback = "TERNYATA INI YANG TERJADI" if language == "id" else "THIS CHANGED EVERYTHING"
        if _headline_is_novel(fallback, headline_history):
            unique.append(HeadlineCandidate(fallback, "conflict", "fallback", (), 1.0))
        else:
            words = [w.upper() for w in re.findall(r"[A-Za-zÀ-ÿ0-9]+", story) if len(w) >= 5][:5]
            dynamic = _clean_headline(" ".join(words[:4]) + "?!") or fallback
            unique.append(HeadlineCandidate(dynamic, "conflict", "story_fallback", (), 1.0))
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


def _safe_text_placements(
    image: Image.Image,
    face_boxes: Sequence[tuple[float, float, float, float]],
    focus_y: float,
) -> list[tuple[str, float, float]]:
    regions = {"top": (0.05, 0.32), "middle": (0.35, 0.65), "bottom": (0.68, 0.95)}
    ranked: list[tuple[str, float, float]] = []
    for name, (top, bottom) in regions.items():
        overlap = max((_box_overlap_y(box, top, bottom) for box in face_boxes), default=0.0)
        edge = _edge_energy(image, top, bottom)
        focus_penalty = 0.45 if top <= focus_y <= bottom else 0.0
        score = 1.5 * (1.0 - overlap) + 0.65 * (1.0 - edge) - focus_penalty
        if overlap <= 0.22:
            ranked.append((name, round(score, 4), round(overlap, 4)))
    ranked.sort(key=lambda row: row[1], reverse=True)
    return ranked

def _safe_text_placement(
    image: Image.Image,
    face_boxes: Sequence[tuple[float, float, float, float]],
    focus_y: float,
) -> tuple[str, float, float]:
    ranked = _safe_text_placements(image, face_boxes, focus_y)
    if not ranked:
        return "top", 0.0, 1.0
    return ranked[0]

def _pair_placement(visual: VisualCandidate, headline: HeadlineCandidate, story_hash: str) -> str:
    with Image.open(io.BytesIO(visual.image_jpeg)) as decoded:
        ranked = _safe_text_placements(decoded.convert("RGB"), visual.face_boxes, visual.focus_y)
    if not ranked:
        return visual.placement
    pool = [row[0] for row in ranked]
    seed = hashlib.sha256(f"{story_hash}|{visual.scene_index}|{headline.text}".encode()).digest()
    return pool[int.from_bytes(seed[:4], "big") % len(pool)]

def _accent_color(image: Image.Image, placement: str) -> tuple[str, tuple[int, int, int, int]]:
    regions = {"top": (0.05, 0.32), "middle": (0.35, 0.65), "bottom": (0.68, 0.95)}
    top, bottom = regions.get(placement, regions["top"])
    sample = image.crop((0, int(image.height * top), image.width, int(image.height * bottom))).resize((32, 32))
    mean = ImageStat.Stat(sample.convert("RGB")).mean
    bg = tuple(float(value) for value in mean[:3])
    def score(rgb: tuple[int, int, int, int]) -> float:
        fg = rgb[:3]
        distance = sum((float(fg[i]) - bg[i]) ** 2 for i in range(3)) ** 0.5 / 441.7
        fg_luma = (0.2126 * fg[0] + 0.7152 * fg[1] + 0.0722 * fg[2]) / 255.0
        bg_luma = (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) / 255.0
        return abs(fg_luma - bg_luma) + 0.55 * distance
    return max(_THUMBNAIL_ACCENT_COLORS.items(), key=lambda item: score(item[1]))


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


def _headline_bait_score(headline: HeadlineCandidate) -> float:
    text = headline.text.upper()
    tokens = _tokens(text)
    score = 0.0
    if "?" in text:
        score += 1.05
    if "!" in text:
        score += 0.3
    hooks = {
        "why", "what", "who", "how", "kenapa", "apa", "siapa",
        "hiding", "hidden", "rahasia", "secret", "late", "terlambat",
        "never", "gak", "bukan", "really", "sebenarnya", "forbidden",
        "danger", "dangerous", "bahaya", "awaken", "bangkit", "dibangkitkan",
    }
    score += min(1.8, 0.38 * len(tokens & hooks))
    if headline.anchor_terms:
        score += 0.75
    generic = (
        "CHANGED EVERYTHING",
        "NO ONE SAW THIS COMING",
        "THIS CHANGED EVERYTHING",
        "WHAT HAPPENED NEXT",
        "AKHIRNYA SEMUA TERUNGKAP",
        "MENGUBAH SEGALANYA",
    )
    if any(phrase in text for phrase in generic):
        score -= 1.75
    if len(text.split()) <= 6:
        score += 0.25
    return round(score, 4)


def rank_pairs(
    visuals: Sequence[VisualCandidate],
    headlines: Sequence[HeadlineCandidate],
    section_texts: Mapping[str, str],
) -> list[tuple[float, VisualCandidate, HeadlineCandidate]]:
    ranked: list[tuple[float, VisualCandidate, HeadlineCandidate]] = []
    for visual in visuals:
        narration_tokens = _tokens(section_texts.get(visual.section, ""))
        for headline in headlines:
            section_match = 2.15 if headline.source_section == visual.section else 0.0
            anchor_overlap = len(narration_tokens & set(headline.anchor_terms))
            cross_match = 0.45 if visual.section in {"conflict", "twist"} and headline.source_section in {"conflict", "twist"} else 0.0
            specificity = 0.8 * anchor_overlap
            bait = _headline_bait_score(headline)
            pair_score = (
                visual.score
                + headline.strength
                + section_match
                + cross_match
                + specificity
                + 1.35 * bait
            )
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
    max_width = int(TARGET_SIZE[0] * 0.90)
    font, lines = _fit_headline(headline.text, max_width)
    font_size = int(getattr(font, "size", 80))
    stroke = max(7, int(font_size * 0.072))
    line_gap = max(8, int(font_size * 0.07))
    shadow = max(3, int(font_size * 0.025))
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    boxes = [
        measure.textbbox((0, 0), line, font=font, stroke_width=stroke)
        for line in lines
    ]
    widths = [box[2] - box[0] for box in boxes]
    heights = [box[3] - box[1] for box in boxes]
    block_h = sum(heights) + line_gap * max(0, len(lines) - 1)
    if visual.placement == "top":
        top_y = 92
    elif visual.placement == "middle":
        top_y = int((TARGET_SIZE[1] - block_h) / 2)
    else:
        top_y = TARGET_SIZE[1] - block_h - 125
    accent_name, accent_fill = _accent_color(base, visual.placement)
    headline_words = headline.text.split()
    accent_indexes = set(_accent_word_indexes(headline))
    accent_words = [headline_words[index] for index in sorted(accent_indexes)]
    y = top_y
    word_cursor = 0
    line_rects: list[tuple[int, int, int, int]] = []
    for line, width, height in zip(lines, widths, heights, strict=True):
        x = int((TARGET_SIZE[0] - width) / 2)
        cursor_x = float(x)
        line_words = line.split()
        for local_index, word in enumerate(line_words):
            global_index = word_cursor + local_index
            fill = accent_fill if global_index in accent_indexes else _THUMBNAIL_MAIN_TEXT_COLOR
            draw.text(
                (int(cursor_x) + shadow, y + shadow),
                word,
                font=font,
                fill=(0, 0, 0, 185),
                stroke_width=stroke + 3,
                stroke_fill=(0, 0, 0, 205),
            )
            draw.text(
                (int(cursor_x), y),
                word,
                font=font,
                fill=fill,
                stroke_width=stroke,
                stroke_fill=(5, 5, 5, 255),
            )
            advance = float(measure.textlength(word, font=font))
            if local_index < len(line_words) - 1:
                advance += float(measure.textlength(" ", font=font))
            cursor_x += advance
        word_cursor += len(line_words)
        pad = stroke + shadow + 3
        line_rects.append((x - pad, y - pad, x + width + pad, y + height + pad))
        y += height + line_gap

    text_rect = (
        max(0, min(rect[0] for rect in line_rects)),
        max(0, min(rect[1] for rect in line_rects)),
        min(TARGET_SIZE[0], max(rect[2] for rect in line_rects)),
        min(TARGET_SIZE[1], max(rect[3] for rect in line_rects)),
    )
    composed = Image.alpha_composite(canvas, overlay).convert("RGB")
    destination.parent.mkdir(parents=True, exist_ok=True)
    composed.save(destination, "JPEG", quality=94, optimize=True)

    gray = composed.convert("L").resize((128, 128))
    stats = ImageStat.Stat(gray)
    mean = float(stats.mean[0]) / 255.0
    variance = float(stats.var[0]) / (255.0 * 255.0)
    overflow = any(
        x1 < 18 or y1 < 18 or x2 > TARGET_SIZE[0] - 18 or y2 > TARGET_SIZE[1] - 18
        for x1, y1, x2, y2 in line_rects
    )
    face_overlap = _rect_face_overlap(text_rect, visual.face_boxes, TARGET_SIZE)
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
        "background_style": "outline_only",
        "text_color": "white",
        "accent_color": accent_name,
        "accent_words": accent_words,
        "accent_word_count": len(accent_words),
        "color_contract": "white_main_selective_accent_v1",
        "stroke_width": stroke,
        "shadow_offset": shadow,
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
    sections, _story = _script_sections(script)
    story_hash = _story_hash(sections)
    video_checksum = sha256_file(video_path)
    if not force:
        existing = _existing_manifest(
            output_dir, video_checksum=video_checksum, story_hash=story_hash
        )
        if existing is not None:
            return existing

    headline_history = _load_headline_history()
    headlines, _sections, language = generate_headlines(script, headline_history)
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
        placement = _pair_placement(visual, headline, story_hash)
        rendered_visual = replace(visual, placement=placement)
        qc = _render_variant(rendered_visual, headline, destination)
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
                "placement": placement,
                "text_color": qc.get("text_color", ""),
                "accent_color": qc.get("accent_color", ""),
                "accent_words": list(qc.get("accent_words", [])),
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
    _record_headline_history(
        str(rendered[0]["headline"]), story_hash=story_hash, video_checksum=video_checksum
    )
    return manifest
