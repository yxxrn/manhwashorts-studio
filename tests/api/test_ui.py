"""UI contract tests (v1.3).

The frontend is plain JS with no build step, so nothing type-checks the contract
between `app.js` and the API. These tests close that gap by reading the actual
files and asserting the invariants that broke during development:

* Every `$('id')` in the JS exists in the template. A rename on one side used to
  produce a silent `null.addEventListener` crash that killed all later handlers.
* Every field the JS reads off an API response actually exists in the schema.
  Two real bugs came from this: `script.similarity_score` (never existed, always
  rendered 0%) and `readiness.reasons` (the endpoint returns `reason`).
* No `innerHTML` assignment anywhere, so user text can never execute as markup.
* Contrast ratios in the CSS palette clear WCAG AA for body text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")

_CSS_RAW = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
#: Comments stripped, because the stylesheet documents which expensive
#: properties it avoids — matching those words in prose is a false positive.
CSS = re.sub(r"/\*.*?\*/", "", _CSS_RAW, flags=re.DOTALL)


def _template_ids() -> set[str]:
    return set(re.findall(r'id="([a-zA-Z0-9_-]+)"', HTML))


def _js_ids() -> set[str]:
    return set(re.findall(r"\$\('([a-zA-Z0-9_-]+)'\)", JS))


# --- wiring ----------------------------------------------------------------


def test_every_js_id_exists_in_the_template():
    """A rename on one side silently breaks every handler after the crash."""
    missing = sorted(_js_ids() - _template_ids())
    assert not missing, f"app.js references ids absent from index.html: {missing}"


def test_no_duplicate_ids_in_template():
    ids = re.findall(r'id="([a-zA-Z0-9_-]+)"', HTML)
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates, f"duplicate ids break getElementById: {duplicates}"


def test_every_interactive_control_is_wired_or_declarative():
    """A button that does nothing is worse than no button."""
    button_ids = set(re.findall(r'<button[^>]*id="([a-zA-Z0-9_-]+)"', HTML))
    # These are handled by form submit or delegated listeners, not by id.
    exempt = {"login-btn"}
    unwired = sorted(button_ids - _js_ids() - exempt)
    assert not unwired, f"buttons with no handler: {unwired}"


def test_step_chips_point_at_real_headings():
    targets = set(re.findall(r'data-target="([a-zA-Z0-9_-]+)"', HTML))
    assert targets, "expected step navigation chips"
    missing = sorted(targets - _template_ids())
    assert not missing, f"step chips jump to missing ids: {missing}"


# --- API contract ----------------------------------------------------------


def test_script_fields_read_by_the_ui_exist():
    """`similarity_score` was read here and never existed; it always showed 0%."""
    from app.schemas import ScriptOut

    for field in ["version", "generator", "word_count", "estimated_duration",
                  "approved_at", "sections", "hook_options", "selected_hook", "warnings"]:
        assert field in ScriptOut.model_fields, f"ScriptOut has no {field}"

    assert "similarity_score" not in JS, (
        "app.js reads a similarity score that no endpoint returns; "
        "the ratio is reported as a quality check instead"
    )


def test_analysis_fields_read_by_the_ui_exist():
    from app.schemas import AnalysisOut, AnalysisUpdate

    for field in ["characters", "locations", "events", "main_conflict",
                  "twist", "cliffhanger", "low_confidence_notes"]:
        assert field in AnalysisOut.model_fields

    # The edit form posts exactly these.
    for field in ["main_conflict", "twist", "cliffhanger", "characters", "locations"]:
        assert field in AnalysisUpdate.model_fields


def test_render_job_encoder_fields_read_by_the_ui_exist():
    from app.schemas import RenderJobOut

    for field in ["encoder", "encoder_requested", "encoder_hardware",
                  "encoder_fell_back", "encoder_reason", "render_profile",
                  "attempt", "kind", "status"]:
        assert field in RenderJobOut.model_fields


def test_motion_and_audit_visibility_contract():
    from app.schemas import SceneOut

    for field in ["motion_mode", "motion_reason", "roi_label", "camera_curve"]:
        assert field in SceneOut.model_fields
    assert "/quality/overrides" in JS
    assert "loadQCOverrides" in JS
    assert "/quality/history" in JS
    assert "loadQCHistory" in JS
    assert "profile ${job.render_profile}" in JS
    assert "body: { kind, encoder, profile }" in JS


def test_publication_fields_read_by_the_ui_exist():
    from app.schemas import PublicationOut

    for field in ["video_title", "privacy_status", "upload_status",
                  "youtube_video_id", "error_message"]:
        assert field in PublicationOut.model_fields


def test_project_fields_read_by_the_ui_exist():
    from app.schemas import ProjectOut

    for field in ["status", "manhwa_title", "chapter", "target_duration",
                  "narration_style", "spoiler_level", "voice_id"]:
        assert field in ProjectOut.model_fields


def test_readiness_uses_the_key_the_endpoint_returns():
    """The endpoint returns `reason` (singular); the UI once read `reasons`."""
    assert "data.reasons" not in JS, "readiness returns `reason`, not `reasons`"
    assert "data.ready" in JS


def test_channel_fields_read_by_the_ui_exist():
    from app.schemas import ChannelOut

    for field in ["channel_title", "channel_id", "revoked"]:
        assert field in ChannelOut.model_fields


# --- feature coverage ------------------------------------------------------


def test_ui_covers_every_pipeline_stage():
    """A stage with no UI is a feature the user cannot reach."""
    for endpoint in [
        "/analysis",       # FR-03
        "/script",         # FR-04
        "/voice",          # FR-05
        "/timeline",       # FR-06
        "/subtitles",      # FR-07
        "/quality",        # FR-08
        "/render",         # FR-09
        "/publish",        # FR-10
        "/publications",   # history
        "/duplicate",
        "/credentials",    # BYOK
        "/encoders",       # CPU/GPU
        "/youtube/channels",
    ]:
        assert endpoint in JS, f"no UI reaches {endpoint}"


def test_script_approval_posts_explicit_confirmation():
    """The explicit backend approval gate must be reachable from the UI."""
    match = re.search(
        r"\$\('approve-script-btn'\)\.addEventListener\('click'.*?\n\}\);",
        JS,
        flags=re.DOTALL,
    )
    assert match, "script approval handler is missing"
    handler = match.group(0)
    assert "`/api/projects/${state.projectId}/script/approve`" in handler
    assert "method: 'POST'" in handler
    body = re.search(r"body:\s*\{\s*([^{}]*?)\s*\}", handler, flags=re.DOTALL)
    assert body, "script approval must send a JSON body"
    assert {part.strip() for part in body.group(1).split(',') if part.strip()} == {
        "editorial_review_confirmed: true",
    }
    assert "opts.headers['Content-Type'] = 'application/json'" in JS


def test_destructive_actions_ask_for_confirmation():
    """Deleting a project or key is irreversible; it must never be one click."""
    confirms = JS.count("window.confirm") + JS.count("confirm(")
    assert confirms >= 4, "expected confirmation on project/asset/key/channel delete"


# --- safety ----------------------------------------------------------------


def test_no_innerhtml_assignment():
    """User text (filenames, script lines) must never be parsed as markup."""
    assert not re.search(r"\.innerHTML\s*=", JS)
    assert not re.search(r"\.outerHTML\s*=", JS)
    assert "insertAdjacentHTML" not in JS


def test_no_eval_or_dynamic_function():
    assert not re.search(r"\beval\s*\(", JS)
    assert "new Function" not in JS


def test_external_links_are_not_a_tabnabbing_risk():
    for match in re.finditer(r"target\s*=\s*'_blank'", JS):
        window = JS[match.start(): match.start() + 200]
        assert "noopener" in window, "target=_blank needs rel=noopener"


# --- accessibility ---------------------------------------------------------


def _luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = [int(value[i: i + 2], 16) / 255 for i in (0, 2, 4)]
    adjusted = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * adjusted[0] + 0.7152 * adjusted[1] + 0.0722 * adjusted[2]


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _css_var(name: str) -> str:
    match = re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", CSS)
    assert match, f"CSS variable --{name} not found"
    return match.group(1)


@pytest.mark.parametrize(
    "pastel",
    ["lemon", "mint", "sky", "peach", "lilac", "rose", "lime", "coral", "aqua", "sand"],
)
def test_ink_on_every_pastel_clears_wcag_aa(pastel):
    """All text is ink; every pastel is used as a background behind it."""
    ratio = _contrast(_css_var("ink"), _css_var(pastel))
    assert ratio >= 4.5, f"ink on {pastel} is only {ratio:.2f}:1"


def test_body_text_clears_wcag_aaa():
    ratio = _contrast(_css_var("ink"), _css_var("paper"))
    assert ratio >= 7.0, f"body text is only {ratio:.2f}:1"


def test_muted_text_still_clears_aa():
    """Muted text is the easiest thing to get wrong; #7a7a95 failed at 3.86:1."""
    ratio = _contrast(_css_var("muted"), _css_var("paper"))
    assert ratio >= 4.5, f"muted text is only {ratio:.2f}:1"


