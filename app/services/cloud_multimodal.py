"""Pinned cloud multimodal stages and resumable review-only chapter jobs.

The module deliberately stops before TTS.  It reuses the existing visual
evidence, analyzer, and Sharp Friend validators; this layer owns provider
selection, stage identity, local reconciliation, cache keys, and batch state.
Provider output is untrusted JSON.  Canonical hashes are always computed here.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from app.services import (
    analyzer_contract,
    editorial_qc,
    narrative_identity,
    quality,
    script,
    visual_scoring,
)
from app.services.vision_adapter import VisionObservationRequest

CAUSAL_MAP_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "cloud_causal_map_v1.txt"
CAUSAL_MAP_PROMPT_VERSION = "cloud-causal-map-v1"


class CloudStageError(RuntimeError):
    """Safe, machine-readable failure at the cloud stage boundary."""

    def __init__(self, code: str, message: str = "cloud stage failed", *, reviewable: bool = False):
        self.code = code
        self.reviewable = reviewable
        super().__init__(message)


class ChapterState(StrEnum):
    INGESTED = "INGESTED"
    VISUAL_ANALYZED = "VISUAL_ANALYZED"
    STORY_MAPPED = "STORY_MAPPED"
    SCRIPTED = "SCRIPTED"
    READY_TO_RENDER = "READY_TO_RENDER"
    RENDERED = "RENDERED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


@dataclass(frozen=True)
class CloudModelIdentity:
    provider: str
    model: str
    model_version: str
    endpoint: str
    prompt_versions: Mapping[str, str]

    def __post_init__(self) -> None:
        values = dict(self.prompt_versions)
        if (
            not self.provider.strip()
            or not self.model.strip()
            or not self.model_version.strip()
            or not isinstance(self.endpoint, str)
            or not self.endpoint.strip()
        ):
            raise CloudStageError("cloud.model_identity_invalid")
        if not values or any(not str(key).strip() or not str(value).strip() for key, value in values.items()):
            raise CloudStageError("cloud.model_identity_invalid")
        object.__setattr__(self, "prompt_versions", tuple(sorted((str(k), str(v)) for k, v in values.items())))

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
            "endpoint": self.endpoint,
            "prompt_versions": dict(self.prompt_versions),
        }

    @property
    def identity_hash(self) -> str:
        return _hash(self.as_dict())


@dataclass(frozen=True)
class CloudPanelInput:
    panel_id: str
    source_asset_id: str
    source_order: int
    mime_type: str
    payload: bytes
    source_checksum: str = ""
    payload_checksum: str = ""
    panel_bounds: tuple[int, int, int, int] | None = None
    source_dimensions: tuple[int, int] | None = None
    strip_region_id: str = ""
    coverage_map_version: str = ""
    coverage_map_hash: str = ""

    def __post_init__(self) -> None:
        if not self.panel_id.strip() or not self.source_asset_id.strip():
            raise CloudStageError("cloud.panel_lineage_invalid")
        if isinstance(self.source_order, bool) or not isinstance(self.source_order, int) or self.source_order < 0:
            raise CloudStageError("cloud.panel_lineage_invalid")
        if not self.mime_type.lower().startswith("image/") or not self.payload:
            raise CloudStageError("cloud.panel_payload_invalid")
        payload_checksum = hashlib.sha256(self.payload).hexdigest()
        if self.payload_checksum and self.payload_checksum != payload_checksum:
            raise CloudStageError("cloud.payload_checksum_mismatch")
        if self.panel_bounds is not None and (
            len(self.panel_bounds) != 4
            or not all(isinstance(value, int) and not isinstance(value, bool) for value in self.panel_bounds)
            or self.panel_bounds[0] < 0
            or self.panel_bounds[1] < 0
            or self.panel_bounds[2] <= self.panel_bounds[0]
            or self.panel_bounds[3] <= self.panel_bounds[1]
        ):
            raise CloudStageError("cloud.panel_lineage_invalid")
        if self.source_dimensions is not None:
            if (
                len(self.source_dimensions) != 2
                or not all(isinstance(value, int) and not isinstance(value, bool) for value in self.source_dimensions)
                or any(value <= 0 for value in self.source_dimensions)
            ):
                raise CloudStageError("cloud.panel_lineage_invalid")
            if self.panel_bounds is not None and (
                self.panel_bounds[2] > self.source_dimensions[0]
                or self.panel_bounds[3] > self.source_dimensions[1]
            ):
                raise CloudStageError("cloud.panel_lineage_invalid")
        if self.coverage_map_hash and len(self.coverage_map_hash) != 64:
            raise CloudStageError("cloud.panel_lineage_invalid")
        object.__setattr__(self, "payload_checksum", payload_checksum)
        if not self.source_checksum:
            object.__setattr__(self, "source_checksum", payload_checksum)

    def descriptor(self) -> dict[str, Any]:
        descriptor = {
            "panel_id": self.panel_id,
            "source_asset_id": self.source_asset_id,
            "source_order": self.source_order,
            "mime_type": self.mime_type,
            "source_checksum": self.source_checksum,
            "payload_checksum": self.payload_checksum,
        }
        if self.panel_bounds is not None:
            descriptor["panel_bounds"] = list(self.panel_bounds)
        if self.source_dimensions is not None:
            descriptor["source_dimensions"] = list(self.source_dimensions)
        if self.strip_region_id:
            descriptor["strip_region_id"] = self.strip_region_id
        if self.coverage_map_version:
            descriptor["coverage_map_version"] = self.coverage_map_version
        if self.coverage_map_hash:
            descriptor["coverage_map_hash"] = self.coverage_map_hash
        return descriptor


class CloudMultimodalProvider(Protocol):
    model_id: str

    def observe(self, request: VisionObservationRequest) -> list[Mapping[str, Any]]:
        ...

    def complete_json(
        self,
        *,
        stage: str,
        prompt_version: str,
        prompt_sha256: str,
        prompt_text: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...


class StageCache(Protocol):
    def get(self, key: str) -> Mapping[str, Any] | None:
        ...

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        ...


class MemoryStageCache:
    def __init__(self) -> None:
        self._values: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> Mapping[str, Any] | None:
        value = self._values.get(key)
        return json.loads(json.dumps(value)) if value is not None else None

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        self._values[key] = json.loads(json.dumps(value))


@dataclass(frozen=True)
class VisualStageResult:
    panels: tuple[dict[str, Any], ...]
    source_hash: str
    model_identity_hash: str
    prompt_version: str
    prompt_sha256: str
    reconciled: bool = True

    @property
    def panel_ids(self) -> tuple[str, ...]:
        return tuple(item["panel_id"] for item in self.panels)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"panels": [dict(item) for item in self.panels]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> VisualStageResult:
        return cls(
            panels=tuple(dict(item) for item in value["panels"]),
            source_hash=str(value["source_hash"]),
            model_identity_hash=str(value["model_identity_hash"]),
            prompt_version=str(value["prompt_version"]),
            prompt_sha256=str(value["prompt_sha256"]),
            reconciled=bool(value["reconciled"]),
        )


@dataclass(frozen=True)
class StoryMapResult:
    panel_ids: tuple[str, ...]
    beats: tuple[dict[str, Any], ...]
    causal_chain: tuple[dict[str, Any], ...]
    claims: tuple[dict[str, Any], ...]
    story_map_hash: str
    model_identity_hash: str
    prompt_version: str
    prompt_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "panel_ids": list(self.panel_ids),
            "beats": [dict(item) for item in self.beats],
            "causal_chain": [dict(item) for item in self.causal_chain],
            "claims": [dict(item) for item in self.claims],
            "story_map_hash": self.story_map_hash,
            "model_identity_hash": self.model_identity_hash,
            "prompt_version": self.prompt_version,
            "prompt_sha256": self.prompt_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StoryMapResult:
        return cls(
            panel_ids=tuple(value["panel_ids"]),
            beats=tuple(dict(item) for item in value["beats"]),
            causal_chain=tuple(dict(item) for item in value["causal_chain"]),
            claims=tuple(dict(item) for item in value["claims"]),
            story_map_hash=str(value["story_map_hash"]),
            model_identity_hash=str(value["model_identity_hash"]),
            prompt_version=str(value["prompt_version"]),
            prompt_sha256=str(value["prompt_sha256"]),
        )


@dataclass(frozen=True)
class NarrationResult:
    spoken_text: str
    display_words: tuple[str, ...]
    passages: tuple[dict[str, Any], ...]
    ending_kind: str
    word_count: int
    estimated_duration_s: float
    qc_report: dict[str, Any]
    model_identity_hash: str
    prompt_version: str
    prompt_sha256: str
    requires_voice_timing: bool = True
    observations: tuple[dict[str, Any], ...] = ()
    continuity_ledger: dict[str, Any] = field(default_factory=dict)
    evidence_graph: dict[str, Any] = field(default_factory=dict)
    story_spine: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"display_words": list(self.display_words), "passages": [dict(item) for item in self.passages]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> NarrationResult:
        qc_report = dict(value["qc_report"])
        signals = dict(qc_report.get("signals", {}))
        for key in ("generic_hype_hits", "cta_hits", "warnings"):
            if key in signals:
                signals[key] = tuple(signals[key])
        if "signals" in qc_report:
            qc_report["signals"] = signals
        return cls(
            spoken_text=str(value["spoken_text"]),
            display_words=tuple(value["display_words"]),
            passages=tuple(dict(item) for item in value["passages"]),
            ending_kind=str(value["ending_kind"]),
            word_count=int(value["word_count"]),
            estimated_duration_s=float(value["estimated_duration_s"]),
            qc_report=qc_report,
            model_identity_hash=str(value["model_identity_hash"]),
            prompt_version=str(value["prompt_version"]),
            prompt_sha256=str(value["prompt_sha256"]),
            requires_voice_timing=bool(value.get("requires_voice_timing", True)),
            observations=tuple(dict(item) for item in value.get("observations", [])),
            continuity_ledger=dict(value.get("continuity_ledger", {})),
            evidence_graph=dict(value.get("evidence_graph", {})),
            story_spine=dict(value.get("story_spine", {})),
        )


@dataclass(frozen=True)
class ChapterResult:
    state: ChapterState
    visual: VisualStageResult
    story_map: StoryMapResult
    narration: NarrationResult

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "visual": self.visual.as_dict(),
            "story_map": self.story_map.as_dict(),
            "narration": self.narration.as_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ChapterResult:
        return cls(
            state=ChapterState(str(value["state"])),
            visual=VisualStageResult.from_dict(value["visual"]),
            story_map=StoryMapResult.from_dict(value["story_map"]),
            narration=NarrationResult.from_dict(value["narration"]),
        )


@dataclass(frozen=True)
class ReviewOnlyRenderGate:
    allowed: bool
    audio_path: None = None
    music_path: None = None
    timing_source: str = "voice_required"
    publish_allowed: bool = False
    reason_code: str = "cloud.voice_timing_required"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _load_causal_prompt() -> tuple[str, str, str]:
    try:
        text = CAUSAL_MAP_PROMPT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise CloudStageError("cloud.prompt_missing") from None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if "Version: cloud-causal-map-v1" not in normalized:
        raise CloudStageError("cloud.prompt_invalid")
    return CAUSAL_MAP_PROMPT_VERSION, hashlib.sha256(normalized.encode("utf-8")).hexdigest(), normalized


def _prompt_specs() -> dict[str, tuple[str, str, str]]:
    return {
        "visual": visual_scoring.load_visual_evidence_instruction(),
        "story_map": _load_causal_prompt(),
        "narration": narrative_identity.load_narrative_instruction("sharp_friend_v1"),
    }


def _cache_key(stage: str, source: object, identity: CloudModelIdentity, prompt: tuple[str, str, str]) -> str:
    return _hash(
        {
            "stage": stage,
            "source": source,
            "model_identity": identity.as_dict(),
            "model_identity_hash": identity.identity_hash,
            "prompt_version": prompt[0],
            "prompt_sha256": prompt[1],
        }
    )


class CloudStageRunner:
    """Run visual, causal-map, and narration calls with one pinned model."""

    def __init__(
        self,
        *,
        provider: CloudMultimodalProvider,
        model_identity: CloudModelIdentity,
        cache: StageCache | None = None,
        max_attempts: int = 2,
        max_requests: int | None = None,
        min_request_interval_s: float = 0.0,
        estimated_cost_per_request: float = 0.0,
    ) -> None:
        model_id = getattr(provider, "model_id", "")
        if not isinstance(model_id, str) or model_id != model_identity.model:
            raise CloudStageError("cloud.model_identity_mismatch")
        self.provider = provider
        self.model_identity = model_identity
        self.cache = cache
        self.max_attempts = max(1, int(max_attempts))
        self.max_requests = max_requests if max_requests is None else max(1, int(max_requests))
        self.min_request_interval_s = max(0.0, float(min_request_interval_s))
        self.estimated_cost_per_request = max(0.0, float(estimated_cost_per_request))
        self.request_count = 0
        self.estimated_cost_usd = 0.0
        self._last_request_at = 0.0
        self.prompts = _prompt_specs()
        expected = dict(model_identity.prompt_versions)
        if any(expected.get(stage) != prompt[0] for stage, prompt in self.prompts.items()):
            raise CloudStageError("cloud.prompt_identity_mismatch")

    def _call(self, operation) -> Any:
        last_error: Exception | None = None
        for _ in range(self.max_attempts):
            if self.max_requests is not None and self.request_count >= self.max_requests:
                raise CloudStageError("cloud.request_budget_exceeded", reviewable=True)
            if self.min_request_interval_s:
                wait = self.min_request_interval_s - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    time.sleep(wait)
            self.request_count += 1
            self.estimated_cost_usd += self.estimated_cost_per_request
            self._last_request_at = time.monotonic()
            try:
                return operation()
            except CloudStageError:
                raise
            except Exception as exc:
                last_error = exc
        del last_error
        raise CloudStageError("cloud.provider_request_failed") from None

    @staticmethod
    def _ordered_panels(panels: Sequence[CloudPanelInput]) -> tuple[CloudPanelInput, ...]:
        values = tuple(sorted(panels, key=lambda item: (item.source_order, item.panel_id)))
        if not values or len({item.panel_id for item in values}) != len(values):
            raise CloudStageError("cloud.panel_coverage_incomplete")
        if len({item.source_order for item in values}) != len(values):
            raise CloudStageError("cloud.panel_lineage_invalid")
        return values

    def run_visual_evidence(self, panels: Sequence[CloudPanelInput]) -> VisualStageResult:
        ordered = self._ordered_panels(panels)
        prompt = self.prompts["visual"]
        source = [item.descriptor() for item in ordered]
        key = _cache_key("visual", source, self.model_identity, prompt)
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            return VisualStageResult.from_dict(cached)
        instruction_version, instruction_sha256, _ = analyzer_contract.load_analyzer_instruction()
        request = VisionObservationRequest(
            analysis_run_id=f"cloud-{_hash(source)[:24]}",
            instruction_version=instruction_version,
            instruction_sha256=instruction_sha256,
            chunk_index=0,
            panels=tuple(
                {
                    **item.descriptor(),
                    "payload": item.payload,
                }
                for item in ordered
            ),
            visual_instruction_version=prompt[0],
            visual_instruction_sha256=prompt[1],
        )
        raw_rows = self._call(lambda: self.provider.observe(request))
        if not isinstance(raw_rows, list) or len(raw_rows) != len(ordered):
            raise CloudStageError("cloud.provider_response_invalid")
        reconciled: list[dict[str, Any]] = []
        for item, raw in zip(ordered, raw_rows, strict=True):
            if not isinstance(raw, Mapping) or raw.get("panel_id") != item.panel_id:
                raise CloudStageError("cloud.panel_lineage_invalid")
            raw_visual = raw.get("visual_evidence")
            if not isinstance(raw_visual, Mapping):
                raise CloudStageError("visual.balloon_mask_unknown", reviewable=True)
            if raw_visual.get("evidence_hash"):
                raise CloudStageError("cloud.provider_hash_forbidden")
            visual = dict(raw_visual)
            visual.setdefault("contract_version", visual_scoring.VISUAL_EVIDENCE_CONTRACT_VERSION)
            visual.pop("evidence_hash", None)
            try:
                merged, evidence = visual_scoring.ensure_panel_visual_evidence(
                    {**dict(raw), "visual_evidence": visual},
                    panel_id=item.panel_id,
                    source_asset_id=item.source_asset_id,
                    source_order=item.source_order,
                )
            except visual_scoring.VisualEvidenceError as exc:
                raise CloudStageError(getattr(exc, "code", "cloud.visual_evidence_invalid")) from None
            if evidence.balloon_mask_status == "unknown":
                raise CloudStageError("visual.balloon_mask_unknown", reviewable=True)
            source_values = {evidence.evidence_source, *(region.evidence_source for region in evidence.balloon_regions)}
            if any("ocr" in value.lower() for value in source_values):
                raise CloudStageError("visual.balloon_geometry_invalid", reviewable=True)
            evidence_json = visual_scoring.panel_visual_evidence_json(evidence)
            merged["visual_evidence"] = evidence_json
            reconciled.append(
                {
                    "panel_id": item.panel_id,
                    "source_asset_id": item.source_asset_id,
                    "source_order": item.source_order,
                    "source_checksum": item.source_checksum,
                    "observation": merged,
                    "visual_evidence": evidence_json,
                    "evidence_hash": evidence_json["evidence_hash"],
                }
            )
        result = VisualStageResult(
            panels=tuple(reconciled),
            source_hash=_hash(source),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
        )
        if self.cache is not None:
            self.cache.put(key, result.as_dict())
        return result

    def run_story_map(self, visual: VisualStageResult) -> StoryMapResult:
        prompt = self.prompts["story_map"]
        source = {
            "panel_ids": list(visual.panel_ids),
            "visual": visual.panels,
            "visual_source_hash": visual.source_hash,
        }
        key = _cache_key("story_map", source, self.model_identity, prompt)
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            return StoryMapResult.from_dict(cached)
        raw = self._call(
            lambda: self.provider.complete_json(
                stage="story_map",
                prompt_version=prompt[0],
                prompt_sha256=prompt[1],
                prompt_text=prompt[2],
                payload=source,
            )
        )
        result = self._reconcile_story_map(raw, visual.panel_ids, prompt)
        if self.cache is not None:
            self.cache.put(key, result.as_dict())
        return result

    def _reconcile_story_map(
        self,
        raw: Any,
        expected_panel_ids: tuple[str, ...],
        prompt: tuple[str, str, str],
    ) -> StoryMapResult:
        if not isinstance(raw, Mapping) or raw.get("story_map_hash"):
            raise CloudStageError("cloud.provider_hash_forbidden" if isinstance(raw, Mapping) and raw.get("story_map_hash") else "cloud.provider_response_invalid")
        if raw.get("panel_ids") != list(expected_panel_ids) or raw.get("random_sampling") is not False:
            raise CloudStageError("cloud.panel_coverage_incomplete")
        beats = raw.get("beats")
        chain = raw.get("causal_chain")
        claims = raw.get("claims")
        if not isinstance(beats, list) or not beats or not isinstance(chain, list) or not chain or not isinstance(claims, list) or not claims:
            raise CloudStageError("cloud.story_map_invalid")
        expected = set(expected_panel_ids)
        covered: set[str] = set()
        for beat in beats:
            if not isinstance(beat, Mapping) or not str(beat.get("beat_id", "")).strip() or not str(beat.get("summary", "")).strip():
                raise CloudStageError("cloud.story_map_invalid")
            refs = beat.get("panel_ids")
            if not isinstance(refs, list) or not refs or any(ref not in expected for ref in refs):
                raise CloudStageError("cloud.panel_coverage_incomplete")
            covered.update(refs)
        for claim in claims:
            if not isinstance(claim, Mapping) or not str(claim.get("claim_id", "")).strip() or not str(claim.get("text", "")).strip() or not str(claim.get("qualification", "")).strip():
                raise CloudStageError("cloud.story_claim_invalid")
            refs = claim.get("panel_ids")
            if not isinstance(refs, list) or not refs or any(ref not in expected for ref in refs):
                raise CloudStageError("cloud.story_claim_invalid")
            covered.update(refs)
        beat_ids = {beat["beat_id"] for beat in beats}
        for link in chain:
            if (
                not isinstance(link, Mapping)
                or link.get("from_beat") not in beat_ids
                or link.get("to_beat") not in beat_ids
                or not str(link.get("reason", "")).strip()
            ):
                raise CloudStageError("cloud.story_map_invalid")
        if covered != expected:
            raise CloudStageError("cloud.panel_coverage_incomplete")
        return StoryMapResult(
            panel_ids=expected_panel_ids,
            beats=tuple(dict(item) for item in beats),
            causal_chain=tuple(dict(item) for item in chain),
            claims=tuple(dict(item) for item in claims),
            story_map_hash=_hash({key: value for key, value in raw.items() if key != "story_map_hash"}),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
        )

    def run_narration(self, visual: VisualStageResult, story_map: StoryMapResult) -> NarrationResult:
        prompt = self.prompts["narration"]
        source = {
            "panel_ids": list(visual.panel_ids),
            "visual_source_hash": visual.source_hash,
            "story_map": story_map.as_dict(),
        }
        key = _cache_key("narration", source, self.model_identity, prompt)
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            return NarrationResult.from_dict(cached)
        raw = self._call(
            lambda: self.provider.complete_json(
                stage="narration",
                prompt_version=prompt[0],
                prompt_sha256=prompt[1],
                prompt_text=prompt[2],
                payload=source,
            )
        )
        if not isinstance(raw, Mapping):
            raise CloudStageError("cloud.provider_response_invalid")
        output = raw.get("analyzer_output", raw)
        if not isinstance(output, Mapping):
            raise CloudStageError("cloud.provider_response_invalid")
        try:
            analyzer_contract.validate_analyzer_output(
                output,
                expected_panel_ids=visual.panel_ids,
                narrative_profile_id="sharp_friend_v1",
            )
        except analyzer_contract.AnalyzerContractError:
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True) from None
        claims = {
            claim["claim_id"]: claim
            for claim in output["evidence_graph"]["claims"]
            if isinstance(claim, Mapping) and isinstance(claim.get("claim_id"), str)
        }
        story_claim_ids = {claim["claim_id"] for claim in story_map.claims}
        narrative_claim_ids = {
            claim["claim_id"]
            for claim in output["evidence_graph"].get("claims", [])
            if isinstance(claim, Mapping) and isinstance(claim.get("claim_id"), str)
        }
        if not narrative_claim_ids <= story_claim_ids:
            raise CloudStageError("cloud.narrative_claim_unmapped", reviewable=True)
        passages = tuple(dict(item) for item in output["script_passages"])
        report = editorial_qc.screen_narrative_naturalness(passages, claims, narrative_identity.SHARP_FRIEND_V1)
        checks = quality.check_narrative_naturalness(report)
        if any(not check.passed and check.severity == "error" for check in checks):
            raise CloudStageError("cloud.narrative_qc_blocked", reviewable=True)
        spoken_text = "\n\n".join(str(item["text"]).strip() for item in passages)
        display_words = tuple(re.findall(r"[A-Z0-9]+", spoken_text.upper()))
        if not display_words or any(not word.isalnum() for word in display_words):
            raise CloudStageError("cloud.display_derivation_invalid")
        duration = script.estimate_duration(spoken_text, "dramatic")
        if not 50.0 <= duration <= 60.0:
            raise CloudStageError("cloud.narrative_duration_out_of_range", reviewable=True)
        qc_report = {
            "profile_id": "sharp_friend_v1",
            "profile_sha256": narrative_identity.SHARP_FRIEND_V1.contract_sha256,
            "total_words": report.total_words,
            "estimated_duration_s": duration,
            "ending_kind": output["narrative_outline"]["ending_kind"],
            "display_word_count": len(display_words),
            "timing_source": "voice_required",
            "warnings": list(report.warnings),
            "signals": asdict(report),
        }
        result = NarrationResult(
            spoken_text=spoken_text,
            display_words=display_words,
            passages=passages,
            ending_kind=str(output["narrative_outline"]["ending_kind"]),
            word_count=report.total_words,
            estimated_duration_s=duration,
            qc_report=qc_report,
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            observations=tuple(dict(item) for item in output["observations"]),
            continuity_ledger=dict(output["continuity_ledger"]),
            evidence_graph=dict(output["evidence_graph"]),
            story_spine=dict(output["narrative_outline"]["story_spine"]),
        )
        if self.cache is not None:
            self.cache.put(key, result.as_dict())
        return result

    def run_chapter(self, panels: Sequence[CloudPanelInput]) -> ChapterResult:
        ordered = self._ordered_panels(panels)
        source = [item.descriptor() for item in ordered]
        key = _cache_key("chapter", source, self.model_identity, self.prompts["narration"])
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            return ChapterResult.from_dict(cached)
        visual = self.run_visual_evidence(ordered)
        story_map = self.run_story_map(visual)
        narration = self.run_narration(visual, story_map)
        result = ChapterResult(ChapterState.READY_TO_RENDER, visual, story_map, narration)
        if self.cache is not None:
            self.cache.put(key, result.as_dict())
        return result


def prepare_project_panels(db: Any, project_id: str) -> tuple[CloudPanelInput, ...]:
    """Build immutable cloud inputs from the current project panel lineage.

    Segmentation and source decoding are reused from the regular pipeline.  No
    StoryAnalysis rows are written here; persistence happens only after all
    three cloud stages reconcile successfully.
    """

    from app.models import PanelRegion
    from app.services import pipeline, segmentation

    try:
        assets = pipeline.image_assets(pipeline.project_assets(db, project_id))
        inputs, asset_by_id = pipeline._build_source_inputs(assets)
        coverage = segmentation.build_complete_coverage_map(
            inputs,
            segmentation_version=segmentation.SEGMENTATION_VERSION,
        )
        errors = segmentation.verify_segmentation_completeness(
            pipeline._coverage_overviews(inputs, coverage), coverage
        )
        errors = tuple(sorted(set(coverage.reconciliation_errors + errors)))
    except Exception:
        raise CloudStageError("cloud.panel_coverage_incomplete") from None
    if errors or coverage.source_content_coverage_ratio != 1.0 or coverage.unresolved_material_area:
        raise CloudStageError("cloud.panel_coverage_incomplete")

    input_by_asset = {item.source_asset_id: item for item in inputs}
    regions = tuple(
        sorted(
            (region for region in coverage.regions if region.region_class == "canonical_panel"),
            key=lambda region: (region.source_order, region.region_id),
        )
    )
    if not regions or [region.source_order for region in regions] != list(range(len(regions))):
        raise CloudStageError("cloud.panel_coverage_incomplete")

    panels: list[CloudPanelInput] = []
    for region in regions:
        source_input = input_by_asset.get(region.source_asset_id)
        asset = asset_by_id.get(region.source_asset_id)
        if source_input is None or asset is None:
            raise CloudStageError("cloud.panel_lineage_invalid")
        transient = PanelRegion(
            id=region.region_id,
            story_analysis_id="cloud-preview",
            source_asset_id=asset.id,
            source_asset_checksum=source_input.original_checksum,
            original_width=source_input.original_width,
            original_height=source_input.original_height,
            strip_region_id=region.region_id,
            panel_id=region.region_id,
            source_order=region.source_order,
            bounds_json={
                "x": region.bounds[0],
                "y": region.bounds[1],
                "width": region.bounds[2] - region.bounds[0],
                "height": region.bounds[3] - region.bounds[1],
            },
        )
        try:
            payload = pipeline._encode_panel_payload(transient, source_input)
        except Exception:
            raise CloudStageError("cloud.panel_payload_invalid") from None
        panels.append(
            CloudPanelInput(
                panel_id=region.region_id,
                source_asset_id=asset.id,
                source_order=region.source_order,
                mime_type="image/png",
                payload=payload,
                source_checksum=source_input.original_checksum,
                panel_bounds=region.bounds,
                source_dimensions=(source_input.original_width, source_input.original_height),
                strip_region_id=region.region_id,
                coverage_map_version=coverage.version,
                coverage_map_hash=coverage.map_sha256,
            )
        )
    return tuple(panels)


def persist_cloud_chapter(
    db: Any,
    project_id: str,
    panels: Sequence[CloudPanelInput],
    result: ChapterResult,
    *,
    model_identity: CloudModelIdentity,
    actor_id: str = "",
) -> tuple[Any, Any]:
    """Persist only a fully reconciled cloud result through existing pipeline gates."""

    from app.models import PanelRegion, SourceAsset, StoryAnalysis
    from app.services import analyzer_contract, narrative_identity, pipeline, visual_scoring

    if result.state != ChapterState.READY_TO_RENDER:
        raise CloudStageError("cloud.stage_not_ready")
    ordered = CloudStageRunner._ordered_panels(tuple(panels))
    visual_by_id = {item["panel_id"]: item for item in result.visual.panels}
    if tuple(item.panel_id for item in ordered) != result.visual.panel_ids:
        raise CloudStageError("cloud.panel_lineage_invalid")
    if len(result.narration.observations) != len(ordered):
        raise CloudStageError("cloud.panel_coverage_incomplete")

    try:
        instruction_version, instruction_sha256, _ = analyzer_contract.load_analyzer_instruction(
            narrative_profile_id="sharp_friend_v1"
        )
    except Exception:
        raise CloudStageError("cloud.prompt_invalid") from None

    coverage_versions = {panel.coverage_map_version for panel in ordered}
    coverage_hashes = {panel.coverage_map_hash for panel in ordered}
    if len(coverage_versions) != 1 or len(coverage_hashes) != 1 or not next(iter(coverage_versions)) or not next(iter(coverage_hashes)):
        raise CloudStageError("cloud.panel_lineage_invalid")
    coverage_version = next(iter(coverage_versions))
    coverage_hash = next(iter(coverage_hashes))
    panel_ids = tuple(panel.panel_id for panel in ordered)
    persisted_observations: list[dict[str, Any]] = []
    panel_rows: list[PanelRegion] = []
    for index, (panel, source_observation) in enumerate(
        zip(ordered, result.narration.observations, strict=True)
    ):
        if panel.panel_bounds is None or panel.source_dimensions is None:
            raise CloudStageError("cloud.panel_lineage_invalid")
        asset = db.get(SourceAsset, panel.source_asset_id)
        if asset is None:
            raise CloudStageError("cloud.panel_lineage_invalid")
        stored_checksum = asset.original_checksum or asset.checksum
        if panel.source_checksum != stored_checksum:
            raise CloudStageError("cloud.source_checksum_mismatch")
        visual = visual_by_id.get(panel.panel_id)
        if visual is None or visual.get("source_checksum") != panel.source_checksum:
            raise CloudStageError("cloud.panel_lineage_invalid")
        visual_evidence = visual.get("visual_evidence")
        try:
            parsed_evidence = visual_scoring.parse_panel_visual_evidence(visual_evidence)
            if (
                parsed_evidence.panel_id != panel.panel_id
                or parsed_evidence.source_asset_id != panel.source_asset_id
                or parsed_evidence.source_order != panel.source_order
            ):
                raise ValueError
        except Exception:
            raise CloudStageError("cloud.panel_lineage_invalid") from None

        bounds = panel.panel_bounds
        observation = dict(source_observation)
        observation.update(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "strip_region_id": panel.strip_region_id or panel.panel_id,
                "source_index": index,
                "region_bounds": {
                    "x": bounds[0],
                    "y": bounds[1],
                    "width": bounds[2] - bounds[0],
                    "height": bounds[3] - bounds[1],
                },
                "coverage_map_version": coverage_version,
                "coverage_map_hash": coverage_hash,
                "visual_evidence": dict(visual_evidence),
            }
        )
        if not isinstance(observation.get("evidence_refs"), list) or panel.panel_id not in observation["evidence_refs"]:
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        persisted_observations.append(observation)
        panel_rows.append(
            PanelRegion(
                source_asset_id=panel.source_asset_id,
                source_asset_checksum=panel.source_checksum,
                original_width=panel.source_dimensions[0],
                original_height=panel.source_dimensions[1],
                strip_region_id=panel.strip_region_id or panel.panel_id,
                panel_id=panel.panel_id,
                source_order=panel.source_order,
                bounds_json=observation["region_bounds"],
                region_class="canonical_panel",
                segmentation_confidence=1.0,
                segmentation_version=coverage_version,
                coverage_map_hash=coverage_hash,
                observation_json=observation,
                evidence_refs_json=list(observation["evidence_refs"]),
            )
        )

    output = {
        "observations": persisted_observations,
        "continuity_ledger": dict(result.narration.continuity_ledger),
        "evidence_graph": dict(result.narration.evidence_graph),
        "coverage_manifest": {
            "total_panels": len(ordered),
            "processed_panels": len(ordered),
            "total_canonical_panels": len(ordered),
            "persisted_canonical_panels": len(ordered),
            "processed_canonical_panel_count": len(ordered),
            "panel_ids": list(panel_ids),
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
            "coverage_map_version": coverage_version,
            "coverage_map_hash": coverage_hash,
        },
        "narrative_outline": {
            "story_spine": dict(result.narration.story_spine),
            "ending_kind": result.narration.ending_kind,
        },
        "script_passages": [dict(item) for item in result.narration.passages],
    }
    output["evidence_graph"]["script_passages"] = list(output["script_passages"])
    validator_output = dict(output)
    validator_output["observations"] = [
        {key: value for key, value in observation.items() if key != "visual_evidence"}
        for observation in persisted_observations
    ]
    try:
        analyzer_contract.validate_analyzer_output(
            validator_output,
            expected_panel_ids=panel_ids,
            narrative_profile_id="sharp_friend_v1",
        )
    except Exception:
        raise CloudStageError("cloud.narrative_not_grounded", reviewable=True) from None

    row = StoryAnalysis(
        project_id=project_id,
        analysis_run_id=f"cloud-{result.visual.source_hash[:24]}",
        state="RECONCILED",
        provider_type=model_identity.provider,
        provider_name=model_identity.provider,
        model_name=model_identity.model,
        instruction_version=instruction_version,
        instruction_sha256=instruction_sha256,
        coverage_manifest_json=output["coverage_manifest"],
        continuity_ledger_json=output["continuity_ledger"],
        evidence_graph_json=output["evidence_graph"],
        story_spine_json=output["narrative_outline"]["story_spine"],
        blocking_reasons_json=None,
        reconciliation_json={
            "coverage_map_version": coverage_version,
            "coverage_map_hash": coverage_hash,
            "canonical_panel_count": len(ordered),
            "processed_panel_count": len(ordered),
            "chain_reconciled": True,
            "chain_errors": [],
            "provenance": "cloud_multimodal_mass_production_v1",
            "random_sampling": False,
            "model_identity": model_identity.as_dict(),
            "model_identity_hash": model_identity.identity_hash,
            "stage_prompt_versions": dict(model_identity.prompt_versions),
            "stage_prompt_hashes": {
                "visual": result.visual.prompt_sha256,
                "story_map": result.story_map.prompt_sha256,
                "narration": result.narration.prompt_sha256,
            },
            "narrative_identity": {
                "profile_id": narrative_identity.SHARP_FRIEND_V1.profile_id,
                "version": narrative_identity.SHARP_FRIEND_V1.profile_version,
                "sha256": narrative_identity.SHARP_FRIEND_V1.contract_sha256,
            },
            "narrative_ending_kind": result.narration.ending_kind,
            "narrative_screening_warning_codes": list(result.narration.qc_report.get("warnings", [])),
            "requires_voice_timing": True,
        },
    )
    pipeline._derive_legacy_fields(row, output)
    db.add(row)
    db.flush()
    for panel_row in panel_rows:
        panel_row.story_analysis_id = row.id
    db.add_all(panel_rows)
    db.flush()
    try:
        script_row = pipeline.generate_script(
            db,
            project_id,
            actor_id=actor_id,
            narrative_profile_id="sharp_friend_v1",
        )
    except Exception:
        raise CloudStageError("cloud.persistence_failed") from None
    return row, script_row


@dataclass
class ChapterJobRecord:
    job_id: str
    state: ChapterState = ChapterState.INGESTED
    stage_results: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    review_queue: list[dict[str, str]] = field(default_factory=list)
    model_identity_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "state": self.state.value,
            "stage_results": self.stage_results,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "review_queue": self.review_queue,
            "model_identity_hash": self.model_identity_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ChapterJobRecord:
        return cls(
            job_id=str(value["job_id"]),
            state=ChapterState(str(value.get("state", ChapterState.INGESTED.value))),
            stage_results=dict(value.get("stage_results", {})),
            error_code=str(value.get("error_code", "")),
            error_message=str(value.get("error_message", "")),
            review_queue=list(value.get("review_queue", [])),
            model_identity_hash=str(value.get("model_identity_hash", "")),
        )


class JsonJobStore:
    """Small ignored local state store; no schema migration or DB artifact."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self, job_id: str) -> ChapterJobRecord | None:
        _validate_job_id(job_id)
        path = self.root / f"{job_id}.json"
        if not path.is_file():
            return None
        try:
            return ChapterJobRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError):
            raise CloudStageError("cloud.job_state_invalid") from None

    def save(self, record: ChapterJobRecord) -> None:
        _validate_job_id(record.job_id)
        path = self.root / f"{record.job_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(_canonical(record.as_dict()), encoding="utf-8")
        tmp.replace(path)


