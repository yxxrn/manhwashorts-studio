# Content-aware panel selection

ManhwaShorts no longer assigns panels by `order_index` alone. The timeline stage
runs each detected image through `app.services.visual_scoring` before selecting
shots.

## Score model

`analyze_panel()` returns normalized 0..1 features and a weighted `visual_score`.
Positive features:

| Feature | Why it matters |
|---|---|
| `face_visibility` | Faces create an immediate human attention anchor. |
| `facial_expression` | Emotion communicates story state without reading dialogue. |
| `action_pose` | Strong diagonals and body movement imply an event. |
| `weapons` / `monsters` | High-salience story objects match recap narration. |
| `visual_effects` / `motion_lines` | Effects and speed lines signal energy. |
| `impact_frame` | Peak moments are stronger retention beats. |
| `close_up` | A readable face/object beats a distant scene when semantics match. |
| `dramatic_composition` | Contrast, edges, and focal placement improve cinematic framing. |
| `object_density` | Information-rich panels carry more story than empty art. |

Penalties:

| Penalty | Why it matters |
|---|---|
| `empty_background` | Sky, floor, walls, and blank gutters waste screen time. |
| `scenery_only` | Scenery is retained only when narration is location-focused. |
| `transition` | Flat low-variance separators are weak shots. |
| repeated candidate | Prevents a long run of nearly identical panels. |

The default weights live in `PanelScoreWeights`. Use `tune_weights()` for an
experiment without changing planner architecture.

## Selection

`select_panel()` computes:

```text
visual_score + semantic_score + small_continuity_bonus - repeat_penalty
```

Continuity is deliberately small. A nearby panel can replace a weak chronological
panel when its visual/semantic score is materially stronger. `semantic_score()`
uses lightweight narration tags (`action`, `reveal`, `explosion`, `thinking`,
`weapon`, `monster`, `dialogue`) to align panel content with the spoken beat.

OCR and OpenCV face detection are optional. Pillow edge/texture analysis always
runs; missing optional tooling never blocks a render. This keeps the UpCloud CPU
path dependency-light. The feature schema is the adapter boundary for a future
local vision encoder.

## Camera planning

`camera_effect()` maps narration tags to shot behavior:

```text
 dialogue   -> kenburns_in
 thinking   -> pan_left
 reveal     -> push_up
 action     -> punch_zoom
 explosion  -> shake_zoom
 fallback   -> alternating kenburns/pan/push
```

`planned_focus()` chooses the strongest detected focal region. Multiple focal
regions can therefore produce multiple shots from one panel before switching to
the next asset. `plan_content_aware_scenes()` handles this while splitting long
narration beats into 2–6 second shots.

## Deliberate ceiling

This is a deterministic CPU heuristic, not a general vision-language model. It
avoids adding a heavy dependency or hallucinating panel semantics. Upgrade path:
attach a local vision encoder to `VisualFeatures`, preserving the scorer and
planner APIs. Human review remains the final authority for rights, continuity,
and publish decisions.

Regression coverage: `tests/test_visual_scoring.py` checks pixel analysis,
semantic selection, repetition penalty, camera mapping, and tunable weights.
