"""Content-aware panel scoring and shot planning.

Geometry only decides where a panel exists. This module inspects pixels, optional
face/OCR signals, edge density, composition, and repetition. Weights stay in one
dataclass so tuning does not touch the planner.
"""

from __future__ import annotations

import io
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat


# Keep Pillow 12+ and older supported without a hard dependency upgrade.
def _pixels(image: Image.Image) -> list[int]:
    return list(image.get_flattened_data()) if hasattr(image, "get_flattened_data") else list(image.getdata())


@dataclass(frozen=True)
class PanelScoreWeights:
    face: float = 2.4
    expression: float = 1.2
    action: float = 1.7
    weapon: float = 1.4
    monster: float = 1.6
    effects: float = 1.5
    motion_lines: float = 1.0
    impact: float = 1.5
    close_up: float = 1.1
    composition: float = 1.4
    object_density: float = 1.3
    semantic: float = 2.0
    continuity: float = 0.35
    empty: float = 2.2
    scenery: float = 1.8
    transition: float = 2.0
    repeated: float = 1.6


WEIGHTS = PanelScoreWeights()


@dataclass(frozen=True)
class VisualFeatures:
    face_visibility: float = 0.0
    facial_expression: float = 0.0
    action_pose: float = 0.0
    weapons: float = 0.0
    monsters: float = 0.0
    visual_effects: float = 0.0
    motion_lines: float = 0.0
    impact_frame: float = 0.0
    close_up: float = 0.0
    dramatic_composition: float = 0.0
    object_density: float = 0.0
    empty_background: float = 0.0
    scenery_only: float = 0.0
    transition: float = 0.0
    ocr_text: str = ""
    semantic_tags: frozenset[str] = frozenset()
    focal_points: tuple[tuple[float, float], ...] = ((0.5, 0.4),)
    face_points: tuple[tuple[float, float], ...] = ()
    visual_signature: str = ""


@dataclass(frozen=True)
class PanelCandidate:
    asset_id: str
    order_index: int
    features: VisualFeatures
    visual_score: float
    semantic_score: float = 0.0
    source_family: str = ""


_ACTION = {"attack", "attacked", "attacks", "hit", "struck", "strike", "fight", "fought", "run", "jump", "fall", "battle", "chase", "serang", "menyerang", "memukul", "merampas", "menebas", "bertarung", "berlari", "melompat", "jatuh", "kejar", "mengejar"}
_REVEAL = {"reveal", "finally", "opened", "awakens", "appears", "appeared", "discovers", "ternyata", "akhirnya", "muncul", "terbuka", "bangkit", "menemukan"}
_EXPLOSION = {"explosion", "explode", "blast", "fire", "destroy", "impact", "ledakan", "meledak", "hancur", "menghancurkan", "dampak"}
_THINKING = {"think", "thinks", "remember", "wonder", "realize", "considers", "berpikir", "teringat", "bertanya", "menyadari", "mempertimbangkan"}
_WEAPON = {"sword", "axe", "blade", "weapon", "bow", "spear", "gun", "pedang", "kapak", "bilah", "senjata", "busur", "tombak", "pistol"}
_MONSTER = {"dragon", "monster", "demon", "beast", "boss", "ogre", "creature", "naga", "iblis", "binatang", "bos", "makhluk"}
_VICTORY = {"victory", "victorious", "wins", "won", "triumph", "defeated", "menang", "kemenangan", "mengalahkan", "ditaklukkan"}



def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def narration_tags(text: str) -> frozenset[str]:
    tokens = _tokens(text)
    tags: set[str] = set()
    if tokens & _ACTION:
        tags.add("action")
        if tokens & {"attack", "attacked", "attacks", "strike", "struck", "hit"}:
            tags.add("attack")
    if tokens & _REVEAL:
        tags.add("reveal")
    if tokens & _EXPLOSION:
        tags.add("explosion")
    if tokens & _THINKING:
        tags.add("thinking")
    if tokens & _WEAPON:
        tags.add("weapon")
    if tokens & _MONSTER:
        tags.add("monster")
    if {"dialogue", "says", "tells"} & tokens:
        tags.add("dialogue")
    if tokens & _VICTORY:
        tags.add("victory")
    return frozenset(tags)


