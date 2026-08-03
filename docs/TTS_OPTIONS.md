# TTS options

Voice-over remains optional. OmniVoice is an external experiment only: not a
ManhwaShorts dependency, provider default, or production gate.

## Project language rule

Every new render defaults to **English text** and **American English voice-over**.
The default voice profile is `the-explainer-american`; offline TTS uses `en-us`.
Indonesian remains an explicit per-project opt-in (`language: "id"`), never an
inferred or global default. Paid TTS must receive `en-US` plus the locked project
voice/settings.

## Recommendation

**First candidate: ElevenLabs.** Indonesian supported. Stable voice IDs,
`stability`, `similarity_boost`, voice cloning/design. Best candidate when
narrator identity matters. Commercial use requires checking the active paid-plan
terms.

**Second candidate: Gemini TTS.** Indonesian supported. Single/multi-speaker
output, 30 preset voices, natural-language control of style, accent, pace, and
tone. Good low-friction experiment. Preview API; no equivalent project-owned
cloned-voice workflow confirmed.

**Third candidate: OpenAI Speech.** Simple API, Indonesian input, fixed built-in
voice names, instruction-controlled delivery via `gpt-4o-mini-tts`. Voices are
currently optimized for English; no guaranteed custom narrator clone.

**Enterprise alternative: Google Cloud Text-to-Speech.** Stable named voice IDs
and Indonesian coverage. Chirp 3 Instant Custom Voice is separate and
eligibility-dependent. More setup; good operational stability.

## Comparison

| Option | Indonesian | Fixed voice ID | Clone/design | Consistency candidate | Project status |
|---|---:|---:|---:|---:|---|
| ElevenLabs | Yes | Yes | Yes | Highest | Adapter exists |
| Gemini TTS | Yes | Preset names | Not confirmed | Validate preview | No adapter |
| OpenAI Speech | Yes | Yes | Limited/varies | Good | Adapter exists |
| Google Cloud TTS | Yes | Yes | Eligibility-dependent | Good/enterprise | No adapter |
| Piper/Kokoro | Model-dependent | Local model | No | Deterministic | Not integrated |
| OmniVoice | Yes | Prompt/clone | Yes | External experiment only | Not in project |

## Voice continuity rules

1. Pick one provider before production.
2. Pin provider, model, voice ID/profile, language, speed, and voice controls.
3. Keep punctuation, number normalization, pronunciation dictionary, and text
   segmentation deterministic.
4. Cache each beat by content/settings hash; regenerate only changed beats.
5. Record provider, model, voice ID, settings hash, and generation timestamp in
   render metadata.
6. Never silently switch provider after a paid provider fails. Stop narration.
7. Test five lines in the selected project language; default test language is American English.
8. Use only self-recorded or explicitly authorized reference audio for cloning.

## Current implementation

The project already supports encrypted BYOK credentials per workspace:

- OpenAI Speech adapter
- ElevenLabs adapter
- Custom OpenAI-compatible `/audio/speech` adapter
- Offline espeak-ng fallback

Gemini TTS and Google Cloud TTS need dedicated adapters because their API shapes
differ from OpenAI Speech. Do not add either until one provider wins the
listening comparison and its commercial terms are accepted.

## Acceptance gate

A provider must pass the same five-line Indonesian test twice on separate
requests with no audible narrator-identity drift, valid audio for every line,
and acceptable cost, latency, license, and commercial-use terms.

## Candidate baselines

ElevenLabs:

```text
model: eleven_multilingual_v2
voice_id: one selected account voice
stability: 0.75
similarity_boost: 0.85
language: Indonesian
speed: fixed project value
```

OpenAI:

```text
model: gpt-4o-mini-tts
voice: one selected built-in voice
response_format: wav
speed: fixed project value
instructions: fixed Indonesian narrator direction
```

Gemini API shape differs from the current adapter. Exact model names and SDK
response handling must be checked against live docs before coding.

## Sources checked

- [Gemini speech generation](https://ai.google.dev/gemini-api/docs/speech-generation)
- [OpenAI text to speech](https://developers.openai.com/api/docs/guides/text-to-speech)
- [ElevenLabs TTS](https://elevenlabs.io/docs/overview/capabilities/text-to-speech)
- [Google Cloud supported voices](https://cloud.google.com/text-to-speech/docs/voices)
- [ManhwaShorts BYOK](BYOK.md)

Checked: 2026-08-04. Models, pricing, preview status, and terms can change.

## Scope decision

- OmniVoice: external exploration only; removed from the core project.
- OmniVoice-Studio: external dashboard only; not a project dependency.
- New local model: not added before a measurable quality win.
- Provider fallback chain: rejected; it breaks narrator identity.

## Next step

Compare ElevenLabs versus Gemini TTS with identical five-line American English
samples first; test Indonesian only when explicitly opting into `language: "id"`. Select one. Then add only the smallest adapter, one integration test,
one cached rights-safe fixture, and one real audio smoke test.

No provider is selected yet. Keep `MS_TTS_PROVIDER=espeak` for tests.

*ponytail: no provider abstraction for an unselected vendor; add one only after
the listening test picks a winner.*

## Security

Store keys through existing encrypted BYOK. Never put keys in commits, URLs,
logs, fixtures, screenshots, or Telegram messages.

## Rights

Reference audio requires documented authorization. Generated-audio rights depend
on provider terms and input rights.

## Operational default

Paid TTS runs only through verified workspace BYOK with explicit model and voice.
A paid provider failure stops narration; it never silently falls back to espeak.

## Metadata requirement

Future selected-provider work must record `provider`, `model`, `voice_id`,
`language`, `speed`, and a settings hash alongside the render job.

## Status

`research-only` — implementation intentionally deferred.

## Maintenance

Recheck provider docs, availability, pricing, and license before implementation
or publication.

## Final recommendation

Test ElevenLabs first. Gemini second. OpenAI third. Google Cloud for enterprise.

No provider selected.

## End

Core motion-comic stages remain provider-neutral.