def _validate_job_id(job_id: str) -> None:
    if (
        not isinstance(job_id, str)
        or not job_id.strip()
        or job_id in {".", ".."}
        or any(separator in job_id for separator in ("/", "\\"))
    ):
        raise CloudStageError("cloud.job_id_invalid")


class CloudBatchService:
    def __init__(
        self,
        *,
        runner: CloudStageRunner,
        store: JsonJobStore,
        max_concurrent: int = 1,
    ) -> None:
        self.runner = runner
        self.store = store
        self.max_concurrent = max(1, int(max_concurrent))

    def run_job(self, job_id: str, panels: Sequence[CloudPanelInput]) -> ChapterJobRecord:
        _validate_job_id(job_id)
        record = self.store.load(job_id) or ChapterJobRecord(job_id=job_id)
        record.model_identity_hash = self.runner.model_identity.identity_hash
        try:
            ordered = self.runner._ordered_panels(tuple(panels))
            source_hash = _hash([item.descriptor() for item in ordered])
            visual = VisualStageResult.from_dict(record.stage_results["visual"])
            if visual.source_hash != source_hash or visual.model_identity_hash != self.runner.model_identity.identity_hash:
                raise KeyError("stale_visual_cache")
            if "visual" not in record.stage_results:
                raise KeyError("visual_missing")
        except (KeyError, TypeError, ValueError):
            record.stage_results.pop("visual", None)
            record.stage_results.pop("story_map", None)
            record.stage_results.pop("narration", None)
            try:
                visual = self.runner.run_visual_evidence(panels)
            except CloudStageError as exc:
                return self._record_failure(record, exc)
        try:
            record.stage_results["visual"] = visual.as_dict()
            record.state = ChapterState.VISUAL_ANALYZED
            self.store.save(record)
            story_map = StoryMapResult.from_dict(record.stage_results["story_map"])
            current_story_prompt = self.runner.prompts["story_map"]
            if (
                story_map.model_identity_hash != self.runner.model_identity.identity_hash
                or story_map.prompt_version != current_story_prompt[0]
                or story_map.prompt_sha256 != current_story_prompt[1]
            ):
                raise KeyError("stale_story_cache")
        except (KeyError, TypeError, ValueError):
            record.stage_results.pop("story_map", None)
            record.stage_results.pop("narration", None)
            try:
                story_map = self.runner.run_story_map(visual)
            except CloudStageError as exc:
                return self._record_failure(record, exc)
        try:
            record.stage_results["story_map"] = story_map.as_dict()
            record.state = ChapterState.STORY_MAPPED
            self.store.save(record)
            narration = NarrationResult.from_dict(record.stage_results["narration"])
            current_narration_prompt = self.runner.prompts["narration"]
            if (
                narration.model_identity_hash != self.runner.model_identity.identity_hash
                or narration.prompt_version != current_narration_prompt[0]
                or narration.prompt_sha256 != current_narration_prompt[1]
            ):
                raise KeyError("stale_narration_cache")
        except (KeyError, TypeError, ValueError):
            try:
                narration = self.runner.run_narration(visual, story_map)
            except CloudStageError as exc:
                return self._record_failure(record, exc)
        try:
            record.stage_results["narration"] = narration.as_dict()
            record.state = ChapterState.SCRIPTED
            record.stage_results["usage"] = {
                "request_count": self.runner.request_count,
                "estimated_cost_usd": round(self.runner.estimated_cost_usd, 8),
            }
            self.store.save(record)
            record.state = ChapterState.READY_TO_RENDER
            self.store.save(record)
            return record
        except CloudStageError as exc:
            return self._record_failure(record, exc)

    def _record_failure(self, record: ChapterJobRecord, exc: CloudStageError) -> ChapterJobRecord:
        record.state = ChapterState.NEEDS_REVIEW if exc.reviewable else ChapterState.FAILED
        record.error_code = exc.code
        record.error_message = str(exc)
        if exc.reviewable:
            record.review_queue.append({"code": exc.code, "reason": str(exc)})
        self.store.save(record)
        return record

    def run_batch(self, jobs: Mapping[str, Sequence[CloudPanelInput]]) -> dict[str, ChapterJobRecord]:
        ordered_ids = sorted(jobs)
        if self.max_concurrent == 1 or len(ordered_ids) < 2:
            return {job_id: self.run_job(job_id, jobs[job_id]) for job_id in ordered_ids}
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            records = executor.map(lambda job_id: self.run_job(job_id, jobs[job_id]), ordered_ids)
            return dict(zip(ordered_ids, records, strict=True))

    def run_project(self, db: Any, project_id: str, *, actor_id: str = "") -> ChapterJobRecord:
        """Run one DB-backed project and persist only after stage reconciliation."""

        try:
            panels = prepare_project_panels(db, project_id)
            record = self.run_job(project_id, panels)
            if record.state != ChapterState.READY_TO_RENDER:
                return record
            result = ChapterResult(
                state=ChapterState.READY_TO_RENDER,
                visual=VisualStageResult.from_dict(record.stage_results["visual"]),
                story_map=StoryMapResult.from_dict(record.stage_results["story_map"]),
                narration=NarrationResult.from_dict(record.stage_results["narration"]),
            )
            analysis, script_row = persist_cloud_chapter(
                db,
                project_id,
                panels,
                result,
                model_identity=self.runner.model_identity,
                actor_id=actor_id,
            )
            record.stage_results["persistence"] = {
                "analysis_id": analysis.id,
                "script_id": script_row.id,
                "script_version": script_row.version,
                "approval_required": True,
                "voice_timing_required": True,
            }
            self.store.save(record)
            return record
        except CloudStageError as exc:
            if hasattr(db, "rollback"):
                db.rollback()
            record = self.store.load(project_id) or ChapterJobRecord(project_id)
            record.model_identity_hash = self.runner.model_identity.identity_hash
            record.state = ChapterState.NEEDS_REVIEW if exc.reviewable else ChapterState.FAILED
            record.error_code = exc.code
            record.error_message = str(exc)
            if exc.reviewable:
                record.review_queue.append({"code": exc.code, "reason": str(exc)})
            self.store.save(record)
            return record


