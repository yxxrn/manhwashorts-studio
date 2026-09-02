# UI and UX

Neobrutalism with a pastel palette, built to stay responsive on weak hardware.

No build step, no framework, no CDN. One stylesheet and one plain JS file, both
served directly. That is a deliberate constraint: it keeps the install to two
commands and means the interface loads instantly on a 2 vCPU box.

## Design language

Neobrutalism, softened by pastels so long editing sessions are not fatiguing:

- **Thick black borders** (3px, 2px on small screens) on every surface.
- **Hard offset shadows** (`4px 4px 0`) with **no blur radius**.
- **Flat pastel fills**, no gradients on large surfaces.
- **Uppercase, heavy labels** for structure.
- **Buttons that physically move.** Hover lifts by 1px and grows the shadow;
  pressing sinks 2px and removes it, so a click feels like a press.

The style and the performance goal happen to agree. A blurred shadow forces a
gaussian pass per element; a zero-blur shadow is a cheap solid rectangle.
Neobrutalism wants hard shadows anyway.

## Palette

All text is a single near-black ink (`#1a1a2e`). Pastels are only ever
backgrounds behind it — no pastel-on-pastel text anywhere.

| Token | Hex | Contrast vs ink | Used for |
|---|---|---|---|
| `--paper` | `#fdf6e3` | 15.8:1 | Page background |
| `--card` | `#ffffff` | 17.1:1 | Card surfaces |
| `--lemon` | `#ffe66d` | 13.6:1 | Primary buttons, top bar |
| `--sand` | `#f5e6c8` | 13.8:1 | Summaries, insets |
| `--lime` | `#c7f0a8` | 13.4:1 | Accents |
| `--mint` | `#a8e6cf` | 12.1:1 | Success, progress |
| `--sky` | `#a8d8f0` | 11.2:1 | Info, table headers |
| `--peach` | `#ffc4a3` | 11.1:1 | Warnings |
| `--aqua` | `#7fdbda` | 10.6:1 | Step accent |
| `--rose` | `#ffb3c6` | 10.2:1 | Step accent |
| `--lilac` | `#d4b8f0` | 9.7:1 | Legends |
| `--coral` | `#ff9b85` | 8.4:1 | Errors |
| `--muted` | `#4a4a60` | 7.98:1 on paper | Secondary text |

**Every pair clears WCAG 2.1 AAA (7:1)**, not merely AA (4.5:1). The lowest is
coral at 8.4:1. These are measured, not estimated — `tests/api/test_ui.py` computes
the ratios from the CSS variables and fails the build if any drops below AA.

An earlier muted grey (`#7a7a95`) measured 3.86:1 and failed. It was replaced
with `#4a4a60`. That is exactly the kind of regression the test now catches.

## Readability

- Body text 15px at 1.55 line-height.
- Labels uppercase 700-weight, but at least 0.82rem — small caps are hard to
  read below that.
- Long values wrap with `word-break: break-word` so filenames never overflow.
- Severity is always spelled out in text as well as colour, so meaning never
  depends on colour perception alone.

## Performance on low-spec machines

This runs on the same 2 vCPU / 3.6 GB VPS that renders the video, so the UI
budget is genuinely tight.

**Avoided entirely:** `backdrop-filter`, `filter: blur()`, gradients on large
surfaces, web fonts, CDN requests, and any framework runtime. A test asserts the
first two never reappear.

**Animation is compositor-only.** Transitions touch `transform` and `box-shadow`
and nothing else — never `width`, `height`, `top`, or `all`, which force layout
on every frame. Another test enforces this.

**Long lists are skipped while off-screen** via `content-visibility: auto` on
asset, cue, timeline, and publication lists. Harmless where unsupported.

**Requests are parallel, not serial.** Opening a project fires its independent
fetches concurrently; serially it was eight sequential round trips before the UI
settled.

