import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace

from app.constants import (
    STANDARD_FINAL_DURATION_MAX_SECONDS,
    STANDARD_FINAL_DURATION_MIN_SECONDS,
)

# Review-only editorial cadence policy.  These values are intentionally kept
# outside the profile hash: they govern the human-review visual density
# contract, while the published v1/v2 profile bytes remain unchanged.
REVIEW_VISUAL_SECONDS_PER_UNIQUE_MIN = 3.0
REVIEW_VISUAL_SECONDS_PER_UNIQUE_MAX = 4.0
REVIEW_MAX_UNCHANGED_HOLD_SECONDS = 4.0
REVIEW_MAX_SHOT_SECONDS = 4.0
REVIEW_PREFERRED_FRAME_EDGE_BLANK_FRACTION = 0.03
REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION = 0.08
# Coherence rescue is deliberately narrow: it may preserve a wider, human-readable
# composition when the 8% gate would otherwise force an extreme crop. It never
# bypasses balloon/protected-region or editorial face/subject guards.
REVIEW_COHERENCE_RESCUE_MAX_FRAME_EDGE_BLANK_FRACTION = 0.17
REVIEW_COHERENCE_RESCUE_MAX_BASE_ZOOM = 1.35
REVIEW_COHERENCE_RESCUE_REASON = "review.coherence_blank_rescue"
REVIEW_MIN_TRANSITION_PIXEL_DIFF = 1.0
REVIEW_MOTION_MAX_NORMALIZED_STEP = 0.08
REVIEW_MOTION_ZOOM_DELTA = 0.10
REVIEW_MOTION_PAN_ZOOM_DELTA = 0.14
REVIEW_MOTION_PAN_FOCUS_TRAVEL = 0.40
REVIEW_MOTION_DIAGONAL_FOCUS_TRAVEL = 0.28
RENDERED_MOTION_ENDPOINT_MEAN_MIN = 8.0
RENDERED_MOTION_LOW_DIFF_THRESHOLD = 4.0
RENDERED_MOTION_LOW_DIFF_MAX_RATIO = 0.35
# A motion may be mathematically non-zero yet still read as a static still to a
# human viewer. These thresholds are the minimum editorial travel required for
# reference living-frame motion.
REVIEW_MOTION_MIN_ZOOM_DELTA = 0.055
REVIEW_MOTION_MIN_FOCUS_TRAVEL = 0.10
REVIEW_MOTION_MIN_MODE_DIVERSITY = 3
REVIEW_MOTION_MAX_DOMINANT_MODE_RATIO = 0.55
REVIEW_MOTION_MICRO_HOLD_DIFF = 0.05
REVIEW_MOTION_MAX_MICRO_HOLD_FRACTION = 0.18
REVIEW_PANEL_REUSE_WINDOW_SHOTS = 4
REVIEW_TRANSITION_DURATION_SECONDS = 0.22
PRODUCTION_REFERENCE_CADENCE_POLICY_VERSION = "production-reference-cadence-v4"
REVIEW_MIN_PANEL_CROP_DIMENSION = 32
REVIEW_MIN_PANEL_CROP_HEIGHT = 400
# Human-facing composition guards. These are review-only and intentionally
# stricter than raw geometric feasibility: a crop can be technically frameable
# yet still look accidental when it slices a face or isolates a minor extremity.
REVIEW_FACE_MIN_VISIBLE_FRACTION = 0.96
REVIEW_FACE_MIN_MARGIN_RATIO = 0.08
REVIEW_SUBJECT_MIN_COMPLETENESS = 0.72
REVIEW_DETAIL_CROP_MAX_AREA_FRACTION = 0.12
REVIEW_EXTREME_CROP_ZOOM = 2.75
REVIEW_SEQUENCE_MAX_ZOOM_RATIO = 1.85


def review_frame_edge_blank_threshold(framing_telemetry: object = None) -> float:
    """Return the per-shot blank limit, admitting only tagged wide-crop rescue."""

    if (
        isinstance(framing_telemetry, dict)
        and framing_telemetry.get("fallback_reason") == REVIEW_COHERENCE_RESCUE_REASON
    ):
        return REVIEW_COHERENCE_RESCUE_MAX_FRAME_EDGE_BLANK_FRACTION
    return REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION


