# Codex Manual Vision Preview 50-60 Second Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a new 50-60 second silent review from the same fully reviewed 24-panel sample chapter while preserving balloon-free close crops, punctuation-free English captions, stable motion, honest manual-vision provenance, and review-only rights status.

**Architecture:** Extract the already-proven temporary renderer into one generic committed review CLI that consumes a versioned ignored edit-plan JSON. Keep sample images, crop coordinates, databases, rendered media, and manual evidence under ignored `data/`; commit only the generic tool, tests, and truthful documentation. Render on the authoritative VPS, audit three frames per shot, then publish one atomic code/test commit and one evidence-only documentation commit.

**Tech Stack:** Python 3.11, Pillow, FFmpeg/FFprobe, pytest, Ruff, Git, SFTP transport.

## Global Constraints

- Authoritative checkout is `/home/yusronrohmani/manhwashorts` through SSH alias `google`.
- Baseline and rollback commit is `c68e839ed83a2e434c29005d9a4cd6753f266529`.
- The existing accepted preview remains immutable at `data/codex-vision-preview-20260811/`.
- Start from the preserved renderer `data/codex-vision-preview-20260811/render_codex_vision_preview.py`, SHA-256 `c5efe905f0729ff2bcc406af85e0efd462587efddaff9c63c74c03962a6f145a`.
- Read all source orders `0..23`; source order `0` is the title page and is documented but excluded from the timeline.
- Timeline uses source orders `1..23` exactly once in chronological order. Do not add random selection or duplicate panels merely to extend duration.
- Final duration must be at least `50.000` and at most `60.000` seconds; target `54.2` seconds before encoder rounding.
- Keep English captions uppercase and punctuation-free. Spoken voice and all audio generation remain deferred.
- No visible speech balloon, balloon tail, narration box, or edge-connected blank padding may appear at shot start, midpoint, or end.
- Blank/padding is color-agnostic; black or colored art is not blank merely because of its color.
- Prefer a tighter crop over visible whitespace, but retain the face, action, effect, or continuity subject named by the shot.
- Motion is monotonic and unidirectional within each shot. No reversal, oscillation, random motion, or zoom shake.
- Hard cuts only for this preview. Do not add crossfades, interpolation, music, SFX, or voice.
- `publish_allowed=false`; source rights remain `internal review only`.
- Provenance is `codex_manual_vision_review_v2`, never provider-generated StoryAnalysis/PanelRegion evidence.
- No media, source images, database, credentials, `.env`, or runtime edit-plan enters Git.
- All tests and renders run on VPS. Windows is exact-object transport and GitHub push only.
- Push only `main`, fast-forward only. No force push, tags, `--all`, or unrelated remote writes.

---

## Baseline Evidence

- Current preview: `data/codex-vision-preview-20260811/codex-vision-preview-silent.mp4`
- Current duration: `36.033333` seconds
- Current dimensions/rate: `1080x1920`, H.264, `30/1` FPS, video-only
- Current SHA-256: `2392a66cca39086cd69e0654a496a4ef1672b3025a7966518d885b4013b83ee9`
- Current full-panel review: six ordered contact sheets covering source orders `0..23`
- Current timeline: 23 chronological shots using source orders `1..23`
- Current manual ledger: `data/codex-vision-preview-20260811/codex-manual-vision-review.json`
- Current Git state at plan authoring: VPS, Windows transport, and GitHub `main` all resolve to `c68e839ed83a2e434c29005d9a4cd6753f266529`; VPS tracked/unignored status is clean. The entire VPS `data/` directory is ignored and is not present on GitHub.

## File Structure

- Create: `scripts/review/render_codex_manual_preview.py` — generic deterministic manual-review renderer and edit-plan validator.
- Create: `tests/test_codex_manual_preview.py` — pure validation and timing tests; no FFmpeg or real media required.
- Create runtime only: `data/codex-vision-preview-50-60s-v2/edit-plan.json` — sample-specific crop, duration, caption, and provenance inputs; ignored by Git.
- Create runtime only: `data/codex-vision-preview-50-60s-v2/` render artifacts and audit frames; ignored by Git.
- Modify after successful render: `docs/STATUS.md` and `CHANGELOG.md` — exact evidence, hashes, QC, limitations, next action.

## Exact Duration Schedule

The edit plan must use these durations in source-order sequence `1..23`:

```json
[1.8, 2.2, 2.4, 2.4, 2.6, 2.4, 2.5, 2.5, 1.9, 2.1, 1.8, 2.1, 2.0, 1.9, 2.3, 2.4, 2.2, 2.6, 2.7, 2.8, 2.8, 2.6, 3.2]
```

The sum is exactly `54.2` seconds. Do not extend duration by freezing the final frame, repeating a panel, or slowing the whole assembled MP4.

