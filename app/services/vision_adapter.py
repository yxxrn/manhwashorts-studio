from __future__ import annotations

import base64
import importlib
import json
import string
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

VISION_REQUEST_TIMEOUT = 30.0

_REQUIRED_OBSERVATION_KEYS = frozenset(
    {
        "panel_id",
        "visible_facts",
        "dialogue_or_ocr",
        "inferences",
        "uncertainties",
        "entities",
        "state_changes",
        "causal_links",
        "evidence_refs",
    }
)

_REQUIRED_SYNTHESIS_OBSERVATION_KEYS = frozenset(
    {
        "panel_id",
        "source_asset_id",
        "strip_region_id",
        "source_index",
        "region_bounds",
        "coverage_map_version",
        "coverage_map_hash",
        "visible_facts",
        "dialogue_or_ocr",
        "inferences",
        "uncertainties",
        "evidence_refs",
    }
)


@dataclass(frozen=True)
class VisionCapabilityReport:
    provider_type: str
    provider_name: str
    model: str | None
    image_input: bool
    structured_json: bool
    available: bool
    blocking_reason: str | None


@dataclass(frozen=True)
class VisionObservationRequest:
    analysis_run_id: str
    instruction_version: str
    instruction_sha256: str
    chunk_index: int
    panels: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class VisionChapterSynthesisRequest:
    analysis_run_id: str
    instruction_version: str
    instruction_sha256: str
    instruction_text: str
    expected_panel_ids: tuple[str, ...]
    coverage_manifest: Mapping[str, Any]
    ordered_observations: tuple[Mapping[str, Any], ...]
    chunks: tuple[Mapping[str, Any], ...]


class VisionObservationProvider(Protocol):
    def capability(self) -> VisionCapabilityReport:
        ...

    def observe(
        self, request: VisionObservationRequest
    ) -> list[Mapping[str, Any]]:
        ...

    def synthesize(
        self, request: VisionChapterSynthesisRequest
    ) -> Mapping[str, Any]:
        ...


class VisionCapabilityError(RuntimeError):
    """Safe, machine-readable failure for vision capability or response gates."""

    code = "vision_capability_missing"


class VisionRequestInvalid(VisionCapabilityError):
    code = "vision_request_invalid"


class VisionResponseInvalid(VisionCapabilityError):
    code = "vision_response_invalid"


class VisionProviderRequestFailed(VisionCapabilityError):
    code = "vision_provider_request_failed"