def _ocr(image: Image.Image) -> str:
    try:
        import pytesseract

        return pytesseract.image_to_string(image, config="--psm 11")[:500].strip().lower()
    except (ImportError, OSError, RuntimeError, subprocess.SubprocessError):
        return ""


def _face_stats(image: Image.Image) -> tuple[float, float, list[tuple[float, float]]]:
    try:
        import cv2
        import numpy as np

        gray = np.asarray(image.convert("L"))
        cascade = cv2.CascadeClassifier(
            str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        )
        faces = cascade.detectMultiScale(gray, 1.1, 4, minSize=(18, 18))
        if len(faces) == 0:
            return 0.0, 0.0, []
        width, height = image.size
        area = sum(float(fw * fh) for _, _, fw, fh in faces) / (width * height)
        expression = _clip(float(gray.std()) / 75.0)
        points = [(float(x + fw / 2) / width, float(y + fh / 2) / height) for x, y, fw, fh in faces]
        return _clip(area * 7.0), expression, points
    except (ImportError, OSError):
        return 0.0, 0.0, []


def _edge_features(image: Image.Image) -> tuple[float, float, float]:
    gray = image.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    values = _pixels(edges.resize((96, 96)))
    strong = sum(value > 42 for value in values) / max(1, len(values))
    variance = ImageStat.Stat(edges).var[0] ** 0.5 / 128.0
    horizontal = 0.0
    vertical = 0.0
    pixels = _pixels(gray.resize((96, 96)))
    for y in range(1, 95):
        for x in range(1, 95):
            index = y * 96 + x
            horizontal += abs(pixels[index] - pixels[index - 1]) > 55
            vertical += abs(pixels[index] - pixels[index - 96]) > 55
    total = 94 * 94
    return _clip(strong * 3.2), _clip((horizontal + vertical) / (2 * total) * 5), _clip(variance)


