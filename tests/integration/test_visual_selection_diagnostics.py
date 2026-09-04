from __future__ import annotations

import pytest

from app.services import vision_adapter as va


def _passage(role: str, ids: list[str]) -> dict:
    return {
        "editorial_role": role,
        "evidence_panel_ids": ids,
    }


def test_visual_selection_failure_reports_safe_counts():
    preferred = tuple(f"p{i}" for i in range(1, 20))
    sections = {
        "hook": preferred[:14],
        "setup": preferred[:9],
        "conflict": preferred[:14],
        "twist": preferred[:9],
        "cta": preferred[:9],
    }
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="run",
        instruction_version="v",
        instruction_sha256="a" * 64,
        instruction_text="x",
        expected_panel_ids=preferred,
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        target_word_count_min=115,
        target_word_count_max=125,
        preferred_visual_panel_ids=preferred,
        preferred_visual_panel_ids_by_section=sections,
    )
    output = {
        "script_passages": [
            _passage("hook", list(preferred[:4])),
            _passage("setup", list(preferred[:4])),
            _passage("escalation", list(preferred[:4])),
            _passage("editorial_insight", list(preferred[:4])),
            _passage("payoff_open_loop", list(preferred[:4])),
        ]
    }
    with pytest.raises(va.VisionResponseInvalid) as caught:
        va.validate_synthesis_visual_selection(output, request)
    diag = caught.value.selection_diagnostics
    assert diag["preferred_total"] == 19
    assert diag["unique_min"] == 18
    assert diag["used_preferred"] == 4
    assert diag["section_capacities"] == {
        "hook": 14, "setup": 9, "conflict": 14, "twist": 9, "cta": 9
    }
    assert [row["selected_section"] for row in diag["passages"]] == [4, 4, 4, 4, 4]
