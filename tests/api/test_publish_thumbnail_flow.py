from __future__ import annotations

from datetime import UTC, datetime


def test_browser_publish_keeps_video_success_when_thumbnail_step_fails(
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
        db.add(
            RenderJob(
                project_id=pid,
                kind="final",
                status=JobStatus.SUCCEEDED,
                output_key=str(video),
                duration=55.0,
                width=1080,
                height=1920,
                completed_at=datetime.now(UTC),
            )
        )

    from app.services import publish as publish_svc
    from app.services.youtube_browser import BrowserPublishResult

    monkeypatch.setattr(publish_svc, "run_quality_checks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        publish_svc,
        "build_metadata_for",
        lambda *args, **kwargs: {
            "title": "He Found the One Spell They Hid | Infinite Mage #shorts",
            "description": "desc",
            "tags": ["shorts"],
        },
    )

    class BrowserPublisher:
        uploads = 0

        def __init__(self, account_id: str = "default") -> None:
            self.account_id = account_id

        def publish(self, **kwargs):
            self.uploads += 1
            return BrowserPublishResult(
                video_id="browser_thumbflow",
                privacy_status="private",
                upload_status="uploaded",
                stages=["published"],
                thumbnail_status="failed",
            )

    provider = BrowserPublisher()

    def fake_publisher(account_id=None):
        provider.account_id = account_id or "default"
        return provider

    monkeypatch.setattr(publish_svc, "YouTubeStudioBrowserPublisher", fake_publisher)

    response = client.post(f"/api/projects/{pid}/publish", json={"privacy_status": "private", "youtube_account_id": "channel-b"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["upload_status"] == "uploaded"
    assert body["youtube_account_id"] == "channel-b"
    assert body["thumbnail_status"] == "failed"
    assert body["thumbnail_note"]
    assert body["thumbnail_retry_url"] is None
    assert provider.uploads == 1

    retry = client.post(f"/api/publications/{body['id']}/thumbnail/retry")
    assert retry.status_code == 422
    assert "archived" in retry.json()["detail"].lower()
    assert provider.uploads == 1