def review_only_render_gate(result: ChapterResult | ChapterJobRecord) -> ReviewOnlyRenderGate:
    state = result.state
    if state != ChapterState.READY_TO_RENDER:
        raise CloudStageError("cloud.stage_not_ready")
    return ReviewOnlyRenderGate(allowed=True)


def require_final_render_ready(result: ChapterResult | ChapterJobRecord) -> None:
    if result.state != ChapterState.READY_TO_RENDER:
        raise CloudStageError("cloud.stage_not_ready")
    raise CloudStageError("cloud.voice_timing_required")


def regular_render_allowed(result: ChapterResult | ChapterJobRecord) -> bool:
    return result.state == ChapterState.READY_TO_RENDER and False


def resolve_cloud_runner(
    db,
    workspace_id: str,
    *,
    model: str | None = None,
    cache: StageCache | None = None,
    max_attempts: int = 2,
    max_requests: int | None = None,
    min_request_interval_s: float = 0.0,
    estimated_cost_per_request: float = 0.0,
) -> CloudStageRunner:
    """Resolve only a verified BYOK multimodal credential; never local fallback."""

    from app.services import resolver

    try:
        provider, report = resolver.resolve_vision(db, workspace_id)
    except Exception:
        raise CloudStageError("cloud.credential_missing") from None
    if provider is None or report is None or not report.available:
        raise CloudStageError(str(getattr(report, "blocking_reason", None) or "cloud.credential_missing"))
    selected_model = str(getattr(report, "model", "") or "")
    if model is not None and model != selected_model:
        raise CloudStageError("cloud.model_identity_mismatch")
    endpoint = str(getattr(provider, "endpoint", "configured_by_byok"))
    identity = CloudModelIdentity(
        provider=str(getattr(report, "provider_name", "openai_compatible")),
        model=selected_model,
        model_version="verified_byok",
        endpoint=endpoint,
        prompt_versions={stage: prompt[0] for stage, prompt in _prompt_specs().items()},
    )
    return CloudStageRunner(
        provider=provider,
        model_identity=identity,
        cache=cache,
        max_attempts=max_attempts,
        max_requests=max_requests,
        min_request_interval_s=min_request_interval_s,
        estimated_cost_per_request=estimated_cost_per_request,
    )


def derive_display_words(spoken_text: str) -> tuple[str, ...]:
    words = tuple(re.findall(r"[A-Z0-9]+", spoken_text.upper()))
    if not words or any(not word.isalnum() for word in words):
        raise CloudStageError("cloud.display_derivation_invalid")
    return words


__all__ = [
    "ChapterJobRecord",
    "ChapterResult",
    "ChapterState",
    "CloudBatchService",
    "CloudModelIdentity",
    "CloudPanelInput",
    "CloudStageError",
    "CloudStageRunner",
    "JsonJobStore",
    "MemoryStageCache",
    "NarrationResult",
    "persist_cloud_chapter",
    "prepare_project_panels",
    "ReviewOnlyRenderGate",
    "StoryMapResult",
    "VisualStageResult",
    "derive_display_words",
    "regular_render_allowed",
    "require_final_render_ready",
    "resolve_cloud_runner",
    "review_only_render_gate",
]
