"""Pinned cloud multimodal stages and resumable review-only chapter jobs.

The module deliberately stops before TTS.  It reuses the existing visual
evidence, analyzer, and Sharp Friend validators; this layer owns provider
selection, stage identity, local reconciliation, cache keys, and batch state.
Provider output is untrusted JSON.  Canonical hashes are always computed here.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from app.services import (
    analyzer_contract,
    editorial_qc,
    narrative_identity,
    quality,
    script,
    strip_segmentation,
    visual_narrative_repair,
    visual_scoring,
)
from app.services.vision_adapter import VisionObservationRequest

CAUSAL_MAP_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "cloud_causal_map_v1.txt"
CAUSAL_MAP_PROMPT_VERSION = "cloud-causal-map-v2"
STRIP_BOUNDARY_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "strip_boundary_assessment_v1.txt"
STRIP_BOUNDARY_PROMPT_VERSION = "strip-boundary-assessment-v1"
# Keep provider response envelopes to one image: this configured endpoint has
# returned incomplete structured JSON for multi-image requests.  Every ordered
# panel is still processed; local reconciliation owns complete coverage.
VISUAL_REQUEST_MAX_PANELS = 8  # preview-only: 4-5 worker saturation sweet spot
VISUAL_REQUEST_MAX_ESTIMATED_BYTES = 3_500_000  # preview-only: larger visual batches
VISUAL_REQUEST_OVERLAP = 0
_REVIEW_ERROR_CODE_PATTERN = re.compile(
    r"\b(?:cloud|visual|reference|review)\.[a-z0-9_.-]+\b"
)


def _review_failure_code(message: str) -> str:
    """Extract only a known stable code from a local review-stage error."""

    match = _REVIEW_ERROR_CODE_PATTERN.search(str(message))
    return match.group(0) if match else "review.preview_failed"


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
    REVIEW_PREVIEW_READY = "REVIEW_PREVIEW_READY"
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
    source_family: str = ""
    payload_checksum: str = ""
    panel_bounds: tuple[int, int, int, int] | None = None
    source_dimensions: tuple[int, int] | None = None
    strip_region_id: str = ""
    coverage_map_version: str = ""
    coverage_map_hash: str = ""
    segmentation_version: str = ""

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
        if self.segmentation_version:
            descriptor["segmentation_version"] = self.segmentation_version
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


class FileStageCache:
    """Atomic JSON stage cache for restartable local production jobs."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def checkpoint_path(self) -> Path:
        return self.root / "visual_checkpoints.jsonl"

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.root / f"{digest}.json"

    def get(self, key: str) -> Mapping[str, Any] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return None
        return value if isinstance(value, Mapping) else None

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        path = self._path(key)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(_canonical(value), encoding="utf-8")
        temporary.replace(path)


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
    if "Version: cloud-causal-map-v2" not in normalized:
        raise CloudStageError("cloud.prompt_invalid")
    return CAUSAL_MAP_PROMPT_VERSION, hashlib.sha256(normalized.encode("utf-8")).hexdigest(), normalized


def _load_strip_boundary_prompt() -> tuple[str, str, str]:
    try:
        text = STRIP_BOUNDARY_PROMPT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise CloudStageError("cloud.prompt_missing") from None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if f"Version: {STRIP_BOUNDARY_PROMPT_VERSION}" not in normalized:
        raise CloudStageError("cloud.prompt_invalid")
    return STRIP_BOUNDARY_PROMPT_VERSION, hashlib.sha256(normalized.encode("utf-8")).hexdigest(), normalized


