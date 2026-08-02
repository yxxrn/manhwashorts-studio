# Content-aware panel selection

ManhwaShorts no longer assigns panels by `order_index` alone. The visual pipeline
is explicitly layered:

```text
Panels → ROI Detection → Shot Director → Camera Planner → Renderer
```

Panel analysis provides evidence. ROI Detection ranks regions. The Shot Director
owns editorial decisions. Camera Planner executes approved camera curves only.

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

`app.services.roi_detection` ranks the strongest detected focal regions. Multiple
regions can produce multiple shots from one panel before the Shot Director switches
panels. `plan_content_aware_scenes()` delegates editorial scheduling to the Shot
Director, which decides ROI order, 1.25–3 second durations, cut points, panel
switches, anticipation, visual lead/follow timing, and camera intent/curve. The
persisted plan carries `roi_label`, `focus_end_x/y`, `camera_intent`,
`narration_timing`, and `camera_curve`.

## Shot Director

`app.services.shot_director` is the editorial layer between ROI Detection and the
Camera Planner. It does not inspect pixels or add a vision dependency. It answers:

- which ROI leads each shot (`face`, `weapon`, `opponent`, `effect`, `detail`),
- whether the same panel should continue while another ROI is available,
- which camera intent/curve fits the narration (`slow_push_in`, `focus_shift`,
  `punch_zoom`, `impact_shake`, `dramatic_zoom_out`, `orbit`),
- whether visuals lead, sync, or follow narration,
- whether the next dramatic beat should lead by a short anticipation cut.

`app.services.camera_planner` executes the selected curve. It has no editorial
fallback: unsupported curves fail fast instead of silently changing the director's
choice.

The renderer interpolates the start and end ROI using smoothstep crop coordinates;
integer-pixel rounding stays in place to avoid shimmer. Every shot remains an
ordinary `TimelineScene`, so manual edits and the existing quality gate continue
to work.

This is deliberately a deterministic editorial ceiling. A future director can
replace ROI ranking or scheduling without changing `VisualFeatures`, the DB scene
contract, or FFmpeg.

Regression coverage: `tests/test_visual_scoring.py` covers ROI exhaustion, motion
diversity, anticipation, and semantic action curves.

## Deliberate ceiling

This is a deterministic CPU heuristic, not a general vision-language model. It
avoids adding a heavy dependency or hallucinating panel semantics. Upgrade path:
attach a local vision encoder to `VisualFeatures`, preserving the scorer and
planner APIs. Human review remains the final authority for rights, continuity,
and publish decisions.

Regression coverage: `tests/test_visual_scoring.py` checks pixel analysis,
semantic selection, repetition penalty, camera mapping, and tunable weights.
