"""Stable production-profile boundaries for agent-facing workflows."""

from __future__ import annotations

from enum import StrEnum

from app.services.production_profiles.comic_lore import ComicLoreProfile
from app.services.production_profiles.comic_lore import (
    profile_from_script as comic_lore_profile_from_script,
)


class ProductionMode(StrEnum):
    MANHWA_RECAP = "manhwa_recap"
    COMIC_LORE = "comic_lore"


def mode_for_script(script: object) -> ProductionMode:
    """Infer only persisted production intent; never perform source research here."""
    return ProductionMode.COMIC_LORE if comic_lore_profile_from_script(script).enabled else ProductionMode.MANHWA_RECAP


__all__ = ["ComicLoreProfile", "ProductionMode", "comic_lore_profile_from_script", "mode_for_script"]
