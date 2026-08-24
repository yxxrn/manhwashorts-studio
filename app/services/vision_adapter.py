from __future__ import annotations

import base64
import hashlib
import importlib
import json
import string
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.services import visual_scoring

VISION_REQUEST_TIMEOUT = 600.0

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

_PROVIDER_VISUAL_KEYS = frozenset(
    {
        "balloon_mask_status",
        "balloon_regions",
        "protected_regions",
        "mask_confidence",
        "evidence_source",
        "mask_reason",
        "panel_id",
        "source_asset_id",
        "source_order",
    }
)
_PROVIDER_REGION_KEYS = frozenset(
    {
        "region_id",
        "kind",
        "normalized_bbox",
        "normalized_polygon",
        "confidence",
        "evidence_source",
        "mask_status",
    }
)
_PROTECTED_REGION_KEYS = frozenset(
    {
        "region_id",
        "kind",
        "normalized_bbox",
        "normalized_polygon",
        "confidence",
        "evidence_source",
        "required",
        "minimum_coverage",
    }
)
_OCR_ONLY_EVIDENCE_SOURCES = frozenset(
    {"ocr_text_only", "text_only_ocr", "ocr_only"}
)
_BALLOON_KIND_ALIASES = frozenset(
    {
        "caption",
        "shout",
        "speech",
        "speech_edge",
        "speech_tail",
        "shout_balloon",
        "tail",
        "thought",
        "thought_edge",
        "thought_balloon",
    }
)
_BALLOON_MASK_STATUS_ALIASES = frozenset({"covered", "mask_required"})


def _is_ocr_only_evidence_source(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in _OCR_ONLY_EVIDENCE_SOURCES


def _normalize_provider_visual_evidence(observation: Any) -> Any:
    """Normalize one conservative provider alias before local validation.

    Some OpenAI-compatible multimodal endpoints describe balloon regions with
    semantic labels (for example ``speech_edge``) and a covered mask as
    ``covered`` or ``mask_required``.  These are conservatively mapped to a
    local speech-balloon/non-empty geometry.  All other values remain
    untrusted and fail in the validator.
    """

    if not isinstance(observation, Mapping):
        return observation
    visual = observation.get("visual_evidence")
    if not isinstance(visual, Mapping):
        return observation
    regions = visual.get("balloon_regions")
    if not isinstance(regions, list):
        return observation
    normalized_regions: list[Any] = []
    changed = False
    for region in regions:
        if not isinstance(region, Mapping):
            normalized_regions.append(region)
            continue
        kind = region.get("kind")
        mask_status = region.get("mask_status")
        normalized_kind = (
            "speech_balloon"
            if isinstance(kind, str)
            and kind.strip().lower().replace("-", "_").replace(" ", "_")
            in _BALLOON_KIND_ALIASES
            else kind
        )
        normalized_mask_status = (
            "known_nonempty"
            if isinstance(mask_status, str)
            and mask_status.strip().lower().replace("-", "_").replace(" ", "_")
            in _BALLOON_MASK_STATUS_ALIASES
            else mask_status
        )
        if normalized_kind != kind or normalized_mask_status != mask_status:
            item = dict(region)
            item["kind"] = normalized_kind
            item["mask_status"] = normalized_mask_status
            normalized_regions.append(item)
            changed = True
        else:
            normalized_regions.append(region)
    if not changed:
        return observation
    normalized_visual = dict(visual)
    normalized_visual["balloon_regions"] = normalized_regions
    normalized_observation = dict(observation)
    normalized_observation["visual_evidence"] = normalized_visual
    return normalized_observation


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
    visual_instruction_version: str | None = None
    visual_instruction_sha256: str | None = None


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
    narrative_profile_id: str | None = None
    narrative_profile_version: str | None = None
    narrative_profile_sha256: str | None = None


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

    def __init__(self, message: str = "invalid vision request") -> None:
        super().__init__(message)
        self.retryable = False
        self.status_code: int | None = None
        self.retry_after_s: float | None = None


class VisionResponseInvalid(VisionCapabilityError):
    code = "vision_response_invalid"

    def __init__(self, message: str = "invalid vision response") -> None:
        super().__init__(message)
        self.retryable = False
        self.status_code: int | None = None
        self.retry_after_s: float | None = None


class VisionProviderRequestFailed(VisionCapabilityError):
    code = "vision_provider_request_failed"

    def __init__(
        self,
        message: str = "vision provider request failed",
        *,
        status_code: int | None = None,
        retry_after_s: float | None = None,
        retryable: bool = True,
        timeout: bool = False,
        transport_subtype: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)
        self.status_code = status_code
        self.retry_after_s = retry_after_s
        self.timeout = bool(timeout)
        self.transport_subtype = transport_subtype


def _chat_completion_content(response: httpx.Response) -> str:
    """Extract assistant text from JSON or OpenAI-style SSE responses."""

    headers = getattr(response, "headers", {})
    content_type = str(headers.get("content-type", "")).lower()
    if "text/event-stream" not in content_type:
        payload = response.json()
        return payload["choices"][0]["message"]["content"]

    fragments: list[str] = []
    for raw_line in response.text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except (TypeError, ValueError):
            raise VisionResponseInvalid() from None
        if not isinstance(event, Mapping):
            continue
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, Mapping):
            continue
        delta = choice.get("delta")
        message = choice.get("message")
        value = (
            delta.get("content")
            if isinstance(delta, Mapping)
            else message.get("content")
            if isinstance(message, Mapping)
            else choice.get("text")
        )
        if isinstance(value, str):
            fragments.append(value)
        elif isinstance(value, list):
            fragments.extend(
                str(part["text"])
                for part in value
                if isinstance(part, Mapping) and isinstance(part.get("text"), str)
            )
    if not fragments:
        raise VisionResponseInvalid()
    return "".join(fragments)