def _focal_points(image: Image.Image) -> tuple[tuple[float, float], ...]:
    gray = image.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    width, height = edges.size
    cells: list[tuple[float, float, float]] = []
    for row in range(3):
        for col in range(3):
            box = (col * width // 3, row * height // 3, (col + 1) * width // 3, (row + 1) * height // 3)
            energy = ImageStat.Stat(edges.crop(box)).mean[0]
            cells.append((energy, (col + 0.5) / 3, (row + 0.5) / 3))
    cells.sort(reverse=True)
    return tuple((x, y) for _, x, y in cells[:3]) or ((0.5, 0.4),)


def _visual_signature(image: Image.Image) -> str:
    """Coarse perceptual signature for repeated-panel suppression."""
    pixels = _pixels(image.convert("L").resize((8, 8), Image.Resampling.BILINEAR))
    average = sum(pixels) / max(1, len(pixels))
    return "".join("1" if pixel >= average else "0" for pixel in pixels)


def analyze_panel(data: bytes, asset_id: str = "", order_index: int = 0, source_family: str = "") -> PanelCandidate:
    """Extract content features from one image and calculate its visual score."""
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGB")
        image.thumbnail((640, 640), Image.Resampling.LANCZOS)
    width, height = image.size
    gray = image.convert("L")
    mean = ImageStat.Stat(gray).mean[0] / 255.0
    variance = min(1.0, ImageStat.Stat(gray).var[0] / (255.0 * 255.0))
    edge_density, motion, texture = _edge_features(image)
    face, expression, face_points = _face_stats(image)
    text = _ocr(image)
    tags = _tokens(text)
    weapons = _clip(0.85 if tags & _WEAPON else edge_density * 0.35)
    monsters = _clip(0.85 if tags & _MONSTER else 0.0)
    effects = _clip(motion * 0.55 + texture * 0.45)
    action = _clip(motion * 0.65 + edge_density * 0.35)
    impact = _clip(effects * 0.6 + motion * 0.4)
    empty = _clip((1.0 - edge_density) * (1.0 - variance) * 1.15)
    scenery = _clip(empty * 0.75 + (1.0 - face) * (1.0 - action) * 0.25)
    transition = _clip(empty if variance < 0.025 else 0.0)
    composition = _clip(texture * 0.45 + edge_density * 0.35 + (1.0 - abs(mean - 0.52)) * 0.2)
    features = VisualFeatures(
        face_visibility=face,
        facial_expression=expression,
        action_pose=action,
        weapons=weapons,
        monsters=monsters,
        visual_effects=effects,
        motion_lines=motion,
        impact_frame=impact,
        close_up=_clip(face * 1.15),
        dramatic_composition=composition,
        object_density=edge_density,
        empty_background=empty,
        scenery_only=scenery,
        transition=transition,
        ocr_text=text,
        semantic_tags=frozenset(tags),
        focal_points=tuple(face_points or _focal_points(image)),
        face_points=tuple(face_points),
        visual_signature=_visual_signature(image),
    )
    positive = sum(
        weight * value
        for weight, value in (
            (WEIGHTS.face, face), (WEIGHTS.expression, expression), (WEIGHTS.action, action),
            (WEIGHTS.weapon, weapons), (WEIGHTS.monster, monsters), (WEIGHTS.effects, effects),
            (WEIGHTS.motion_lines, motion), (WEIGHTS.impact, impact),
            (WEIGHTS.close_up, features.close_up), (WEIGHTS.composition, composition),
            (WEIGHTS.object_density, edge_density),
        )
    )
    penalty = WEIGHTS.empty * empty + WEIGHTS.scenery * scenery + WEIGHTS.transition * transition
    family = source_family or f"legacy-strip-{max(0, order_index) // 8}"
    return PanelCandidate(asset_id, order_index, features, round(max(0.0, positive - penalty), 3), source_family=family)


def analyze_assets(assets: Iterable[object], read: Callable[[str], bytes]) -> list[PanelCandidate]:
    return [
        analyze_panel(read(asset.storage_key), asset.id, asset.order_index, getattr(asset, "source_family", ""))
        for asset in assets
    ]


def semantic_score(candidate: PanelCandidate, narration: str) -> float:
    tags = narration_tags(narration)
    f = candidate.features
    score = 0.0
    if "action" in tags:
        score += 2.5 * (f.action_pose + f.impact_frame)
    if "reveal" in tags:
        score += 3.0 * (f.close_up + f.visual_effects)
    if "explosion" in tags:
        score += 3.0 * (f.visual_effects + f.impact_frame)
    if "thinking" in tags:
        score += 2.0 * (f.close_up + f.facial_expression)
    if "weapon" in tags:
        score += 2.5 * f.weapons
    if "monster" in tags:
        score += 2.5 * f.monsters
    if "dialogue" in tags:
        score += 1.5 * f.face_visibility
    return round(score, 3)


def select_panel(candidates: list[PanelCandidate], narration: str, previous_order: int | None = None, used_ids: set[str] | None = None, used_signatures: set[str] | None = None, nearby: int = 6) -> PanelCandidate | None:
    """Choose engagement first; continuity is only a small tie-breaker."""
    if not candidates:
        return None
    used_ids = used_ids or set()
    used_signatures = used_signatures or set()
    ranked: list[tuple[float, PanelCandidate]] = []
    for candidate in candidates:
        semantic = semantic_score(candidate, narration)
        distance = abs(candidate.order_index - previous_order) if previous_order is not None else 0
        continuity = max(0.0, 1.0 - distance / max(1, nearby))
        repeat_penalty = WEIGHTS.repeated * (
            1.0 if candidate.asset_id in used_ids else 0.0
        )
        # Reuse is allowed when the pool is exhausted, not when a fresh panel
        # exists. This prevents a high-scoring frame from dominating every beat.
        if candidate.asset_id in used_ids and len(used_ids) < len(candidates):
            repeat_penalty += max(WEIGHTS.repeated * 2.5, candidate.visual_score * 0.45)
        if candidate.features.visual_signature and candidate.features.visual_signature in used_signatures:
            repeat_penalty += WEIGHTS.repeated * 0.75
        value = candidate.visual_score + semantic + WEIGHTS.continuity * continuity - repeat_penalty
        ranked.append((value, PanelCandidate(candidate.asset_id, candidate.order_index, candidate.features, candidate.visual_score, semantic, candidate.source_family)))
    ranked.sort(key=lambda item: item[0], reverse=True)
    best = ranked[0][1]
    if previous_order is not None:
        nearby_candidates = [item for item in ranked if abs(item[1].order_index - previous_order) <= nearby]
        if nearby_candidates and nearby_candidates[0][0] >= ranked[0][0] - 1.5:
            best = nearby_candidates[0][1]
    return best


def selection_reasons(candidate: PanelCandidate, narration: str) -> list[str]:
    """Deterministic reason codes explaining why a panel fits a beat."""
    tags = narration_tags(narration)
    reasons = [f"source_order:{candidate.order_index}", f"visual_score:{candidate.visual_score:.3f}"]
    if candidate.source_family:
        reasons.append(f"source_family:{candidate.source_family}")
    for tag, value in (("action", candidate.features.action_pose), ("face", candidate.features.face_visibility), ("weapon", candidate.features.weapons), ("monster", candidate.features.monsters), ("effect", candidate.features.visual_effects), ("reveal", candidate.features.close_up)):
        if tag in tags and value > 0.15:
            reasons.append(f"{tag}_match:{value:.3f}")
    if candidate.features.ocr_text:
        reasons.append("ocr_text_available")
    return reasons


def camera_effect(narration: str, index: int) -> str:
    tags = narration_tags(narration)
    if "explosion" in tags:
        return "shake_zoom"
    if "action" in tags:
        return "punch_zoom"
    if "reveal" in tags:
        return "push_up"
    if "thinking" in tags:
        return "pan_left"
    if "dialogue" in tags:
        return "kenburns_in"
    return ("kenburns_in", "pan_right", "push_down")[index % 3]


def planned_focus(candidate: PanelCandidate | None, shot_index: int = 0) -> tuple[float, float]:
    if candidate is None:
        return 0.5, 0.4
    points = candidate.features.focal_points
    return points[shot_index % len(points)]


def plan_content_aware_scenes(spans: Iterable[object], candidates: list[PanelCandidate], min_scene_seconds: float = 2.0, max_scene_seconds: float = 6.0) -> list[dict]:
    """Plan directed shots; panel scoring remains the candidate provider."""
    from app.services.camera_planner import apply_camera_plans
    from app.services.shot_director import plan_shots

    return apply_camera_plans(
        plan_shots(list(spans), candidates, min_scene_seconds, max_scene_seconds)
    )


def score_breakdown(candidate: PanelCandidate) -> dict[str, float | str]:
    f = candidate.features
    return {
        "visual_score": candidate.visual_score, "semantic_score": candidate.semantic_score,
        "face": round(f.face_visibility, 3), "expression": round(f.facial_expression, 3),
        "action": round(f.action_pose, 3), "weapons": round(f.weapons, 3),
        "monsters": round(f.monsters, 3), "effects": round(f.visual_effects, 3),
        "motion_lines": round(f.motion_lines, 3), "impact": round(f.impact_frame, 3),
        "close_up": round(f.close_up, 3), "composition": round(f.dramatic_composition, 3),
        "object_density": round(f.object_density, 3), "empty_penalty": round(f.empty_background, 3),
        "scenery_penalty": round(f.scenery_only, 3), "transition_penalty": round(f.transition, 3),
        "ocr": f.ocr_text,
    }


def diversity_penalty(previous: PanelCandidate | None, current: PanelCandidate) -> float:
    if previous is None:
        return 0.0
    a, b = previous.features, current.features
    return round(max(0.0, 1.0 - sum(abs(getattr(a, field) - getattr(b, field)) for field in ("face_visibility", "action_pose", "object_density", "dramatic_composition"))), 3)


def tune_weights(**changes: float) -> PanelScoreWeights:
    values = {field: getattr(WEIGHTS, field) for field in WEIGHTS.__dataclass_fields__}
    unknown = set(changes) - set(values)
    if unknown:
        raise ValueError(f"unknown visual weight(s): {', '.join(sorted(unknown))}")
    values.update(changes)
    return PanelScoreWeights(**values)


__all__ = ["PanelCandidate", "PanelScoreWeights", "VisualFeatures", "analyze_assets", "analyze_panel", "camera_effect", "diversity_penalty", "narration_tags", "planned_focus", "plan_content_aware_scenes", "score_breakdown", "select_panel", "selection_reasons", "tune_weights"]

# ponytail: heuristic CV ceiling; upgrade to a local vision encoder when GPU
# inference is available, preserving this feature schema as the adapter boundary.
