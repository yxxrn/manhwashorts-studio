from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

EXPECTED_SOURCE_ORDERS = tuple(range(1, 24))
MIN_DURATION_SECONDS = 50.0
MAX_DURATION_SECONDS = 60.0
CAPTION_PATTERN = re.compile(r"[A-Z0-9]+(?: [A-Z0-9]+)*\Z")
PREPARED_SIZE = (1296, 2304)
OUTPUT_SIZE = (1080, 1920)


@dataclass(frozen=True)
class ValidatedShot:
    source_order: int
    duration: float
    crop: tuple[float, float, float, float]
    motion: str


@dataclass(frozen=True)
class ValidatedCaption:
    start_shot: int
    end_shot: int
    text: str


@dataclass(frozen=True)
class ValidatedPlan:
    contract_version: str
    fps: int
    width: int
    height: int
    total_duration: float
    shots: tuple[ValidatedShot, ...]
    captions: tuple[ValidatedCaption, ...]
    random_sampling: bool
    publish_allowed: bool
    rights_status: str


def _fail(code: str, message: str) -> ValueError:
    return ValueError(f"{code}: {message}")


def _assets_by_order(manifest: Mapping[str, object]) -> dict[int, Mapping[str, object]]:
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise _fail("preview.manifest_invalid", "assets must be a list")
    result: dict[int, Mapping[str, object]] = {}
    for asset in assets:
        if not isinstance(asset, Mapping):
            raise _fail("preview.manifest_invalid", "asset must be an object")
        try:
            order = int(asset["source_order"])
        except (KeyError, TypeError, ValueError):
            raise _fail("preview.manifest_invalid", "asset source_order is invalid") from None
        if order in result:
            raise _fail("preview.manifest_invalid", "duplicate manifest source order")
        result[order] = asset
    return result


def validate_edit_plan(
    plan: Mapping[str, object], manifest: Mapping[str, object]
) -> ValidatedPlan:
    if plan.get("contract_version") != "codex_manual_vision_review_v2":
        raise _fail("preview.contract_invalid", "unsupported contract version")
    if plan.get("random_sampling") is not False:
        raise _fail("preview.random_sampling_forbidden", "random sampling must be false")
    if plan.get("publish_allowed") is not False:
        raise _fail("preview.publish_forbidden", "manual preview cannot be publishable")
    if plan.get("rights_status") != "internal review only":
        raise _fail("preview.rights_status_invalid", "rights status must remain internal review only")
    try:
        fps = int(plan["fps"])
        width = int(plan["width"])
        height = int(plan["height"])
    except (KeyError, TypeError, ValueError):
        raise _fail("preview.output_contract_invalid", "output geometry is invalid") from None
    if (fps, width, height) != (30, *OUTPUT_SIZE):
        raise _fail("preview.output_contract_invalid", "expected 1080x1920 at 30 fps")

    assets = _assets_by_order(manifest)
    if tuple(sorted(assets)) != tuple(range(24)):
        raise _fail("preview.manifest_coverage_invalid", "manifest must cover source orders 0..23")
    raw_shots = plan.get("shots")
    if not isinstance(raw_shots, list) or len(raw_shots) != len(EXPECTED_SOURCE_ORDERS):
        raise _fail("preview.source_order_coverage_invalid", "expected 23 shots")

    shots: list[ValidatedShot] = []
    for raw in raw_shots:
        if not isinstance(raw, Mapping):
            raise _fail("preview.shot_invalid", "shot must be an object")
        try:
            source_order = int(raw["source_order"])
            duration = float(raw["duration"])
            crop_values = tuple(float(value) for value in raw["crop"])
            motion = str(raw["motion"])
        except (KeyError, TypeError, ValueError):
            raise _fail("preview.shot_invalid", "shot fields are invalid") from None
        if len(crop_values) != 4:
            raise _fail("preview.crop_invalid", "crop requires four coordinates")
        x0, y0, x1, y1 = crop_values
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            raise _fail("preview.crop_invalid", "crop must be normalized and ordered")
        if duration <= 0.0 or not motion:
            raise _fail("preview.shot_invalid", "duration and motion are required")
        if source_order not in assets:
            raise _fail("preview.source_order_coverage_invalid", "shot references unknown source order")
        shots.append(ValidatedShot(source_order, duration, crop_values, motion))

    if tuple(shot.source_order for shot in shots) != EXPECTED_SOURCE_ORDERS:
        raise _fail("preview.source_order_coverage_invalid", "shots must use source orders 1..23 once")
    total_duration = sum(shot.duration for shot in shots)
    if not MIN_DURATION_SECONDS <= total_duration <= MAX_DURATION_SECONDS:
        raise _fail("preview.duration_out_of_range", "duration must be between 50 and 60 seconds")

    raw_captions = plan.get("captions")
    if not isinstance(raw_captions, list):
        raise _fail("preview.caption_contract_invalid", "captions must be a list")
    captions: list[ValidatedCaption] = []
    for raw in raw_captions:
        if not isinstance(raw, Mapping):
            raise _fail("preview.caption_contract_invalid", "caption must be an object")
        try:
            start = int(raw["start_shot"])
            end = int(raw["end_shot"])
            text = str(raw["text"])
        except (KeyError, TypeError, ValueError):
            raise _fail("preview.caption_contract_invalid", "caption fields are invalid") from None
        if not (0 <= start < end <= len(shots)) or not CAPTION_PATTERN.fullmatch(text):
            raise _fail("preview.caption_contract_invalid", "caption range or text is invalid")
        captions.append(ValidatedCaption(start, end, text))

    return ValidatedPlan(
        contract_version=str(plan["contract_version"]),
        fps=fps,
        width=width,
        height=height,
        total_duration=total_duration,
        shots=tuple(shots),
        captions=tuple(captions),
        random_sampling=False,
        publish_allowed=False,
        rights_status="internal review only",
    )


