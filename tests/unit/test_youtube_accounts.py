from __future__ import annotations

from app.config import settings
from app.services.youtube_accounts import YouTubeBrowserAccountRegistry
from app.services.youtube_browser import YouTubeStudioBrowserPublisher


def _isolated_registry(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy-browser"
    accounts = tmp_path / "youtube-accounts"
    monkeypatch.setattr(settings, "youtube_browser_profile_dir", legacy)
    monkeypatch.setattr(settings, "youtube_browser_accounts_dir", accounts)
    return YouTubeBrowserAccountRegistry(), legacy, accounts


def test_legacy_browser_profile_is_preserved_as_default(monkeypatch, tmp_path):
    registry, legacy, _ = _isolated_registry(monkeypatch, tmp_path)
    account = registry.get()
    assert account.account_id == "default"
    assert account.profile_dir == legacy
    assert registry.default_account_id() == "default"


def test_accounts_get_isolated_profiles_and_can_switch_default(monkeypatch, tmp_path):
    registry, legacy, accounts = _isolated_registry(monkeypatch, tmp_path)
    first = registry.get()
    second = registry.create(account_id="channel-b", label="Channel B")

    assert first.profile_dir == legacy
    assert second.profile_dir == accounts / "channel-b"
    assert second.profile_dir != first.profile_dir
    assert (second.profile_dir / ".manhwashorts-account.json").is_file()

    registry.update("channel-b", make_default=True)
    assert registry.get().account_id == "channel-b"
    assert registry.get().profile_dir == accounts / "channel-b"


def test_publisher_uses_requested_account_profile(monkeypatch, tmp_path):
    registry, _, accounts = _isolated_registry(monkeypatch, tmp_path)
    registry.create(account_id="channel-b", label="Channel B")
    publisher = YouTubeStudioBrowserPublisher(account_id="channel-b")

    assert publisher.account_id == "channel-b"
    assert publisher.account_label == "Channel B"
    assert publisher.profile_dir == accounts / "channel-b"


def test_trust_channel_defaults_account_override_and_inheritance(monkeypatch, tmp_path):
    registry, _, _ = _isolated_registry(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "youtube_trust_channel_defaults", True)

    inherited = registry.get()
    assert inherited.trust_channel_defaults is None
    assert registry.describe()[0]["effective_trust_channel_defaults"] is True

    account = registry.create(
        account_id="channel-b", label="Channel B", trust_channel_defaults=False
    )
    assert account.trust_channel_defaults is False
    assert YouTubeStudioBrowserPublisher(account_id="channel-b").trust_channel_defaults is False

    assert YouTubeStudioBrowserPublisher(
        account_id="channel-b", trust_channel_defaults=True
    ).trust_channel_defaults is True

    registry.update("channel-b", trust_channel_defaults=None)
    assert YouTubeStudioBrowserPublisher(account_id="channel-b").trust_channel_defaults is True
