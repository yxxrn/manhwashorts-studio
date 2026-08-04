# P0 editorial pipeline

Current release contract for the CPU-only motion-comic pipeline.

## Order

1. Editorial script validation
2. Immutable voice profile
3. Spoken/display subtitle split
4. Panel quality and gutter-aware ingest
5. Deterministic scene-to-panel alignment
6. Perceptual repetition control and internal motion
7. Optional licensed ambience/music ducking
8. Blocking QC and machine-readable review artifacts

## Implemented gates

- Script requires hook, setup, escalation, insight, payoff/open loop, contextual CTA.
- Script stores editorial roles, evidence references, confidence, and fact/interpretation boundaries.
- TTS stores provider, model, voice ID, language, speed, sample rate, channels, seed/instruction, and SHA-256 profile hash.
- Provider failure never silently changes narrator.
- Caption display removes terminal periods; punctuation never becomes a karaoke token.
- Tall strips use projection/gutter heuristics; blank/connector-like assets are rejected.
- Panel catalog stores bbox, blank ratio, edge density, OCR placeholder, source family, decision.
- Shot list stores alignment score, reasons, rejected candidates, ROI, motion reason, visual signature.
- QC blocks voice drift, mixed formats, weak alignment, dominant backgrounds, A-B-A-B loops, excessive static runs, subtitle errors, decode errors, drift, black frames, and missing QC reports.

## Review artifacts

Every completed render writes beside the video:

```text
final.qc.json
shot_list.json
panel_catalog.json
panel_to_script_mapping.json
subtitle_list.json
source_rights_report.json
contact_sheet.jpg
```

`publish_allowed` is false when rights are undeclared or marked test-only. Internal sample renders remain review artifacts, never publication proof.

## Font

Default repository font:

```text
assets/fonts/BarberChop.otf
```

Override with `MS_SUBTITLE_FONT`. Keep the font family name in `MS_SUBTITLE_FONT_NAME`.

## Limits

- Ubuntu CPU-only supported.
- No image-to-video, diffusion, or external video generation.
- CV semantics remain heuristic CPU analysis. Low-confidence panel choices stay visible in shot metadata and require review.