def _resolve_asset_path(asset: Mapping[str, object], manifest_path: Path) -> Path:
    candidates = [
        Path(str(asset.get("local_path", ""))),
        manifest_path.parent / str(asset.get("review_path", "")),
        manifest_path.parent / ".." / "codex-vision-preview-20260811" / "prepared" / (
            f"shot-{int(asset['source_order']):02d}-order-{int(asset['source_order']):02d}.jpg"
        ),
    ]
    for candidate in candidates:
        if str(candidate) and candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(f"no local source for order {asset['source_order']}")


def _run(command: Sequence[str]) -> None:
    subprocess.run(list(command), check=True)


def render_preview(
    plan: ValidatedPlan,
    manifest: Mapping[str, object],
    manifest_path: Path,
    output_dir: Path,
    ffmpeg: str = "ffmpeg",
) -> Path:
    assets = _assets_by_order(manifest)
    prepared_dir = output_dir / "prepared"
    shots_dir = output_dir / "shots"
    prepared_dir.mkdir(parents=True, exist_ok=True)
    shots_dir.mkdir(parents=True, exist_ok=True)
    shot_paths: list[Path] = []
    for index, shot in enumerate(plan.shots, start=1):
        source = _resolve_asset_path(assets[shot.source_order], manifest_path)
        with Image.open(source) as image:
            image = image.convert("RGB")
            x0, y0, x1, y1 = shot.crop
            crop = image.crop((round(x0 * image.width), round(y0 * image.height), round(x1 * image.width), round(y1 * image.height)))
            prepared = ImageOps.fit(crop, PREPARED_SIZE, method=Image.Resampling.LANCZOS)
            prepared_path = prepared_dir / f"shot-{index:02d}-order-{shot.source_order:02d}.jpg"
            prepared.save(prepared_path, quality=96, subsampling=0)
        output = shots_dir / f"shot-{index:02d}.mp4"
        direction = 1 if index % 2 else -1
        x_expr = f"108+{direction * 36}*t/{shot.duration}"
        y_expr = "192"
        vf = f"crop=1080:1920:x='{x_expr}':y='{y_expr}',format=yuv420p"
        _run([ffmpeg, "-y", "-v", "error", "-loop", "1", "-i", str(prepared_path), "-t", str(shot.duration), "-vf", vf, "-r", str(plan.fps), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "18", str(output)])
        shot_paths.append(output)
    concat = output_dir / "concat.txt"
    concat.write_text("".join(f"file '{path.resolve().as_posix()}'\n" for path in shot_paths), encoding="utf-8")
    assembled = output_dir / "assembled-silent.mp4"
    _run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-bsf:v", "h264_mp4toannexb", str(assembled)])
    ass = output_dir / "captions.ass"
    lines = [
        "[Script Info]\\nScriptType: v4.00+\\nPlayResX: 1080\\nPlayResY: 1920\\n",
        "[V4+ Styles]\\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\\n",
        "Style: Main,Arial,82,&H00FFFFFF,&H00FFFFFF,&H00101010,&H90000000,-1,0,0,0,100,100,1,0,1,7,2,2,80,80,260,1\\n",
        "[Events]\\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\\n",
    ]
    for caption in plan.captions:
        start = sum(shot.duration for shot in plan.shots[:caption.start_shot])
        end = sum(shot.duration for shot in plan.shots[:caption.end_shot])
        def stamp(seconds: float) -> str:
            total = round(seconds * 100)
            hours, total = divmod(total, 360000)
            minutes, total = divmod(total, 6000)
            secs, centis = divmod(total, 100)
            return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"
        lines.append(f"Dialogue: 0,{stamp(start)},{stamp(end)},Main,,0,0,0,,{caption.text}\\n")
    ass.write_text("".join(lines).replace("\\n", "\n"), encoding="utf-8")
    final = output_dir / "codex-vision-preview-54s-silent.mp4"
    ass_filter = str(ass.resolve()).replace("\\", "/").replace(":", "\\:")
    _run([ffmpeg, "-y", "-v", "error", "-i", str(assembled), "-vf", f"ass='{ass_filter}',format=yuv420p", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", "-preset", "medium", "-crf", "18", str(final)])
    return final


def write_review_artifacts(
    plan: ValidatedPlan,
    output: Path,
    output_dir: Path,
    ffmpeg: str = "ffmpeg",
) -> dict[str, object]:
    audit_dir = output_dir / "audit-frames"
    audit_dir.mkdir(parents=True, exist_ok=True)
    cursor = 0.0
    frame_paths: list[Path] = []
    labels = ("start", "mid", "end")
    for index, shot in enumerate(plan.shots, start=1):
        for label, fraction in zip(labels, (0.10, 0.50, 0.90), strict=True):
            frame = audit_dir / f"shot-{index:02d}-{label}.jpg"
            timestamp = cursor + shot.duration * fraction
            _run([ffmpeg, "-y", "-v", "error", "-ss", f"{timestamp:.6f}", "-i", str(output), "-frames:v", "1", "-vf", "scale=270:480", str(frame)])
            frame_paths.append(frame)
        cursor += shot.duration
    sheet = output_dir / "contact-sheet-69-frame.jpg"
    frame_concat = output_dir / "audit-frames.txt"
    frame_concat.write_text(
        "".join(f"file '{frame.resolve().as_posix()}'\n" for frame in frame_paths),
        encoding="utf-8",
    )
    _run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(frame_concat), "-vf", "tile=9x8", "-frames:v", "1", str(sheet)])
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    review = {
        "review_contract": "codex_manual_vision_review_v2",
        "provenance": "Codex model visual inspection of all six ordered contact sheets",
        "timeline_source_orders": list(EXPECTED_SOURCE_ORDERS),
        "title_panel_excluded_from_timeline": 0,
        "random_sampling": False,
        "audit_frame_count": len(frame_paths),
        "audio": False,
        "publish_allowed": False,
        "rights_status": "internal review only",
        "duration_seconds": plan.total_duration,
        "sha256": digest,
    }
    sidecar = output_dir / "codex-manual-vision-review-v2.json"
    sidecar.write_text(json.dumps(review, indent=2), encoding="utf-8")
    return review


def _load_json(path: Path) -> Mapping[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    validated = validate_edit_plan(_load_json(args.plan), _load_json(args.manifest))
    if not args.validate_only:
        output = render_preview(validated, _load_json(args.manifest), args.manifest, args.output_dir)
        review = write_review_artifacts(validated, output, args.output_dir)
        print(json.dumps({"output": str(output), **review}, indent=2))
    else:
        print(json.dumps({"shots": len(validated.shots), "source_orders": list(EXPECTED_SOURCE_ORDERS), "total_duration": validated.total_duration, "publish_allowed": False}))


if __name__ == "__main__":
    main()
