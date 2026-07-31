# Changelog

Notable changes per release. Dates are ISO 8601.

## [1.1.0] — 2026-08-01

### Added — BYOK (bring your own key)

Use your own API keys for the AI stages instead of relying on the server's
environment variables. The model list is fetched **from your key**, so the
choices offered are exactly what that key can reach.

- **New `provider_credentials` table** storing one credential per
  (workspace, capability, provider). Keys are Fernet-encrypted before they touch
  the database; the column holds a `gAAAAA…` token, never the key.
- **Two capabilities**: `llm` (chapter analysis, highlights, beats, script
  rewriting) and `tts` (voice narration). Split by capability rather than vendor,
  because one vendor can serve both roles and one role has many vendors.
- **13 providers.** LLM: OpenAI, Anthropic, Google AI Studio, OpenRouter, Groq,
  DeepSeek, Mistral, Together AI, xAI, and any OpenAI-compatible endpoint
  (Ollama, LM Studio, vLLM, LiteLLM). TTS: OpenAI Speech, ElevenLabs, and any
  `/audio/speech` endpoint.
- **Model discovery + verification in one step.** Saving a key calls the
  provider's model endpoint first. A key that cannot list models is rejected with
  `400` instead of being stored to fail later mid-render.
- **10 new endpoints** under `/api/credentials` — catalogue, test-without-saving,
  save, list, active-resolution, refresh, model select, set default, delete.
- **New settings panel in the UI** with provider picker, key field, endpoint
  override, a "test & fetch models" button, and per-credential model switching.
- **`GET /api/credentials/active`** reports which provider each stage will really
  use and why, so you are never guessing whether a render hits your paid key or
  the offline engine.
- **Alembic migration `f139cbb1f257`**, purely additive. Verified on a populated
  v1.0 database: existing rows survive, and downgrade removes only the new table.
- **`docs/BYOK.md`** covering setup, self-hosted endpoints, the security model,
  failure behaviour, cost control, and troubleshooting.
- **`tests/mock_provider.py`** — a local stand-in speaking the OpenAI wire
  format, so the BYOK suite runs with no network, no real key, and no spend.

### Changed

- Provider selection now resolves through `app/services/resolver.py` in a fixed
  order: **verified BYOK credential → environment config → offline engine**. The
  offline path is unchanged and still covered by tests, so an install with no
  keys behaves exactly as it did in v1.0.
- `run_analysis` and `generate_voiceover` record the provider source, vendor, and
  model in the audit log. The key itself is never recorded.
- Analysis results carry a note stating which engine produced them, visible in
  the UI, so a fallback is never silent.
- `analysis.py`: response parsing extracted into `parse_llm_json`, shared by the
  env-configured and BYOK analysers. It now tolerates ` ```json ` fences that
  several models emit despite being told not to, and validates every field.

### Fixed

- **Audit rows written during a credential operation could look missing.** The
  session runs with `autoflush=False`, so an added-but-unflushed `AuditLog` row
  was invisible to a later query in the same transaction. Credential auditing now
  flushes explicitly.

### Security

- Stored keys are never returned by any endpoint. Responses expose `key_hint`
  (last four characters) only, enough to tell two keys apart.
- Provider error text is scrubbed before it is surfaced, in case a vendor echoes
  the submitted key back in an error body.
- User-supplied endpoints are restricted to `http`/`https`, so `file://` and
  similar cannot be dialled by the server.
- Every credential route is workspace-scoped; another account gets a `400`, never
  data.
- Deleting a credential removes the row and its ciphertext rather than flagging it
  inactive.
- A test opens the SQLite file directly and asserts the plaintext key is not
  present anywhere in it.

**Known limitation, stated plainly:** anyone who can read both
`data/manhwashorts.db` and `data/.fernet_key` can decrypt the stored keys, and
both live in `data/` by default. This keeps a single-user local install simple; it
is not a secrets manager. See `docs/BYOK.md` for mitigations.

### Behaviour worth knowing

- **Analysis** degrades to the offline analyser if a provider call fails, and says
  so in the notes. A failed API call costs a weaker analysis, not a dead pipeline.
- **Narration** raises instead of degrading. You chose to pay for a specific
  voice; quietly substituting robotic espeak audio into a video you are about to
  publish would be the worse outcome.
- **Models are never substituted.** Requesting a model the key does not offer is
  an error, not a silent swap to something else that also bills you.
- A credential with no model selected counts as not configured, so the pipeline
  uses the offline engine rather than guessing.

### Tests

134 passing (was 94). 40 new BYOK tests covering adapters, storage, resolution
order, generation through a user key, the HTTP surface, and the security claims
above.

---

## [1.0.0] — 2026-07-31

First working release. Generates YouTube Shorts recapping manhwa chapters from
material you have the right to use.

### Added

- **Pipeline**: ingest → analysis → script → voice-over → timeline → subtitles →
  quality gate → render → publish.
- **Rendering**: 1080×1920 H.264 + AAC with Ken Burns motion and burned-in
  captions, produced by FFmpeg. Video duration tracks narration exactly, because
  audio is treated as the clock rather than the other way round.
- **Rights gate**: assets need an owner and a licence basis, not just a ticked
  box. Narration that is ≥50% verbatim from the source is refused.
- **Human-in-the-loop publishing**: public upload needs config opt-in *and*
  per-request confirmation, and the rendered file is checksummed again
  immediately before upload.
- **Quality gate** with blocking errors and overridable warnings; an override
  requires a recorded reason and actor.
- **Offline by default**: espeak-ng narration, rule-based summarising, SQLite,
  filesystem storage, and a dry-run YouTube provider that writes a local receipt
  instead of uploading. LLM, HTTP TTS, and real OAuth are opt-in.
- Web UI with no build step, standalone render worker, Alembic baseline, seed
  script, and full documentation.

### Notable fixes during development

- `ffmpeg -t` used as an **input** option before `zoompan` multiplied output
  length roughly 100×: a 4-second scene rendered 400 seconds of video. Switched to
  `-frames:v`; per-scene render went from 258s to 4.1s.
- Pre-scaling before `zoompan` pushed frames to ~8 MP for no benefit.
- Word timings are clip-relative and must be shifted onto the master timeline,
  otherwise every subtitle cue restarted at zero and overlapped.
- Scenes must absorb inter-beat silence or `-shortest` clips the final line.
- The rules script generator was extractive (60% verbatim) and its own policy gate
  correctly refused to render it. The generator now summarises.
- The session signing key was regenerated per process, logging everyone out on
  restart. It is now persisted to `data/.secret_key`.
- Lazy SQLAlchemy relationships are cached per session, so rows written earlier in
  the same transaction were invisible to later pipeline stages.

[1.1.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.1.0
[1.0.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.0.0
