#!/usr/bin/env python3
"""Minimal loopback OmniVoice HTTP service for ManhwaShorts."""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import soundfile as sf
import torch
from omnivoice import OmniVoice

HOST = os.getenv("OMNIVOICE_HOST", "127.0.0.1")
PORT = int(os.getenv("OMNIVOICE_PORT", "3900"))
MODEL_NAME = os.getenv("OMNIVOICE_MODEL", "k2-fsa/OmniVoice")
DEVICE = os.getenv("OMNIVOICE_DEVICE", "cpu")
THREADS = int(os.getenv("OMNIVOICE_NUM_THREADS", "8"))
DEFAULT_INSTRUCT = os.getenv(
    "OMNIVOICE_DEFAULT_INSTRUCT",
    "male, young adult, moderate pitch, american accent",
)
REF_AUDIO = os.getenv("OMNIVOICE_REF_AUDIO", "")
REF_TEXT = os.getenv("OMNIVOICE_REF_TEXT", "")
MAX_INPUT = int(os.getenv("OMNIVOICE_MAX_INPUT_CHARS", "12000"))

if DEVICE == "cpu":
    torch.set_num_threads(THREADS)
    torch.set_num_interop_threads(min(2, THREADS))

started = time.monotonic()
model = OmniVoice.from_pretrained(
    MODEL_NAME,
    device_map=DEVICE,
    dtype=torch.float32 if DEVICE == "cpu" else torch.float16,
)
voice_prompt = None
if REF_AUDIO and REF_TEXT:
    if not Path(REF_AUDIO).is_file():
        raise RuntimeError(f"OMNIVOICE_REF_AUDIO not found: {REF_AUDIO}")
    voice_prompt = model.create_voice_clone_prompt(REF_AUDIO, REF_TEXT)
load_seconds = time.monotonic() - started
synthesis_lock = threading.Lock()


def json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "OmniVoiceLoopback/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send(404, "application/json", json_bytes({"error": "not_found"}))
            return
        self._send(
            200,
            "application/json",
            json_bytes(
                {
                    "status": "ok",
                    "model": MODEL_NAME,
                    "device": DEVICE,
                    "sample_rate": model.sampling_rate,
                    "voice_mode": "clone" if voice_prompt is not None else "design",
                    "load_seconds": round(load_seconds, 2),
                    "busy": synthesis_lock.locked(),
                }
            ),
        )

    def do_POST(self) -> None:
        if self.path != "/v1/audio/speech":
            self._send(404, "application/json", json_bytes({"error": "not_found"}))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            text = str(payload.get("input") or payload.get("text") or "").strip()
            if not text:
                raise ValueError("input is required")
            if len(text) > MAX_INPUT:
                raise ValueError(f"input exceeds {MAX_INPUT} characters")
            language = str(payload.get("language") or "id")
            speed = max(0.5, min(2.0, float(payload.get("speed", 0.9))))
            steps = max(8, min(32, int(payload.get("num_step", 32))))
            instruct = str(payload.get("instruct") or DEFAULT_INSTRUCT)
            kwargs: dict[str, Any] = {
                "text": text,
                "language": language,
                "speed": speed,
                "num_step": steps,
                "guidance_scale": float(payload.get("guidance_scale", 1.8)),
                "instruct": instruct,
            }
            if voice_prompt is not None:
                kwargs["voice_clone_prompt"] = voice_prompt
            with synthesis_lock:
                audio = model.generate(**kwargs)[0]
            import io

            out = io.BytesIO()
            sf.write(out, audio, model.sampling_rate, format="WAV", subtype="PCM_16")
            self._send(200, "audio/wav", out.getvalue())
        except Exception as exc:
            self._send(400, "application/json", json_bytes({"error": str(exc)}))


if __name__ == "__main__":
    print(
        f"OmniVoice ready model={MODEL_NAME} device={DEVICE} "
        f"voice_mode={'clone' if voice_prompt is not None else 'design'} "
        f"load={load_seconds:.1f}s http://{HOST}:{PORT}",
        flush=True,
    )
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