## Exact Caption Schedule

Caption text contains only `A-Z`, digits, and spaces. Shot intervals use zero-based half-open indices `[start_shot, end_shot)`:

```json
[
  {"start_shot": 0, "end_shot": 2, "text": "THE BATTLEFIELD IS ALREADY COLLAPSING"},
  {"start_shot": 2, "end_shot": 4, "text": "YET HE STILL REFUSES TO RUN"},
  {"start_shot": 4, "end_shot": 6, "text": "THE GODDESS THINKS HE IS CORNERED"},
  {"start_shot": 6, "end_shot": 8, "text": "BUT HE HAS ALREADY SEEN THE TRAP"},
  {"start_shot": 8, "end_shot": 10, "text": "THEN HER ATTACK FINALLY BEGINS"},
  {"start_shot": 10, "end_shot": 12, "text": "SHE THROWS EVERYTHING INTO ONE STRIKE"},
  {"start_shot": 12, "end_shot": 14, "text": "THE ENEMY ANSWERS WITH BRUTE FORCE"},
  {"start_shot": 14, "end_shot": 16, "text": "AND EVEN THAT IS NOT ENOUGH"},
  {"start_shot": 16, "end_shot": 18, "text": "SO HE GETS EVERYONE OUT ALIVE"},
  {"start_shot": 18, "end_shot": 20, "text": "ELSEWHERE THE OTHERS CAN ONLY WAIT"},
  {"start_shot": 20, "end_shot": 22, "text": "ONE MAN SEEMS FAR TOO CALM"},
  {"start_shot": 22, "end_shot": 23, "text": "BECAUSE PATIENCE IS PART OF HIS PLAN"}
]
```

## Task 1: Extract a Reusable Review Renderer

**Files:**
- Create: `scripts/review/render_codex_manual_preview.py`
- Create: `tests/test_codex_manual_preview.py`

**Interfaces:**
- Consumes: `--manifest PATH`, `--plan PATH`, `--output-dir PATH`, and optional `--validate-only`.
- Produces: `validate_edit_plan(plan: Mapping[str, object], manifest: Mapping[str, object]) -> ValidatedPlan` plus deterministic media/audit sidecars when not validating only.

- [ ] **Step 1: Write collection-clean failing validation tests**

Create tests that import the new script module by file path and assert:

```python
def test_accepts_exact_542_second_chronological_plan():
    validated = module.validate_edit_plan(valid_plan(), manifest())
    assert validated.total_duration == pytest.approx(54.2)
    assert [shot.source_order for shot in validated.shots] == list(range(1, 24))

def test_rejects_duration_outside_50_to_60_seconds():
    plan = valid_plan()
    plan["shots"][0]["duration"] = 10.0
    with pytest.raises(ValueError, match="preview.duration_out_of_range"):
        module.validate_edit_plan(plan, manifest())

def test_rejects_random_duplicate_or_missing_source_order():
    plan = valid_plan()
    plan["shots"][3]["source_order"] = 2
    with pytest.raises(ValueError, match="preview.source_order_coverage_invalid"):
        module.validate_edit_plan(plan, manifest())

def test_rejects_caption_punctuation():
    plan = valid_plan()
    plan["captions"][0]["text"] = "RUN NOW!"
    with pytest.raises(ValueError, match="preview.caption_contract_invalid"):
        module.validate_edit_plan(plan, manifest())

def test_rejects_invalid_normalized_crop():
    plan = valid_plan()
    plan["shots"][0]["crop"] = [0.4, 0.2, 0.3, 0.8]
    with pytest.raises(ValueError, match="preview.crop_invalid"):
        module.validate_edit_plan(plan, manifest())
```

- [ ] **Step 2: Run RED on VPS**

```bash
cd /home/yusronrohmani/manhwashorts
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_codex_manual_preview.py -q
```

Expected: collection succeeds and test bodies fail because the module or `validate_edit_plan` boundary is absent. Import/setup failures are not acceptable RED evidence.

- [ ] **Step 3: Implement the minimal generic boundary**

Use frozen dataclasses `ValidatedShot`, `ValidatedCaption`, and `ValidatedPlan`. Validation must enforce:

```python
EXPECTED_SOURCE_ORDERS = tuple(range(1, 24))
MIN_DURATION_SECONDS = 50.0
MAX_DURATION_SECONDS = 60.0
CAPTION_PATTERN = re.compile(r"[A-Z0-9]+(?: [A-Z0-9]+)*\Z")
```

The renderer must read crop coordinates and durations from the plan, use Pillow `ImageOps.fit(..., (1296, 2304))`, render each shot at 30 FPS, and concatenate with hard cuts. Motion must use a single linear crop direction per shot with a maximum displacement of 48 prepared-image pixels and no random module calls.