class OpenAICompatibleVisionProvider:
    """Minimal OpenAI-compatible multimodal adapter with fail-closed parsing."""

    def __init__(self, *, base_url: str, model: str, api_key: str) -> None:
        self._base_url = base_url.strip() if isinstance(base_url, str) else ""
        self._model = model.strip() if isinstance(model, str) else ""
        self._api_key = api_key.strip() if isinstance(api_key, str) else ""

    def __repr__(self) -> str:
        return (
            "OpenAICompatibleVisionProvider("
            f"base_url={self._base_url!r}, model={self._model!r}, "
            "api_key='[redacted]')"
        )

    def capability(self) -> VisionCapabilityReport:
        available = self._configured()
        return VisionCapabilityReport(
            provider_type="openai_compatible",
            provider_name="openai_compatible",
            model=self._model or None,
            image_input=available,
            structured_json=available,
            available=available,
            blocking_reason=None
            if available
            else VisionCapabilityError.code,
        )

    def observe(
        self, request: VisionObservationRequest
    ) -> list[Mapping[str, Any]]:
        report = self.capability()
        if not report.available:
            raise VisionCapabilityError()

        panels = _validate_request(request)
        payload = _build_payload(request, panels, self._model)
        try:
            response = httpx.post(
                f"{self._base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=VISION_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except Exception:
            raise VisionProviderRequestFailed() from None

        try:
            provider_payload = response.json()
            content = provider_payload["choices"][0]["message"]["content"]
        except Exception:
            raise VisionResponseInvalid() from None
        if not isinstance(content, str):
            raise VisionResponseInvalid()

        try:
            observations = json.loads(content)
        except (TypeError, ValueError):
            raise VisionResponseInvalid() from None
        return _validate_observations(observations, panels)

    def synthesize(
        self, request: VisionChapterSynthesisRequest
    ) -> Mapping[str, Any]:
        try:
            expected_panel_ids = _validate_synthesis_request(request)
        except VisionRequestInvalid:
            raise
        except Exception:
            raise VisionRequestInvalid() from None

        report = self.capability()
        if not report.available:
            raise VisionCapabilityError()

        payload = _build_synthesis_payload(request, expected_panel_ids, self._model)
        try:
            response = httpx.post(
                f"{self._base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=VISION_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except Exception:
            raise VisionProviderRequestFailed() from None

        try:
            provider_payload = response.json()
            content = provider_payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError
            result = json.loads(content)
        except Exception:
            raise VisionResponseInvalid() from None

        try:
            analyzer_contract = importlib.import_module(
                "app.services.analyzer_contract"
            )
            analyzer_contract.validate_analyzer_output(
                result, expected_panel_ids=expected_panel_ids
            )
        except Exception:
            raise VisionResponseInvalid() from None
        if not isinstance(result, Mapping):
            raise VisionResponseInvalid()
        return result

    def _configured(self) -> bool:
        parsed = urlparse(self._base_url)
        return bool(
            self._base_url
            and parsed.scheme in {"http", "https"}
            and parsed.netloc
            and self._model
            and self._api_key
        )


def _validate_request(
    request: VisionObservationRequest,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(request, VisionObservationRequest):
        raise VisionRequestInvalid()
    if (
        not isinstance(request.analysis_run_id, str)
        or not request.analysis_run_id.strip()
    ):
        raise VisionRequestInvalid()
    if (
        not isinstance(request.instruction_version, str)
        or not request.instruction_version.strip()
    ):
        raise VisionRequestInvalid()
    if (
        not isinstance(request.instruction_sha256, str)
        or len(request.instruction_sha256) != 64
        or any(
            character not in string.hexdigits
            for character in request.instruction_sha256
        )
    ):
        raise VisionRequestInvalid()
    if (
        isinstance(request.chunk_index, bool)
        or not isinstance(request.chunk_index, int)
        or request.chunk_index < 0
    ):
        raise VisionRequestInvalid()
    if not request.panels:
        raise VisionRequestInvalid()

    normalized: list[dict[str, Any]] = []
    seen_panel_ids: set[str] = set()
    previous_order = -1
    for panel in request.panels:
        if not isinstance(panel, Mapping):
            raise VisionRequestInvalid()
        panel_id = panel.get("panel_id")
        source_asset_id = panel.get("source_asset_id")
        source_order = panel.get("source_order")
        mime_type = panel.get("mime_type")
        payload = panel.get("payload")
        if (
            not isinstance(panel_id, str)
            or not panel_id.strip()
            or panel_id in seen_panel_ids
            or not isinstance(source_asset_id, str)
            or not source_asset_id.strip()
            or isinstance(source_order, bool)
            or not isinstance(source_order, int)
            or source_order < 0
            or source_order <= previous_order
            or not isinstance(mime_type, str)
            or not mime_type.lower().startswith("image/")
            or not isinstance(payload, bytes)
            or not payload
        ):
            raise VisionRequestInvalid()
        seen_panel_ids.add(panel_id)
        previous_order = source_order
        normalized.append(
            {
                "panel_id": panel_id,
                "source_asset_id": source_asset_id,
                "source_order": source_order,
                "mime_type": mime_type,
                "payload": payload,
            }
        )
    return tuple(normalized)


def _build_payload(
    request: VisionObservationRequest,
    panels: tuple[dict[str, Any], ...],
    model: str,
) -> dict[str, Any]:
    metadata = {
        "analysis_run_id": request.analysis_run_id,
        "instruction_version": request.instruction_version,
        "instruction_sha256": request.instruction_sha256,
        "chunk_index": request.chunk_index,
        "panels": [
            {
                "panel_id": panel["panel_id"],
                "source_asset_id": panel["source_asset_id"],
                "source_order": panel["source_order"],
            }
            for panel in panels
        ],
    }
    instruction = (
        "Observe every supplied image panel in the ordered manifest. Return only "
        "a structured JSON list. Each observation must contain panel_id, "
        "visible_facts, dialogue_or_ocr, inferences, uncertainties, entities, "
        "state_changes, causal_links, and evidence_refs. Do not write a recap "
        "or use file labels or list positions as evidence; never infer missing "
        "panels. Request metadata: "
        f"{json.dumps(metadata, sort_keys=True, separators=(',', ':'))}"
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": instruction}]
    for panel in panels:
        encoded = base64.b64encode(panel["payload"]).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{panel['mime_type']};base64,{encoded}",
                },
            }
        )
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }


