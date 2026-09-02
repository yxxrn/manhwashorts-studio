from __future__ import annotations

from types import SimpleNamespace


def test_thumbnail_path_prefers_generated_thumbnail(tmp_path):
    from app.services import publish

    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    (tmp_path / "final.jpg").write_bytes(b"frame")
    preferred = tmp_path / "thumbnail.jpg"
    preferred.write_bytes(b"thumb")

    job = SimpleNamespace(output_key=str(video))
    assert publish._thumbnail_path(job) == preferred


def test_thumbnail_path_falls_back_to_final_frame(tmp_path):
    from app.services import publish

    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    fallback = tmp_path / "final.jpg"
    fallback.write_bytes(b"frame")

    job = SimpleNamespace(output_key=str(video))
    assert publish._thumbnail_path(job) == fallback


def test_browser_thumbnail_failure_has_no_api_retry_url():
    from app.models import Publication

    publication = Publication(thumbnail_status="failed", thumbnail_error="browser_ui")
    assert "studio" in publication.thumbnail_note.lower()
    assert "manual" in publication.thumbnail_note.lower()
    assert publication.thumbnail_retry_url is None
