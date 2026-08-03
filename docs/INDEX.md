# Documentation index

ManhwaShorts Studio is a review-first, rights-aware motion-comic renderer.

## Start

| Need | Document |
|---|---|
| Understand the product | [README](../README.md) |
| Know what is actually done | [Current status](STATUS.md) |
| Run a render | [Operations](OPERATIONS.md) |
| Prepare a release | [Release runbook](RELEASE_RUNBOOK.md) |
| Understand the pipeline | [Architecture](ARCHITECTURE.md) |
| Inspect motion/QC rules | [Motion-comic pipeline](MOTION_COMIC.md) |

## Product and safety

- [Copyright and rights handling](COPYRIGHT.md)
- [TTS options and voice consistency](TTS_OPTIONS.md)
- [BYOK provider keys](BYOK.md)
- [UI/accessibility](UI.md)
- [Visual selection](VISUAL_SELECTION.md)
- [YouTube setup](YOUTUBE_SETUP.md)

## Engineering

- [API reference](API.md)
- [AI-agent operation](AGENT.md)
- [GPU encoding](GPU.md)
- [Changelog](../CHANGELOG.md)

## Documentation policy

- `STATUS.md` is the source of truth for implementation state.
- `RELEASE_RUNBOOK.md` is the source of truth for release checks.
- `ARCHITECTURE.md` describes the current code, not the original plan.
- Historical requirements do not live in the active docs tree. Update current
  documents instead of adding another planning file.
- Examples using third-party panels, fonts, voices, or models are review-only
  unless their rights are explicitly verified.