def _validate_observations(
    observations: Any,
    panels: tuple[dict[str, Any], ...],
) -> list[Mapping[str, Any]]:
    if not isinstance(observations, list):
        raise VisionResponseInvalid()

    requested_ids = [panel["panel_id"] for panel in panels]
    requested_set = set(requested_ids)
    by_panel_id: dict[str, Mapping[str, Any]] = {}
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise VisionResponseInvalid()
        panel_id = observation.get("panel_id")
        if (
            not isinstance(panel_id, str)
            or panel_id not in requested_set
            or panel_id in by_panel_id
            or set(observation) != _REQUIRED_OBSERVATION_KEYS
            or any(
                not isinstance(observation[key], list)
                for key in observation
                if key != "panel_id"
            )
        ):
            raise VisionResponseInvalid()
        evidence_refs = observation["evidence_refs"]
        if (
            not evidence_refs
            or panel_id not in evidence_refs
            or any(
                not isinstance(reference, str)
                or reference not in requested_set
                for reference in evidence_refs
            )
        ):
            raise VisionResponseInvalid()
        by_panel_id[panel_id] = dict(observation)

    if set(by_panel_id) != requested_set:
        raise VisionResponseInvalid()
    return [by_panel_id[panel_id] for panel_id in requested_ids]