**Heavy panels load lazily.** Encoder probing and channel listing only run when
the settings section is first opened, so boot stays cheap.

## UX guards

**Double-submit protection.** On a slow machine a user who sees no feedback
clicks again, queueing a second render. Every async action routes through
`withBusy()`, which disables the control, swaps in a spinner, and refuses
re-entry for the same key.

**Nothing waits silently.** Any action over a moment shows a spinner in the
button itself, with a verb: “Menganalisa…”, “Menyimpan…”, “Memeriksa…”.

**Destructive actions confirm first**, and the prompt says what will be lost.
Deleting a project spells out that material, scripts, and renders go with it.

**Failures are specific.** Errors surface the server's message, not “something
went wrong”. A GPU fallback names the driver problem.

**Empty states instruct.** Rather than a blank list, each says which button to
press next.

**Step navigation.** Eight chips jump to any stage, moving focus as well as
scroll so keyboard and screen-reader users follow the jump.

## Accessibility

- Skip link to main content.
- Every control has a `<label for>`, a wrapping label, or an `aria-label`.
  Enforced by test.
- Sections are `aria-labelledby` their heading.
- 3px offset focus ring on every focusable element; `outline: none` appears
  nowhere.
- Async status regions are `aria-live`, so updates are announced.
- `prefers-reduced-motion` disables all animation.
- Colour is never the only signal.

Full WCAG conformance needs manual testing with real assistive technology and
expert review. What is verified here is the measurable part: contrast ratios,
labelling, focus visibility, and live regions.

## Feature coverage

The current UI exposes the production pipeline plus the browser-account controls that are safe to operate interactively:

| Surface | Current behavior |
|---|---|
| Source/project controls | manual text/image ingest, project duplicate/delete, metadata |
| Analysis/script | extracted-fact review/edit, script versions, explicit approval |
| Timeline/subtitles | persisted scene/visual plan and subtitle cues |
| Quality/render | blocking QC, warning handling, encoder selection, render history/preview |
| Publication | readiness, generated/editable metadata, explicit Private/Unlisted/Public publish, publication history/retry |
| YouTube accounts | isolated Chrome profiles, session check, default-account selection, per-account Trust Upload defaults toggle |
| Provider/BYOK | provider test/save/model/default controls without exposing stored keys |
| System | encoder capability and health/readiness surfaces |

Browser-account rows display whether Upload defaults are currently `trusted` or `full automation`. Toggling trust updates only that account's static metadata behavior; title, description, tags, thumbnail, audience, and visibility remain uploader-controlled.

A UI contract test asserts that core pipeline/API surfaces remain reachable and that every referenced DOM control exists.

## Layout

```text
top bar        health · encoder · account
step nav       pipeline-stage jump controls
settings       BYOK keys · YouTube Chrome accounts/trust defaults · encoder table
projects       select · duplicate · delete · metadata
materi         manual source ingest / source metadata
draft/analisa  analysis + grounded script workflow
naskah         editing/versioning/approval
timeline       visual plan + subtitle cues
kualitas       blocking errors + warning handling
render         encoder choice · progress · final preview
publikasi      readiness · account · metadata · visibility · upload/history
```

The optional Suwayomi connector is primarily an API/operator source boundary; imported pages become normal project assets and the rest of the UI/pipeline treats them identically to manually ingested source images.

## Extending it

Keep these invariants; each is enforced by a test in `tests/api/test_ui.py`:

1. **Never assign `innerHTML`.** Use `el()` and `textContent`, so user text
   cannot execute as markup.
2. **Every `$('id')` must exist in the template.** A rename on one side used to
   crash the handler chain silently.
3. **Verify the field exists in the schema before reading it.** Two real bugs
   came from this: a similarity score that never existed (always rendered 0%),
   and `readiness.reasons` when the endpoint returns `reason`.
4. **No blur, no layout-triggering transitions.**
5. **Confirm destructive actions.**
