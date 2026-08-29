"""Shared test factories extracted from regression modules."""
# ruff: noqa: F401

from __future__ import annotations

import hashlib
import importlib
from dataclasses import replace
from pathlib import Path

import pytest


def _v3_chapter(
    *,
    chapter_prefix: str,
    passages: list[dict[str, object]],
    ending_kind: str,
    dialogue: list[str] | None = None,
) -> dict[str, object]:
    panel_ids = tuple(f"{chapter_prefix}-panel-{index}" for index in range(3))
    dialogue_values = dialogue or []
    observations = []
    for source_index, panel_id in enumerate(panel_ids):
        observations.append(
            {
                "panel_id": panel_id,
                "source_asset_id": f"{chapter_prefix}-asset",
                "strip_region_id": f"{chapter_prefix}-region-{source_index}",
                "source_index": source_index,
                "region_bounds": {
                    "x": 0,
                    "y": source_index * 100,
                    "width": 800,
                    "height": 100,
                },
                "coverage_map_version": "coverage-v3-test",
                "coverage_map_hash": f"{chapter_prefix}-coverage",
                "visible_facts": [f"{chapter_prefix} fact {source_index}"],
                "dialogue_or_ocr": dialogue_values if source_index == 0 else [],
                "inferences": [f"{chapter_prefix} inference {source_index}"],
                "uncertainties": [],
                "evidence_refs": [panel_id],
            }
        )
    claims = [
        {
            "claim_id": f"{chapter_prefix}-claim-fact",
            "claim_type": "fact",
            "text": f"{chapter_prefix} fact claim is visible.",
            "qualification": "The panel visibly supports this fact.",
            "evidence_panel_ids": [panel_ids[0]],
        },
        {
            "claim_id": f"{chapter_prefix}-claim-interpretation",
            "claim_type": "interpretation",
            "text": f"{chapter_prefix} decision may change the route.",
            "qualification": "The sequence suggests this consequence but does not prove intent.",
            "evidence_panel_ids": [panel_ids[1], panel_ids[2]],
        },
    ]
    return {
        "observations": observations,
        "continuity_ledger": {
            "chunks": [
                {
                    "chunk_id": f"{chapter_prefix}-chunk-0",
                    "panel_ids": list(panel_ids[:2]),
                },
                {
                    "chunk_id": f"{chapter_prefix}-chunk-1",
                    "panel_ids": list(panel_ids[1:]),
                },
            ],
            "entities": [
                {
                    "entity_id": f"{chapter_prefix}-entity",
                    "canonical_name": f"{chapter_prefix} witness",
                    "aliases": [],
                    "panel_ids": list(panel_ids),
                }
            ],
            "motives": [],
            "state_changes": [],
            "causal_links": [],
            "reconciled_after_final_chunk": True,
        },
        "evidence_graph": {"claims": claims},
        "coverage_manifest": {
            "total_panels": 3,
            "processed_panels": 3,
            "panel_ids": list(panel_ids),
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
        },
        "narrative_outline": {
            "story_spine": {
                "who_wants_what": f"{chapter_prefix} wants an answer.",
                "obstacle": f"{chapter_prefix} faces a locked route.",
                "decision": f"{chapter_prefix} chooses a risky opening.",
                "consequence": f"{chapter_prefix} changes the immediate balance.",
                "changed_stakes": f"{chapter_prefix} may lose the next chance.",
                "unresolved_question": f"What will {chapter_prefix} do next?",
            },
            "ending_kind": ending_kind,
        },
        "script_passages": passages,
    }

def _fit_passage(text: str, word_count: int, ending: str) -> str:
    words = text.split()[:word_count]
    assert len(words) == word_count
    return " ".join(words).rstrip(".!?") + ending

def _passages(
    prefix: str,
    count: int,
    ending_kind: str,
    total_words: int | None = None,
) -> list[dict[str, object]]:
    if total_words is None:
        budgets = {
            4: [24, 24, 26, 26],
            5: [18, 20, 22, 20, 20],
            6: [16, 16, 17, 17, 17, 17],
        }[count]
    else:
        base, remainder = divmod(total_words, count)
        budgets = [base + (index < remainder) for index in range(count)]
    source_texts = [
        "At the first panel, the witness spots a red signal and chooses the locked route while pressure gathers around the gate. The visible clue gives this decision a concrete cost.",
        "By the next beat, the guarded path closes, and the visible clue changes what the witness can safely attempt. The sequence keeps the motive grounded without proving every hidden intention.",
        "Then the sequence turns: the witness may risk an opening, while the panels suggest that someone else controls the route. That qualified possibility matters because the immediate choice narrows.",
        "The consequence is concrete, because losing that opening could strand the witness before the hidden direction becomes clear. The evidence supports the pressure, not an invented identity or motive.",
        "Still, the evidence leaves room for a sharper turn rather than proving the hidden motive behind the waiting group. A careful friend would keep that uncertainty visible.",
        "For now, the next move remains unresolved, and the final image keeps the cost close to the surface. The chapter earns its tension from what the panels show and withhold.",
    ]
    labels = (
        "opening_observation",
        "rising_choice",
        "pressure_turn",
        "visible_consequence",
        "qualified_insight",
        "unresolved_direction",
    )
    claim_ids = (f"{prefix}-claim-fact", f"{prefix}-claim-interpretation")
    panel_ids = [f"{prefix}-panel-{index}" for index in range(3)]
    passages: list[dict[str, object]] = []
    for index, budget in enumerate(budgets):
        ending = "."
        if index == count - 1:
            ending = "?" if ending_kind == "open_question" else "."
        passages.append(
            {
                "passage_id": f"{prefix}-passage-{index}",
                "editorial_role": labels[index],
                "text": _fit_passage(source_texts[index], budget, ending),
                "claim_ids": [claim_ids[index % 2]],
                "evidence_panel_ids": panel_ids,
            }
        )
    return passages

