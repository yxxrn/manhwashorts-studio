from __future__ import annotations

from types import SimpleNamespace


def _objects(tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    publication = SimpleNamespace(
        id="pub1", youtube_video_id="yt123", thumbnail_attempt=0,
        thumbnail_status="pending", thumbnail_error="",
    )
    return publication, SimpleNamespace(output_key=str(video)), video


def test_thumbnail_failure_is_non_blocking_and_retryable(tmp_path, monkeypatch):
    from app.services import publish, youtube

    publication, job, video = _objects(tmp_path)
    (video.parent / "thumbnail.jpg").write_bytes(b"jpeg")
    monkeypatch.setattr(publish, "audit", lambda *args, **kwargs: None)

    class Provider:
        def set_thumbnail(self, video_id, thumbnail_path, credentials):
            raise youtube.YouTubeError("rate limited", code="thumbnail_http_429", retryable=True)

    assert publish._attempt_thumbnail_upload(None, publication, job, Provider(), {}, "agent") is False
    assert publication.thumbnail_status == "failed"
    assert publication.thumbnail_attempt == 1
    assert "thumbnail_http_429" in publication.thumbnail_error


def test_thumbnail_success_clears_previous_error(tmp_path, monkeypatch):
    from app.services import publish

    publication, job, video = _objects(tmp_path)
    publication.thumbnail_error = "old failure"
    (video.parent / "thumbnail.jpg").write_bytes(b"jpeg")
    monkeypatch.setattr(publish, "audit", lambda *args, **kwargs: None)

    class Provider:
        def set_thumbnail(self, video_id, thumbnail_path, credentials):
            return None

    assert publish._attempt_thumbnail_upload(None, publication, job, Provider(), {}, "agent") is True
    assert publication.thumbnail_status == "uploaded"
    assert publication.thumbnail_error == ""
    assert publication.thumbnail_attempt == 1
