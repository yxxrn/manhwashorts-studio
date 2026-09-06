from __future__ import annotations

from app.services.entity_resolution import build_continuity_entities


def _obs(*, dialogue: list[str], entities: list[str] | None = None):
    return {"dialogue_or_ocr": dialogue, "entities": list(entities or [])}


def _by_name(rows):
    return {row["canonical_name"]: row for row in rows}


def test_promotes_run21_explicit_names_without_title_specific_logic():
    observations = {
        "p1": _obs(dialogue=["I THINK I WILL CHOOSE JANG NEUNG-AK."]),
        "p2": _obs(dialogue=["YOUNG LADY WI SOYEON..."], entities=["young lady"]),
    }

    rows = _by_name(build_continuity_entities(observations, ["p1", "p2"]))

    assert rows["Jang Neung-ak"]["panel_ids"] == ["p1"]
    assert rows["Wi Soyeon"]["panel_ids"] == ["p2"]
    assert "Young Lady Wi Soyeon" in rows["Wi Soyeon"]["aliases"]
    assert "young lady" in rows["Wi Soyeon"]["aliases"]
    assert "young lady" not in rows


def test_rejects_capitalized_story_terms_as_person_names():
    observations = {
        "p1": _obs(dialogue=[
            "ASSOCIATION LEADER",
            "EVOLUTIONARY REALM",
            "POISON EVASION ORB",
            "SWORD STYLE OF HEAVEN",
        ])
    }

    rows = build_continuity_entities(observations, ["p1"])

    assert rows == [{
        "entity_id": "visual-entity-observed-context",
        "canonical_name": "observed context",
        "aliases": [],
        "panel_ids": ["p1"],
    }]


def test_repeated_name_shape_promotes_across_panels_without_a_cue():
    observations = {
        "p1": _obs(dialogue=["JANG NEUNG-AK"]),
        "p2": _obs(dialogue=["JANG NEUNG-AK"]),
    }

    rows = _by_name(build_continuity_entities(observations, ["p1", "p2"]))

    assert rows["Jang Neung-ak"]["panel_ids"] == ["p1", "p2"]


def test_unproven_role_is_not_bound_to_a_name():
    observations = {
        "p1": _obs(
            dialogue=["I THINK I WILL CHOOSE JANG NEUNG-AK."],
            entities=["young master"],
        )
    }

    rows = _by_name(build_continuity_entities(observations, ["p1"]))

    assert "Jang Neung-ak" in rows
    assert "young master" in rows
    assert "young master" not in rows["Jang Neung-ak"]["aliases"]


def test_direct_title_binding_can_promote_single_token_name():
    observations = {
        "p1": _obs(dialogue=["LADY MARA, PLEASE WAIT."], entities=["lady"]),
    }

    rows = _by_name(build_continuity_entities(observations, ["p1"]))

    assert "Mara" in rows
    assert "Lady Mara" in rows["Mara"]["aliases"]
    assert "lady" in rows["Mara"]["aliases"]
