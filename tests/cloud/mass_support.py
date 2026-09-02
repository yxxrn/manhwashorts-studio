"""Shared fixtures/helpers for cloud multimodal regression suites."""
# ruff: noqa: F401

from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest


def _module():
    try:
        return importlib.import_module("app.services.cloud_multimodal")
    except Exception as exc:
        pytest.fail(f"cloud multimodal boundary import failed in test body: {exc}")

def _identity(module):
    return module.CloudModelIdentity(
        provider="openai_compatible",
        model="mock-multimodal-v1",
        model_version="pinned",
        endpoint="http://mock.invalid/v1",
        prompt_versions={
            "visual": "balloon-free-visual-evidence-v2",
            "story_map": "cloud-causal-map-v2",
            "narration": "vision-first-story-analyzer-v3",
        },
    )

def _panels(module, prefix: str = "chapter-a"):
    return tuple(
        module.CloudPanelInput(
            panel_id=f"{prefix}-panel-{index}",
            source_asset_id=f"{prefix}-asset-{index}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"{prefix}-panel-payload-{index}".encode(),
            panel_bounds=(0, 0, 100, 100),
            source_dimensions=(100, 100),
            strip_region_id=f"{prefix}-region-{index}",
            coverage_map_version="coverage-v1",
            coverage_map_hash="b" * 64,
        )
        for index in range(3)
    )

def _boundary_request(module):
    segmentation = importlib.import_module("app.services.strip_segmentation")
    return segmentation.BoundaryRequest(
        source_asset_id="strip-a",
        source_checksum="a" * 64,
        width=400,
        height=2200,
        candidates=(
            segmentation.BoundaryCandidate(
                position=1100,
                confidence=0.8,
                score=0.8,
                run_top=1080,
                run_bottom=1120,
                reason="structural separator",
            ),
        ),
        tiles=(
            {"tile_index": 0, "y0": 0, "y1": 1200, "payload_b64": "cG5n"},
            {"tile_index": 1, "y0": 1000, "y1": 2200, "payload_b64": "cG5n"},
        ),
    )

def _visual_row(panel, *, unknown: bool = False, provider_hash: bool = False):
    sidecar = {
        "contract_version": "COLOR_AGNOSTIC_BALLOON_FREE_V1",
        "panel_id": panel["panel_id"],
        "source_asset_id": panel["source_asset_id"],
        "source_order": panel["source_order"],
        "balloon_regions": [],
        "protected_regions": [],
        "balloon_mask_status": "unknown" if unknown else "known_empty",
        "mask_confidence": 0.0 if unknown else 0.95,
        "evidence_source": "vision_geometry_unavailable" if unknown else "vision_geometry_v1",
        "mask_reason": "geometry is unavailable" if unknown else "provider explicitly reports no speech region",
    }
    return {
        "panel_id": panel["panel_id"],
        "visible_facts": [f"visible fact {panel['source_order']}"],
        "dialogue_or_ocr": [],
        "inferences": [],
        "uncertainties": [],
        "entities": [],
        "state_changes": [],
        "causal_links": [],
        "evidence_refs": [panel["panel_id"]],
        "visual_evidence": sidecar | ({"evidence_hash": "a" * 64} if provider_hash else {}),
    }

def _narrative_output(prefix: str, panel_ids: list[str]):
    from tests.factories import narrative_identity as helper
    passages = helper._passages(prefix, 4, "consequence")
    extensions = (
        " as pressure starts building nearby",
        " while the safer route disappears",
        " without proving who controls it",
        " before the next turn arrives",
    )
    for passage, extension in zip(passages, extensions, strict=True):
        passage["text"] = str(passage["text"]).rstrip(".!?") + extension + "."
    output = helper._v3_chapter(
        chapter_prefix=prefix,
        passages=passages,
        ending_kind="consequence",
    )
    for observation, panel_id in zip(output["observations"], panel_ids, strict=True):
        observation["panel_id"] = panel_id
        observation["evidence_refs"] = [panel_id]
    for passage in output["script_passages"]:
        passage["evidence_panel_ids"] = list(panel_ids)
    for claim in output["evidence_graph"]["claims"]:
        claim["evidence_panel_ids"] = list(panel_ids)
    output["coverage_manifest"]["panel_ids"] = list(panel_ids)
    output["coverage_manifest"]["total_panels"] = len(panel_ids)
    output["coverage_manifest"]["processed_panels"] = len(panel_ids)
    for chunk in output["continuity_ledger"]["chunks"]:
        chunk["panel_ids"] = list(panel_ids)
    for entity in output["continuity_ledger"]["entities"]:
        entity["panel_ids"] = list(panel_ids)
    output["narrative_outline"]["story_spine"]["unresolved_question"] = "What changes next?"
    return output