def review_framing_quality_key(
    blank_fraction: float,
    base_zoom: float,
    protected_retained_fraction: float,
    *,
    preferred_blank_fraction: float = REVIEW_PREFERRED_FRAME_EDGE_BLANK_FRACTION,
) -> tuple[float, float, float, float]:
    """Rank review crops with 3% as a quality band and 8% as admission only.

    Once a crop is inside the preferred blank band, avoid needless extra zoom
    before chasing the final few blank pixels. Outside that band, reduce blank
    area first until the crop reaches the preference target.
    """

    blank = max(0.0, float(blank_fraction))
    zoom = max(0.0, float(base_zoom))
    retained = min(1.0, max(0.0, float(protected_retained_fraction)))
    target = max(0.0, float(preferred_blank_fraction))
    if blank <= target + 1e-9:
        return (0.0, zoom, -retained, blank)
    return (1.0, blank, zoom, -retained)


def review_panel_source_geometry_is_renderable(
    panel_size: tuple[int, int],
    source_upscale_manifest: object = None,
) -> bool:
    """Return whether the original source crop can support review framing."""
    try:
        width, height = int(panel_size[0]), int(panel_size[1])
        if isinstance(source_upscale_manifest, dict):
            bounds = source_upscale_manifest.get("source_panel_bounds")
            if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                left, top, right, bottom = (int(value) for value in bounds)
                width, height = right - left, bottom - top
    except (IndexError, TypeError, ValueError):
        return False
    return (
        width >= REVIEW_MIN_PANEL_CROP_DIMENSION
        and height >= REVIEW_MIN_PANEL_CROP_HEIGHT
    )


def review_visual_density_contract(
    total_duration: float,
    available_visuals: int,
) -> dict[str, float | int]:
    """Return the generic review cadence bounds for a known visual pool.

    The lower bound is the acceptance requirement; the target is the planner
    preference.  Both are capped by genuinely available evidence so the
    contract never invents panels to satisfy a cadence number.
    """

    duration = float(total_duration)
    available = int(available_visuals)
    if not math.isfinite(duration) or duration < 0.0 or available < 0:
        raise ValueError("review visual density inputs are invalid")
    minimum_required = (
        max(1, math.ceil(duration / REVIEW_MAX_SHOT_SECONDS))
        if duration > 0.0
        else 0
    )
    target = min(
        available,
        max(
            minimum_required,
            math.floor(duration / REVIEW_VISUAL_SECONDS_PER_UNIQUE_MIN),
        ),
    )
    return {
        "available_visuals": available,
        "minimum_required_visuals": minimum_required,
        "target_visuals": target,
        "min_seconds_per_visual": REVIEW_VISUAL_SECONDS_PER_UNIQUE_MIN,
        "max_seconds_per_visual": REVIEW_VISUAL_SECONDS_PER_UNIQUE_MAX,
    }


@dataclass(frozen=True)
class ReferenceProfileConfig:
    profile_id: str
    version: str
    duration_min_s: float
    duration_max_s: float
    shot_min: int
    shot_max: int
    hold_min_s: float
    hold_max_s: float
    emphasis_min_s: float
    emphasis_max_s: float
    hold_ratio_min: float
    hold_ratio_max: float
    emphasis_ratio_min: float
    emphasis_ratio_max: float
    mean_shot_min_s: float
    mean_shot_max_s: float
    hard_cut_ratio_min: float
    transition_min_s: float
    transition_max_s: float
    normal_zoom_max: float
    impact_zoom_max: float
    base_frame_zoom_max: float
    max_blank_fraction: float
    framing_contract_version: str
    framing_blank_target_fraction: float
    framing_balloon_intersection_max: float
    framing_mask_grid_long_edge: int
    framing_safe_area_margin: float
    caption_words_per_cue: int
    caption_uppercase: bool
    caption_unicode_punctuation_allowed: bool
    caption_top_sentence_allowed: bool
    caption_safe_region: tuple[float, float, float, float]
    caption_anchor: tuple[float, float]
    caption_font_weight: str
    caption_font_height_ratio: float
    caption_italic: bool
    caption_highlight_current_word: bool
    caption_primary_color: str
    caption_outline_color: str
    caption_outline_pixels: int
    caption_shadow_color: str
    caption_shadow_alpha_max: float
    caption_alignment: int
    max_canonical_panel_uses: int
    consecutive_panel_reuse_allowed: bool
    final_width: int
    final_height: int
    final_fps: int
    final_codec: str
    final_codec_profile: str
    final_pixel_format: str
    audio_lufs_target: float
    audio_true_peak_max_db: float
    unlicensed_music_sfx_allowed: bool