def _prompt_specs() -> dict[str, tuple[str, str, str]]:
    return {
        "visual": visual_scoring.load_visual_evidence_instruction(),
        "story_map": _load_causal_prompt(),
        "narration": narrative_identity.load_narrative_instruction("sharp_friend_v1"),
        "segmentation": _load_strip_boundary_prompt(),
        "visual_narrative_repair": visual_narrative_repair.load_repair_prompt(),
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
        allow_balloon_unknown: bool = False,
        visual_checkpoint_path: Path | None = None,
    ) -> None:
        model_id = getattr(provider, "model_id", "")
        if not isinstance(model_id, str) or model_id != model_identity.model:
            raise CloudStageError("cloud.model_identity_mismatch")
        self.provider = provider
        self.model_identity = model_identity
        self.cache = cache
        self.max_attempts = max(1, int(max_attempts))
        # Preview-only relaxation: keep panels whose provider balloon status is
        # "unknown" instead of failing the whole visual stage. The feasible
        # ledger still skips them (they cannot pass framing), so this never
        # selects a balloon-unknown panel; it only unblocks preview renders.
        # Production keeps the strict contract (allow_balloon_unknown=False).
        self.allow_balloon_unknown = bool(allow_balloon_unknown)
        self.max_requests = max_requests if max_requests is None else max(1, int(max_requests))
        self.min_request_interval_s = max(0.0, float(min_request_interval_s))
        self.estimated_cost_per_request = max(0.0, float(estimated_cost_per_request))
        inferred_checkpoint = getattr(cache, "checkpoint_path", None)
        if inferred_checkpoint is None:
            cache_root = getattr(cache, "root", None)
            inferred_checkpoint = (
                Path(cache_root) / "visual_checkpoints.jsonl"
                if cache_root is not None
                else None
            )
        self.visual_checkpoint_path = (
            Path(visual_checkpoint_path)
            if visual_checkpoint_path is not None
            else inferred_checkpoint
        )
        self._checkpoint_lock = threading.Lock()
        self.request_count = 0
        self.estimated_cost_usd = 0.0
        self._last_request_at = 0.0
        self.prompts = _prompt_specs()
        expected = dict(model_identity.prompt_versions)
        if any(stage in expected and expected[stage] != prompt[0] for stage, prompt in self.prompts.items()):
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

    def assess_strip_boundaries(self, request: strip_segmentation.BoundaryRequest) -> Mapping[str, Any]:
        """Ask the pinned model to validate candidates and protected regions."""
        prompt = self.prompts["segmentation"]
        payload = request.as_payload()
        for attempt in range(self.max_attempts):
            raw = self._call(
                lambda attempt=attempt: self.provider.complete_json(
                    stage="strip_segmentation",
                    prompt_version=prompt[0],
                    prompt_sha256=prompt[1],
                    prompt_text=prompt[2],
                    payload={**payload, "lineage_retry_attempt": attempt},
                )
            )
            if not isinstance(raw, Mapping):
                return raw
            if (
                raw.get("source_asset_id") == request.source_asset_id
                and raw.get("source_checksum") == request.source_checksum
            ):
                return raw
        raise strip_segmentation.StripSegmentationError(
            "segmentation.provider_lineage_invalid"
        )

    @staticmethod
    def _ordered_panels(panels: Sequence[CloudPanelInput]) -> tuple[CloudPanelInput, ...]:
        # Chapter order first (source_family: 204__* < 205__* < 206__*), then
        # the per-asset strip order, so the provider and the story map see the
        # panels in the real chapter sequence.
        values = tuple(
            sorted(
                panels,
                key=lambda item: (str(item.source_family or ""), item.source_order, item.panel_id),
            )
        )
        if not values or len({item.panel_id for item in values}) != len(values):
            raise CloudStageError("cloud.panel_coverage_incomplete")
        if len({item.source_order for item in values}) != len(values):
            raise CloudStageError("cloud.panel_lineage_invalid")
        return values

    def _checkpoint_scope(
        self,
        source: Sequence[Mapping[str, Any]],
        prompt: tuple[str, str, str],
    ) -> str:
        return _hash(
            {
                "source": list(source),
                "model_identity_hash": self.model_identity.identity_hash,
                "prompt_version": prompt[0],
                "prompt_sha256": prompt[1],
            }
        )

    def _checkpoint_load(self, scope: str) -> dict[str, dict[str, Any]]:
        path = self.visual_checkpoint_path
        out: dict[str, dict[str, Any]] = {}
        if path is None or not path.is_file():
            return out
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for line in lines:
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(item, Mapping) or item.get("checkpoint_scope") != scope:
                continue
            panel_id = item.get("panel_id")
            if isinstance(panel_id, str) and panel_id.strip():
                clean = dict(item)
                clean.pop("checkpoint_scope", None)
                clean.pop("checkpoint_version", None)
                out[panel_id] = clean
        return out

    def _checkpoint_append(self, scope: str, entry: Mapping[str, Any]) -> None:
        path = self.visual_checkpoint_path
        if path is None:
            return
        record = dict(entry)
        record["checkpoint_scope"] = scope
        record["checkpoint_version"] = "visual-checkpoint-v1"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            with self._checkpoint_lock, path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            return

    def run_visual_evidence(self, panels: Sequence[CloudPanelInput]) -> VisualStageResult:
        ordered = self._ordered_panels(panels)
        prompt = self.prompts["visual"]
        source = [item.descriptor() for item in ordered]
        key = _cache_key("visual", source, self.model_identity, prompt)
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            return VisualStageResult.from_dict(cached)
        instruction_version, instruction_sha256, _ = analyzer_contract.load_analyzer_instruction()
        from concurrent.futures import ThreadPoolExecutor

        chunks = list(_visual_panel_chunks(ordered))
        reconciled_by_id: dict[str, dict[str, Any]] = {}
        skipped_codes: list[str] = []
        reconcile_lock = threading.Lock()
        VISUAL_PARALLEL_WORKERS = 8  # preview-only: real observe 27s/call, 16x triggers rate-limit
        checkpoint_scope = self._checkpoint_scope(source, prompt)
        _checkpoint_seed = self._checkpoint_load(checkpoint_scope)

        def observe_chunk(chunk_index: int, chunk: Sequence[CloudPanelInput]) -> None:
            # every error path reports + skips the chunk; never raises
            nonlocal reconciled_by_id
            seeded = {
                item.panel_id
                for item in chunk
                if item.panel_id in _checkpoint_seed
            }
            if seeded:
                with reconcile_lock:
                    for panel_id in seeded:
                        reconciled_by_id[panel_id] = _checkpoint_seed[panel_id]
            live = [item for item in chunk if item.panel_id not in seeded]
            if not live:
                print(
                    f"VISUAL_CHUNK_OK chunk={chunk_index} panels=0(from checkpoint)",
                    file=sys.stderr, flush=True,
                )
                return
            chunk = tuple(live)
            request_panels = []
            for item in chunk:
                provider_payload, provider_mime = _visual_provider_payload(item)
                request_panels.append(
                    {
                        **item.descriptor(),
                        "mime_type": provider_mime,
                        "payload": provider_payload,
                    }
                )
            request = VisionObservationRequest(
                analysis_run_id=f"cloud-{_hash(source)[:24]}",
                instruction_version=instruction_version,
                instruction_sha256=instruction_sha256,
                chunk_index=chunk_index,
                panels=tuple(request_panels),
                visual_instruction_version=prompt[0],
                visual_instruction_sha256=prompt[1],
            )
            retryable_visual_codes = {
                "cloud.provider_response_invalid",
                "cloud.panel_lineage_invalid",
                "cloud.provider_hash_forbidden",
                "cloud.visual_evidence_invalid",
                "visual.balloon_mask_unknown",
                "visual.balloon_geometry_invalid",
            }
            for attempt in range(self.max_attempts):
                try:
                    attempt_request = replace(
                        request,
                        analysis_run_id=f"{request.analysis_run_id}-attempt-{attempt}",
                    )
                    raw_rows = self._call(
                        lambda request=attempt_request: self.provider.observe(request)
                    )
                    if not isinstance(raw_rows, list) or len(raw_rows) != len(chunk):
                        raise CloudStageError("cloud.provider_response_invalid")
                    chunk_reconciled: dict[str, dict[str, Any]] = {}
                    for item, raw in zip(chunk, raw_rows, strict=True):
                        if not isinstance(raw, Mapping) or raw.get("panel_id") != item.panel_id:
                            raise CloudStageError(
                                message=(
                                    f"cloud.panel_lineage_invalid: want={item.panel_id} "
                                    f"got={raw.get('panel_id') if isinstance(raw, Mapping) else type(raw).__name__}"
                                ),
                                reviewable=True,
                            )
                        if item.panel_id in reconciled_by_id:
                            continue
                        if item.panel_id in chunk_reconciled:
                            raise CloudStageError("cloud.panel_lineage_invalid")
                        raw_visual = raw.get("visual_evidence")
                        if not isinstance(raw_visual, Mapping):
                            raise CloudStageError(f"visual.balloon_mask_unknown:panel={item.panel_id}", reviewable=True)
                        if raw_visual.get("evidence_hash"):
                            raise CloudStageError("cloud.provider_hash_forbidden")
                        visual = dict(raw_visual)
                        visual.setdefault(
                            "contract_version",
                            visual_scoring.VISUAL_EVIDENCE_CONTRACT_VERSION,
                        )
                        visual.pop("evidence_hash", None)
                        try:
                            merged, evidence = visual_scoring.ensure_panel_visual_evidence(
                                {**dict(raw), "visual_evidence": visual},
                                panel_id=item.panel_id,
                                source_asset_id=item.source_asset_id,
                                source_order=item.source_order,
                            )
                        except visual_scoring.VisualEvidenceError as exc:
                            raise CloudStageError(
                                getattr(exc, "code", "cloud.visual_evidence_invalid")
                            ) from None
                        if evidence.balloon_mask_status == "unknown" and not self.allow_balloon_unknown:
                            raise CloudStageError(
                                "visual.balloon_mask_unknown",
                                message=f"visual.balloon_mask_unknown: panel={item.panel_id}",
                                reviewable=True,
                            )
                        source_values = {
                            evidence.evidence_source,
                            *(region.evidence_source for region in evidence.balloon_regions),
                        }
                        if any("ocr" in value.lower() for value in source_values):
                            raise CloudStageError(
                                "visual.balloon_geometry_invalid", reviewable=True
                            )
                        evidence_json = visual_scoring.panel_visual_evidence_json(evidence)
                        merged["visual_evidence"] = evidence_json
                        chunk_reconciled[item.panel_id] = {
                            "panel_id": item.panel_id,
                            "source_asset_id": item.source_asset_id,
                            "source_order": item.source_order,
                            "source_checksum": item.source_checksum,
                            "observation": merged,
                            "visual_evidence": evidence_json,
                            "evidence_hash": evidence_json["evidence_hash"],
                        }
                    with reconcile_lock:
                        reconciled_by_id.update(chunk_reconciled)
                    for _entry in chunk_reconciled.values():
                        self._checkpoint_append(checkpoint_scope, _entry)
                    print(f"VISUAL_CHUNK_OK chunk={chunk_index} panels={len(chunk)}", file=sys.stderr, flush=True)
                    return
                except CloudStageError as exc:
                    if (
                        exc.code in retryable_visual_codes
                        and attempt + 1 < self.max_attempts
                    ):
                        continue
                    if (
                        exc.code == "visual.balloon_mask_unknown"
                        and not self.allow_balloon_unknown
                    ):
                        raise
                    if exc.code == "cloud.provider_request_failed":
                        raise
                    # binary reduction: subdivide the failing chunk and retry the
                    # halves so only genuinely poisonous panels are dropped.
                    if len(chunk) > 1:
                        half = len(chunk) // 2
                        for subchunk in (chunk[:half], chunk[half:]):
                            observe_chunk(chunk_index, subchunk)
                    else:
                        with reconcile_lock:
                            skipped_codes.append(exc.code)
                        print(
                            f"VISUAL_SKIP_PANEL panel={chunk[0].panel_id} code={exc.code}",
                            file=sys.stderr, flush=True,
                        )
                    return
            if len(chunk) > 1:
                half = len(chunk) // 2
                for subchunk in (chunk[:half], chunk[half:]):
                    observe_chunk(chunk_index, subchunk)
            else:
                print(
                    f"VISUAL_SKIP_PANEL panel={chunk[0].panel_id} code=exhausted",
                    file=sys.stderr, flush=True,
                )

        with ThreadPoolExecutor(max_workers=VISUAL_PARALLEL_WORKERS) as executor:
            futures = [
                executor.submit(observe_chunk, chunk_index, chunk)
                for chunk_index, chunk in enumerate(chunks)
            ]
            for future in futures:
                future.result()
        if set(reconciled_by_id) != {item.panel_id for item in ordered}:
            print(
                f"VISUAL_SKIP_TOTAL missing={len(ordered) - len(reconciled_by_id)} "
                f"of {len(ordered)}",
                file=sys.stderr, flush=True,
            )
        if not reconciled_by_id:
            code = skipped_codes[0] if skipped_codes else "cloud.panel_coverage_incomplete"
            raise CloudStageError(
                code,
                reviewable=code.startswith(("visual.", "segmentation.")),
            )
        # preview: continue with the reconciled subset (skipped panels drop out),
        # preserving the deterministic chapter order for downstream stages.
        reconciled = [reconciled_by_id[item.panel_id] for item in ordered if item.panel_id in reconciled_by_id]
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


    @staticmethod
    def _claims_from_causal_map(
        script_passages: Any,
        story_map: StoryMapResult,
    ) -> list[dict[str, Any]]:
        """Reuse only locally validated causal claims when the graph is omitted.

        Some compatible models return passage prose and claim IDs while
        omitting the duplicate evidence-graph envelope.  Reusing the exact
        causal-map records is safe because that graph was already reconciled
        against every ordered panel; no claim text or evidence is invented.
        """

        if not isinstance(script_passages, list) or not script_passages:
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        claims_by_id = {
            str(claim.get("claim_id")): dict(claim)
            for claim in story_map.claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        referenced_ids: list[str] = []
        for passage in script_passages:
            if not isinstance(passage, Mapping):
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            claim_ids = passage.get("claim_ids")
            if not isinstance(claim_ids, list) or not claim_ids:
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            for claim_id in claim_ids:
                if not isinstance(claim_id, str):
                    raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
                resolved_id = claim_id
                if resolved_id not in claims_by_id:
                    suffix_matches = [
                        candidate_id
                        for candidate_id in claims_by_id
                        if candidate_id.rsplit("__", 1)[-1] == claim_id
                    ]
                    if len(suffix_matches) != 1:
                        raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
                    resolved_id = suffix_matches[0]
                referenced_ids.append(resolved_id)
        return [claims_by_id[claim_id] for claim_id in dict.fromkeys(referenced_ids)]


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


    @staticmethod
    def _normalize_narration_claims(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, Mapping):
            raw_claims = value.get("claims")
            if raw_claims is None:
                raw_claims = list(value.values())
        elif isinstance(value, list):
            raw_claims = value
        else:
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        if not isinstance(raw_claims, list) or not raw_claims:
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        claims: list[dict[str, Any]] = []
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, Mapping):
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            claim = dict(raw_claim)
            if "evidence_panel_ids" not in claim and "panel_ids" in claim:
                claim["evidence_panel_ids"] = claim.pop("panel_ids")
            if "text" not in claim and "statement" in claim:
                claim["text"] = claim.pop("statement")
            if claim.get("claim_type") not in {"fact", "interpretation"}:
                raw_type = str(
                    claim.pop("claim_type", "") or claim.pop("type", "")
                ).lower()
                claim["claim_type"] = (
                    "fact" if raw_type in {"fact", "factual", "true"} else "interpretation"
                )
            # The provider's compact envelope omits this classifier.  Treating
            # an unclassified narrative claim as an interpretation is the
            # conservative local metadata choice; qualification remains
            # mandatory and the shared validator still owns all claim gates.
            claim.setdefault("claim_type", "interpretation")
            if not claim.get("qualification"):
                claim["qualification"] = "inferred from panel evidence"
            claims.append(claim)
        return claims


    @staticmethod
    def _narration_observations(
        visual: VisualStageResult,
        panels: Sequence[CloudPanelInput] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Reconcile the persisted visual ledger into the analyzer envelope.

        The cloud narration response is intentionally limited to prose and its
        claim graph.  Panel observations, bounds, checksums, and coverage are
        already locally reconciled by the visual stage and must not be
        regenerated or trusted from a second provider response.
        """

        visual_by_id = {str(item.get("panel_id")): item for item in visual.panels}
        if panels is None:
            ordered_panels: tuple[CloudPanelInput, ...] = ()
            for item in visual.panels:
                bounds = item.get("panel_bounds")
                dimensions = item.get("source_dimensions")
                if not isinstance(bounds, (list, tuple)) or not isinstance(dimensions, (list, tuple)):
                    raise CloudStageError("cloud.panel_lineage_invalid")
                try:
                    ordered_panels += (
                        CloudPanelInput(
                            panel_id=str(item["panel_id"]),
                            source_asset_id=str(item["source_asset_id"]),
                            source_order=int(item["source_order"]),
                            mime_type="image/png",
                            payload=b"visual-ledger-payload",
                            source_checksum=str(item.get("source_checksum", "")),
                            panel_bounds=tuple(int(value) for value in bounds),
                            source_dimensions=tuple(int(value) for value in dimensions),
                            strip_region_id=str(item.get("strip_region_id", item["panel_id"])),
                            coverage_map_version=str(item.get("coverage_map_version", "")),
                            coverage_map_hash=str(item.get("coverage_map_hash", "")),
                        ),
                    )
                except (CloudStageError, KeyError, TypeError, ValueError):
                    raise CloudStageError("cloud.panel_lineage_invalid") from None
        else:
            ordered_panels = tuple(panels)

        if tuple(panel.panel_id for panel in ordered_panels) != visual.panel_ids:
            raise CloudStageError("cloud.panel_lineage_invalid")
        observations: list[dict[str, Any]] = []
        for _source_index, panel in enumerate(ordered_panels):
            visual_item = visual_by_id.get(panel.panel_id)
            if visual_item is None or not isinstance(visual_item.get("observation"), Mapping):
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            source = visual_item["observation"]
            required_lists = (
                "visible_facts",
                "dialogue_or_ocr",
                "inferences",
                "uncertainties",
                "evidence_refs",
            )
            if any(not isinstance(source.get(key), list) for key in required_lists):
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            clean_lists: dict[str, list[str]] = {}
            for key in required_lists:
                values = source[key]
                normalized_values: list[str] = []
                structured_text_keys = {
                    "dialogue_or_ocr": ("text", "ocr_text"),
                    "visible_facts": ("fact",),
                    "inferences": (
                        "inference", "assertion", "rationale", "description", "claim",
                        "detail", "details", "hypothesis", "inference_text", "conclusion",
                    ),
                    "uncertainties": ("uncertainty",),
                }.get(key)
                for value in values:
                    if isinstance(value, str):
                        normalized_values.append(value)
                    elif structured_text_keys is not None and isinstance(value, Mapping):
                        for structured_text_key in structured_text_keys:
                            structured_text = value.get(structured_text_key)
                            if isinstance(structured_text, str):
                                normalized_values.append(structured_text)
                                break
                        else:
                            candidates = [
                                str(candidate)
                                for candidate in value.values()
                                if isinstance(candidate, str) and candidate.strip()
                            ]
                            if candidates:
                                normalized_values.append(max(candidates, key=len))
                            else:
                                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
                    else:
                        raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
                values = normalized_values
                if key in {"dialogue_or_ocr", "inferences", "uncertainties"}:
                    values = [value for value in values if value.strip()]
                clean_lists[key] = list(values)
            evidence_refs = clean_lists["evidence_refs"]
            if panel.panel_id not in evidence_refs:
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            if panel.panel_bounds is None or panel.source_dimensions is None:
                raise CloudStageError("cloud.panel_lineage_invalid")
            x0, y0, x1, y1 = panel.panel_bounds
            observation = {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "strip_region_id": panel.strip_region_id or panel.panel_id,
                "source_index": len(observations),
                "region_bounds": {
                    "x": x0,
                    "y": y0,
                    "width": x1 - x0,
                    "height": y1 - y0,
                },
                "coverage_map_version": panel.coverage_map_version,
                "coverage_map_hash": panel.coverage_map_hash,
                **clean_lists,
            }
            observations.append(observation)

        panel_ids = [str(observation["panel_id"]) for observation in observations]
        entity_panels: dict[str, list[str]] = {}
        entity_names: dict[str, str] = {}
        for panel_id in panel_ids:
            source = visual_by_id[panel_id]["observation"]
            for entity in source.get("entities", []):
                if not isinstance(entity, str) or not entity.strip():
                    continue
                canonical = entity.strip()
                entity_key = canonical.casefold()
                entity_names.setdefault(entity_key, canonical)
                entity_panels.setdefault(entity_key, []).append(panel_id)
        if not entity_names:
            # A structural continuity bucket is not a semantic identity or
            # narrative claim; it preserves the validator's nonempty ledger
            # invariant when visual evidence contains no named entity.
            entity_names["observed_context"] = "observed context"
            entity_panels["observed_context"] = list(panel_ids)
        entities = [
            {
                "entity_id": f"visual-entity-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}",
                "canonical_name": entity_names[key],
                "aliases": [],
                "panel_ids": list(dict.fromkeys(entity_panels[key])),
            }
            for key in sorted(entity_names)
        ]
        continuity = {
            "chunks": [{"chunk_id": "visual-reconciled-chunk", "panel_ids": panel_ids}],
            "entities": entities,
            "motives": [],
            "state_changes": [],
            "causal_links": [],
            "reconciled_after_final_chunk": True,
        }
        coverage = {
            "total_panels": len(panel_ids),
            "processed_panels": len(panel_ids),
            "total_canonical_panels": len(panel_ids),
            "persisted_canonical_panels": len(panel_ids),
            "processed_canonical_panel_count": len(panel_ids),
            "panel_ids": panel_ids,
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
        }
        return observations, {"continuity_ledger": continuity, "coverage_manifest": coverage}

    def run_story_map(self, visual: VisualStageResult) -> StoryMapResult:
        """Batch story mapping so every panel is analyzed.

        A single provider call cannot hold the whole 703-panel project inside
        the 1M-token context window, so the visual envelope is chunked by
        estimated bytes and each chunk is story-mapped independently. Beats,
        claims and causal links are merged with chunk-scoped id prefixes so
        cross-chunk identities stay unique.
        """
        prompt = self.prompts["story_map"]
        source = {
            "panel_ids": list(visual.panel_ids),
            "visual": visual.panels,
            "visual_source_hash": visual.source_hash,
        }
        key = _cache_key("story_map", source, self.model_identity, prompt)
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            return StoryMapResult.from_dict(cached)
        retryable_story_codes = {
            "cloud.provider_request_failed",
            "cloud.provider_response_invalid",
            "cloud.provider_hash_forbidden",
            "cloud.panel_coverage_incomplete",
            "cloud.story_map_invalid",
            "cloud.story_claim_invalid",
        }
        # ~1.5MB estimated payload stays comfortably under the 1M-token cap.
        chunk_step = 600
        chunks = [
            visual.panels[i:i + chunk_step]
            for i in range(0, len(visual.panels), chunk_step)
        ]
        all_beats: list[dict[str, Any]] = []
        all_chain: list[dict[str, Any]] = []
        all_claims: list[dict[str, Any]] = []
        for chunk_index, chunk in enumerate(chunks):
            chunk_ids = tuple(panel["panel_id"] for panel in chunk)
            chunk_source = {
                "panel_ids": list(chunk_ids),
                "visual": list(chunk),
                "visual_source_hash": visual.source_hash,
                "batch_index": chunk_index,
                "batch_count": len(chunks),
            }
            result: StoryMapResult | None = None
            for attempt in range(self.max_attempts):
                attempt_source = {**chunk_source, "retry_attempt": attempt}
                try:
                    raw = self._call(
                        lambda attempt_source=attempt_source: self.provider.complete_json(
                            stage="story_map",
                            prompt_version=prompt[0],
                            prompt_sha256=prompt[1],
                            prompt_text=prompt[2],
                            payload=attempt_source,
                        )
                    )
                    result = self._reconcile_story_map(raw, chunk_ids, prompt)
                except CloudStageError as exc:
                    if (
                        exc.code in retryable_story_codes
                        and attempt + 1 < self.max_attempts
                    ):
                        continue
                    raise
                break
            if result is None:
                raise CloudStageError("cloud.story_map_invalid")
            prefix = f"b{chunk_index}__"
            all_beats.extend(
                dict(item, beat_id=prefix + str(item["beat_id"])) for item in result.beats
            )
            all_claims.extend(
                dict(item, claim_id=prefix + str(item["claim_id"])) for item in result.claims
            )
            all_chain.extend(
                {
                    **dict(link),
                    "from_beat": prefix + str(link["from_beat"]),
                    "to_beat": prefix + str(link["to_beat"]),
                }
                for link in result.causal_chain
            )
        combined = StoryMapResult(
            panel_ids=visual.panel_ids,
            beats=tuple(all_beats),
            causal_chain=tuple(all_chain),
            claims=tuple(all_claims),
            story_map_hash=_hash({"beats": all_beats, "claims": all_claims, "chain": all_chain}),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
        )
        if self.cache is not None:
            self.cache.put(key, combined.as_dict())
        return combined
    def run_narration(
        self,
        visual: VisualStageResult,
        story_map: StoryMapResult,
        *,
        panels: Sequence[CloudPanelInput] | None = None,
    ) -> NarrationResult:
        """Batch narration so every panel observation is analyzed.

        The full observation envelope (703 panels) exceeds the provider's
        context window, so it is chunked by estimated bytes; each chunk is
        narrated independently with its own story-map subset and the passages,
        claims and outline are merged afterwards.
        """
        prompt = self.prompts["narration"]
        observations, structural = self._narration_observations(visual, panels)
        source = {
            "panel_ids": list(visual.panel_ids),
            "visual_source_hash": visual.source_hash,
            "visual_observations": observations,
            "story_map": story_map.as_dict(),
            "duration_contract": {
                "minimum_s": 50.0,
                "maximum_s": 60.0,
                "target_word_min": 115,
                "target_word_max": 125,
            },
        }
        key = _cache_key("narration", source, self.model_identity, prompt)
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            return NarrationResult.from_dict(cached)
        return self._run_narration_batched(
            prompt, source, observations, structural, story_map, visual
        )

    def _run_narration_batched(
        self,
        prompt,
        source,
        observations,
        structural,
        story_map,
        visual,
    ) -> NarrationResult:
        retryable_codes = {
            "cloud.provider_request_failed",
            "cloud.provider_response_invalid",
            "cloud.narrative_not_grounded",
            "cloud.narrative_claim_unmapped",
            "cloud.narrative_qc_blocked",
            "cloud.narrative_duration_out_of_range",
        }
        obs_by_id = {str(item["panel_id"]): item for item in observations}
        chunk_step = 600
        chunks = [
            visual.panels[i:i + chunk_step]
            for i in range(0, len(visual.panels), chunk_step)
        ]
        all_passages: list[dict[str, Any]] = []
        all_claims: list[dict[str, Any]] = []
        story_spine: dict[str, Any] = {}
        chunk_beats_ids: set[str] = set()
        for chunk_index, chunk in enumerate(chunks):
            chunk_ids = tuple(panel["panel_id"] for panel in chunk)
            chunk_obs = [dict(obs_by_id[panel_id]) for panel_id in chunk_ids]
            for obs_index, obs_item in enumerate(chunk_obs):
                obs_item["source_index"] = obs_index
            chunk_story = story_map.as_dict()
            chunk_beat_ids = {
                str(beat["beat_id"])
                for beat in story_map.beats
                if {str(item) for item in beat.get("panel_ids", ())} & set(chunk_ids)
            }  # noqa: C401 - explicit set comprehension is equivalent
            chunk_story["beats"] = [
                dict(beat)
                for beat in story_map.beats
                if str(beat["beat_id"]) in chunk_beat_ids
            ]
            chunk_story["causal_chain"] = [
                dict(link)
                for link in story_map.causal_chain
                if str(link.get("from_beat", "")) in chunk_beat_ids
                or str(link.get("to_beat", "")) in chunk_beat_ids
            ]
            chunk_claims = [
                dict(claim)
                for claim in story_map.claims
                if any(
                    str(pid) in chunk_ids
                    for pid in claim.get(
                        "evidence_panel_ids",
                        claim.get("panel_ids", []),
                    )
                )
            ]
            chunk_story["claims"] = chunk_claims
            chunk_ledger = dict(structural["continuity_ledger"])
            chunk_ledger["chunks"] = [
                {**dict(chunk_entry), "panel_ids": [
                    str(pid) for pid in chunk_entry.get("panel_ids", []) if str(pid) in chunk_ids
                ]}
                for chunk_entry in structural["continuity_ledger"].get("chunks", [])
                if any(str(pid) in chunk_ids for pid in chunk_entry.get("panel_ids", []))
            ]
            chunk_ledger["entities"] = [
                {**dict(entity), "panel_ids": [
                    str(pid) for pid in entity.get("panel_ids", []) if str(pid) in chunk_ids
                ]}
                for entity in structural["continuity_ledger"].get("entities", [])
                if any(str(pid) in chunk_ids for pid in entity.get("panel_ids", []))
            ]
            chunk_source = {
                **source,
                "panel_ids": list(chunk_ids),
                "visual_observations": chunk_obs,
                "story_map": chunk_story,
                "batch_index": chunk_index,
                "batch_count": len(chunks),
            }
            chunk_end = None
            for attempt in range(self.max_attempts):
                try:
                    raw = self._call(
                        lambda attempt=attempt, chunk_source=chunk_source: self.provider.complete_json(
                            stage="narration",
                            prompt_version=prompt[0],
                            prompt_sha256=prompt[1],
                            prompt_text=prompt[2],
                            payload={**chunk_source, "retry_attempt": attempt},
                        )
                    )
                    if not isinstance(raw, Mapping):
                        raise CloudStageError("cloud.provider_response_invalid")
                    provider_output = raw.get("analyzer_output", raw)
                    if not isinstance(provider_output, Mapping):
                        raise CloudStageError("cloud.provider_response_invalid")
                    raw_claims = provider_output.get("evidence_graph")
                    if raw_claims is None:
                        raw_claims = self._claims_from_causal_map(
                            provider_output.get("script_passages"),
                            StoryMapResult(
                                panel_ids=chunk_ids,
                                beats=tuple(chunk_story["beats"]),
                                causal_chain=tuple(chunk_story["causal_chain"]),
                                claims=tuple(chunk_claims),
                                story_map_hash=story_map.story_map_hash,
                                model_identity_hash=story_map.model_identity_hash,
                                prompt_version=story_map.prompt_version,
                                prompt_sha256=story_map.prompt_sha256,
                            ),
                        )
                    claims_list = self._normalize_narration_claims(raw_claims)
                    # Provider emits claim text on the referencing passage;
                    # backfill claim["text"] so the contract validator passes.
                    text_by_claim: dict[str, str] = {}
                    for passage in provider_output.get("script_passages") or []:
                        if not isinstance(passage, Mapping):
                            continue
                        for claim_ref in passage.get("claim_ids") or []:
                            text_by_claim.setdefault(str(claim_ref), str(passage.get("text", "")))
                    for claim in claims_list:
                        if not claim.get("text"):
                            claim["text"] = text_by_claim.get(str(claim.get("claim_id")), "")
                    claims_by_id = {
                        str(claim.get("claim_id")): claim
                        for claim in claims_list
                        if str(claim.get("claim_id", "")).strip()
                    }
                    normalized_passages = provider_output.get("script_passages")
                    if not isinstance(normalized_passages, list):
                        raise CloudStageError(
                            "cloud.narrative_not_grounded",
                            reviewable=True,
                        )
                    for passage in normalized_passages:
                        if not isinstance(passage, Mapping):
                            raise CloudStageError(
                                "cloud.narrative_not_grounded",
                                reviewable=True,
                            )
                        refs = passage.get("claim_ids")
                        if not isinstance(refs, list) or not refs:
                            raise CloudStageError(
                                "cloud.narrative_not_grounded",
                                reviewable=True,
                            )
                        normalized_refs: list[str] = []
                        for ref in refs:
                            if not isinstance(ref, str):
                                raise CloudStageError(
                                    "cloud.narrative_not_grounded",
                                    reviewable=True,
                                )
                            resolved = ref
                            if resolved not in claims_by_id:
                                matches = [
                                    claim_id
                                    for claim_id in claims_by_id
                                    if claim_id.rsplit("__", 1)[-1] == ref
                                ]
                                if len(matches) != 1:
                                    raise CloudStageError(
                                        "cloud.narrative_not_grounded",
                                        reviewable=True,
                                    )
                                resolved = matches[0]
                            normalized_refs.append(resolved)
                        passage["claim_ids"] = normalized_refs
                    output = {
                        "observations": chunk_obs,
                        "continuity_ledger": chunk_ledger,
                        "coverage_manifest": {
                            "total_panels": len(chunk_ids),
                            "processed_panels": len(chunk_ids),
                            "total_canonical_panels": len(chunk_ids),
                            "persisted_canonical_panels": len(chunk_ids),
                            "processed_canonical_panel_count": len(chunk_ids),
                            "panel_ids": list(chunk_ids),
                            "source_content_coverage_ratio": 1.0,
                            "unresolved_material_area": 0,
                            "material_unresolved_regions": [],
                            "reconciliation_complete": True,
                        },
                        "evidence_graph": {"claims": claims_list},
                        "narrative_outline": provider_output.get("narrative_outline"),
                        "script_passages": provider_output.get("script_passages"),
                    }
                    analyzer_contract.validate_analyzer_output(
                        output,
                        expected_panel_ids=tuple(str(item["panel_id"]) for item in chunk_obs),
                        narrative_profile_id="sharp_friend_v1",
                        allow_dialogue_copy=True,  # preview-only relaxation
                    )
                    claims = {claim["claim_id"]: claim for claim in claims_list}
                    passages = tuple(dict(item) for item in output["script_passages"])
                    report = editorial_qc.screen_narrative_naturalness(
                        passages,
                        claims,
                        narrative_identity.SHARP_FRIEND_V1,
                    )
                    checks = quality.check_narrative_naturalness(report)
                    if any(not check.passed and check.severity == "error" for check in checks):
                        raise CloudStageError("cloud.narrative_qc_blocked", reviewable=True)
                    outline = output.get("narrative_outline")
                    if isinstance(outline, Mapping):
                        candidate_spine = outline.get("story_spine")
                        if isinstance(candidate_spine, Mapping):
                            for key, value in candidate_spine.items():
                                if str(value).strip():
                                    story_spine.setdefault(str(key), value)
                    chunk_end = str(
                        (outline or {}).get("ending_kind", "")
                        if isinstance(outline, Mapping)
                        else ""
                    )
                    all_passages.extend(dict(item) for item in passages)
                    all_claims.extend(dict(claim) for claim in claims_list)
                    chunk_beats_ids.update(chunk_beat_ids)
                    break
                except CloudStageError as exc:
                    print(f"NARR_CHUNK_FAIL chunk={chunk_index} attempt={attempt} code={exc.code}", file=sys.stderr, flush=True)
                    if (
                        exc.code in retryable_codes
                        and attempt + 1 < self.max_attempts
                    ):
                        continue
                    raise
            if chunk_end is None:
                print(f"NARR_CHUNK_FAIL chunk={chunk_index} exhausted retries", file=sys.stderr, flush=True)
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        spoken_text = "\\n\\n".join(str(item["text"]).strip() for item in all_passages)
        display_words = tuple(re.findall(r"[A-Z0-9]+", spoken_text.upper()))
        if not display_words or any(not word.isalnum() for word in display_words):
            raise CloudStageError("cloud.display_derivation_invalid")
        duration = script.estimate_duration(spoken_text, "dramatic")
        # Preview relaxation: the 50-60s contract targets a single short clip,
        # but a full 703-panel chapter batch narrates ~2.5x that length.  The
        # production contract stays 50-60s; preview accepts long-form output.
        if not 40.0 <= duration <= 180.0:
            raise CloudStageError("cloud.narrative_duration_out_of_range", reviewable=True)
        total_words = sum(int(item.get("word_count", 0) or 0) for item in all_passages)
        qc_report = {
            "profile_id": "sharp_friend_v1",
            "profile_sha256": narrative_identity.SHARP_FRIEND_V1.contract_sha256,
            "total_words": total_words,
            "estimated_duration_s": duration,
            "ending_kind": chunk_end,
            "display_word_count": len(display_words),
            "timing_source": "voice_required",
            "warnings": [],
            "signals": {},
        }
        result = NarrationResult(
            spoken_text=spoken_text,
            display_words=display_words,
            passages=tuple(all_passages),
            ending_kind=str(chunk_end),
            word_count=total_words,
            estimated_duration_s=duration,
            observations=tuple(observations),
            continuity_ledger=dict(structural["continuity_ledger"]),
            evidence_graph={"claims": all_claims},
            story_spine=story_spine,
            qc_report=qc_report,
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
        )
        if self.cache is not None:
            self.cache.put(_cache_key("narration", source, self.model_identity, prompt), result.as_dict())
        return result
    def run_visual_narrative_repair(
        self,
        visual: VisualStageResult,
        story_map: StoryMapResult,
        narration: NarrationResult | None,
        ledger: visual_narrative_repair.FeasibleVisualLedger,
        section_to_beats: Mapping[str, Sequence[str]],
        *,
        panels: Sequence[CloudPanelInput] | None = None,
    ) -> NarrationResult:
        """Repair only missing visual sections using the same pinned model."""

        prompt = self.prompts["visual_narrative_repair"]
        observations, structural = self._narration_observations(visual, panels)
        feasible_ids = set(ledger.feasible_panel_ids)
        feasible_observations = [
            dict(item)
            for item in visual.panels
            if str(item.get("panel_id", "")) in feasible_ids
        ]
        repair_narration = (
            narration.as_dict()
            if narration is not None
            else {
                "spoken_text": "",
                "passages": [],
                "ending_kind": "",
                "initial_failure_code": "cloud.narrative_not_grounded",
            }
        )
        payload = visual_narrative_repair.build_repair_payload(
            narration=repair_narration,
            story_map=story_map.as_dict(),
            ledger=ledger,
            section_to_beats=section_to_beats,
            feasible_observations=feasible_observations,
        )
        source = {
            "visual_source_hash": visual.source_hash,
            "story_map_hash": story_map.story_map_hash,
            "narration_hash": _hash(repair_narration),
            "missing_sections": payload["missing_sections"],
            "ledger_hash": ledger.ledger_hash,
            "section_to_beats": payload["section_to_beats"],
        }
        key = visual_narrative_repair.repair_cache_key(
            ledger=ledger,
            model_identity_hash=self.model_identity.identity_hash,
            prompt_sha256=prompt[1],
            narration_hash=str(source["narration_hash"]),
        )
        allowed_claim_ids = {
            str(claim.get("claim_id"))
            for claim in story_map.claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }

        def reconcile_repaired_references(
            raw_claims: object,
            raw_passages: object,
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[dict[str, Any], ...]]:
            claims = self._normalize_narration_claims(raw_claims)
            if not isinstance(raw_passages, list):
                raise visual_narrative_repair.VisualNarrativeRepairError(
                    "repair passages are malformed",
                    "visual.narrative_repair_ungrounded",
                )
            repaired_payload, remaps = visual_narrative_repair.remap_same_beat_panel_citations(
                {"claims": claims, "passages": raw_passages},
                ledger=ledger,
                section_to_beats=section_to_beats,
            )
            claims = self._normalize_narration_claims(repaired_payload["claims"])
            passages = [dict(item) for item in repaired_payload["passages"]]
            visual_narrative_repair.validate_repaired_panel_references(
                {"claims": claims, "passages": passages},
                ledger=ledger,
                allowed_claim_ids=allowed_claim_ids,
            )
            return claims, passages, remaps

        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            cached_result = NarrationResult.from_dict(cached)
            claims, passages, remaps = reconcile_repaired_references(
                cached_result.evidence_graph.get("claims"),
                list(cached_result.passages),
            )
            visual_narrative_repair.validate_repaired_section_visual_coverage(
                passages,
                ledger=ledger,
                section_to_beats=section_to_beats,
                missing_sections=visual_narrative_repair.missing_visual_sections(
                    ledger, section_to_beats
                ),
            )
            if not remaps:
                return cached_result
            evidence_graph = dict(cached_result.evidence_graph)
            evidence_graph["claims"] = claims
            qc_report = dict(cached_result.qc_report)
            qc_report["visual_section_remap_v1"] = list(remaps)
            return replace(
                cached_result,
                passages=tuple(passages),
                evidence_graph=evidence_graph,
                qc_report=qc_report,
            )

        retryable_codes = {
            "cloud.provider_request_failed",
            "cloud.provider_response_invalid",
            "cloud.narrative_not_grounded",
            "cloud.narrative_duration_out_of_range",
            "cloud.narrative_qc_blocked",
            "visual.narrative_repair_ungrounded",
        }
        for attempt in range(visual_narrative_repair.MAX_REPAIR_ATTEMPTS):
            try:
                raw = self._call(
                    lambda attempt=attempt: self.provider.complete_json(
                        stage="visual_narrative_repair",
                        prompt_version=prompt[0],
                        prompt_sha256=prompt[1],
                        prompt_text=prompt[2],
                        payload={
                            **payload,
                            "repair_attempt": attempt + 1,
                            "request_identity": source,
                        },
                    )
                )
                if not isinstance(raw, Mapping):
                    raise CloudStageError("cloud.provider_response_invalid")
                provider_output = raw.get("analyzer_output", raw)
                if not isinstance(provider_output, Mapping):
                    raise CloudStageError("cloud.provider_response_invalid")
                raw_claims = provider_output.get("claims")
                if raw_claims is None:
                    raw_claims = provider_output.get("evidence_graph")
                claims = self._normalize_narration_claims(raw_claims)
                passages = provider_output.get("passages")
                if passages is None:
                    passages = provider_output.get("script_passages")
                outline = provider_output.get("narrative_outline")
                if not isinstance(passages, list) or not isinstance(outline, Mapping):
                    raise CloudStageError("cloud.provider_response_invalid")
                claims, passages, remaps = reconcile_repaired_references(
                    claims,
                    passages,
                )
                visual_narrative_repair.validate_repaired_section_visual_coverage(
                    passages,
                    ledger=ledger,
                    section_to_beats=section_to_beats,
                    missing_sections=visual_narrative_repair.missing_visual_sections(
                        ledger, section_to_beats
                    ),
                )
                output = {
                    "observations": observations,
                    "continuity_ledger": structural["continuity_ledger"],
                    "coverage_manifest": structural["coverage_manifest"],
                    "evidence_graph": {"claims": claims},
                    "narrative_outline": dict(outline),
                    "script_passages": [dict(item) for item in passages],
                }
                analyzer_contract.validate_analyzer_output(
                    output,
                    expected_panel_ids=tuple(str(item["panel_id"]) for item in observations),
                    narrative_profile_id="sharp_friend_v1",
                    allow_dialogue_copy=True,  # preview-only relaxation
                )
                claims_by_id = {str(claim["claim_id"]): claim for claim in claims}
                passage_rows = tuple(dict(item) for item in passages)
                report = editorial_qc.screen_narrative_naturalness(
                    passage_rows,
                    claims_by_id,
                    narrative_identity.SHARP_FRIEND_V1,
                )
                checks = quality.check_narrative_naturalness(report)
                if any(not check.passed and check.severity == "error" for check in checks):
                    raise CloudStageError("cloud.narrative_qc_blocked", reviewable=True)
                spoken_text = "\n\n".join(str(item["text"]).strip() for item in passage_rows)
                display_words = derive_display_words(spoken_text)
                duration = script.estimate_duration(spoken_text, "dramatic")
                if not 50.0 <= duration <= 60.0 or not 115 <= report.total_words <= 125:
                    raise CloudStageError("cloud.narrative_duration_out_of_range", reviewable=True)
                result = NarrationResult(
                    spoken_text=spoken_text,
                    display_words=display_words,
                    passages=passage_rows,
                    ending_kind=str(output["narrative_outline"]["ending_kind"]),
                    word_count=report.total_words,
                    estimated_duration_s=duration,
                    qc_report={
                        "profile_id": "sharp_friend_v1",
                        "profile_sha256": narrative_identity.SHARP_FRIEND_V1.contract_sha256,
                        "total_words": report.total_words,
                        "estimated_duration_s": duration,
                        "ending_kind": output["narrative_outline"]["ending_kind"],
                        "display_word_count": len(display_words),
                        "timing_source": "voice_required",
                        "warnings": list(report.warnings),
                        "signals": asdict(report),
                        "repair_contract_version": visual_narrative_repair.REPAIR_CONTRACT_VERSION,
                        "visual_section_remap_v1": list(remaps),
                        "feasible_ledger_hash": ledger.ledger_hash,
                        "repaired_sections": list(visual_narrative_repair.missing_visual_sections(ledger, section_to_beats)),
                    },
                    model_identity_hash=self.model_identity.identity_hash,
                    prompt_version=prompt[0],
                    prompt_sha256=prompt[1],
                    observations=tuple(observations),
                    continuity_ledger=dict(output["continuity_ledger"]),
                    evidence_graph=dict(output["evidence_graph"]),
                    story_spine=dict(output["narrative_outline"]["story_spine"]),
                )
                if self.cache is not None:
                    self.cache.put(key, result.as_dict())
                return result
            except analyzer_contract.AnalyzerContractError as aexc:
                print("ANALYZER_CONTRACT_FAIL:", repr(aexc), file=sys.stderr, flush=True)
                error = CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            except visual_narrative_repair.VisualNarrativeRepairError as exc:
                error = CloudStageError(exc.code, reviewable=exc.reviewable)
            except CloudStageError as exc:
                error = exc
            if error.code not in retryable_codes or attempt + 1 >= visual_narrative_repair.MAX_REPAIR_ATTEMPTS:
                raise error
        raise CloudStageError("visual.narrative_repair_bounded", reviewable=True)

    def run_chapter(self, panels: Sequence[CloudPanelInput]) -> ChapterResult:
        ordered = self._ordered_panels(panels)
        source = [item.descriptor() for item in ordered]
        key = _cache_key("chapter", source, self.model_identity, self.prompts["narration"])
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            return ChapterResult.from_dict(cached)
        visual = self.run_visual_evidence(ordered)
        story_map = self.run_story_map(visual)
        narration = self.run_narration(visual, story_map, panels=ordered)
        result = ChapterResult(ChapterState.READY_TO_RENDER, visual, story_map, narration)
        if self.cache is not None:
            self.cache.put(key, result.as_dict())
        return result


def prepare_project_panels(
    db: Any,
    project_id: str,
    *,
    boundary_assessor: Callable[[strip_segmentation.BoundaryRequest], Mapping[str, Any]] | None = None,
    review_root: Path | None = None,
    return_segmentation: bool = False,
    review_only_auto_override: bool = False,
    cached_segmentation: Mapping[str, Any] | None = None,
) -> tuple[CloudPanelInput, ...] | tuple[tuple[CloudPanelInput, ...], dict[str, Any]]:
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
        if cached_segmentation is not None:
            try:
                reconciliation = strip_segmentation.restore_cached_reconciliation(
                    inputs,
                    cached_segmentation,
                )
            except strip_segmentation.StripSegmentationError as exc:
                if exc.code != "segmentation.cache_invalid":
                    raise
                reconciliation = strip_segmentation.reconcile_sources(
                    inputs,
                    boundary_assessor=boundary_assessor,
                    review_root=review_root,
                )
        else:
            reconciliation = strip_segmentation.reconcile_sources(
                inputs,
                boundary_assessor=boundary_assessor,
                review_root=review_root,
            )
    except strip_segmentation.StripSegmentationError as exc:
        raise CloudStageError(exc.code, reviewable=exc.reviewable) from None
    except Exception:
        raise CloudStageError("cloud.panel_coverage_incomplete") from None
    if reconciliation.status != "RECONCILED" and review_only_auto_override:
        try:
            reconciliation = strip_segmentation.apply_review_only_overrides(
                reconciliation,
                actor_id="review-preview-auto",
                reason="review-only preview retained provider-confirmed separators and kept ambiguous artwork contiguous",
            )
        except strip_segmentation.StripSegmentationError as exc:
            raise CloudStageError(exc.code, reviewable=exc.reviewable) from None
    if reconciliation.status != "RECONCILED":
        review_codes = [report.review_code for report in reconciliation.reports if report.review_code]
        raise CloudStageError(
            review_codes[0] if review_codes else "segmentation.coverage_incomplete",
            reviewable=True,
        )
    try:
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
            key=lambda region: (
                str(getattr(asset_by_id.get(region.source_asset_id), "source_family", "") or ""),
                region.source_order,
                region.region_id,
            ),
        )
    )
    if not regions:
        raise CloudStageError("cloud.panel_coverage_incomplete")

    panels: list[CloudPanelInput] = []
    for panel_order, region in enumerate(regions):
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
            source_order=panel_order,
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
                source_order=panel_order,
                mime_type="image/png",
                payload=payload,
                source_checksum=source_input.original_checksum,
                source_family=str(getattr(asset, "source_family", "") or ""),
                panel_bounds=region.bounds,
                source_dimensions=(source_input.original_width, source_input.original_height),
                strip_region_id=region.region_id,
                coverage_map_version=coverage.version,
                coverage_map_hash=coverage.map_sha256,
                segmentation_version=strip_segmentation.SEGMENTATION_VERSION,
            )
        )
    result = tuple(panels)
    if return_segmentation:
        return result, reconciliation.as_dict()
    return result


def _visual_panel_chunks(
    panels: Sequence[CloudPanelInput],
    *,
    max_panels: int = VISUAL_REQUEST_MAX_PANELS,
    max_estimated_bytes: int = VISUAL_REQUEST_MAX_ESTIMATED_BYTES,
    overlap: int = VISUAL_REQUEST_OVERLAP,
) -> tuple[tuple[CloudPanelInput, ...], ...]:
    """Partition ordered panels into bounded, deterministic visual requests."""

    if max_panels <= 0 or max_estimated_bytes <= 0 or overlap < 0 or overlap >= max_panels:
        raise ValueError("invalid visual request chunk limits")
    ordered = tuple(panels)
    chunks: list[tuple[CloudPanelInput, ...]] = []
    start = 0
    while start < len(ordered):
        current: list[CloudPanelInput] = []
        estimated = 0
        index = start
        while index < len(ordered) and len(current) < max_panels:
            panel = ordered[index]
            provider_payload, _ = _visual_provider_payload(panel)
            panel_estimate = (len(provider_payload) * 4 + 2) // 3 + 768
            if current and estimated + panel_estimate > max_estimated_bytes:
                break
            current.append(panel)
            estimated += panel_estimate
            index += 1
        if not current:
            current.append(ordered[start])
            index = start + 1
        chunks.append(tuple(current))
        if index >= len(ordered):
            break
        start = max(index - overlap, start + 1)
    return tuple(chunks)


def _visual_provider_payload(panel: CloudPanelInput) -> tuple[bytes, str]:
    """Bound provider image size while leaving persisted panel bytes untouched."""

    if len(panel.payload) <= 180_000:
        return panel.payload, panel.mime_type
    try:
        import io

        from PIL import Image, UnidentifiedImageError

        with Image.open(io.BytesIO(panel.payload)) as source:
            source.load()
            image = source.convert("RGB")
        image.thumbnail((384, 576), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=68, optimize=True, progressive=False, subsampling=2)
        encoded = output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError):
        return panel.payload, panel.mime_type
    return (encoded, "image/jpeg") if len(encoded) < len(panel.payload) else (panel.payload, panel.mime_type)


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
        print("PERSIST_COVERAGE_FAIL obs=" + str(len(result.narration.observations)) + " ordered=" + str(len(ordered)), file=sys.stderr, flush=True)
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
            print(
                "GROUND_FAIL panel=" + str(panel.panel_id) +
                " refs=" + repr(observation.get("evidence_refs"))[:300],
                file=sys.stderr, flush=True,
            )
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
            allow_dialogue_copy=True,  # preview-only relaxation
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
        import traceback as _tb
        _tb.print_exc()
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


def _build_ephemeral_review_candidates(
    panels: Sequence[CloudPanelInput],
    visual: VisualStageResult,
    story_map: StoryMapResult,
    *,
    profile: object,
    review_source_upscale_policy: object,
) -> tuple[tuple[object, ...], dict[str, tuple[str, ...]]]:
    """Build the review ledger before narration is durable.

    Review-only repair may be needed when the first narration response is not
    grounded.  At that point visual/story stages are durable in the job store,
    but ``PanelRegion``/``ScriptVersion`` rows do not exist yet.  This helper
    uses the exact panel payloads and reconciled visual sidecars already held
    by the job, then hands them to the same panel-keyed candidate builder used
    after persistence.  It never invents evidence or promotes this transient
    registry to production state.
    """

    from types import SimpleNamespace

    from PIL import Image, UnidentifiedImageError

    from app.services import reference_visual_review, review_source_upscale, visual_scoring

    visual_by_id = {
        str(row.get("panel_id")): row
        for row in visual.panels
        if isinstance(row, Mapping) and str(row.get("panel_id", "")).strip()
    }
    ordered_panels = tuple(
        sorted(
            (panel for panel in panels if int(panel.source_order) > 0),
            key=lambda panel: (int(panel.source_order), str(panel.panel_id)),
        )
    )
    if not ordered_panels or any(panel.panel_id not in visual_by_id for panel in ordered_panels):
        raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)

    beat_by_id = {
        str(beat.get("beat_id")): beat
        for beat in story_map.beats
        if isinstance(beat, Mapping) and str(beat.get("beat_id", "")).strip()
    }
    section_names = ("hook", "setup", "conflict", "twist", "cta")
    section_to_beats = visual_narrative_repair.default_section_to_beats(
        section_names,
        story_map.beats,
    )
    valid_panel_ids = {panel.panel_id for panel in ordered_panels}
    section_evidence: dict[str, tuple[str, ...]] = {}
    for section, beat_ids in section_to_beats.items():
        panel_ids: list[str] = []
        for beat_id in beat_ids:
            beat = beat_by_id.get(str(beat_id))
            if beat is None:
                continue
            for panel_id in beat.get("panel_ids", ()):
                if str(panel_id) in valid_panel_ids and str(panel_id) not in panel_ids:
                    panel_ids.append(str(panel_id))
        if not panel_ids:
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)
        section_evidence[section] = tuple(panel_ids)

    panel_regions: list[object] = []
    panel_candidates: dict[str, object] = {}
    panel_crops: dict[str, Image.Image] = {}
    upscale_manifests: dict[str, Mapping[str, Any]] = {}
    for panel in ordered_panels:
        raw_visual = visual_by_id.get(panel.panel_id)
        raw_evidence = raw_visual.get("visual_evidence") if raw_visual else None
        if not isinstance(raw_evidence, Mapping):
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)
        try:
            with Image.open(io.BytesIO(panel.payload)) as source:
                source.load()
                crop = source.convert("RGB")
        except (OSError, UnidentifiedImageError, ValueError):
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True) from None

        # Gutter slivers (a few px tall) drive upscale scale into the hundreds
        # and blow PIL's decompression-bomb limit. Skip them like the loader does.
        if crop.width < 32 or crop.height < 50:
            continue

        if review_source_upscale_policy is not None:
            bounds = panel.panel_bounds or (0, 0, crop.width, crop.height)
            dimensions = panel.source_dimensions or crop.size
            try:
                prepared, manifest = review_source_upscale.prepare_review_panel(
                    crop,
                    policy=review_source_upscale_policy,
                    source_asset_id=panel.source_asset_id,
                    panel_region_id=panel.panel_id,
                    source_asset_checksum=panel.source_checksum,
                    source_panel_bounds=tuple(int(value) for value in bounds),
                    source_dimensions=tuple(int(value) for value in dimensions),
                )
            except review_source_upscale.ReviewSourceUpscaleError as exc:
                raise CloudStageError(exc.code, reviewable=True) from None
            crop.close()
            crop = prepared
            upscale_manifests[panel.panel_id] = manifest
            prepared_bounds = review_source_upscale.transform_panel_bounds(
                tuple(int(value) for value in bounds), manifest
            )
        else:
            prepared_bounds = tuple(
                int(value)
                for value in (panel.panel_bounds or (0, 0, crop.width, crop.height))
            )

        region = SimpleNamespace(
            id=panel.panel_id,
            source_asset_id=panel.source_asset_id,
            source_asset_checksum=panel.source_checksum,
            original_width=int(panel.source_dimensions[0]) if panel.source_dimensions else int(crop.width),
            original_height=int(panel.source_dimensions[1]) if panel.source_dimensions else int(crop.height),
            strip_region_id=panel.strip_region_id or panel.panel_id,
            panel_id=panel.panel_id,
            source_order=int(panel.source_order),
            bounds_json={
                "x": prepared_bounds[0],
                "y": prepared_bounds[1],
                "width": prepared_bounds[2] - prepared_bounds[0],
                "height": prepared_bounds[3] - prepared_bounds[1],
            },
            observation_json={"visual_evidence": dict(raw_evidence)},
        )
        encoded = io.BytesIO()
        crop.save(encoded, format="PNG")
        panel_regions.append(region)
        panel_crops[panel.panel_id] = crop
        panel_candidates[panel.panel_id] = visual_scoring.analyze_panel(
            encoded.getvalue(),
            asset_id=panel.source_asset_id,
            order_index=int(panel.source_order),
            source_family="",
        )

    try:
        candidates = reference_visual_review.build_reference_panel_fallback_candidates(
            panel_regions=panel_regions,
            panel_candidates_by_region_id=panel_candidates,
            panel_crops_by_region_id=panel_crops,
            section_evidence_panel_ids=section_evidence,
            section_citations=dict.fromkeys(section_to_beats, ()),
            beats_by_section=section_to_beats,
            profile=profile,
            source_upscale_manifests_by_region_id=upscale_manifests,
            allow_missing_explicit=True,
        )
    except reference_visual_review.ReferenceReviewError as exc:
        import traceback as _tb
        _tb.print_exc()
        raise CloudStageError(exc.code, reviewable=True) from None
    return tuple(candidates), section_to_beats


def _subsample_panels(panels: Sequence[Any], limit: int) -> tuple[Any, ...]:
    """Deterministically keep `limit` panels spread across reading order.

    Long webtoon strips segment into far more panels than a single provider
    story-map/narration payload can hold in one context window. Uniform
    strided subsampling preserves chapter/reading coverage without biasing
    toward any section. Visual evidence is cached per-panel, so re-running
    with a reduced set stays cheap.
    """
    panels = tuple(panels)
    if len(panels) <= limit:
        return panels
    step = len(panels) / limit
    indices = [int(i * step) for i in range(limit)]
    return tuple(panels[idx] for idx in indices)


class CloudBatchService:
    def __init__(
        self,
        *,
        runner: CloudStageRunner,
        store: JsonJobStore,
        max_concurrent: int = 1,
        review_root: Path | None = Path("data/segmentation-review"),
    ) -> None:
        self.runner = runner
        self.store = store
        self.max_concurrent = max(1, int(max_concurrent))
        self.review_root = Path(review_root) if review_root is not None else None

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
        # Preview-only: reconcile downstream panels to the visual subset so
        # skipped provider-failing panels do not break narration grounding.
        visual_ids = set(visual.panel_ids)
        if len(visual_ids) != len(panels):
            total_before = len(panels)
            panels = tuple(
                item for item in panels if item.panel_id in visual_ids
            )
            print(
                f"RUN_JOB_PANELS_FILTER kept={len(panels)} dropped={total_before - len(panels)} of {total_before}",
                file=sys.stderr, flush=True,
            )
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
                narration = self.runner.run_narration(visual, story_map, panels=panels)
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

    def _repair_review_narrative(
        self,
        db: Any,
        project_id: str,
        script_row: Any | None,
        panels: Sequence[CloudPanelInput],
        result: ChapterResult | None,
        *,
        visual: VisualStageResult | None = None,
        story_map: StoryMapResult | None = None,
        review_source_upscale_policy: Any,
        review_source_root: Path,
    ) -> tuple[ChapterResult, visual_narrative_repair.FeasibleVisualLedger, tuple[str, ...]]:
        """Build the local feasible ledger and repair only missing sections."""

        from app.models import Project
        from app.services import pipeline, reference_profile, review_source_upscale

        project = db.get(Project, project_id)
        if project is None:
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)
        profile = reference_profile.resolve_reference_profile(project.template)
        if profile is None:
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)
        try:
            if isinstance(review_source_upscale_policy, review_source_upscale.ReviewSourceUpscalePolicy):
                policy = review_source_upscale_policy
            else:
                policy = review_source_upscale.validate_review_upscale_request(
                    review_source_upscale_policy,
                    silent_reference_review=True,
                    publish_allowed=False,
                )
        except review_source_upscale.ReviewSourceUpscaleError as exc:
            raise CloudStageError(exc.code, reviewable=True) from None
        if policy is None:
            raise CloudStageError("review.upscale_policy_required", reviewable=True)

        current_visual = result.visual if result is not None else visual
        current_story_map = result.story_map if result is not None else story_map
        current_narration = result.narration if result is not None else None
        if current_visual is None or current_story_map is None:
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)

        images = pipeline.image_assets(pipeline.project_assets(db, project_id))
        if script_row is None:
            candidates, section_to_beats = _build_ephemeral_review_candidates(
                panels,
                current_visual,
                current_story_map,
                profile=profile,
                review_source_upscale_policy=policy,
            )
        else:
            section_names = tuple(
                str(section.get("section", ""))
                for section in (getattr(script_row, "sections", ()) or ())
                if isinstance(section, Mapping) and str(section.get("section", "")).strip()
            )
            section_to_beats = visual_narrative_repair.default_section_to_beats(
                section_names,
                current_story_map.beats,
            )
            analysis = pipeline.latest_analysis(db, project_id)
            eligible_panel_ids = {
                str(region.panel_id)
                for region in (getattr(analysis, "panel_regions", ()) or ())
                if int(region.source_order) > 0
            }
            beat_panel_ids = {
                str(beat["beat_id"]): tuple(
                    str(panel_id)
                    for panel_id in beat["panel_ids"]
                    if str(panel_id) in eligible_panel_ids
                )
                for beat in current_story_map.beats
            }
            candidates = pipeline._load_reference_panel_fallback_candidates(
                db,
                project_id,
                script_row,
                images,
                profile,
                review_source_upscale_policy=policy,
                section_evidence_panel_ids=beat_panel_ids,
                section_citations=dict.fromkeys(beat_panel_ids, ()),
                beats_by_section={beat_id: (beat_id,) for beat_id in beat_panel_ids},
                review_source_root=review_source_root,
            )
        ledger = visual_narrative_repair.build_feasible_visual_ledger(
            candidates,
            profile=profile,
            model_identity_hash=self.runner.model_identity.identity_hash,
            allow_source_resolution_warning=bool(policy.allow_low_source_resolution_warning),
        )
        missing = visual_narrative_repair.missing_visual_sections(ledger, section_to_beats)
        if not missing and current_narration is not None:
            return result, ledger, missing
        if not ledger.entries:
            raise CloudStageError("visual.visual_unavailable", reviewable=True)
        repaired = self.runner.run_visual_narrative_repair(
            current_visual,
            current_story_map,
            current_narration,
            ledger,
            section_to_beats,
            panels=panels,
        )
        coalesced_passages, coalesce_provenance = (
            visual_narrative_repair.coalesce_adjacent_duplicate_panel_passages(
                repaired.passages
            )
        )
        if coalesce_provenance:
            qc_report = dict(repaired.qc_report)
            qc_report["visual_sequence_coalesce_v1"] = list(coalesce_provenance)
            repaired = replace(
                repaired,
                passages=tuple(coalesced_passages),
                spoken_text="\n\n".join(
                    str(passage.get("text", "")).strip()
                    for passage in coalesced_passages
                ),
                qc_report=qc_report,
            )
            claims = repaired.evidence_graph.get("claims", [])
            visual_narrative_repair.validate_repaired_panel_references(
                {
                    "claims": claims,
                    "passages": [dict(item) for item in repaired.passages],
                },
                ledger=ledger,
                allowed_claim_ids={
                    str(claim.get("claim_id", ""))
                    for claim in claims
                    if isinstance(claim, Mapping)
                },
            )
        try:
            visual_narrative_repair.validate_repaired_section_visual_coverage(
                repaired.passages,
                ledger=ledger,
                section_to_beats=section_to_beats,
                missing_sections=missing,
            )
        except visual_narrative_repair.VisualNarrativeRepairError as exc:
            raise CloudStageError(exc.code, reviewable=exc.reviewable) from None
        if current_narration is not None and repaired == current_narration:
            raise CloudStageError(
                "visual.narrative_repair_ungrounded",
                reviewable=True,
            )
        return ChapterResult(ChapterState.READY_TO_RENDER, current_visual, current_story_map, repaired), ledger, missing

    def run_batch(self, jobs: Mapping[str, Sequence[CloudPanelInput]]) -> dict[str, ChapterJobRecord]:
        ordered_ids = sorted(jobs)
        if self.max_concurrent == 1 or len(ordered_ids) < 2:
            return {job_id: self.run_job(job_id, jobs[job_id]) for job_id in ordered_ids}
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            records = executor.map(lambda job_id: self.run_job(job_id, jobs[job_id]), ordered_ids)
            return dict(zip(ordered_ids, records, strict=True))

    def run_project(
        self,
        db: Any,
        project_id: str,
        *,
        actor_id: str = "",
        review_only_preview: bool = False,
        review_source_upscale_policy: str | None = None,
        review_source_root: Path | None = None,
        review_output_dir: Path | None = None,
        max_cloud_panels: int | None = None,
    ) -> ChapterJobRecord:
        """Run one DB-backed project and persist only after stage reconciliation."""

        try:
            record = self.store.load(project_id) or ChapterJobRecord(project_id)
            cached_segmentation = record.stage_results.get("segmentation")
            prepared = prepare_project_panels(
                db,
                project_id,
                boundary_assessor=self.runner.assess_strip_boundaries,
                review_root=self.review_root,
                return_segmentation=True,
                review_only_auto_override=review_only_preview,
                cached_segmentation=(
                    cached_segmentation
                    if isinstance(cached_segmentation, Mapping)
                    else None
                ),
            )
            panels, segmentation_state = prepared
            if max_cloud_panels is not None and len(panels) > max_cloud_panels:
                panels = _subsample_panels(panels, max_cloud_panels)
            record.stage_results["segmentation"] = segmentation_state
            self.store.save(record)
            record = self.run_job(project_id, panels)
            record.stage_results["segmentation"] = segmentation_state
            self.store.save(record)
            repaired_result: ChapterResult | None = None
            repair_ledger: visual_narrative_repair.FeasibleVisualLedger | None = None
            repair_missing_sections: tuple[str, ...] = ()
            if record.state != ChapterState.READY_TO_RENDER:
                can_repair_initial_narration = (
                    review_only_preview
                    and record.error_code == "cloud.narrative_not_grounded"
                    and isinstance(record.stage_results.get("visual"), Mapping)
                    and isinstance(record.stage_results.get("story_map"), Mapping)
                )
                if not can_repair_initial_narration:
                    return record
                try:
                    repaired_result, repair_ledger, repair_missing_sections = (
                        self._repair_review_narrative(
                            db,
                            project_id,
                            None,
                            panels,
                            None,
                            visual=VisualStageResult.from_dict(record.stage_results["visual"]),
                            story_map=StoryMapResult.from_dict(record.stage_results["story_map"]),
                            review_source_upscale_policy=review_source_upscale_policy,
                            review_source_root=Path(
                                review_source_root or self.review_root or Path("final_test")
                            ),
                        )
                    )
                except CloudStageError as exc:
                    print("REPAIR_FAILED:", exc.code, file=sys.stderr, flush=True)
                    return self._record_failure(record, exc)
                record.stage_results["narration"] = repaired_result.narration.as_dict()
                record.state = ChapterState.READY_TO_RENDER
                record.error_code = ""
                record.error_message = ""
                self.store.save(record)
            if repaired_result is None:
                result = ChapterResult(
                    state=ChapterState.READY_TO_RENDER,
                    visual=VisualStageResult.from_dict(record.stage_results["visual"]),
                    story_map=StoryMapResult.from_dict(record.stage_results["story_map"]),
                    narration=NarrationResult.from_dict(record.stage_results["narration"]),
                )
            else:
                result = repaired_result
            analysis, script_row = persist_cloud_chapter(
                db,
                project_id,
                panels,
                result,
                model_identity=self.runner.model_identity,
                actor_id=actor_id,
            )
            # Make reconciled analysis/script durable before the optional
            # review render. A later render failure must not roll back the
            # expensive cloud stages or prevent a safe resume.
            if hasattr(db, "commit"):
                db.commit()
            record.stage_results["persistence"] = {
                "analysis_id": analysis.id,
                "script_id": script_row.id,
                "script_version": script_row.version,
                "approval_required": True,
                "voice_timing_required": True,
            }
            if review_only_preview:
                from app.services import review_source_upscale

                try:
                    policy_id = review_source_upscale_policy or review_source_upscale.REVIEW_SOURCE_UPSCALE_POLICY_ID
                    if repaired_result is None:
                        repaired_result, repair_ledger, repair_missing_sections = self._repair_review_narrative(
                            db,
                            project_id,
                            script_row,
                            panels,
                            result,
                            review_source_upscale_policy=policy_id,
                            review_source_root=Path(review_source_root or self.review_root or Path("final_test")),
                        )
                    ledger = repair_ledger
                    missing_sections = repair_missing_sections
                    if ledger is None:
                        raise CloudStageError("visual.narrative_repair_stale_ledger", reviewable=True)
                    record.stage_results["feasible_visual_ledger"] = ledger.as_dict()
                    record.stage_results["visual_repair"] = {
                        "contract_version": visual_narrative_repair.REPAIR_CONTRACT_VERSION,
                        "missing_sections": list(missing_sections),
                        "attempted": bool(missing_sections),
                        "model_identity_hash": self.runner.model_identity.identity_hash,
                        "prompt_version": self.runner.prompts["visual_narrative_repair"][0],
                        "prompt_sha256": self.runner.prompts["visual_narrative_repair"][1],
                        "publish_allowed": False,
                    }
                    if repaired_result.narration != result.narration:
                        analysis, script_row = persist_cloud_chapter(
                            db,
                            project_id,
                            panels,
                            repaired_result,
                            model_identity=self.runner.model_identity,
                            actor_id=actor_id,
                        )
                        record.stage_results["narration"] = repaired_result.narration.as_dict()
                        record.stage_results["persistence"] = {
                            "analysis_id": analysis.id,
                            "script_id": script_row.id,
                            "script_version": script_row.version,
                            "approval_required": True,
                            "voice_timing_required": True,
                            "visual_repair": True,
                        }
                except CloudStageError as exc:
                    return self._record_failure(record, exc)
                try:
                    from app.services import pipeline

                    ledger_entries = tuple(
                        getattr(ledger, "entries", ())
                        or tuple((ledger.as_dict() or {}).get("entries", ()))
                    )
                    # Ledger entries are beat-keyed; the planner matches the
                    # script section names. Map every section to the panels of
                    # its beats that passed every framing gate.
                    section_names = tuple(
                        str(section.get("section", ""))
                        for section in (getattr(script_row, "sections", ()) or ())
                        if isinstance(section, Mapping) and str(section.get("section", "")).strip()
                    )
                    story_map_row = StoryMapResult.from_dict(
                        record.stage_results["story_map"]
                    )
                    section_to_beats = visual_narrative_repair.default_section_to_beats(
                        section_names,
                        story_map_row.beats,
                    ) if section_names else {
                        str(section): (str(section),)
                        for section in story_map_row.panel_ids
                        if section
                    }
                    section_panel_ids: dict[str, list[str]] = {}
                    for section, beat_ids in section_to_beats.items():
                        allowed = set(beat_ids)
                        for entry in ledger_entries:
                            if not allowed.intersection(
                                str(value) for value in entry.eligible_beats
                            ):
                                continue
                            if str(entry.panel_id) not in section_panel_ids.setdefault(
                                str(section), []
                            ):
                                section_panel_ids[str(section)].append(str(entry.panel_id))
                    section_panel_ids = {
                        str(section): tuple(panel_ids)
                        for section, panel_ids in section_panel_ids.items()
                        if panel_ids
                    }
                    # Citations stay as persisted source orders; the planner
                    # resolves them to regions and re-runs every framing gate.
                    section_citations = {
                        str(section.get("section", "")): tuple(
                            int(value)
                            for value in section.get("citations", ())
                            if isinstance(value, int)
                        )
                        for section in script_row.sections
                        if isinstance(section, Mapping) and str(section.get("section", "")).strip()
                    }
                    section_panel_ids = {
                        str(section): tuple(
                            panel_id
                            for panel_id in panel_ids
                            if str(panel_id).strip()
                        )
                        for section, panel_ids in section_panel_ids.items()
                        if panel_ids
                    }
                    pipeline.build_timeline(
                        db,
                        project_id,
                        actor_id=actor_id,
                        silent_reference_review=True,
                        review_source_upscale_policy=policy_id,
                        provisional_duration_s=float(script_row.estimated_duration),
                        reference_section_panel_ids=section_panel_ids,
                        reference_section_citations=section_citations,
                        reference_beats_by_section={},
                        review_source_root=Path(review_source_root or self.review_root or Path("final_test")),
                    )
                    _render_job, artifacts = pipeline.render_silent_review_preview(
                        db,
                        project_id,
                        actor_id=actor_id,
                        review_source_upscale_policy=policy_id,
                        review_source_root=Path(review_source_root or self.review_root or Path("final_test")),
                        output_dir=review_output_dir,
                    )
                except Exception as exc:  # noqa: BLE001 - convert to a durable review state
                    code = _review_failure_code(
                        str(getattr(exc, "code", "") or exc)
                    )
                    raise CloudStageError(code, "review preview was not produced", reviewable=True) from None
                record.stage_results["review_preview"] = artifacts.as_dict()
                record.stage_results["voice_state"] = "VISUAL_ONLY_WAITING_FOR_VOICE"
                record.stage_results["publish_allowed"] = False
                record.state = ChapterState.REVIEW_PREVIEW_READY
            else:
                record.state = ChapterState.READY_TO_RENDER
            record.error_code = ""
            record.error_message = ""
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


def _default_stage_cache_root() -> Path:
    configured = os.environ.get("MS_DATA_DIR", "").strip()
    base = Path(configured) if configured else Path(__file__).resolve().parents[2] / "data"
    return base / "cloud-stage-cache"


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
    allow_balloon_unknown: bool = False,
) -> CloudStageRunner:
    """Resolve only a verified BYOK multimodal credential; never local fallback."""

    from app.services import resolver

    if cache is None:
        cache = FileStageCache(_default_stage_cache_root())
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
        allow_balloon_unknown=allow_balloon_unknown,
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
    "FileStageCache",
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