@dataclass
class _FakeProvider:
    model_id: str = "mock-multimodal-v1"
    unknown_visual: bool = False
    transient_unknown_count: int = 0
    transient_story_map_invalid_count: int = 0
    fail_for_prefix: str = ""
    fail_count: int = 0
    provider_hash: bool = False
    structured_dialogue: bool = False

    def __post_init__(self):
        self.calls: list[tuple[str, str, str]] = []
        self.analysis_run_ids: list[str] = []
        self.boundary_payloads: list[dict] = []
        self.boundary_prompts: list[str] = []
        self.narration_payloads: list[dict] = []

    def observe(self, request):
        self.calls.append(("visual", request.visual_instruction_version, request.visual_instruction_sha256))
        self.analysis_run_ids.append(request.analysis_run_id)
        if self.transient_unknown_count:
            self.transient_unknown_count -= 1
            return [
                _visual_row(panel, unknown=True, provider_hash=self.provider_hash)
                for panel in request.panels
            ]
        if self.fail_count:
            self.fail_count -= 1
            raise RuntimeError("provider secret-bearing failure detail")
        if self.fail_for_prefix and request.panels[0]["panel_id"].startswith(self.fail_for_prefix):
            raise RuntimeError("provider failure for one chapter")
        rows = [
            _visual_row(panel, unknown=self.unknown_visual, provider_hash=self.provider_hash)
            for panel in request.panels
        ]
        if self.structured_dialogue:
            for row in rows:
                row["dialogue_or_ocr"] = [{"text": "visible words", "type": "ocr"}]
                row["visible_facts"] = [{"fact": "a visible fact"}]
                row["inferences"] = [{"inference": "a qualified inference"}]
        return rows

    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        self.calls.append((stage, prompt_version, prompt_sha256))
        if stage == "narration":
            self.narration_payloads.append(dict(payload))
        if stage == "strip_segmentation":
            self.boundary_payloads.append(dict(payload))
            self.boundary_prompts.append(prompt_text)
            return {
                "source_asset_id": payload["source_asset_id"],
                "source_checksum": payload["source_checksum"],
                "random_sampling": False,
                "boundaries": [
                    {
                        "y": candidate["position"],
                        "accepted": True,
                        "confidence": 0.96,
                        "reason": "the overlapping tiles support this boundary",
                        "protected_regions": [],
                    }
                    for candidate in payload["candidate_boundaries"]
                ],
            }
        if stage == "story_semantic_audit":
            return {"verdicts": [
                {"beat_id": str(beat["beat_id"]), "supported": True,
                 "reason": "The supplied evidence semantically supports this beat."}
                for beat in payload.get("beats", [])
            ]}
        panel_ids = list(payload["panel_ids"])
        if stage == "story_understanding":
            story_map = payload.get("story_map", {})
            raw_claims = story_map.get("claims", []) if isinstance(story_map, dict) else []
            claim_ids = [
                str(claim.get("claim_id"))
                for claim in raw_claims
                if isinstance(claim, dict) and str(claim.get("claim_id", "")).strip()
            ]
            first = panel_ids[:1] or panel_ids
            last = panel_ids[-1:] or panel_ids
            return {
                "entity_registry": [],
                "narration_ready_beats": [
                    {
                        "beat_id": "understanding-1", "story_role": "setup",
                        "fact": "The grounded situation changes around the current choice.",
                        "narrative_function": "Establish the chapter's grounded change.",
                        "change": "The situation changes around the current choice.",
                        "consequence": "", "open_question": "", "importance": 4,
                        "evidence_strength": "supported_interpretation",
                        "evidence_panel_ids": first, "source_claim_ids": claim_ids[:1],
                        "entity_ids": [], "confidence": "qualified",
                        "qualification": "The supplied evidence supports this cautious reading.",
                    },
                    {
                        "beat_id": "understanding-2", "story_role": "consequence",
                        "fact": "The next grounded consequence remains unresolved.",
                        "narrative_function": "Preserve the unresolved consequence.",
                        "change": "", "consequence": "", "open_question": "What follows?",
                        "importance": 3, "evidence_strength": "supported_interpretation",
                        "evidence_panel_ids": last, "source_claim_ids": [], "entity_ids": [],
                        "confidence": "qualified",
                        "qualification": "The supplied evidence does not establish a final outcome.",
                    },
                ],
                "unresolved_threads": [],
            }
        if stage == "story_map":
            if self.transient_story_map_invalid_count:
                self.transient_story_map_invalid_count -= 1
                return {
                    "panel_ids": panel_ids,
                    "random_sampling": False,
                    "beats": [{"beat_id": "beat-1", "panel_ids": panel_ids, "summary": "pressure builds"}],
                    "causal_chain": [{"from_beat": "beat-1", "to_beat": "missing", "reason": ""}],
                    "claims": [{"claim_id": "claim-1", "panel_ids": panel_ids}],
                }
            return {
                "contract_version": "cloud-causal-map-v1",
                "panel_ids": panel_ids,
                "random_sampling": False,
                "beats": [
                    {"beat_id": "beat-1", "panel_ids": panel_ids[:2], "summary": "pressure builds"},
                    {"beat_id": "beat-2", "panel_ids": panel_ids[1:] or panel_ids, "summary": "the next choice stays open"},
                ],
                "causal_chain": [
                    {"from_beat": "beat-1", "to_beat": "beat-2", "reason": "the visible choice changes the stakes"}
                ],
                "claims": [
                    {
                        "claim_id": "map-claim-1",
                        "text": "The visible choice changes the stakes.",
                        "panel_ids": panel_ids,
                        "qualification": "The sequence supports this reading.",
                    }
                    ,{
                        "claim_id": "cloud-claim-fact",
                        "text": "The visible route changes the immediate balance.",
                        "panel_ids": panel_ids,
                        "qualification": "The ordered panels support this visible reading.",
                    },
                    {
                        "claim_id": "cloud-claim-interpretation",
                        "text": "The next choice may narrow the available route.",
                        "panel_ids": panel_ids,
                        "qualification": "This remains a qualified interpretation of the sequence.",
                    }
                ],
            }
        return _narrative_output("cloud", panel_ids)

