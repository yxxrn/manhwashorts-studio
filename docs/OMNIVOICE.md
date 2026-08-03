# OmniVoice TTS

ManhwaShorts supports OmniVoice through the generic HTTP TTS adapter. Production synthesis stays on UpCloud; Google is an execution worker for tests or renders only.

## Production topology

```text
app / render worker (UpCloud)
        │ http://127.0.0.1:3900/v1/audio/speech
        ▼
OmniVoice-Studio (isolated venv, CPU, systemd)
```

Configure `MS_TTS_HTTP_URL`. Keep OmniVoice private. If app and TTS run on different machines, use a private network or authenticated TLS; never commit an API key.

## Configuration

```bash
MS_TTS_PROVIDER=http
MS_TTS_HTTP_URL=http://127.0.0.1:3900/v1/audio/speech
MS_TTS_HTTP_PROTOCOL=openai
MS_TTS_HTTP_MODEL=omnivoice
MS_TTS_HTTP_RESPONSE_FORMAT=wav
MS_TTS_HTTP_LANGUAGE=en
MS_TTS_HTTP_VOICE=default
MS_TTS_HTTP_INSTRUCT="male, young adult, moderate pitch, american accent"
MS_TTS_HTTP_SEED=42
MS_TTS_HTTP_NUM_STEP=32
MS_TTS_HTTP_GUIDANCE_SCALE=1.8
MS_TTS_HTTP_AUDIO_FILTER=expressive
```

Project language remains authoritative: Indonesian sends `language=id`; explicitly English sends `language=en`. The app never infers language from panels.

## Request contract

With `MS_TTS_HTTP_PROTOCOL=openai`, the JSON payload is:

```json
{
  "model": "omnivoice",
  "input": "narration text",
  "voice": "default",
  "response_format": "wav",
  "speed": 0.9,
  "language": "en",
  "instruct": "male, young adult, moderate pitch, american accent",
  "num_step": 32,
  "guidance_scale": 1.8,
  "seed": 42
}
```

The endpoint returns audio bytes. The app writes them to scratch, applies the configured mastering preset with FFmpeg, probes duration with FFprobe, then derives word timings from measured duration.

## Shared narrator reference

The current Google service runs voice design mode by default. For voice cloning, set `OMNIVOICE_REF_AUDIO` to an audio reference you are authorized to use and `OMNIVOICE_REF_TEXT` to its exact transcript; the service creates one official `VoiceClonePrompt` at startup and reuses it for every request. Later sections reuse the same deterministic seed and instruct settings. HTTP `503` responses retry using `Retry-After`, capped by the implementation. A failed configured TTS request raises an error; it does not silently replace OmniVoice with espeak.

## Audio and subtitle path

```text
OmniVoice WAV → FFmpeg mastering → measured duration → word timings
→ 0.18s beat gaps + master voice WAV → timeline + karaoke ASS → final mux
```

No forced-aligner dependency is used. Timings are deterministic weighted estimates from measured clip duration. Full captions remain visible; only the active word is highlighted.

## Verification

Run on the execution host:

```bash
.venv/bin/python -c "from app.services.render import check_environment; print(check_environment() or 'OK')"
curl -sS http://127.0.0.1:8000/api/health
curl -sS http://127.0.0.1:8000/api/voices
systemctl is-active omnivoice.service
curl -sS http://127.0.0.1:3900/health
```

The app must report `tts_provider: http` before a render can be called an OmniVoice render. `tts_provider: espeak` means offline fallback, not OmniVoice. Never log API keys or persist smoke-test audio as production material.

Documented UpCloud deployment:

- service: `omnivoice.service`
- installation: `/opt/OmniVoice-Studio`
- bind: loopback port `3900`
- application: `/opt/manhwashorts`

Google worker deployment:

```bash
cp deploy/omnivoice.service ~/.config/systemd/user/omnivoice.service
systemctl --user daemon-reload
systemctl --user enable --now omnivoice.service
loginctl enable-linger "$USER"
curl -sS http://127.0.0.1:3900/health
```

The Google test deployment uses `~/omnivoice-official/.venv312`, Python 3.12, PyTorch CPU, and model `k2-fsa/OmniVoice`. Python 3.14 is unsupported by the available PyTorch wheels. The unit binds loopback only and processes one synthesis request at a time.

If the endpoint is unreachable, report the blocker. A Google test may use local espeak only when explicitly labelled as fallback; it does not validate OmniVoice quality.

