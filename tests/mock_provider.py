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
import io
import json
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