- [ ] **Step 4: Run GREEN and static checks**

```bash
cd /home/yusronrohmani/manhwashorts
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_codex_manual_preview.py -q
.venv/bin/ruff check scripts/review/render_codex_manual_preview.py tests/test_codex_manual_preview.py
.venv/bin/python -m compileall -q scripts/review/render_codex_manual_preview.py
git diff --check
```

Expected: all focused tests pass, Ruff exits `0`, compile exits `0`, and diff check is empty.

- [ ] **Step 5: Commit and publish the reusable tool**

```bash
git add -- scripts/review/render_codex_manual_preview.py tests/test_codex_manual_preview.py
git diff --cached --check
git commit -m "feat: add deterministic manual vision preview renderer"
```

Publish the exact commit object through a clean Windows transport clone, push only `main`, then verify VPS and GitHub SHA parity before Task 2.

## Task 2: Build the 54.2 Second Sample Edit Plan

**Files:**
- Create runtime only: `data/codex-vision-preview-50-60s-v2/edit-plan.json`
- Read: `data/codex-vision-preview-20260811/codex-manual-vision-review.json`
- Read: `data/codex-vision-preview-20260811/render_codex_vision_preview.py`

**Interfaces:**
- Consumes: the 23 accepted v1 manual crops, exact duration schedule, and exact caption schedule in this plan.
- Produces: a validated v2 edit-plan with `provenance=codex_manual_vision_review_v2` and no Git-tracked runtime data.

- [ ] **Step 1: Create a new output directory without changing v1**

```bash
cd /home/yusronrohmani/manhwashorts
mkdir -p data/codex-vision-preview-50-60s-v2
test -f data/codex-vision-preview-20260811/codex-vision-preview-silent.mp4
```

- [ ] **Step 2: Materialize `edit-plan.json`**

Copy the 23 normalized crops from the v1 ledger in chronological order. Pair them with the exact durations and caption schedule above. Required top-level fields are:

```json
{
  "contract_version": "codex_manual_vision_review_v2",
  "random_sampling": false,
  "publish_allowed": false,
  "rights_status": "internal review only",
  "fps": 30,
  "width": 1080,
  "height": 1920,
  "shots": [],
  "captions": []
}
```

- [ ] **Step 3: Validate without rendering**

```bash
.venv/bin/python scripts/review/render_codex_manual_preview.py \
  --manifest data/p0-real3-luna-phase2-final/panel-review-9c1-20260809/manifest.json \
  --plan data/codex-vision-preview-50-60s-v2/edit-plan.json \
  --output-dir data/codex-vision-preview-50-60s-v2 \
  --validate-only
```

Expected JSON summary: 23 shots, source orders `1..23`, total duration `54.2`, no random sampling, `publish_allowed=false`.

## Task 3: Render and Perform Three-Point Visual QC

**Files:**
- Create runtime only: `data/codex-vision-preview-50-60s-v2/codex-vision-preview-54s-silent.mp4`
- Create runtime only: `data/codex-vision-preview-50-60s-v2/audit-frames/`
- Create runtime only: `data/codex-vision-preview-50-60s-v2/contact-sheet-three-point.jpg`
- Create runtime only: `data/codex-vision-preview-50-60s-v2/codex-manual-vision-review-v2.json`

**Interfaces:**
- Consumes: validated edit plan and ordered source assets.
- Produces: silent MP4 plus 69-frame start/mid/end visual audit and immutable ledger.

- [ ] **Step 1: Render on VPS**

```bash
.venv/bin/python scripts/review/render_codex_manual_preview.py \
  --manifest data/p0-real3-luna-phase2-final/panel-review-9c1-20260809/manifest.json \
  --plan data/codex-vision-preview-50-60s-v2/edit-plan.json \
  --output-dir data/codex-vision-preview-50-60s-v2
```

- [ ] **Step 2: Verify media contract**

```bash
ffprobe -v error -show_streams -show_format -of json \
  data/codex-vision-preview-50-60s-v2/codex-vision-preview-54s-silent.mp4
ffmpeg -v warning \
  -i data/codex-vision-preview-50-60s-v2/codex-vision-preview-54s-silent.mp4 \
  -vf "blackdetect=d=0.10:pix_th=0.10" -an -f null -
sha256sum data/codex-vision-preview-50-60s-v2/codex-vision-preview-54s-silent.mp4
```

Acceptance: one H.264 video stream, zero audio streams, 1080x1920, 30 FPS, duration within 50-60 seconds, no blackdetect findings.

- [ ] **Step 3: Extract 10 percent, midpoint, and 90 percent frame from every shot**

The renderer must use cumulative edit-plan timing to write 69 JPEGs named `shot-01-start.jpg` through `shot-23-end.jpg`, then tile them in chronological order. Do not use evenly spaced whole-video sampling because that can miss shot boundaries.

