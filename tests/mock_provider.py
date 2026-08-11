"""A tiny stand-in for a real AI provider, used by tests and manual checks.

Speaks just enough of the OpenAI wire format to exercise the BYOK path without
network access or spending anyone's credits:

* ``GET  /v1/models``          -> model list, gated on the Authorization header
* ``POST /v1/chat/completions``-> a fixed analysis JSON payload
* ``POST /v1/audio/speech``    -> a real WAV of silence

Run standalone with:  python tests/mock_provider.py --port 8931
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

#: Only this key is accepted; anything else gets a 401 so tests can assert on it.
GOOD_KEY = "sk-mock-valid-key-0001"

MODELS = ["mock-large", "mock-small", "tts-1"]

ANALYSIS_JSON = {
    "characters": [
        {"name": "Rian", "role": "protagonist", "aliases": ["Si Pemburu"]},
        {"name": "Kaela", "role": "ally", "aliases": []},
    ],
    "locations": ["Menara Kelabu"],
    "events": [
        {"text": "Rian memasuki menara yang belum pernah dijamah", "kind": "event"},
        {"text": "Penjaga lantai tujuh menghadang jalan mereka", "kind": "conflict"},
        {"text": "Ternyata penjaga itu adalah ayah Rian", "kind": "twist"},
        {"text": "Pintu lantai delapan terbuka sendiri", "kind": "cliffhanger"},
    ],
    "main_conflict": "Rian harus melewati penjaga untuk menyelamatkan Kaela",
    "twist": "Penjaga menara itu ayahnya sendiri",
    "cliffhanger": "Pintu lantai delapan terbuka tanpa disentuh",
    "pronunciation_candidates": ["Kaela"],
    "low_confidence_notes": [],
}

_VISION_LOCK = threading.Lock()
_VISION_REQUESTS: list[dict] = []
_VISION_RESPONSE_CONTENT: str | None = None


def default_vision_response() -> list[dict]:
    """Return deterministic observations for the multimodal adapter tests."""
    return [
        {
            "panel_id": f"panel-{suffix}",
            "visible_facts": [f"visible fact {suffix}"],
            "dialogue_or_ocr": [],
            "inferences": [],
            "uncertainties": [],
            "entities": [f"entity-{suffix}"],
            "state_changes": [],
            "causal_links": [],
            "evidence_refs": [f"panel-{suffix}"],
        }
        for suffix in ("a", "b", "c")
    ]


def default_visual_vision_response() -> list[dict]:
    """Return ordered observations with provider-owned visual sidecars."""
    rows = default_vision_response()
    sidecars = (
        {
            "balloon_mask_status": "known_nonempty",
            "balloon_regions": [
                {
                    "region_id": "balloon-a",
                    "kind": "speech_balloon",
                    "normalized_bbox": [0.10, 0.12, 0.48, 0.30],
                    "normalized_polygon": [],
                    "confidence": 0.91,
                    "evidence_source": "vision_geometry_v1",
                    "mask_status": "known_nonempty",
                }
            ],
            "protected_regions": [
                {
                    "region_id": "subject-a",
                    "kind": "subject",
                    "normalized_bbox": [0.38, 0.36, 0.86, 0.94],
                    "normalized_polygon": [],
                    "confidence": 0.88,
                    "evidence_source": "vision_geometry_v1",
                    "required": True,
                    "minimum_coverage": 0.60,
                }
            ],
            "mask_confidence": 0.91,
            "evidence_source": "vision_geometry_v1",
            "mask_reason": "speech geometry is visible in the panel",
        },
        {
            "balloon_mask_status": "known_empty",
            "balloon_regions": [],
            "protected_regions": [],
            "mask_confidence": 0.96,
            "evidence_source": "vision_geometry_v1",
            "mask_reason": "the provider explicitly reports no speech region",
        },
        {
            "balloon_mask_status": "unknown",
            "balloon_regions": [],
            "protected_regions": [],
            "mask_confidence": 0.0,
            "evidence_source": "vision_geometry_unavailable",
            "mask_reason": "geometry is unavailable for this panel",
        },
    )
    for row, sidecar in zip(rows, sidecars, strict=True):
        row["visual_evidence"] = {
            **sidecar,
            "panel_id": row["panel_id"],
            "source_asset_id": f"asset-{row['panel_id'][-1]}",
            "source_order": {"a": 17, "b": 23, "c": 41}[row["panel_id"][-1]],
        }
    return rows


def reset_vision_state() -> None:
    """Clear captured multimodal requests and restore the default response."""
    global _VISION_RESPONSE_CONTENT
    with _VISION_LOCK:
        _VISION_REQUESTS.clear()
        _VISION_RESPONSE_CONTENT = None


def set_vision_response_content(content: str | None) -> None:
    """Override the next deterministic response body used by vision tests."""
    global _VISION_RESPONSE_CONTENT
    with _VISION_LOCK:
        _VISION_RESPONSE_CONTENT = content


def captured_vision_requests() -> list[dict]:
    """Return a copy of multimodal request bodies without headers or secrets."""
    with _VISION_LOCK:
        return copy.deepcopy(_VISION_REQUESTS)


def _is_vision_request(body: dict) -> bool:
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for part in content
        ):
            return True
    return False


def _silent_wav(seconds: float = 1.5, rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        """Silence request logging so test output stays readable."""

    def _authorised(self) -> bool:
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {GOOD_KEY}"

    def _send(self, code: int, payload: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _reject(self) -> None:
        self._json(401, {"error": {"message": "Invalid API key provided", "type": "invalid_key"}})

    def do_GET(self) -> None:
        if not self.path.rstrip("/").endswith("/models"):
            self._json(404, {"error": {"message": "not found"}})
            return
        if not self._authorised():
            self._reject()
            return
        self._json(200, {"data": [{"id": m} for m in MODELS]})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not self._authorised():
            self._reject()
            return

        if self.path.endswith("/chat/completions"):
            body = json.loads(raw or b"{}")
            if body.get("model") not in MODELS:
                self._json(400, {"error": {"message": f"unknown model {body.get('model')}"}})
                return
            if _is_vision_request(body):
                with _VISION_LOCK:
                    _VISION_REQUESTS.append(copy.deepcopy(body))
                    content = _VISION_RESPONSE_CONTENT
                if content is None:
                    content = json.dumps(default_vision_response())
                self._json(
                    200,
                    {"choices": [{"message": {"content": content}}]},
                )
                return
            self._json(
                200,
                {
                    "choices": [
                        {"message": {"content": json.dumps(ANALYSIS_JSON)}}
                    ]
                },
            )
            return

        if self.path.endswith("/audio/speech"):
            self._send(200, _silent_wav(), "audio/wav")
            return

        self._json(404, {"error": {"message": "not found"}})


def serve(port: int = 0) -> HTTPServer:
    """Start the mock server. Port 0 picks a free one."""
    return HTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":  # pragma: no cover - manual use
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8931)
    args = parser.parse_args()
    server = serve(args.port)
    print(f"mock provider on http://127.0.0.1:{server.server_port}/v1  key={GOOD_KEY}")
    server.serve_forever()
