"""Slow FFmpeg validation for split-focus and panel-stack rendering."""
from __future__ import annotations

import subprocess

import pytest
from PIL import Image

pytestmark = pytest.mark.slow


def test_split_focus_and_panel_stack_render_h264_aac_without_black_frames(tmp_path):
    from app.services.render import RenderRequest, SceneInput, probe, render_video

    panels = []
    for index, colour in enumerate(("red", "blue")):
        path = tmp_path / f"panel{index}.jpg"
        Image.new("RGB", (400, 800), colour).save(path)
        panels.append(path)

    audio = tmp_path / "voice.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-ar", "48000", "-ac", "2", str(audio),
        ],
        check=True,
    )
    output = tmp_path / "split-stack.mp4"
    result = render_video(
        RenderRequest(
            project_id="split-stack-test",
            scenes=[
                SceneInput(panels[0], 0.0, 1.0, camera_curve="static", motion_mode="split_focus"),
                SceneInput(panels[1], 1.0, 2.0, camera_curve="static", motion_mode="panel_stack"),
            ],
            audio_path=audio,
            output_path=output,
            width=360,
            height=640,
            fps=30,
            encoder="cpu",
            preview=True,
        )
    )
    info = probe(result.output_path)
    assert info["width"] == 360
    assert info["height"] == 640
    assert info["duration"] == pytest.approx(2.0, abs=0.08)
    assert info["has_audio"] is True
    probe_audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", str(output)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert probe_audio == "aac"
    black_probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(output), "-vf", "blackdetect=d=0.2:pix_th=0.01", "-an", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    assert "black_duration:" not in black_probe.stderr
    assert result.output_path.stat().st_size > 10_000