REFERENCE_MATCHED_SHORTS_V1 = ReferenceProfileConfig(
    profile_id="reference_matched_shorts_v1",
    version="1.0.0",
    duration_min_s=STANDARD_FINAL_DURATION_MIN_SECONDS,
    duration_max_s=STANDARD_FINAL_DURATION_MAX_SECONDS,
    shot_min=36,
    shot_max=52,
    hold_min_s=0.65,
    hold_max_s=1.59,
    emphasis_min_s=1.6,
    emphasis_max_s=2.2,
    hold_ratio_min=0.70,
    hold_ratio_max=0.80,
    emphasis_ratio_min=0.20,
    emphasis_ratio_max=0.30,
    mean_shot_min_s=1.15,
    mean_shot_max_s=1.40,
    hard_cut_ratio_min=0.85,
    transition_min_s=0.12,
    transition_max_s=0.18,
    normal_zoom_max=1.08,
    impact_zoom_max=1.14,
    base_frame_zoom_max=1.35,
    max_blank_fraction=0.18,
    framing_contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
    framing_blank_target_fraction=0.03,
    framing_balloon_intersection_max=0.0,
    framing_mask_grid_long_edge=256,
    framing_safe_area_margin=0.03,
    caption_words_per_cue=1,
    caption_uppercase=True,
    caption_unicode_punctuation_allowed=False,
    caption_top_sentence_allowed=False,
    caption_safe_region=(0.15, 0.85, 0.50, 0.75),
    caption_anchor=(0.50, 0.56),
    caption_font_weight="bold",
    caption_font_height_ratio=0.028,
    caption_italic=True,
    caption_highlight_current_word=False,
    caption_primary_color="white",
    caption_outline_color="black",
    caption_outline_pixels=6,
    caption_shadow_color="black",
    caption_shadow_alpha_max=0.35,
    caption_alignment=5,
    max_canonical_panel_uses=2,
    consecutive_panel_reuse_allowed=False,
    final_width=1080,
    final_height=1920,
    final_fps=30,
    final_codec="h264",
    final_codec_profile="High",
    final_pixel_format="yuv420p",
    audio_lufs_target=-14.0,
    audio_true_peak_max_db=-1.5,
    unlicensed_music_sfx_allowed=False,
)


REFERENCE_MATCHED_SHORTS_V2 = replace(
    REFERENCE_MATCHED_SHORTS_V1,
    profile_id="reference_matched_shorts_v2",
    version="2.0.0",
    final_fps=60,
)


def canonical_profile_json(profile: ReferenceProfileConfig) -> str:
    return json.dumps(
        asdict(profile),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def profile_hash(profile: ReferenceProfileConfig) -> str:
    return hashlib.sha256(
        canonical_profile_json(profile).encode("utf-8")
    ).hexdigest()


def resolve_reference_profile(
    profile_id: str | None,
) -> ReferenceProfileConfig | None:
    if profile_id == REFERENCE_MATCHED_SHORTS_V1.profile_id:
        return REFERENCE_MATCHED_SHORTS_V1
    if profile_id == REFERENCE_MATCHED_SHORTS_V2.profile_id:
        return REFERENCE_MATCHED_SHORTS_V2
    return None