def _decode_json_content(content: str) -> Any:
    """Decode strict JSON, accepting only a whole-response JSON code fence."""

    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if (
            len(lines) < 3
            or lines[-1].strip() != "```"
            or lines[0].strip().lower() not in {"```", "```json"}
        ):
            raise ValueError("invalid JSON code fence")
        text = "\n".join(lines[1:-1]).strip()
    return json.loads(text)


def _raise_http_failure(response: httpx.Response) -> None:
    """Map known provider request errors without retaining provider payloads."""

    try:
        body = response.json()
    except Exception:
        body = None
    if (
        response.status_code == 400
        and isinstance(body, Mapping)
        and body.get("code") == "invalid_image"
    ):
        raise VisionRequestInvalid() from None
    retry_after: float | None = None
    raw_retry_after = getattr(response, "headers", {}).get("retry-after")
    if isinstance(raw_retry_after, str):
        try:
            retry_after = max(0.0, min(60.0, float(raw_retry_after.strip())))
        except (TypeError, ValueError):
            retry_after = None
    retryable = response.status_code == 429 or response.status_code >= 500
    raise VisionProviderRequestFailed(
        status_code=int(response.status_code),
        retry_after_s=retry_after,
        retryable=retryable,
    ) from None


class OpenAICompatibleVisionProvider:
    """Minimal OpenAI-compatible multimodal adapter with fail-closed parsing."""

    def __init__(self, *, base_url: str, model: str, api_key: str) -> None:
        self._base_url = base_url.strip() if isinstance(base_url, str) else ""
        self._model = model.strip() if isinstance(model, str) else ""
        self._api_key = api_key.strip() if isinstance(api_key, str) else ""
        # Process-local only: encoded image data is never persisted or exposed
        # in stage payloads.  The bounded cache avoids re-encoding the same
        # panel on a missing-only retry.
        self._ephemeral_image_cache: dict[str, str] = {}

    def __repr__(self) -> str:
        return (
            "OpenAICompatibleVisionProvider("
            f"base_url={self._base_url!r}, model={self._model!r}, "
            "api_key='[redacted]')"
        )

    @property
    def model_id(self) -> str:
        """The configured model identity, safe to expose in stage metadata."""

        return self._model

    @property
    def endpoint(self) -> str:
        """Configured endpoint without credentials, for pinned stage identity."""

        return self._base_url

    def _encoded_image(self, *, mime_type: str, payload: bytes) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        key = f"{mime_type.lower()}:{digest}"
        encoded = self._ephemeral_image_cache.get(key)
        if encoded is None:
            encoded = base64.b64encode(payload).decode("ascii")
            if len(self._ephemeral_image_cache) >= 256:
                self._ephemeral_image_cache.pop(next(iter(self._ephemeral_image_cache)))
            self._ephemeral_image_cache[key] = encoded
        return encoded

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
        payload = _build_payload(
            request,
            panels,
            self._model,
            encode_image=self._encoded_image,
        )
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
        except httpx.HTTPStatusError as exc:
            _raise_http_failure(exc.response)
        except httpx.TimeoutException:
            raise VisionProviderRequestFailed(
                timeout=True, retryable=True, transport_subtype="timeout"
            ) from None
        except httpx.TransportError:
            raise VisionProviderRequestFailed(
                retryable=True, transport_subtype="connect"
            ) from None
        except Exception:
            raise VisionProviderRequestFailed(retryable=False) from None

        try:
            content = _chat_completion_content(response)
        except Exception:
            raise VisionResponseInvalid() from None
        if not isinstance(content, str):
            raise VisionResponseInvalid()

        try:
            observations = _decode_json_content(content)
        except (TypeError, ValueError):
            raise VisionResponseInvalid() from None
        if request.visual_instruction_version is not None:
            observations = [
                _normalize_provider_visual_evidence(item)
                for item in observations
            ] if isinstance(observations, list) else observations
        return _validate_observations(
            observations,
            panels,
            require_visual_evidence=request.visual_instruction_version is not None,
        )

    def synthesize(
        self, request: VisionChapterSynthesisRequest
    ) -> Mapping[str, Any]:
        try:
            expected_panel_ids, profile = _validate_synthesis_request(request)
        except VisionRequestInvalid:
            raise
        except Exception:
            raise VisionRequestInvalid() from None

        report = self.capability()
        if not report.available:
            raise VisionCapabilityError()

        payload = _build_synthesis_payload(
            request, expected_panel_ids, self._model, profile
        )
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
        except httpx.HTTPStatusError as exc:
            _raise_http_failure(exc.response)
        except httpx.TimeoutException:
            raise VisionProviderRequestFailed(
                timeout=True, retryable=True, transport_subtype="timeout"
            ) from None
        except httpx.TransportError:
            raise VisionProviderRequestFailed(
                retryable=True, transport_subtype="connect"
            ) from None
        except Exception:
            raise VisionProviderRequestFailed(retryable=False) from None

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
            if profile is None:
                analyzer_contract.validate_analyzer_output(
                    result, expected_panel_ids=expected_panel_ids
                )
            else:
                analyzer_contract.validate_analyzer_output(
                    result,
                    expected_panel_ids=expected_panel_ids,
                    narrative_profile_id=profile.profile_id,
                )
        except Exception:
            raise VisionResponseInvalid() from None
        if not isinstance(result, Mapping):
            raise VisionResponseInvalid()
        return result

    def complete_json(
        self,
        *,
        stage: str,
        prompt_version: str,
        prompt_sha256: str,
        prompt_text: str = "",
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Run a strict JSON text stage through the same configured model.

        The cloud orchestration layer supplies stage prompts and performs all
        semantic reconciliation.  This adapter only speaks the existing
        OpenAI-compatible wire format and never returns provider error text.
        """

        if not isinstance(stage, str) or not stage.strip() or not isinstance(prompt_version, str) or not prompt_version.strip() or not isinstance(prompt_sha256, str) or len(prompt_sha256) != 64 or not isinstance(prompt_text, str):
            raise VisionRequestInvalid()
        if not isinstance(payload, Mapping):
            raise VisionRequestInvalid()
        report = self.capability()
        if not report.available:
            raise VisionCapabilityError()
        body = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{prompt_text.rstrip()}\n\n"
                        f"Stage: {stage}. Prompt version: {prompt_version}. "
                        f"Prompt SHA-256: {prompt_sha256}. Return only valid JSON."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, sort_keys=True, separators=(",", ":"))},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 65536,

        }
        try:
            response = httpx.post(
                f"{self._base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=VISION_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            content = _chat_completion_content(response)
            value = _decode_json_content(content)
        except httpx.HTTPStatusError as exc:
            _raise_http_failure(exc.response)
        except httpx.TimeoutException:
            raise VisionProviderRequestFailed(
                timeout=True, retryable=True, transport_subtype="timeout"
            ) from None
        except httpx.TransportError:
            raise VisionProviderRequestFailed(
                retryable=True, transport_subtype="connect"
            ) from None
        except Exception:
            raise VisionProviderRequestFailed(retryable=False) from None
        if not isinstance(value, Mapping):
            raise VisionResponseInvalid()
        return value

    def complete_json_with_images(
        self,
        *,
        stage: str,
        prompt_version: str,
        prompt_sha256: str,
        prompt_text: str = "",
        payload: Mapping[str, Any],
        images: tuple[Mapping[str, Any], ...],
    ) -> Mapping[str, Any]:
        """Send structured JSON plus real multimodal image content.

        ``payload`` remains metadata-only.  Raw bytes are encoded only while
        constructing the request body and are retained solely in the bounded
        process-local image cache for a missing-only retry.
        """

        if (
            not isinstance(stage, str)
            or not stage.strip()
            or not isinstance(prompt_version, str)
            or not prompt_version.strip()
            or not isinstance(prompt_sha256, str)
            or len(prompt_sha256) != 64
            or not isinstance(prompt_text, str)
            or not isinstance(payload, Mapping)
            or not isinstance(images, tuple)
            or not images
        ):
            raise VisionRequestInvalid()
        report = self.capability()
        if not report.available:
            raise VisionCapabilityError()
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"{prompt_text.rstrip()}\n\nStage: {stage}. Prompt version: "
                    f"{prompt_version}. Prompt SHA-256: {prompt_sha256}. "
                    "Return only valid JSON. Metadata: "
                    f"{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"
                ),
            }
        ]
        for image in images:
            mime_type = image.get("mime_type")
            image_bytes = image.get("payload")
            if (
                not isinstance(mime_type, str)
                or not mime_type.lower().startswith("image/")
                or not isinstance(image_bytes, bytes)
                or not image_bytes
            ):
                raise VisionRequestInvalid()
            encoded = self._encoded_image(
                mime_type=mime_type,
                payload=image_bytes,
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{encoded}",
                    },
                }
            )
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 65536,
        }
        try:
            response = httpx.post(
                f"{self._base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=VISION_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            value = _decode_json_content(_chat_completion_content(response))
        except httpx.HTTPStatusError as exc:
            _raise_http_failure(exc.response)
        except httpx.TimeoutException:
            raise VisionProviderRequestFailed(
                timeout=True, retryable=True, transport_subtype="timeout"
            ) from None
        except httpx.TransportError:
            raise VisionProviderRequestFailed(
                retryable=True, transport_subtype="connect"
            ) from None
        except (TypeError, ValueError, KeyError):
            raise VisionResponseInvalid() from None
        except Exception:
            raise VisionProviderRequestFailed(retryable=False) from None
        if not isinstance(value, Mapping):
            raise VisionResponseInvalid()
        return value

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

    visual_fields_supplied = (
        request.visual_instruction_version is not None
        or request.visual_instruction_sha256 is not None
    )
    if visual_fields_supplied:
        if not isinstance(request.visual_instruction_version, str) or not isinstance(
            request.visual_instruction_sha256, str
        ):
            raise VisionRequestInvalid()
        try:
            if (
                request.visual_instruction_version
                == visual_scoring.VISUAL_EVIDENCE_REPAIR_PROMPT_VERSION
            ):
                expected_visual_version, expected_visual_sha256, _ = (
                    visual_scoring.load_visual_evidence_repair_instruction()
                )
            else:
                expected_visual_version, expected_visual_sha256, _ = (
                    visual_scoring.load_visual_evidence_instruction()
                )
        except Exception:
            raise VisionRequestInvalid() from None
        if (
            request.visual_instruction_version != expected_visual_version
            or request.visual_instruction_sha256 != expected_visual_sha256
        ):
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
    *,
    encode_image=None,
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
    if request.visual_instruction_version is not None:
        metadata["visual_instruction_version"] = request.visual_instruction_version
        metadata["visual_instruction_sha256"] = request.visual_instruction_sha256
    if request.visual_instruction_version is not None:
        if (
            request.visual_instruction_version
            == visual_scoring.VISUAL_EVIDENCE_REPAIR_PROMPT_VERSION
        ):
            _, _, visual_prompt = visual_scoring.load_visual_evidence_repair_instruction()
        else:
            _, _, visual_prompt = visual_scoring.load_visual_evidence_instruction()
        legacy_fields = ", ".join(sorted(_REQUIRED_OBSERVATION_KEYS))
        instruction = (
            f"{visual_prompt.rstrip()}\n\n"
            "Return a JSON array containing the exact legacy observation fields "
            f"{legacy_fields} plus exactly one visual_evidence object per panel. "
            "Every legacy field is mandatory; return no markdown fences or commentary. "
            "Visual sidecar keys exactly: balloon_mask_status, balloon_regions, "
            "protected_regions, mask_confidence, evidence_source, mask_reason, "
            "panel_id, source_asset_id, source_order. "
            "Balloon region keys exactly: region_id, kind, normalized_bbox, "
            "normalized_polygon, confidence, evidence_source, mask_status. "
            "Protected region keys exactly: region_id, kind, normalized_bbox, "
            "normalized_polygon, confidence, evidence_source, required, "
            "minimum_coverage. Local reconciliation owns the evidence hash. "
            "evidence_refs must include the panel_id and be non-empty. "
            "Request metadata: "
            f"{json.dumps(metadata, sort_keys=True, separators=(',', ':'))}"
        )
    else:
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
        encoded = (
            encode_image(mime_type=panel["mime_type"], payload=panel["payload"])
            if encode_image is not None
            else base64.b64encode(panel["payload"]).decode("ascii")
        )
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
        "max_tokens": 65536,

    }


def validate_visual_evidence_observation(
    observation: Mapping[str, Any],
    *,
    expected_panel_id: str,
    expected_source_asset_id: str,
    expected_source_order: int,
) -> Mapping[str, Any]:
    """Validate untrusted provider visual geometry without hashing it."""

    try:
        if (
            not isinstance(observation, Mapping)
            or not _PROVIDER_VISUAL_KEYS.issubset(observation)
            or "evidence_hash" in observation
        ):
            raise VisionResponseInvalid()
        if (
            observation.get("panel_id") != expected_panel_id
            or observation.get("source_asset_id") != expected_source_asset_id
            or observation.get("source_order") != expected_source_order
        ):
            raise VisionResponseInvalid()
        status = observation.get("balloon_mask_status")
        if status not in {"unknown", "known_empty", "known_nonempty"}:
            raise VisionResponseInvalid()
        confidence = observation.get("mask_confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            raise VisionResponseInvalid()
        source = observation.get("evidence_source")
        reason = observation.get("mask_reason")
        if not isinstance(source, str) or not source.strip() or not isinstance(reason, str) or not reason.strip():
            raise VisionResponseInvalid()
        if status in {"known_empty", "known_nonempty"} and _is_ocr_only_evidence_source(source):
            raise VisionResponseInvalid()

        balloon_regions = observation.get("balloon_regions")
        protected_regions = observation.get("protected_regions")
        if not isinstance(balloon_regions, list) or not isinstance(protected_regions, list):
            raise VisionResponseInvalid()
        region_ids: set[str] = set()

        def validate_region(raw: Any, *, protected: bool) -> dict[str, Any]:
            required_keys = _PROTECTED_REGION_KEYS if protected else _PROVIDER_REGION_KEYS
            if not isinstance(raw, Mapping) or not required_keys.issubset(raw):
                raise VisionResponseInvalid()
            region_id = raw.get("region_id")
            kind = raw.get("kind")
            if not isinstance(region_id, str) or not region_id.strip() or region_id in region_ids:
                raise VisionResponseInvalid()
            allowed_kinds = {"background", "subject", "face", "action", "effect", "continuity_context"}
            if not protected:
                allowed_kinds = {"speech_balloon"}
            if not isinstance(kind, str) or kind not in allowed_kinds:
                raise VisionResponseInvalid()
            region_ids.add(region_id)
            bbox = raw.get("normalized_bbox")
            polygon = raw.get("normalized_polygon")
            if bbox is not None and (
                not isinstance(bbox, list)
                or len(bbox) != 4
                or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in bbox)
                or not all(0.0 <= float(item) <= 1.0 for item in bbox)
                or bbox[2] <= bbox[0]
                or bbox[3] <= bbox[1]
            ):
                raise VisionResponseInvalid()
            if polygon is not None and not isinstance(polygon, list):
                raise VisionResponseInvalid()
            if polygon and len(polygon) < 3:
                raise VisionResponseInvalid()
            for point in polygon or []:
                if (
                    not isinstance(point, list)
                    or len(point) != 2
                    or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in point)
                    or not all(0.0 <= float(item) <= 1.0 for item in point)
                ):
                    raise VisionResponseInvalid()
            region_confidence = raw.get("confidence")
            if (
                isinstance(region_confidence, bool)
                or not isinstance(region_confidence, (int, float))
                or not 0.0 <= float(region_confidence) <= 1.0
            ):
                raise VisionResponseInvalid()
            evidence_source = raw.get("evidence_source")
            if not isinstance(evidence_source, str) or not evidence_source.strip():
                raise VisionResponseInvalid()
            if not protected and raw.get("mask_status") not in {
                "unknown",
                "known_nonempty",
            }:
                raise VisionResponseInvalid()
            if protected:
                required = raw.get("required")
                minimum_coverage = raw.get("minimum_coverage")
                if not isinstance(required, bool) or (
                    isinstance(minimum_coverage, bool)
                    or not isinstance(minimum_coverage, (int, float))
                    or not 0.0 <= float(minimum_coverage) <= 1.0
                ):
                    raise VisionResponseInvalid()
            elif raw.get("mask_status") == "known_nonempty" and bbox is None and not polygon:
                raise VisionResponseInvalid()
            return {key: raw[key] for key in required_keys}

        normalized_balloon_regions = [
            validate_region(region, protected=False) for region in balloon_regions
        ]
        normalized_protected_regions = [
            validate_region(region, protected=True) for region in protected_regions
        ]

        if status == "known_empty" and (
            balloon_regions or float(confidence) <= 0.0
        ):
            raise VisionResponseInvalid()
        if status == "unknown" and (
            balloon_regions
            or not any(
                marker in source.lower()
                for marker in ("unavailable", "insufficient", "unknown")
            )
        ):
            raise VisionResponseInvalid()
        if status == "known_nonempty":
            if not balloon_regions or any(
                region.get("mask_status") != "known_nonempty"
                for region in balloon_regions
            ):
                raise VisionResponseInvalid()
            if any(
                _is_ocr_only_evidence_source(region.get("evidence_source"))
                for region in balloon_regions
            ):
                raise VisionResponseInvalid()
        normalized = {
            key: observation[key]
            for key in sorted(_PROVIDER_VISUAL_KEYS)
        }
        normalized["balloon_regions"] = normalized_balloon_regions
        normalized["protected_regions"] = normalized_protected_regions
        return normalized
    except VisionResponseInvalid:
        raise
    except (KeyError, TypeError, ValueError):
        raise VisionResponseInvalid() from None


def _validate_observations(
    observations: Any,
    panels: tuple[dict[str, Any], ...],
    *,
    require_visual_evidence: bool = False,
) -> list[Mapping[str, Any]]:
    if not isinstance(observations, list):
        raise VisionResponseInvalid()

    requested_ids = [panel["panel_id"] for panel in panels]
    requested_by_panel_id = {panel["panel_id"]: panel for panel in panels}
    requested_set = set(requested_by_panel_id)
    by_panel_id: dict[str, Mapping[str, Any]] = {}
    required_observation_keys = _REQUIRED_OBSERVATION_KEYS | (
        {"visual_evidence"} if require_visual_evidence else set()
    )
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise VisionResponseInvalid()
        panel_id = observation.get("panel_id")
        if (
            not isinstance(panel_id, str)
            or panel_id not in requested_set
            or panel_id in by_panel_id
            or not required_observation_keys.issubset(observation)
            or any(
                not isinstance(observation[key], list)
                for key in _REQUIRED_OBSERVATION_KEYS
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
        row = {
            key: observation[key]
            for key in sorted(required_observation_keys)
        }
        if require_visual_evidence:
            requested_panel = requested_by_panel_id[panel_id]
            row["visual_evidence"] = dict(
                validate_visual_evidence_observation(
                    row["visual_evidence"],
                    expected_panel_id=panel_id,
                    expected_source_asset_id=requested_panel["source_asset_id"],
                    expected_source_order=requested_panel["source_order"],
                )
            )
        by_panel_id[panel_id] = row

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
) -> tuple[tuple[str, ...], Any | None]:
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

    profile = validate_narrative_identity(request)

    try:
        analyzer_contract = importlib.import_module(
            "app.services.analyzer_contract"
        )
        committed = analyzer_contract.load_analyzer_instruction(
            narrative_profile_id=profile.profile_id if profile is not None else None
        )
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
    return expected_panel_ids, profile


def validate_narrative_identity(
    request: VisionChapterSynthesisRequest,
) -> Any | None:
    """Validate the explicit immutable identity carried by a synthesis request."""

    values = (
        request.narrative_profile_id,
        request.narrative_profile_version,
        request.narrative_profile_sha256,
    )
    if all(value is None for value in values):
        return None
    if (
        not isinstance(request.narrative_profile_id, str)
        or not request.narrative_profile_id.strip()
        or not isinstance(request.narrative_profile_version, str)
        or not request.narrative_profile_version.strip()
        or not isinstance(request.narrative_profile_sha256, str)
        or len(request.narrative_profile_sha256) != 64
        or any(character not in string.hexdigits for character in request.narrative_profile_sha256)
    ):
        raise VisionRequestInvalid()
    try:
        narrative_identity = importlib.import_module(
            "app.services.narrative_identity"
        )
        profile = narrative_identity.get_narrative_identity(
            request.narrative_profile_id
        )
    except Exception:
        raise VisionRequestInvalid() from None
    if (
        request.narrative_profile_version != profile.profile_version
        or request.narrative_profile_sha256 != profile.contract_sha256
    ):
        raise VisionRequestInvalid()
    return profile


def _build_synthesis_payload(
    request: VisionChapterSynthesisRequest,
    expected_panel_ids: tuple[str, ...],
    model: str,
    profile: Any | None = None,
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
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": request.instruction_text},
            {"role": "user", "content": ledger_instruction},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 65536,

    }
    if profile is not None:
        payload["narrative_identity"] = {
            "profile_id": profile.profile_id,
            "version": profile.profile_version,
            "sha256": profile.contract_sha256,
        }
    return payload
