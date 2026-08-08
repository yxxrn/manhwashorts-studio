from __future__ import annotations

import base64
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


class VisionObservationProvider(Protocol):
    def capability(self) -> VisionCapabilityReport:
        ...

    def observe(
        self, request: VisionObservationRequest
    ) -> list[Mapping[str, Any]]:
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
            or set(observation) < _REQUIRED_OBSERVATION_KEYS
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
