from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.services.youtube_browser import BrowserPublishError, YouTubeStudioBrowserPublisher


def _cookie_text() -> str:
    return "\n".join([
        "# Netscape HTTP Cookie File",
        ".youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN_INFO\tsecret-login",
        "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\t__Secure-3PSID\tsecret-psid",
        ".example.com\tTRUE\t/\tTRUE\t0\tSID\tforeign-secret",
    ])


def test_cookie_parser_filters_foreign_domains_and_requires_complete_session():
    cookies = YouTubeStudioBrowserPublisher.parse_netscape_youtube_cookies(_cookie_text())
    assert [row["name"] for row in cookies] == ["LOGIN_INFO", "__Secure-3PSID"]
    assert all(str(row["domain"]).endswith("youtube.com") for row in cookies)
    assert "foreign-secret" not in repr(cookies)
    with pytest.raises(BrowserPublishError, match="complete YouTube login session"):
        YouTubeStudioBrowserPublisher.parse_netscape_youtube_cookies(
            ".youtube.com\tTRUE\t/\tTRUE\t0\tVISITOR_INFO1_LIVE\tvisitor"
        )

class _Page:
    def goto(self, *_args, **_kwargs): pass
    def wait_for_timeout(self, *_args): pass


class _Context:
    def __init__(self, fail_add: bool = False):
        self.pages = [_Page()]
        self.clear_count = 0
        self.added = []
        self.fail_add = fail_add
    def clear_cookies(self): self.clear_count += 1
    def add_cookies(self, cookies):
        self.added = list(cookies)
        if self.fail_add:
            raise RuntimeError("SECRET_COOKIE_VALUE")
    def new_page(self): return self.pages[0]
    def close(self): pass


class _PlaywrightManager:
    def __enter__(self): return object()
    def __exit__(self, *_args): return False


class _Publisher(YouTubeStudioBrowserPublisher):
    def __init__(self, tmp_path, context, persisted_after=True):
        self.account_id = "cookie-account"
        self.account_label = "Cookie account"
        self.profile_dir = tmp_path / "profile"
        self.executable = "/fake/chrome"
        self.timeout_ms = 1000
        self._context = context
        self._persisted_calls = 0
        self._persisted_after = persisted_after
    @contextmanager
    def _single_browser(self):
        yield
    def _launch_context(self, _playwright, *, headless=None):
        assert headless is True
        return self._context
    @staticmethod
    def _dismiss_compatibility_warning(_page): pass
    @staticmethod
    def _looks_signed_out(_page): return False
    def _profile_has_persisted_google_auth(self, _profile):
        self._persisted_calls += 1
        return self._persisted_after and self._persisted_calls > 1

def test_cookie_import_verifies_headlessly_and_never_echoes_values(monkeypatch, tmp_path):
    import playwright.sync_api

    context = _Context()
    publisher = _Publisher(tmp_path, context)
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: _PlaywrightManager())

    result = publisher.import_netscape_cookies(_cookie_text())
    assert result == {
        "authenticated": True,
        "method": "cookies_txt",
        "imported_cookie_count": 2,
        "account_id": "cookie-account",
    }
    assert context.clear_count == 1
    assert {row["name"] for row in context.added} == {"LOGIN_INFO", "__Secure-3PSID"}
    assert "secret-login" not in repr(result)
    assert "secret-psid" not in repr(result)


def test_cookie_import_failure_clears_profile_and_redacts_cookie_values(monkeypatch, tmp_path):
    import playwright.sync_api

    context = _Context(fail_add=True)
    publisher = _Publisher(tmp_path, context, persisted_after=False)
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: _PlaywrightManager())

    with pytest.raises(BrowserPublishError) as error:
        publisher.import_netscape_cookies(_cookie_text())
    assert context.clear_count == 2
    assert "SECRET_COOKIE_VALUE" not in str(error.value)
    assert "secret-login" not in str(error.value)
    assert "secret-psid" not in str(error.value)
