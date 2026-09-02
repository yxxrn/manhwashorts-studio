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

    def evaluate(self, script: str):
        del script
        if self.page is not None:
            self.page.active = self
            self.value = ""

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

    def evaluate(self, script: str):
        del script
        self.click()

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


class _TagControl:
    def __init__(self, page, kind: str) -> None:
        self.page = page
        self.kind = kind
        self.pending = ""

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self, timeout=None):
        del timeout
        return True

    def get_attribute(self, name):
        return "false" if name == "aria-disabled" else None

    def click(self):
        if self.kind == "clear":
            self.page.tags.clear()

    def evaluate(self, script):
        del script
        self.click()

    def wait_for(self, **kwargs):
        del kwargs

    def fill(self, value):
        self.pending = value

    def press(self, key):
        if key == "Comma" and self.pending:
            self.page.tags.append(self.pending)
            self.pending = ""


class _TagChips:
    def __init__(self, page) -> None:
        self.page = page

    def all_inner_texts(self):
        return list(self.page.tags)


class _TagPage:
    def __init__(self) -> None:
        self.tags = ["old"]
        self.clear = _TagControl(self, "clear")
        self.input = _TagControl(self, "input")

    def locator(self, selector: str):
        if selector == "#tags-container #clear-button":
            return self.clear
        if selector == "#tags-container #text-input":
            return self.input
        if selector == "#tags-container ytcp-chip #chip-text":
            return _TagChips(self)
        raise AssertionError(selector)

    def wait_for_timeout(self, ms: int) -> None:
        del ms


def test_tags_replace_channel_defaults_and_are_verified():
    page = _TagPage()
    YouTubeStudioBrowserPublisher()._set_tags(page, ["manhwa", "shorts", "manhwa", "infinite,mage"])
    assert page.tags == ["manhwa", "shorts", "infinite mage"]


class _ThumbNode:
    def __init__(self, *, count=1, visible=True, src="") -> None:
        self._count = count
        self.visible = visible
        self.src = src
        self.sent = None

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def is_visible(self, timeout=None):
        del timeout
        return self.visible

    def get_attribute(self, name):
        return self.src if name == "src" else None

    def set_input_files(self, path):
        self.sent = path


class _ThumbUploader(_ThumbNode):
    def __init__(self) -> None:
        super().__init__()
        self.file = _ThumbNode()
        self.preview = _ThumbNode(visible=True)
        self.uploading = _ThumbNode(visible=False)
        self.image = _ThumbNode(src="blob:thumbnail")
        self.retry = _ThumbNode(count=0, visible=False)
        self.select = _ThumbNode(count=0, visible=False)

    def locator(self, selector):
        mapping = {
            "input[type='file'][accept*='image']": self.file,
            ".preview": self.preview,
            ".uploading": self.uploading,
            "#img-with-fallback": self.image,
            "#select-button": self.select,
        }
        return mapping[selector]

    def get_by_role(self, role, name=None):
        del role, name
        return self.retry


class _ThumbPage:
    def __init__(self) -> None:
        self.uploader = _ThumbUploader()

    def locator(self, selector):
        assert selector == "ytcp-thumbnail-uploader"
        return self.uploader

    def wait_for_timeout(self, ms):
        del ms


def test_thumbnail_publish_waits_for_completed_preview(tmp_path):
    thumbnail = tmp_path / "thumbnail.jpg"
    thumbnail.write_bytes(b"jpg")
    page = _ThumbPage()
    result = YouTubeStudioBrowserPublisher()._set_thumbnail(page, thumbnail)
    assert result == "uploaded"
    assert page.uploader.file.sent == str(thumbnail)


def test_missing_thumbnail_blocks_publish(tmp_path):
    with pytest.raises(BrowserPublishError) as exc:
        YouTubeStudioBrowserPublisher()._set_thumbnail(_ThumbPage(), tmp_path / "missing.jpg")
    assert exc.value.code == "thumbnail_missing"


def test_thumbnail_failure_is_best_effort(tmp_path):
    publisher = YouTubeStudioBrowserPublisher()
    status, detail = publisher._try_set_thumbnail(_ThumbPage(), tmp_path / "missing.jpg")
    assert status == "failed"
    assert detail.startswith("thumbnail_missing:")


def test_extract_video_identity_supports_studio_edit_link():
    class _StudioAnchors:
        def evaluate_all(self, script: str):
            del script
            return ["https://studio.youtube.com/video/qKk2wSK8PG4/edit"]

    class _StudioPage:
        def locator(self, selector: str):
            return _StudioAnchors() if selector == "a" else _Body()

    video_id, url = YouTubeStudioBrowserPublisher._extract_video_identity(_StudioPage())
    assert video_id == "qKk2wSK8PG4"
    assert url == "https://www.youtube.com/shorts/qKk2wSK8PG4"


def test_trust_channel_defaults_skips_static_metadata(monkeypatch):
    publisher = object.__new__(YouTubeStudioBrowserPublisher)
    publisher.timeout_ms = 120000
    publisher.trust_channel_defaults = True
    calls = []
    monkeypatch.setattr(publisher, "_open_advanced_details", lambda page: calls.append("open"))
    monkeypatch.setattr(publisher, "_set_tags", lambda page, tags: calls.append(("tags", tags)))
    monkeypatch.setattr(
        publisher, "_select_labeled_dropdown", lambda *args, **kwargs: calls.append("language")
    )
    monkeypatch.setattr(
        publisher, "_select_category", lambda *args, **kwargs: calls.append("category")
    )

    result = publisher._fill_advanced_metadata(object(), tags=["shorts"])
    assert result is None
    assert calls == ["open", ("tags", ["shorts"])]
