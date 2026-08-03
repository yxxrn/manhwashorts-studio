# Motion Comic Pipeline

Status: **development / test-only**. Production publication blocked by source rights.

## Deployment boundary

The UpCloud VPS is the execution host. The local machine is an orchestrator only.

```text
local machine
  ├── source code
  ├── tests/docs/config templates
  └── SSH/API orchestration
       │
       └── UpCloud VPS
            ├── database
            ├── source assets
            ├── TTS
            ├── FFmpeg/Pillow/OpenCV
            ├── scratch renders
            └── final outputs
```

Do not persist generated media, project storage, audio, or render outputs in the
local checkout. Upload source panels directly to the VPS, render there, and fetch
only the requested preview/final artifact. Local `data/` is for tests only and
should be removed or kept outside the working tree after a test run.

## Product goal

Motion comic editorial berbasis retention. Panel asli tetap utuh. Kamera, framing, compositing, typography, transition, dan procedural effect boleh berubah; isi panel tidak boleh diubah.

Constraints:

- CPU-only Ubuntu target: 12 vCPU, 24 GB RAM, no GPU requirement.
- No generative AI, diffusion, image-to-video, external video API, paid render service, or internet asset at render time.
- Deterministic output for identical input + seed.
- Audio is master clock.
- Default narration: English, The Explainer no. 4, speed 0.90.
- Output: 1080x1920, 30 FPS, H.264, AAC.
- Target duration: 60–90s; ideal 70–85s; absolute max 90s.

## Implemented architecture

### Motion Director

`app/services/motion_director.py` creates a deterministic `MotionPlan` from:

```text
section
narration tags
ROI/focus target
duration
previous motion history
seed
```

Supported modes:

```text
hold
slow_push
slow_pull
guided_pan
focus_shift
panel_reveal
split_focus
panel_stack
impact
whip_transition
atmospheric
static_emphasis
```

Every shot persists:

```text
asset_id
source_family
roi_label
motion_mode
motion_reason
camera_curve
transition
start_time
end_time
```

Director rules:

- ROI list is a possibility list, not a visitation mandate.
- Static is valid when movement adds no information.
- Action uses movement only at meaningful beats.
- Strong effects cannot occur consecutively.
- No more than two same-asset shots when an alternative exists.
- Source-family alternatives are preferred after cooldown.
- Same framing is measured by asset + focus coordinates, not camera label alone.

### Source-family grouping

`PanelCandidate.source_family` currently uses deterministic strip grouping from source order:

```text
strip-{order_index // 8}
```

Cooldown preference:

1. different asset;
2. different source family;
3. same family only when no usable alternative exists.

This is an editorial heuristic, not a panel scoring change.

### Renderer

CPU renderer uses Pillow + FFmpeg:

- safe vertical crop;
- focus-to-focus camera motion;
- split-focus composition;
- panel-stack composition;
- blurred enlarged background;
- atmospheric desaturation/vignette;
- impact contrast accent;
- static-emphasis vignette;
- deterministic prepared-image cache;
- hard-cut concat fallback when transition graph fails;
- exact audio mux to video-master duration.

Scene-render failure fails the job. Concat graph failure may use hard-cut fallback. No partial render is presented as final.

### Render profiles

`POST /api/projects/{project_id}/render`:

```json
{"kind":"final","encoder":"cpu","profile":"Balanced"}
```

Profiles:

- `Auto`: execute Director plan.
- `Calm`: static/hold except impact or explosion.
- `Balanced`: Director plan unchanged.
- `Dynamic`: action/attack gets at least guided movement.
- `No motion`: static hold.

UI exposes all five profiles.

### Reliability

- Test environment is set before pytest collection.
- Test DB lives under `data/test_runs/`.
- `safe_drop_all()` rejects non-test targets.
- Render claim is atomic.
- Render jobs carry lease token, lease expiry, and heartbeat.
- Worker calls `recover_stale_jobs()` before queue polling.
- Stale jobs return to queue; completed jobs cannot be reclaimed.
- Scratch work is per render job.

### QC and policy

QC report is persisted to `final.qc.json` and blocks status `READY` when it fails.

Blocking checks:

```text
duration outside 60–90s
average shot outside 1.2–2.4s
same panel/same crop >2.5s
single-word caption ratio >=15%
invalid motion mode
consecutive impact
missing motion reason
freeze/playback failure
audio/video drift > one frame
black frame >0.4s
source/rights failure
```

Media integrity fields:

```text
audio_video_drift
black_frame_duration
full_playback_verified
```

`NOT_FOR_PUBLICATION` is a hard test-only gate:

```text
rights_confidence=0
source_cleanliness=0
publish_allowed=false
status=REVIEW
```

### Immutable QC overrides

Warning override endpoint:

```text
POST /api/projects/{project_id}/quality/override
GET  /api/projects/{project_id}/quality/overrides
```

Each override appends a `qc_override_events` row containing actor, reason, timestamp, code, and before/after state. Existing events are never deleted by QC refresh.

## Current verified sample

Latest Balanced test render:

```text
URL: https://n.uguu.se/JyZDoAPt.mp4
Duration: 60.833s
Resolution: 1080x1920
FPS: 30
Video: H.264
Audio: AAC
Average shot: 1.789s
Longest static: 0.767s
Same crop max: 1.855s
Unique crops: 20
Audio/video drift: 0.0003s
Black frame: 0.0s
Decode: passed
```

Current sample remains test-only because the Catbox fixture contains a third-party watermark/banner.

Benchmark artifact:

```text
ffmpeg decode probe: 1.101s
output size: 22,187,746 bytes
```

Benchmark table, identical synthetic source/seed:

| Path | Duration | Resolution | Codec | Decode | Notes |
|---|---:|---|---|---|---|
| Balanced reference | 60.833s | 1080x1920 | H.264/AAC | passed | 20 unique crops, 0.0003s drift |
| Split-focus/panel-stack fixture | 2.000s | 360x640 | H.264/AAC | passed | exact duration, no black frame |
| Broken QC fixture | 1.000s | 64x64 | H.264/AAC | blocked | deliberate 0.5s audio drift + black frame |
| Clean synthetic demo | 60.433s | 1080x1920 | H.264/AAC | passed | QC pass, 0.0003s drift, 0.0s black frame |

## Ordered implementation plan — remaining work

Do not skip stages. Each stage requires focused tests before the next stage.

### Stage 1 — UI audit visibility

- [x] Add read-only QC override history panel to UI.
- [x] Show actor, reason, code, before/after, timestamp.
- [x] Show motion mode, ROI, reason, transition, and timing in timeline UI.
- [x] Show render profile and actual encoder in render history.
- [x] Add frontend smoke test for profile submission and override-history loading.

### Stage 2 — Real source-family metadata

- [x] Replace order-only `strip-{n}` heuristic with persisted family metadata from ingest.
- [x] Derive family from archive path/page sequence when available.
- [x] Preserve manual family override without changing panel score.
- [x] Add migration and ingest regression tests.
- [x] Include family in `shot_list.json` and QC report.

### Stage 3 — Audio-driven editorial timing

- [x] Persist word-level dramatic event timing.
- [x] Cut 100–300 ms before event words when it improves anticipation.
- [x] Lock impact frames to attack/explosion words.
- [x] Validate English/Indonesian tag consistency per project.
- [x] Add unintended code-switch QC failure.
- [x] Add timing contract tests against synthetic narration.

### Stage 4 — Effect renderer expansion

- [x] Implement deterministic low/medium/high intensity for atmospheric effects.
- [x] Add optional local speed lines, dust, smoke/fog, rain, embers, glow, flash.
- [x] Keep face, hands, weapons, speech bubbles, and subtitle safe area clear.
- [x] Add per-effect disable flag and safe fallback.
- [x] Add pixel-level tests proving effect presence and panel geometry preservation.

### Stage 5 — Real split-focus/panel-stack validation

- [x] Add synthetic-panel fixtures with two known ROIs.
- [x] Assert both ROIs appear in expected regions.
- [x] Assert no panel deformation.
- [x] Assert subtitle safe area remains clear.
- [x] Add slow FFmpeg test with H.264/AAC, exact duration, no black frame.

### Stage 6 — Final QC enforcement

- [x] Add black-frame and drift tests using deliberately broken synthetic media.
- [x] Add QC blocking test proving failed QC cannot become `READY`.
- [x] Add immutable override-history API integration test.
- [x] Add override audit UI test.
- [x] Persist QC history snapshots instead of only the latest report.

### Stage 7 — Worker and resource reliability

- [x] Add lease heartbeat integration test with a real worker loop.
- [x] Add stale-job recovery test across process restart.
- [x] Measure per-stage CPU time, peak RSS, scratch size, and render wall time.
- [x] Resolve encoder once per job and record resource metrics.
- [x] Add cleanup test for failed scratch directories.

### Stage 8 — Documentation and release gate

- [x] Add changelog entry for the complete motion-comic pipeline.
- [x] Add operator runbook for source rights, test-only fixtures, and QC overrides.
- [x] Add benchmark before/after table from identical source/seed.
- [x] Run Ruff, compileall, fast suite, slow FFmpeg suite, and diff check.
- [x] Render one clean-source sample.
- [x] Playback-check clean-source sample.
- [ ] Supply a real clean source plus verified rights declaration.
- [ ] Only then consider production readiness.

## Verification commands

```bash
.venv/bin/ruff check .
.venv/bin/python -m compileall -q app tests scripts
unset MS_DATABASE_URL
.venv/bin/python -m pytest -q
```

No YouTube upload is performed by the pipeline. Never remove, blur, hide, crop-hide, or inpaint source watermarks.

## Current blocker

The former Catbox fixture is explicitly `NOT_FOR_PUBLICATION` and contains a third-party watermark/banner. The synthetic rights-safe sample now renders, decodes, and passes QC. Production publication still requires a real clean source with a rights declaration; synthetic fixtures are not evidence of third-party source rights.

Next resume point: **Stage 8 — production-rights gate**.
