"""YouTube integration (PRD FR-10, FR-12).

Two providers behind one interface:

* ``GoogleYouTubeProvider`` - real Data/Analytics API calls via OAuth.
* ``DryRunProvider``        - records the intent locally, uploads nothing.

The dry-run provider is the default so a fresh local install can exercise the
whole publish path without OAuth credentials, and so tests never touch the
network. It never fabricates analytics: metrics stay zero and are labelled
``dry_run`` so the dashboard cannot mistake them for real numbers.

Safety posture:
* least-privilege scopes,
* refresh tokens encrypted at rest (``app.security``),
* private/unlisted default, public requires explicit config + confirmation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.config import settings
from app.constants import PrivacyStatus

# Upload + read-only analytics. Deliberately excludes broad account scopes.
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


class YouTubeError(RuntimeError):
    """Raised when a YouTube operation fails. Message is user-facing."""

    def __init__(self, message: str, code: str = "youtube_error", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class UploadResult:
    video_id: str
    privacy_status: str
    provider: str
    upload_status: str = "uploaded"
    watch_url: str = ""


@dataclass
class ChannelInfo:
    channel_id: str
    title: str
    scopes: list[str] = field(default_factory=list)


@dataclass
class StatsResult:
    """Analytics snapshot. ``available`` is False when the API returned nothing."""

    views: int = 0
    likes: int = 0
    comments: int = 0
    average_view_duration: float = 0.0
    average_percentage_viewed: float = 0.0
    subscribers_gained: int = 0
    source: str = "youtube_api"
    available: bool = True


class YouTubeProvider(Protocol):
    name: str

    def upload(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str,
        scheduled_at: datetime | None,
        credentials: dict,
    ) -> UploadResult: ...

    def fetch_stats(self, video_id: str, credentials: dict) -> StatsResult: ...


# --- metadata generation ---------------------------------------------------


def build_metadata(
    project_title: str,
    manhwa_title: str,
    chapter: str,
    script_text: str,
    attribution: str = "",
) -> dict:
    """Draft title/description/tags from the approved script.

    Always editable by the user before publishing (FR-10).
    """
    chapter_label = f"Chapter {chapter}" if chapter.strip().isdigit() else chapter.strip()
    base = " ".join(x for x in [manhwa_title.strip(), chapter_label] if x).strip()
    title = (base or project_title.strip() or "Manhwa Recap")
    if len(title) > 90:
        title = title[:87].rstrip() + "..."
    title = f"{title} #shorts" if len(title) <= 90 else title

    first_lines = " ".join(script_text.split())[:280]
    parts = [first_lines]
    if manhwa_title.strip():
        parts.append(f"Rangkuman {manhwa_title.strip()} {chapter_label}".strip())
    parts.append(
        "Video ini adalah rangkuman dan komentar. Semua hak atas karya asli "
        "tetap milik pemegang hak masing-masing."
    )
    if attribution.strip():
        parts.append(f"Kredit: {attribution.strip()}")
    description = "\n\n".join(p for p in parts if p)[:4900]

    raw_tags = ["manhwa", "manhwarecap", "shorts", "rangkumanmanhwa"]
    for token in (manhwa_title or "").lower().split():
        cleaned = "".join(ch for ch in token if ch.isalnum())
        if len(cleaned) > 2 and cleaned not in raw_tags:
            raw_tags.append(cleaned)
    return {"title": title, "description": description, "tags": raw_tags[:15]}


# --- dry-run provider ------------------------------------------------------


class DryRunProvider:
    """Records what would be uploaded, without any network call."""

    name = "dry_run"

    def upload(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str,
        scheduled_at: datetime | None,
        credentials: dict,
    ) -> UploadResult:
        video_path = Path(video_path)
        if not video_path.is_file():
            raise YouTubeError(
                f"video file not found: {video_path.name}", code="file_missing"
            )

        receipt_dir = settings.output_dir / "dry_run_uploads"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        # A local receipt makes the dry run auditable and testable.
        receipt = receipt_dir / f"{stamp}_{video_path.stem}.json"
        receipt.write_text(
            json.dumps(
                {
                    "provider": self.name,
                    "video_file": str(video_path),
                    "size_bytes": video_path.stat().st_size,
                    "title": title,
                    "description": description,
                    "tags": tags,
                    "privacy_status": privacy_status,
                    "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
                    "created_at": datetime.now(UTC).isoformat(),
                    "note": "DRY RUN - nothing was uploaded to YouTube",
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return UploadResult(
            video_id=f"dryrun_{stamp}",
            privacy_status=privacy_status,
            provider=self.name,
            upload_status="uploaded",
            watch_url=f"file://{receipt}",
        )

    def fetch_stats(self, video_id: str, credentials: dict) -> StatsResult:
        # No invented numbers: flagged unavailable so the UI shows "no data".
        return StatsResult(source="dry_run", available=False)


# --- real provider ---------------------------------------------------------


class GoogleYouTubeProvider:
    """Uploads via the YouTube Data API v3 using resumable upload."""

    name = "youtube"

    def _credentials(self, credentials: dict):
        from google.oauth2.credentials import Credentials

        if not credentials:
            raise YouTubeError(
                "channel is not connected; run the OAuth flow first",
                code="not_connected",
            )
        try:
            return Credentials(
                token=credentials.get("token"),
                refresh_token=credentials.get("refresh_token"),
                token_uri=credentials.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=credentials.get("client_id"),
                client_secret=credentials.get("client_secret"),
                scopes=credentials.get("scopes", YOUTUBE_SCOPES),
            )
        except Exception as exc:
            raise YouTubeError(f"invalid stored credentials: {exc}", code="bad_credentials") from exc

    def _service(self, credentials: dict, name: str = "youtube", version: str = "v3"):
        from googleapiclient.discovery import build

        return build(name, version, credentials=self._credentials(credentials), cache_discovery=False)

    def upload(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str,
        scheduled_at: datetime | None,
        credentials: dict,
    ) -> UploadResult:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        video_path = Path(video_path)
        if not video_path.is_file():
            raise YouTubeError(f"video file not found: {video_path.name}", code="file_missing")

        status_body: dict = {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        }
        if scheduled_at is not None:
            # YouTube requires private + publishAt for scheduling.
            status_body["privacyStatus"] = PrivacyStatus.PRIVATE
            status_body["publishAt"] = scheduled_at.astimezone(UTC).isoformat()

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:20],
                "categoryId": "1",
            },
            "status": status_body,
        }

        media = MediaFileUpload(str(video_path), chunksize=1024 * 1024 * 4, resumable=True)
        try:
            service = self._service(credentials)
            request = service.videos().insert(
                part="snippet,status", body=body, media_body=media
            )
            response = None
            while response is None:
                _, response = request.next_chunk()
        except HttpError as exc:
            code = getattr(exc.resp, "status", 0)
            # 403 here is usually quota exhaustion; 5xx is transient.
            retryable = code in (403, 429, 500, 502, 503, 504)
            hint = ""
            if code == 403:
                hint = (
                    " This is often a daily quota limit. Wait for the quota to reset "
                    "(midnight Pacific Time) and retry, which will not re-render."
                )
            raise YouTubeError(
                f"YouTube upload failed (HTTP {code}).{hint}",
                code=f"http_{code}",
                retryable=retryable,
            ) from exc
        except Exception as exc:
            raise YouTubeError(
                f"YouTube upload failed: {type(exc).__name__}",
                code="upload_failed",
                retryable=True,
            ) from exc

        video_id = response.get("id", "")
        return UploadResult(
            video_id=video_id,
            privacy_status=response.get("status", {}).get("privacyStatus", privacy_status),
            provider=self.name,
            upload_status=response.get("status", {}).get("uploadStatus", "uploaded"),
            watch_url=f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        )

    def fetch_stats(self, video_id: str, credentials: dict) -> StatsResult:
        """Read public counters plus retention from the Analytics API."""
        from googleapiclient.errors import HttpError

        result = StatsResult(source="youtube_api")
        try:
            service = self._service(credentials)
            response = service.videos().list(part="statistics", id=video_id).execute()
            items = response.get("items", [])
            if not items:
                return StatsResult(source="youtube_api", available=False)
            stats = items[0].get("statistics", {})
            result.views = int(stats.get("viewCount", 0) or 0)
            result.likes = int(stats.get("likeCount", 0) or 0)
            result.comments = int(stats.get("commentCount", 0) or 0)
        except HttpError as exc:
            raise YouTubeError(
                f"could not read video statistics (HTTP {getattr(exc.resp, 'status', 0)})",
                code="stats_failed",
                retryable=True,
            ) from exc

        # Retention metrics live in a different API and may be unavailable for
        # very new or low-traffic videos. Missing data stays zero, not guessed.
        try:
            analytics = self._service(credentials, "youtubeAnalytics", "v2")
            report = (
                analytics.reports()
                .query(
                    ids="channel==MINE",
                    startDate="2005-01-01",
                    endDate=datetime.now(UTC).date().isoformat(),
                    metrics="averageViewDuration,averageViewPercentage,subscribersGained",
                    filters=f"video=={video_id}",
                )
                .execute()
            )
            rows = report.get("rows") or []
            if rows:
                row = rows[0]
                result.average_view_duration = float(row[0] or 0)
                result.average_percentage_viewed = float(row[1] or 0)
                result.subscribers_gained = int(row[2] or 0)
        except Exception:
            # Analytics is best-effort; the basic counters above still stand.
            pass

        return result


# --- OAuth helpers ---------------------------------------------------------


def oauth_configured() -> bool:
    return bool(
        settings.youtube_enabled
        and settings.youtube_client_id
        and settings.youtube_client_secret
    )


def build_flow(state: str | None = None):
    """Create an OAuth flow for the installed-app redirect pattern."""
    from google_auth_oauthlib.flow import Flow

    if not oauth_configured() or settings.youtube_client_secret is None:
        raise YouTubeError(
            "YouTube OAuth is not configured. Set MS_YOUTUBE_ENABLED=true, "
            "MS_YOUTUBE_CLIENT_ID, and MS_YOUTUBE_CLIENT_SECRET.",
            code="not_configured",
        )
    client_config = {
        "web": {
            "client_id": settings.youtube_client_id,
            "client_secret": settings.youtube_client_secret.get_secret_value(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.youtube_redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=YOUTUBE_SCOPES, state=state)
    flow.redirect_uri = settings.youtube_redirect_uri
    return flow


def credentials_to_dict(creds) -> dict:
    """Serialise credentials for encrypted storage."""
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or YOUTUBE_SCOPES),
    }


def fetch_channel_info(credentials: dict) -> ChannelInfo:
    """Look up the connected channel's id and title."""
    provider = GoogleYouTubeProvider()
    service = provider._service(credentials)
    response = service.channels().list(part="snippet", mine=True).execute()
    items = response.get("items", [])
    if not items:
        raise YouTubeError("no channel found for this account", code="no_channel")
    item = items[0]
    return ChannelInfo(
        channel_id=item["id"],
        title=item.get("snippet", {}).get("title", ""),
        scopes=list(credentials.get("scopes", YOUTUBE_SCOPES)),
    )


def get_provider() -> YouTubeProvider:
    """Real provider only when OAuth is configured; dry run otherwise."""
    if oauth_configured():
        return GoogleYouTubeProvider()
    return DryRunProvider()
