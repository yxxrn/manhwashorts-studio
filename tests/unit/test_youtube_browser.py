from __future__ import annotations

import pytest

from app.services.youtube_browser import BrowserPublishError, YouTubeStudioBrowserPublisher


class _Editable:
    def __init__(self, value: str = "") -> None:
        self.value = value
        self.page = None

    def click(self) -> None:
        if self.page is not None:
            self.page.active = self

    def inner_text(self) -> str:
        return self.value


class _Keyboard:
    def __init__(self, page) -> None:
        self.page = page

    def press(self, key: str) -> None:
        if key == "Control+A" and self.page.active is not None:
            self.page.active.value = ""

    def insert_text(self, value: str) -> None:
        if self.page.active is not None and self.page.persist:
            self.page.active.value = value


class _EditablePage:
    def __init__(self, *, persist: bool = True) -> None:
        self.persist = persist
        self.active = None
        self.keyboard = _Keyboard(self)

    def wait_for_timeout(self, ms: int) -> None:
        del ms


def test_contenteditable_value_must_persist():
    page = _EditablePage()
    loc = _Editable("filename title")
    loc.page = page
    YouTubeStudioBrowserPublisher._replace_contenteditable(
        page, loc, "Requested title", field_name="title"
    )
    assert loc.value == "Requested title"


def test_contenteditable_overwrite_is_rejected():
    page = _EditablePage(persist=False)
    loc = _Editable("filename title")
    loc.page = page
    with pytest.raises(BrowserPublishError) as exc:
        YouTubeStudioBrowserPublisher._replace_contenteditable(
            page, loc, "Requested title", field_name="title"
        )
    assert exc.value.code == "title_not_saved"


class _Body:
    def inner_text(self) -> str:
        return "Link video https://youtube.com/shorts/_0grji_t7GM"


class _Anchors:
    def evaluate_all(self, script: str):
        del script
        return []


class _IdentityPage:
    def locator(self, selector: str):
        return _Anchors() if selector == "a" else _Body()


def test_extract_video_identity_supports_shorts_link_text():
    video_id, url = YouTubeStudioBrowserPublisher._extract_video_identity(_IdentityPage())
    assert video_id == "_0grji_t7GM"
    assert url == "https://www.youtube.com/shorts/_0grji_t7GM"

class _Radio:
    def __init__(self, *, toggles: bool = True, visible: bool = True) -> None:
        self.checked = "false"
        self.toggles = toggles
        self.visible = visible

    def count(self) -> int:
        return 1

    def is_visible(self, timeout=None) -> bool:
        del timeout
        return self.visible

    def click(self) -> None:
        if self.toggles:
            self.checked = "true"

    def get_attribute(self, name: str):
        return self.checked if name == "aria-checked" else None

    @property
    def first(self):
        return self


class _RolePage:
    def __init__(self, radio: _Radio) -> None:
        self.radio = radio

    def get_by_role(self, role: str, name=None):
        del role, name
        return self.radio

    def locator(self, selector: str):
        del selector
        return self.radio

    def wait_for_timeout(self, ms: int) -> None:
        del ms


def test_audience_selection_is_verified():
    page = _RolePage(_Radio(toggles=True))
    YouTubeStudioBrowserPublisher()._set_audience(page)
    assert page.radio.checked == "true"


def test_audience_selection_failure_is_not_silently_accepted():
    publisher = YouTubeStudioBrowserPublisher()
    publisher.timeout_ms = 1
    page = _RolePage(_Radio(toggles=False))
    with pytest.raises(BrowserPublishError) as exc:
        publisher._set_audience(page)
    assert exc.value.code == "audience_not_saved"


def test_draft_row_is_never_treated_as_published():
    text = "ManhwaShorts Browser Test — Draf Pribadi"
    assert not YouTubeStudioBrowserPublisher._row_matches_visibility(text, "private")


def test_verified_private_row_is_publish_success():
    text = "ManhwaShorts Browser Test — Pribadi 2 Sep 2026 Diupload"
    assert YouTubeStudioBrowserPublisher._row_matches_visibility(text, "private")


def test_wrong_visibility_is_rejected():
    text = "ManhwaShorts Browser Test — Publik 2 Sep 2026 Dipublikasikan"
    assert not YouTubeStudioBrowserPublisher._row_matches_visibility(text, "private")