def test_reduced_motion_is_respected():
    assert "prefers-reduced-motion" in CSS


def test_focus_is_always_visible():
    assert ":focus-visible" in CSS
    assert "outline: none" not in CSS.replace(" ", "").replace("outline:none", "outline: none")


def test_skip_link_exists():
    assert "skip-link" in CSS
    assert 'class="skip-link"' in HTML


def test_form_controls_are_labelled():
    """Every input needs a label or an aria-label to be usable by a reader."""
    control_ids = set(re.findall(r'<(?:input|select|textarea)[^>]*id="([a-zA-Z0-9_-]+)"', HTML))
    labelled = set(re.findall(r'<label[^>]*for="([a-zA-Z0-9_-]+)"', HTML))
    # Checkboxes inside <label class="check"> wrap their input instead.
    wrapped = set(re.findall(r'<label class="check[^"]*">\s*<input[^>]*id="([a-zA-Z0-9_-]+)"', HTML))
    aria = set(re.findall(r'<(?:input|select|textarea)[^>]*id="([a-zA-Z0-9_-]+)"[^>]*aria-label', HTML))
    unlabelled = sorted(control_ids - labelled - wrapped - aria)
    assert not unlabelled, f"controls with no label: {unlabelled}"


def test_live_regions_announce_async_updates():
    """Status text that changes after a fetch must be announced."""
    assert HTML.count("aria-live") >= 8