class _BoundaryLineageProvider(_FakeProvider):
    def __init__(self, *, foreign_responses: int | None):
        super().__init__()
        self.foreign_responses = foreign_responses

    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        output = super().complete_json(
            stage=stage,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_text=prompt_text,
            payload=payload,
        )
        if stage == "strip_segmentation" and (
            self.foreign_responses is None or self.foreign_responses > 0
        ):
            if self.foreign_responses is not None:
                self.foreign_responses -= 1
            output = dict(output)
            output["source_asset_id"] = "foreign-source"
        return output

class _CompactNarrationProvider(_FakeProvider):
    """Models that return narrative content without the persisted analyzer envelope."""

    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        output = super().complete_json(
            stage=stage,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_text=prompt_text,
            payload=payload,
        )
        if stage != "narration":
            return output
        claims = []
        for claim in output["evidence_graph"]["claims"]:
            compact_claim = dict(claim)
            compact_claim.pop("claim_type", None)
            claims.append(compact_claim)
        return {
            "narrative_outline": output["narrative_outline"],
            "script_passages": output["script_passages"],
            "evidence_graph": claims,
        }

class _CausalMapClaimsOnlyProvider(_FakeProvider):
    """Models that reuse causal-map claim IDs but omit the graph envelope."""

    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        output = super().complete_json(
            stage=stage,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_text=prompt_text,
            payload=payload,
        )
        if stage != "narration":
            return output
        claim_ids = ["map-claim-1", "cloud-claim-fact", "cloud-claim-interpretation"]
        for index, passage in enumerate(output["script_passages"]):
            passage["claim_ids"] = [claim_ids[index % len(claim_ids)]]
        return {
            "narrative_outline": output["narrative_outline"],
            "script_passages": output["script_passages"],
        }

