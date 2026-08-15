import hashlib
import json
from dataclasses import asdict, dataclass, replace


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
    duration_min_s=38.0,
    duration_max_s=50.0,
    shot_min=28,
    shot_max=36,
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
