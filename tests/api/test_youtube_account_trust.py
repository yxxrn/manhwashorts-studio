def test_youtube_account_trust_defaults_api(client, app_settings, monkeypatch, tmp_path):
    from app.services import youtube_accounts as account_svc

    monkeypatch.setattr(account_svc.settings, "youtube_browser_profile_dir", tmp_path / "youtube-profile")
    monkeypatch.setattr(account_svc.settings, "youtube_browser_accounts_dir", tmp_path / "youtube-accounts")
    assert client.post(
        "/api/auth/register",
        json={"email": "yt-defaults@example.com", "password": "agentpass1234"},
    ).status_code == 201

    created = client.post(
        "/api/youtube/browser/accounts",
        json={
            "account_id": "defaults-test",
            "label": "Defaults Test",
            "trust_channel_defaults": True,
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["trust_channel_defaults"] is True
    assert created.json()["effective_trust_channel_defaults"] is True

    updated = client.patch(
        "/api/youtube/browser/accounts/defaults-test",
        json={"trust_channel_defaults": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["trust_channel_defaults"] is False
    assert updated.json()["effective_trust_channel_defaults"] is False
