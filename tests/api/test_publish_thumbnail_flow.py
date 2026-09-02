from __future__ import annotations

from datetime import UTC, datetime


def test_video_publish_survives_thumbnail_failure_and_thumbnail_retries(
    client, app_settings, monkeypatch
):
    assert client.post(
        "/api/auth/register",
        json={"email": "thumb-flow@example.com", "password": "agentpass1234"},
    ).status_code == 201
    project = client.post(
        "/api/projects",
        json={"title": "Thumbnail Flow", "manhwa_title": "Infinite Mage", "chapter": "22-25"},
    ).json()
    pid = project["id"]

    output_dir = app_settings.output_dir / "thumbnail-flow"
    output_dir.mkdir(parents=True, exist_ok=True)
    video = output_dir / "final.mp4"
    video.write_bytes(b"fake-video")
    (output_dir / "thumbnail.jpg").write_bytes(b"fake-jpeg")

    from app.constants import JobStatus
    from app.db import session_scope
    from app.models import RenderJob
    with session_scope() as db:
        db.add(RenderJob(
            project_id=pid, kind="final", status=JobStatus.SUCCEEDED,
            output_key=str(video), duration=55.0, width=1080, height=1920,
            completed_at=datetime.now(UTC),
        ))

    from app.services import publish as publish_svc
    from app.services import youtube as yt
    monkeypatch.setattr(publish_svc, "run_quality_checks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        publish_svc, "build_metadata_for",
        lambda *args, **kwargs: {"title": "He Found the One Spell They Hid | Infinite Mage #shorts", "description": "desc", "tags": ["shorts"]},
    )

    class Provider:
        name = "test"
        thumbnail_should_fail = True
        uploads = 0

        def upload(self, **kwargs):
            self.uploads += 1
            return yt.UploadResult("dryrun_thumbflow", "private", self.name)

        def set_thumbnail(self, video_id, thumbnail_path, credentials):
            if self.thumbnail_should_fail:
                raise yt.YouTubeError("thumbnail rate limited", code="thumbnail_http_429", retryable=True)

    provider = Provider()
    monkeypatch.setattr(publish_svc.yt, "get_provider", lambda: provider)

    response = client.post(f"/api/projects/{pid}/publish", json={"privacy_status": "private"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["upload_status"] == "uploaded"
    assert body["thumbnail_status"] == "failed"
    assert body["thumbnail_note"]
    assert body["thumbnail_retry_url"].endswith("/thumbnail/retry")
    assert provider.uploads == 1

    provider.thumbnail_should_fail = False
    retry = client.post(body["thumbnail_retry_url"])
    assert retry.status_code == 200, retry.text
    assert retry.json()["thumbnail_status"] == "uploaded"
    assert retry.json()["thumbnail_note"] == ""
    assert provider.uploads == 1
