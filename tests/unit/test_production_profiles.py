from types import SimpleNamespace

from app.services.production_profiles import ProductionMode, mode_for_script
from app.services.production_profiles.comic_lore import profile_from_script, topic_title_for_script


def _script(features=None):
    return SimpleNamespace(editorial_metadata={"render_features": features or {}})


def test_plain_script_defaults_to_manhwa_recap_mode() -> None:
    script = _script()
    assert mode_for_script(script) is ProductionMode.MANHWA_RECAP
    assert topic_title_for_script(script) == ""


def test_comic_lore_profile_is_persisted_production_intent_only() -> None:
    script = _script(
        {
            "version": "comic-lore-render-v2",
            "topic_title": "Why Doctor Doom Is So Dangerous: Science + Sorcery",
            "thumbnail_headline": "WHY DR. DOOM IS SO DANGEROUS",
            "thumbnail_panel_ids": ["doom-panel"],
            "thumbnail_accent_words": ["doom", "dangerous", "ignored"],
            "thumbnail_palette": "comic_dual_contrast_v1",
            "thumbnail_text_cleanup": True,
        }
    )
    profile = profile_from_script(script)
    assert mode_for_script(script) is ProductionMode.COMIC_LORE
    assert profile.enabled is True
    assert profile.topic_title == "Why Doctor Doom Is So Dangerous: Science + Sorcery"
    assert profile.thumbnail_headline == "WHY DR. DOOM IS SO DANGEROUS"
    assert profile.thumbnail_panel_ids == ("doom-panel",)
    assert profile.thumbnail_accent_words == ("DOOM", "DANGEROUS")
    assert profile.comic_thumbnail_palette is True
    assert profile.thumbnail_text_cleanup is True
    assert topic_title_for_script(script) == profile.topic_title


def test_comic_lore_profile_does_not_do_research_or_source_resolution() -> None:
    profile = profile_from_script(_script({"topic_title": "How Strong Is Doctor Doom?"}))
    assert profile.enabled is True
    assert profile.thumbnail_panel_ids == ()
    assert not hasattr(profile, "sources")
