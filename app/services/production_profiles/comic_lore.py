"""Persisted Comic Lore production intent.

Research, trend discovery, bibliography selection, and Suwayomi source lookup stay
outside this module.  This profile only carries the deterministic decisions that
the production pipeline needs after an agent has finished that research.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

COMIC_LORE_RENDER_VERSION = "comic-lore-render-v2"
COMIC_THUMBNAIL_PALETTES = frozenset({"comic_yellow_red_v1", "comic_dual_contrast_v1"})


@dataclass(frozen=True)
class ComicLoreProfile:
    topic_title: str = ""
    thumbnail_headline: str = ""
    thumbnail_panel_ids: tuple[str, ...] = ()
    thumbnail_accent_words: tuple[str, ...] = ()
    thumbnail_palette: str = ""
    thumbnail_text_cleanup: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.topic_title or self.thumbnail_headline or self.thumbnail_panel_ids)

    @property
    def comic_thumbnail_palette(self) -> bool:
        return self.thumbnail_palette in COMIC_THUMBNAIL_PALETTES

    def thumbnail_binding(self, *, clean_headline) -> dict[str, object]:
        return {
            "topic_title": self.topic_title,
            "headline": clean_headline(self.thumbnail_headline),
            "panel_ids": self.thumbnail_panel_ids,
            "accent_words": self.thumbnail_accent_words,
            "comic_palette": self.comic_thumbnail_palette,
            "text_cleanup": self.thumbnail_text_cleanup,
        }


def _render_features(script: object) -> Mapping[str, object]:
    raw_metadata = getattr(script, "editorial_metadata", None) or {}
    if not isinstance(raw_metadata, Mapping):
        return {}
    raw_features = raw_metadata.get("render_features") or {}
    return raw_features if isinstance(raw_features, Mapping) else {}


def profile_from_script(script: object) -> ComicLoreProfile:
    features = _render_features(script)
    panel_ids = tuple(str(value).strip() for value in list(features.get("thumbnail_panel_ids") or []) if str(value).strip())
    accent_words = tuple(str(value).strip().upper() for value in list(features.get("thumbnail_accent_words") or []) if str(value).strip())[:2]
    return ComicLoreProfile(
        topic_title=str(features.get("topic_title") or "").strip(),
        thumbnail_headline=str(features.get("thumbnail_headline") or "").strip(),
        thumbnail_panel_ids=panel_ids,
        thumbnail_accent_words=accent_words,
        thumbnail_palette=str(features.get("thumbnail_palette") or "").strip(),
        thumbnail_text_cleanup=features.get("thumbnail_text_cleanup") is True,
    )


def topic_title_for_script(script: object) -> str:
    return profile_from_script(script).topic_title