class _InvalidNarrationProvider(_FakeProvider):
    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        if stage == "narration":
            return {"narrative_outline": {}, "script_passages": [], "evidence_graph": []}
        return super().complete_json(
            stage=stage,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_text=prompt_text,
            payload=payload,
        )

def _immutable_slot_fixture(module):
    base_panels = _panels(module, "immutable-slot")
    panels = base_panels + tuple(
        replace(
            base_panels[-1],
            panel_id=f"immutable-slot-panel-{index}",
            source_asset_id=f"immutable-slot-asset-{index}",
            source_order=index,
            payload=f"immutable-slot-payload-{index}".encode(),
            payload_checksum="",
            source_checksum="",
            strip_region_id=f"immutable-slot-region-{index}",
        )
        for index in (4, 5)
    )
    rows = []
    for panel in panels:
        observation = _visual_row(panel.descriptor())
        rows.append(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
                "panel_bounds": list(panel.panel_bounds),
                "source_dimensions": list(panel.source_dimensions),
                "observation": observation,
                "visual_evidence": observation["visual_evidence"],
                "evidence_hash": "",
            }
        )
    identity = _identity(module)
    visual = module.VisualStageResult(
        panels=tuple(rows),
        source_hash="immutable-slot-source",
        model_identity_hash=identity.identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    panel_ids = [panel.panel_id for panel in panels]
    beats = tuple(
        {
            "beat_id": f"immutable-beat-{index}",
            "panel_ids": [panel_id],
            "summary": f"the sequence reaches beat {index}",
        }
        for index, panel_id in enumerate(panel_ids)
    )
    claims = tuple(
        {
            "claim_id": f"immutable-claim-{index}-{claim_index}",
            "claim_type": "fact",
            "text": f"The visible beat {index} claim {claim_index} changes the situation.",
            "panel_ids": [panel_id],
            "evidence_panel_ids": [panel_id],
            "qualification": "The ordered panel supports this reading.",
        }
        for index, panel_id in enumerate(panel_ids)
        for claim_index in range(2)
    )
    passages = tuple(
        {
            "passage_id": f"immutable-passage-{index}",
            "editorial_role": "causal_turn",
            "text": f"The sequence reaches beat {index} before the next turn.",
            "claim_ids": [
                f"immutable-claim-{index}-0",
                f"immutable-claim-{index}-1",
            ],
            "evidence_panel_ids": [panel_id],
        }
        for index, panel_id in enumerate(panel_ids)
    )
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel_ids),
        beats=beats,
        causal_chain=tuple(
            {
                "from_beat": beats[index]["beat_id"],
                "to_beat": beats[index + 1]["beat_id"],
                "reason": "the next visible beat follows",
            }
            for index in range(len(beats) - 1)
        ),
        claims=claims,
        story_map_hash="immutable-story-map",
        model_identity_hash=identity.identity_hash,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    prompt = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
    ).prompts["narration"]
    candidate = module.NarrationResult(
        spoken_text=" ".join(item["text"] for item in passages),
        display_words=("THE", "SEQUENCE"),
        passages=passages,
        ending_kind="consequence",
        word_count=160,
        estimated_duration_s=64.35,
        qc_report={},
        model_identity_hash=identity.identity_hash,
        prompt_version=prompt[0],
        prompt_sha256=prompt[1],
        observations=tuple(row["observation"] for row in rows),
        continuity_ledger={},
        evidence_graph={"claims": [dict(claim) for claim in claims]},
        story_spine={
            "wants": "understand the visible turn",
            "obstacle": "the route changes",
            "decision": "respond to the change",
            "consequence": "the stakes move",
            "changed_stakes": "the next beat matters",
            "unresolved_direction": "what follows",
        },
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
    )
    return runner, candidate, visual, story_map