def _valid_synthesis_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _synthesis_string_list(value: Any, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise VisionRequestInvalid()
    if not allow_empty and not value:
        raise VisionRequestInvalid()
    if any(not _valid_synthesis_text(item) for item in value):
        raise VisionRequestInvalid()
    return tuple(value)


def _validate_synthesis_observations(
    observations: tuple[Mapping[str, Any], ...],
    expected_panel_ids: tuple[str, ...],
) -> None:
    if len(observations) != len(expected_panel_ids):
        raise VisionRequestInvalid()
    expected_set = set(expected_panel_ids)
    for source_index, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise VisionRequestInvalid()
        if set(observation) != _REQUIRED_SYNTHESIS_OBSERVATION_KEYS:
            raise VisionRequestInvalid()
        panel_id = observation.get("panel_id")
        if panel_id != expected_panel_ids[source_index]:
            raise VisionRequestInvalid()
        if not _valid_synthesis_text(observation.get("source_asset_id")):
            raise VisionRequestInvalid()
        if not _valid_synthesis_text(observation.get("strip_region_id")):
            raise VisionRequestInvalid()
        observed_index = observation.get("source_index")
        if (
            isinstance(observed_index, bool)
            or not isinstance(observed_index, int)
            or observed_index != source_index
        ):
            raise VisionRequestInvalid()

        bounds = observation.get("region_bounds")
        if not isinstance(bounds, Mapping) or set(bounds) != {
            "x",
            "y",
            "width",
            "height",
        }:
            raise VisionRequestInvalid()
        for coordinate in ("x", "y", "width", "height"):
            number = bounds.get(coordinate)
            if (
                isinstance(number, bool)
                or not isinstance(number, int)
                or number < 0
            ):
                raise VisionRequestInvalid()
        if bounds["width"] == 0 or bounds["height"] == 0:
            raise VisionRequestInvalid()

        if not _valid_synthesis_text(observation.get("coverage_map_version")):
            raise VisionRequestInvalid()
        if not _valid_synthesis_text(observation.get("coverage_map_hash")):
            raise VisionRequestInvalid()
        for field in (
            "visible_facts",
            "dialogue_or_ocr",
            "inferences",
            "uncertainties",
        ):
            _synthesis_string_list(observation.get(field))
        evidence_refs = _synthesis_string_list(
            observation.get("evidence_refs"), allow_empty=False
        )
        if not set(evidence_refs) <= expected_set:
            raise VisionRequestInvalid()
        if panel_id not in evidence_refs:
            raise VisionRequestInvalid()


def _validate_synthesis_coverage(
    coverage_manifest: Mapping[str, Any], expected_panel_ids: tuple[str, ...]
) -> None:
    if not isinstance(coverage_manifest, Mapping):
        raise VisionRequestInvalid()
    required = {
        "total_panels",
        "processed_panels",
        "panel_ids",
        "source_content_coverage_ratio",
        "unresolved_material_area",
        "material_unresolved_regions",
        "reconciliation_complete",
    }
    if not required <= set(coverage_manifest):
        raise VisionRequestInvalid()
    if coverage_manifest["total_panels"] != len(expected_panel_ids):
        raise VisionRequestInvalid()
    if coverage_manifest["processed_panels"] != len(expected_panel_ids):
        raise VisionRequestInvalid()
    if (
        not isinstance(coverage_manifest["panel_ids"], list)
        or tuple(coverage_manifest["panel_ids"]) != expected_panel_ids
    ):
        raise VisionRequestInvalid()
    ratio = coverage_manifest["source_content_coverage_ratio"]
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
        raise VisionRequestInvalid()
    if ratio != 1.0:
        raise VisionRequestInvalid()
    unresolved = coverage_manifest["unresolved_material_area"]
    if isinstance(unresolved, bool) or unresolved != 0:
        raise VisionRequestInvalid()
    if coverage_manifest["material_unresolved_regions"] != []:
        raise VisionRequestInvalid()
    if coverage_manifest["reconciliation_complete"] is not True:
        raise VisionRequestInvalid()


def _declared_overlap(
    chunk: Mapping[str, Any], field: str, expected_panel_ids: tuple[str, ...]
) -> tuple[str, ...]:
    value = chunk.get(field, [])
    declared = _synthesis_string_list(value)
    if len(set(declared)) != len(declared):
        raise VisionRequestInvalid()
    if not set(declared) <= set(expected_panel_ids):
        raise VisionRequestInvalid()
    return declared


def _validate_synthesis_chunks(
    chunks: tuple[Mapping[str, Any], ...], expected_panel_ids: tuple[str, ...]
) -> None:
    if not chunks:
        raise VisionRequestInvalid()
    expected_set = set(expected_panel_ids)
    chunk_ids: set[str] = set()
    chunk_panel_ids: list[tuple[str, ...]] = []
    flattened: list[str] = []
    seen: set[str] = set()
    positions = {panel_id: index for index, panel_id in enumerate(expected_panel_ids)}

    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            raise VisionRequestInvalid()
        chunk_id = chunk.get("chunk_id")
        if not _valid_synthesis_text(chunk_id) or chunk_id in chunk_ids:
            raise VisionRequestInvalid()
        chunk_ids.add(chunk_id)
        panel_ids = _synthesis_string_list(chunk.get("panel_ids"), allow_empty=False)
        if len(set(panel_ids)) != len(panel_ids):
            raise VisionRequestInvalid()
        if not set(panel_ids) <= expected_set:
            raise VisionRequestInvalid()
        if tuple(sorted(panel_ids, key=positions.__getitem__)) != panel_ids:
            raise VisionRequestInvalid()
        chunk_panel_ids.append(panel_ids)
        for panel_id in panel_ids:
            if panel_id not in seen:
                flattened.append(panel_id)
                seen.add(panel_id)

    if set(seen) != expected_set or tuple(flattened) != expected_panel_ids:
        raise VisionRequestInvalid()

    if _declared_overlap(chunks[0], "overlap_with_previous", expected_panel_ids):
        raise VisionRequestInvalid()
    if _declared_overlap(chunks[-1], "overlap_with_next", expected_panel_ids):
        raise VisionRequestInvalid()
    for index, (previous, current) in enumerate(
        zip(chunk_panel_ids, chunk_panel_ids[1:], strict=False)
    ):
        intersection = set(previous).intersection(current)
        expected_overlap = tuple(
            panel_id for panel_id in expected_panel_ids if panel_id in intersection
        )
        if not expected_overlap:
            raise VisionRequestInvalid()
        if (
            _declared_overlap(
                chunks[index],
                "overlap_with_next",
                expected_panel_ids,
            )
            != expected_overlap
            or _declared_overlap(
                chunks[index + 1],
                "overlap_with_previous",
                expected_panel_ids,
            )
            != expected_overlap
        ):
            raise VisionRequestInvalid()


def _validate_synthesis_request(
    request: VisionChapterSynthesisRequest,
) -> tuple[str, ...]:
    if not isinstance(request, VisionChapterSynthesisRequest):
        raise VisionRequestInvalid()
    if not _valid_synthesis_text(request.analysis_run_id):
        raise VisionRequestInvalid()
    if not _valid_synthesis_text(request.instruction_version):
        raise VisionRequestInvalid()
    if not _valid_synthesis_text(request.instruction_sha256):
        raise VisionRequestInvalid()
    if not _valid_synthesis_text(request.instruction_text):
        raise VisionRequestInvalid()
    if not isinstance(request.expected_panel_ids, tuple):
        raise VisionRequestInvalid()
    expected_panel_ids = request.expected_panel_ids
    if (
        not expected_panel_ids
        or any(not _valid_synthesis_text(panel_id) for panel_id in expected_panel_ids)
        or len(set(expected_panel_ids)) != len(expected_panel_ids)
    ):
        raise VisionRequestInvalid()
    if not isinstance(request.ordered_observations, tuple):
        raise VisionRequestInvalid()
    if not isinstance(request.chunks, tuple):
        raise VisionRequestInvalid()

    try:
        analyzer_contract = importlib.import_module(
            "app.services.analyzer_contract"
        )
        committed = analyzer_contract.load_analyzer_instruction()
    except Exception:
        raise VisionRequestInvalid() from None
    if (
        not isinstance(committed, tuple)
        or len(committed) != 3
        or (request.instruction_version, request.instruction_sha256, request.instruction_text)
        != committed
    ):
        raise VisionRequestInvalid()

    _validate_synthesis_observations(
        request.ordered_observations, expected_panel_ids
    )
    _validate_synthesis_coverage(request.coverage_manifest, expected_panel_ids)
    _validate_synthesis_chunks(request.chunks, expected_panel_ids)
    return expected_panel_ids


def _build_synthesis_payload(
    request: VisionChapterSynthesisRequest,
    expected_panel_ids: tuple[str, ...],
    model: str,
) -> dict[str, Any]:
    ledger = {
        "analysis_run_id": request.analysis_run_id,
        "instruction_version": request.instruction_version,
        "instruction_sha256": request.instruction_sha256,
        "expected_panel_ids": list(expected_panel_ids),
        "coverage_manifest": dict(request.coverage_manifest),
        "ordered_observations": [
            dict(observation) for observation in request.ordered_observations
        ],
        "chunks": [dict(chunk) for chunk in request.chunks],
    }
    ledger_json = json.dumps(
        ledger, ensure_ascii=False, separators=(",", ":")
    )
    ledger_instruction = (
        "Synthesize the chapter from this complete ordered evidence ledger. "
        "Return exactly one structured JSON object matching the analyzer "
        "contract. Do not invent, repair, or omit evidence.\n"
        f"{ledger_json}"
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": request.instruction_text},
            {"role": "user", "content": ledger_instruction},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