- [ ] **Step 4: Sol visual review gate**

Inspect all 69 frames. Reject and re-render if any frame contains:

- speech balloon text or balloon geometry;
- narration/system boxes from the source panel;
- white, black, gray, gradient, or colored edge padding that is not meaningful art;
- clipped primary face/action subject;
- caption punctuation or caption crossing a hard cut;
- motion revealing material that the midpoint crop concealed.

Record every rejected shot and corrected crop in `codex-manual-vision-review-v2.json`. Never replace a failed crop silently.

## Task 4: Full Verification and Durable Handoff

**Files:**
- Modify: `docs/STATUS.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: final MP4 hash, FFprobe JSON, blackdetect result, 69-frame audit, focused test results, and Git state.
- Produces: one evidence-backed checkpoint that another agent can resume without this conversation.

- [ ] **Step 1: Run repository verification**

```bash
cd /home/yusronrohmani/manhwashorts
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_codex_manual_preview.py -q
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m pytest -q -m "not slow"
.venv/bin/ruff check scripts/review/render_codex_manual_preview.py tests/test_codex_manual_preview.py
git diff --check
```

- [ ] **Step 2: Update documentation with exact evidence**

Document exact commit parent, test totals, output paths, MP4 SHA-256, duration, codecs, stream count, 69-frame audit result, any rejected first-pass crops, rights gate, provenance, and rollback point. State explicitly that manual v2 is not provider-generated StoryAnalysis/PanelRegion evidence.

- [ ] **Step 3: Commit documentation only**

```bash
git add -- docs/STATUS.md CHANGELOG.md
git diff --cached --check
git commit -m "docs: record 54 second manual vision review"
```

- [ ] **Step 4: Push exact history and prove parity**

Use the established SFTP/Git-bundle transport. Push only `main`, then verify:

```bash
git status --short
git rev-parse HEAD
git show -s --format=%T HEAD
git ls-remote https://github.com/yxxrn/manhwashorts-studio.git refs/heads/main
```

VPS and GitHub commit IDs must match; VPS tracked and unignored status must be empty. The ignored `data/codex-vision-preview-50-60s-v2/` artifacts remain on VPS and are not Git parity failures.

## Local Development Boundary

Cloning GitHub reproduces all tracked project source, tests, migrations, prompts, and documentation at the verified commit. It does **not** reproduce ignored VPS state: `.env`, API keys, SQLite databases, uploaded chapter images, temporary files, or rendered media.

For local preview work, copy only the explicitly required review dataset into a separate local data directory. Do not commit it:

```powershell
scp -r google:/home/yusronrohmani/manhwashorts/data/p0-real3-luna-phase2-final `
  C:\path\to\local\manhwashorts\data\
scp -r google:/home/yusronrohmani/manhwashorts/data/codex-vision-preview-50-60s-v2 `
  C:\path\to\local\manhwashorts\data\
```

Copy `.env` or credentials only through a separate secure process if actually needed; they are unnecessary for this silent manual-review render.

## Final Acceptance Checklist

- [ ] Duration is `50.000 <= duration <= 60.000` seconds.
- [ ] Target edit-plan duration is exactly `54.2` seconds before encoder rounding.
- [ ] Source orders `1..23` occur exactly once in order; title order `0` is documented only.
- [ ] Full ordered panel coverage is retained; no random selection.
- [ ] All 69 start/mid/end audit frames are balloon-free and padding-free.
- [ ] English captions contain no punctuation and do not cross hard cuts.
- [ ] Motion is monotonic, unidirectional, and free of visible shake.
- [ ] Output is 1080x1920, 30 FPS, H.264, video-only, with no black frames.
- [ ] Manual provenance, crop corrections, source checksums, and final hash are recorded.
- [ ] `publish_allowed=false` and voice remains deferred.
- [ ] Focused tests, non-slow tests, Ruff, compile, and diff checks pass.
- [ ] VPS tracked tree, Windows transport, and GitHub `main` resolve to the same final commit.

## Self-Review

- Spec coverage: duration, chronology, full-panel review, balloon/blank removal, subtitle contract, motion, voice deferral, rights, local-data boundary, QC, documentation, and push parity each have an explicit task and acceptance check.
- Placeholder scan: no unresolved marker, unspecified test, or unnamed file remains.
- Type consistency: `ValidatedShot`, `ValidatedCaption`, `ValidatedPlan`, and `validate_edit_plan` are defined once and used consistently by Tasks 1-4.

## Execution Handoff

Recommended execution is subagent-driven with a fresh executor for each task and Sol review after Task 1 and Task 3. If that orchestration is unavailable, execute inline in exact task order and stop immediately at any failed RED/GREEN, visual QC, or Git parity gate.