def test_sections_are_labelled_by_their_heading():
    sections = re.findall(r'<section[^>]*aria-labelledby="([a-zA-Z0-9_-]+)"', HTML)
    assert len(sections) >= 8
    missing = sorted(set(sections) - _template_ids())
    assert not missing, f"aria-labelledby points at missing ids: {missing}"


# --- performance on weak hardware -----------------------------------------


def test_no_expensive_visual_effects():
    """These are the usual cause of jank on a 2 vCPU box with no GPU."""
    assert "backdrop-filter" not in CSS
    assert not re.search(r"filter:\s*blur", CSS)
    # Hard neobrutalism shadows have no blur radius, which is also the cheap one.
    for shadow in re.findall(r"--shadow[a-z-]*:\s*([^;]+);", CSS):
        parts = shadow.split()
        assert len(parts) <= 4, f"shadow with a blur radius is costly: {shadow}"


def test_animations_only_touch_compositor_properties():
    """Animating width/height/top forces layout on every frame."""
    for block in re.findall(r"transition:\s*([^;]+);", CSS):
        for banned in ("width", "height", "top", "left", "margin", "padding", "all"):
            assert banned not in block, f"transition animates {banned}: {block}"


def test_long_lists_are_virtualised_by_the_browser():
    assert "content-visibility" in CSS
    assert 'class="list long"' in HTML or "list long" in HTML


def test_no_external_requests():
    """No CDN fonts or scripts: the app must work offline and load instantly."""
    assert "https://fonts." not in CSS and "https://fonts." not in HTML
    assert "@import url(http" not in CSS
    for match in re.finditer(r'<(?:script|link)[^>]*(?:src|href)="(http[^"]+)"', HTML):
        pytest.fail(f"external asset would block offline use: {match.group(1)}")


def test_no_render_blocking_synchronous_work_on_load():
    """The settings panel does its fetching on first open, not at boot."""
    assert "state.settingsLoaded" in JS


def test_double_submit_is_guarded():
    """On a slow box a user clicks twice; the second must be refused."""
    assert "withBusy" in JS
    assert "state.busy" in JS
