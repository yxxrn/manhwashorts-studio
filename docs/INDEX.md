# Documentation index

ManhwaShorts Studio documentation is split into **current operational truth** and
**historical evidence**. Agents must not treat old handoffs as current commands.

## Start here

| Need | Authoritative document |
|---|---|
| Agent coding rules | [AGENTS.md](../AGENTS.md) |
| Current verified state | [Current status](STATUS.md) |
| Current module boundaries | [Architecture](ARCHITECTURE.md) |
| Extend/refactor safely | [Maintainer guide](MAINTAINER_GUIDE.md) |
| Fresh-machine install/upgrade | [Fresh-machine setup](FRESH_MACHINE.md) |
| Operate the service | [Operations](OPERATIONS.md) |
| Release/verification gate | [Release runbook](RELEASE_RUNBOOK.md) |
| Drive the HTTP API | [Agent API guide](AGENT.md) |

## Product/reference docs

- [Motion-comic pipeline](MOTION_COMIC.md)
- [Visual selection](VISUAL_SELECTION.md)
- [API reference](API.md)
- [TTS options](TTS_OPTIONS.md)
- [BYOK provider keys](BYOK.md)
- [Copyright/rights metadata](COPYRIGHT.md)
- [GPU encoding](GPU.md)
- [UI](UI.md)
- [YouTube setup](YOUTUBE_SETUP.md)

## Historical evidence

`docs/history/`, `docs/superpowers/`, `tasks/`, and documents explicitly marked
HISTORICAL preserve incident reports, benchmarks, old test paths, and old product
assumptions. They are useful for forensic context only.

## Documentation rules

- `STATUS.md` describes current verified state, not a chronological scratchpad.
- `ARCHITECTURE.md` describes current code, not the original design.
- `AGENTS.md` and `MAINTAINER_GUIDE.md` define safe modification boundaries.
- Update active docs in the same change when module paths, gates, tests, or output
  contracts change.
- If historical text conflicts with current code/docs, current code plus the active
  contract documents win; investigate before changing behavior.