def _position_rewrite_text(word_budget, prefix):
    return " ".join(f"{prefix.rstrip('_')}word{index}" for index in range(word_budget))

def _micro_compaction_rewrite_texts(counts):
    rewrites = []
    for index, count in enumerate(counts):
        if index == 0:
            prefix = "it is"
            filler_count = count - 2
        elif index == 1:
            prefix = "does not"
            filler_count = count - 2
        else:
            prefix = ""
            filler_count = count
        fillers = [f"compact{index}word{word_index}" for word_index in range(filler_count)]
        rewrites.append(" ".join(part for part in (prefix, *fillers) if part))
    return rewrites

def _provider_position_vector(payload):
    rows = payload["targeted_repair"]["position_context"]
    vocabulary = [
        "Now",
        "the",
        "visible",
        "turn",
        "changes",
        "what",
        "comes",
        "next",
        "because",
        "the",
        "stakes",
        "shift",
        "while",
        "the",
        "next",
        "choice",
        "keeps",
        "pressure",
        "moving",
        "forward",
    ]
    return {
        "rewrites": [
            " ".join((vocabulary * ((row["word_budget"] // len(vocabulary)) + 1))[: row["word_budget"]])
            for row in rows
        ]
    }

def _repair_identity_metadata(module):
    return {
        "policy_version": "narration-repair-identity-v1",
        "panel_lineage": {
            "ordered_panel_ids": ["panel-1", "panel-2", "panel-3"],
            "panel_identity_hashes": ["a" * 64, "b" * 64, "c" * 64],
            "visual_evidence_hash": "v" * 64,
            "panels": [
                {
                    "panel_id": "panel-1",
                    "source_order": 10,
                    "prepared_order": 0,
                    "evidence_hash": "a" * 64,
                },
                {
                    "panel_id": "panel-2",
                    "source_order": 11,
                    "prepared_order": 1,
                    "evidence_hash": "b" * 64,
                },
                {
                    "panel_id": "panel-3",
                    "source_order": 12,
                    "prepared_order": 2,
                    "evidence_hash": "c" * 64,
                },
            ],
        },
        "model": {"identity_hash": "m" * 64},
        "prompt": {"version": "narration-v1", "sha256": "p" * 64},
        "story": {
            "panel_ids": ["panel-1", "panel-2", "panel-3"],
            "beats_hash": "b" * 64,
            "claims_hash": "c" * 64,
            "causal_chain_hash": "h" * 64,
            "story_map_hash": "s" * 64,
            "beat_count": 2,
            "claim_count": 3,
            "causal_link_count": 1,
        },
        "selection": {
            "beat_ids": ["beat-1", "beat-2"],
            "panel_ids": ["panel-1", "panel-2", "panel-3"],
            "claim_ids": ["claim-1", "claim-2"],
            "selection_hash": "e" * 64,
        },
        "slot_registry": {
            "slot_ids": ["slot-1", "slot-2"],
            "claim_ids": ["claim-1", "claim-2"],
            "evidence_panel_ids": ["panel-1", "panel-2", "panel-3"],
            "slot_order_hash": "o" * 64,
        },
        "candidate": {
            "candidate_hash": "q" * 64,
            "visual_evidence_hash": "v" * 64,
            "model_identity_hash": "m" * 64,
            "prompt_version": "narration-v1",
            "prompt_sha256": "p" * 64,
            "story_map_hash": "s" * 64,
        },
    }

def _admission_png(color: tuple[int, int, int], *, size: tuple[int, int] = (32, 32)) -> bytes:
    import io

    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()

def _admission_panel(module, panel_id: str, *, order: int, bounds=(0, 0, 32, 32), payload=None):
    return module.CloudPanelInput(
        panel_id=panel_id,
        source_asset_id="admission-asset",
        source_order=order,
        mime_type="image/png",
        payload=payload or _admission_png((order + 1, 80, 120)),
        source_checksum="a" * 64,
        panel_bounds=bounds,
        source_dimensions=(32, 128),
        strip_region_id=panel_id,
        coverage_map_hash="c" * 64,
    )

