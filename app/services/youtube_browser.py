"""YouTube Studio browser publisher.

Production publishing intentionally uses the same Studio UI a human uses rather
than the YouTube Data API. Authentication lives in a dedicated persistent Chrome
profile; passwords, OAuth client secrets, and raw cookies are never stored by the
application.
"""

from __future__ import annotations

import fcntl
import re
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.services.youtube_accounts import YouTubeBrowserAccountRegistry


class BrowserPublishError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "browser_publish_error",
        retryable: bool = False,
        action_required: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.action_required = action_required


@dataclass
class BrowserSessionStatus:
    available: bool
    authenticated: bool
    account_id: str
    account_label: str
    profile_dir: str
    browser: str
    trust_channel_defaults: bool = False
    action_required: str | None = None
    detail: str = ""


@dataclass
class BrowserPublishResult:
    video_id: str = ""
    watch_url: str = ""
    privacy_status: str = "private"
    upload_status: str = "uploaded"
    provider: str = "youtube_studio_browser"
    stages: list[str] = field(default_factory=list)
    thumbnail_status: str = "not_attempted"
    thumbnail_error: str = ""
    metadata_warning: str = ""


class YouTubeStudioBrowserPublisher:
    name = "youtube_studio_browser"
    studio_url = "https://studio.youtube.com"
    _ADVANCED_LABELS = {
        "video_language": ("Bahasa video", "Video language"),
        "metadata_language": ("Bahasa judul dan deskripsi", "Title and description language"),
    }
    _VALUE_ALIASES = {
        "Indonesia": ("Indonesia", "Indonesian"),
        "Indonesian": ("Indonesian", "Indonesia"),
        "Inggris": ("Inggris", "English"),
        "English": ("English", "Inggris"),
        "Film & Animasi": ("Film & Animasi", "Film & Animation"),
        "Film & Animation": ("Film & Animation", "Film & Animasi"),
    }

    def __init__(
        self, account_id: str | None = None, *, trust_channel_defaults: bool | None = None
    ) -> None:
        self.account_registry = YouTubeBrowserAccountRegistry()
        try:
            self.account = self.account_registry.get(account_id)
        except ValueError as exc:
            raise BrowserPublishError(
                str(exc),
                code="browser_account_not_found",
                action_required="select_youtube_account",
            ) from exc
        self.account_id = self.account.account_id
        self.account_label = self.account.label
        self.profile_dir = self.account.profile_dir
        self.trust_channel_defaults = (
            trust_channel_defaults
            if trust_channel_defaults is not None
            else (
                self.account.trust_channel_defaults
                if self.account.trust_channel_defaults is not None
                else settings.youtube_trust_channel_defaults
            )
        )
        self.executable = self._resolve_browser_executable(settings.youtube_browser_executable)
        self.timeout_ms = int(settings.youtube_browser_timeout_seconds * 1000)

    @staticmethod
    def _resolve_browser_executable(configured: str) -> str:
        requested = str(configured or "").strip()
        if requested:
            expanded = Path(requested).expanduser()
            if expanded.is_absolute() or "/" in requested:
                return str(expanded) if expanded.is_file() else ""
            found = shutil.which(requested)
            if found:
                return found
        for candidate in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            found = shutil.which(candidate)
            if found:
                return found
        return ""

    @contextmanager
    def _single_browser(self) -> Iterator[None]:
        lock_path = self.profile_dir / ".publisher.lock"
        with lock_path.open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BrowserPublishError(
                    "another YouTube Studio browser session is already running",
                    code="browser_busy",
                    retryable=True,
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _launch_context(self, playwright, *, headless: bool | None = None):
        return playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            executable_path=self.executable or None,
            headless=settings.youtube_browser_headless if headless is None else headless,
            viewport={"width": 1440, "height": 1000},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
            ),
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

    @staticmethod
    def _dismiss_compatibility_warning(page) -> None:
        candidates = [
            page.get_by_text(re.compile(r"^(go to youtube studio|langsung ke youtube studio)$", re.I)),
            page.get_by_role("button", name=re.compile(r"go to youtube studio|langsung ke youtube studio", re.I)),
            page.get_by_role("link", name=re.compile(r"go to youtube studio|langsung ke youtube studio", re.I)),
        ]
        for locator in candidates:
            try:
                if locator.count() and locator.first.is_visible(timeout=800):
                    locator.first.click()
                    page.wait_for_timeout(1200)
                    return
            except Exception:
                continue

    @staticmethod
    def _looks_signed_out(page) -> bool:
        url = page.url.casefold()
        if "accounts.google.com" in url or "/signin" in url:
            return True
        return bool(
            page.get_by_role("button", name=re.compile(r"^(sign in|masuk)$", re.I)).count()
            or page.get_by_text(re.compile(r"sign in to youtube|login ke youtube", re.I)).count()
        )

    def session_status(self) -> BrowserSessionStatus:
        if not settings.youtube_browser_enabled:
            return BrowserSessionStatus(
                available=False,
                authenticated=False,
                account_id=self.account_id,
                account_label=self.account_label,
                profile_dir=str(self.profile_dir),
                browser=self.executable,
                trust_channel_defaults=self.trust_channel_defaults,
                detail="browser publisher is disabled",
            )
        if not self.executable:
            return BrowserSessionStatus(
                available=False,
                authenticated=False,
                account_id=self.account_id,
                account_label=self.account_label,
                profile_dir=str(self.profile_dir),
                browser=str(settings.youtube_browser_executable),
                trust_channel_defaults=self.trust_channel_defaults,
                action_required="install_chrome",
                detail="Google Chrome/Chromium executable was not found",
            )
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return BrowserSessionStatus(
                available=False,
                authenticated=False,
                account_id=self.account_id,
                account_label=self.account_label,
                profile_dir=str(self.profile_dir),
                browser=self.executable,
                trust_channel_defaults=self.trust_channel_defaults,
                action_required="install_playwright",
                detail="Playwright is not installed",
            )

        try:
            with self._single_browser(), sync_playwright() as p:
                context = self._launch_context(p)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(self.studio_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                    page.wait_for_timeout(1500)
                    self._dismiss_compatibility_warning(page)
                    authenticated = not self._looks_signed_out(page)
                    return BrowserSessionStatus(
                        available=True,
                        authenticated=authenticated,
                        account_id=self.account_id,
                        account_label=self.account_label,
                        profile_dir=str(self.profile_dir),
                        browser=self.executable,
                        trust_channel_defaults=self.trust_channel_defaults,
                        action_required=None if authenticated else "youtube_reauthentication",
                        detail="YouTube Studio session is ready" if authenticated else "Google login is required",
                    )
                finally:
                    context.close()
        except BrowserPublishError as exc:
            return BrowserSessionStatus(
                available=True,
                authenticated=False,
                account_id=self.account_id,
                account_label=self.account_label,
                profile_dir=str(self.profile_dir),
                browser=self.executable,
                trust_channel_defaults=self.trust_channel_defaults,
                action_required=exc.action_required,
                detail=str(exc),
            )
        except Exception as exc:
            return BrowserSessionStatus(
                available=False,
                authenticated=False,
                account_id=self.account_id,
                account_label=self.account_label,
                profile_dir=str(self.profile_dir),
                browser=self.executable,
                trust_channel_defaults=self.trust_channel_defaults,
                action_required="browser_unavailable",
                detail=f"{type(exc).__name__}: {exc}",
            )

    def _diagnostic_dir(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = settings.tmp_dir / "youtube_browser" / self.account_id / stamp
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _first_visible(page, selectors: list[str]):
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count() and locator.is_visible(timeout=800):
                    return locator
            except Exception:
                continue
        return None

    def _open_upload(self, page) -> None:
        # Prefer Studio's direct upload quick action when available.
        direct = self._first_visible(
            page,
            [
                "#upload-icon",
                "ytcp-icon-button[aria-label='Upload video']",
                "ytcp-icon-button[aria-label='Upload videos']",
            ],
        )
        if direct is None:
            for name in (r"^Upload video$", r"^Upload videos$"):
                try:
                    candidate = page.get_by_role("button", name=re.compile(name, re.I)).first
                    candidate.wait_for(state="visible", timeout=min(self.timeout_ms, 8000))
                    direct = candidate
                    break
                except Exception:
                    continue
        if direct is not None:
            direct.click()
            page.wait_for_timeout(700)
            return

        create = self._first_visible(
            page,
            [
                "button[aria-label*='Create']",
                "button[aria-label*='Buat']",
            ],
        )
        if create is None:
            for name in (r"^Create$", r"^Buat$"):
                try:
                    candidate = page.get_by_role("button", name=re.compile(name, re.I)).first
                    candidate.wait_for(state="visible", timeout=min(self.timeout_ms, 8000))
                    create = candidate
                    break
                except Exception:
                    continue
        if create is None:
            raise BrowserPublishError("could not find YouTube Studio Create button", code="create_button_missing")
        create.click()
        page.wait_for_timeout(500)

        upload = self._first_visible(
            page,
            [
                "tp-yt-paper-item[test-id='upload']",
                "tp-yt-paper-item[test-id='upload-beta']",
                "tp-yt-paper-item:has-text('Upload video')",
                "tp-yt-paper-item:has-text('Upload videos')",
            ],
        )
        if upload is None:
            for name in (r"^Upload video$", r"^Upload videos$"):
                try:
                    candidate = page.get_by_role("menuitem", name=re.compile(name, re.I)).first
                    candidate.wait_for(state="visible", timeout=min(self.timeout_ms, 5000))
                    upload = candidate
                    break
                except Exception:
                    continue
        if upload is None:
            raise BrowserPublishError("could not find YouTube Studio Upload video action", code="upload_action_missing")
        upload.click()
        page.wait_for_timeout(700)

    def _assert_authenticated(self, page) -> None:
        if self._looks_signed_out(page):
            raise BrowserPublishError(
                "YouTube Studio needs Google login or re-authentication",
                code="not_authenticated",
                action_required="youtube_reauthentication",
            )

    @staticmethod
    def _replace_contenteditable(page, locator, value: str, *, field_name: str) -> None:
        # Avoid pointer-based focus: YouTube's hashtag/tooltip overlays can sit
        # above the editor and intercept clicks even though the field is visible.
        page.keyboard.press("Escape")
        locator.evaluate(
            """el => {
                el.focus();
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(el);
                selection.removeAllRanges();
                selection.addRange(range);
            }"""
        )
        page.keyboard.insert_text(value)
        page.wait_for_timeout(150)
        page.keyboard.press("Escape")
        page.wait_for_timeout(350)
        actual = " ".join(locator.inner_text().split())
        expected = " ".join(value.split())
        if actual != expected:
            raise BrowserPublishError(
                f"YouTube Studio did not retain the requested {field_name}",
                code=f"{field_name}_not_saved",
                retryable=True,
            )

    def _fill_metadata(self, page, *, title: str, description: str) -> None:
        title_box = self._first_visible(
            page,
            [
                "ytcp-social-suggestions-textbox#title-textarea #textbox",
                "#title-textarea #textbox",
                "div[contenteditable='true'][aria-label*='title' i]",
            ],
        )
        if title_box is None:
            raise BrowserPublishError("could not find YouTube title field", code="title_field_missing")

        # Studio populates a filename-derived title asynchronously. Wait for that
        # value to settle before replacing it, otherwise Studio can overwrite the
        # requested title moments after automation fills the field.
        previous = None
        stable = 0
        deadline = time.monotonic() + min(12.0, self.timeout_ms / 1000)
        while time.monotonic() < deadline:
            current = title_box.inner_text().strip()
            if current and current == previous:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
            previous = current
            page.wait_for_timeout(250)

        self._replace_contenteditable(page, title_box, title[:100], field_name="title")

        description_box = self._first_visible(
            page,
            [
                "ytcp-social-suggestions-textbox#description-textarea #textbox",
                "#description-textarea #textbox",
                "div[contenteditable='true'][aria-label*='description' i]",
            ],
        )
        if description_box is not None:
            self._replace_contenteditable(
                page,
                description_box,
                description[:5000],
                field_name="description",
            )

        # Catch delayed reactive rewrites before leaving the Details step.
        page.wait_for_timeout(900)
        if " ".join(title_box.inner_text().split()) != " ".join(title[:100].split()):
            raise BrowserPublishError(
                "YouTube Studio overwrote the requested title after entry",
                code="title_not_saved",
                retryable=True,
            )
        if description_box is not None and (
            " ".join(description_box.inner_text().split())
            != " ".join(description[:5000].split())
        ):
            raise BrowserPublishError(
                "YouTube Studio overwrote the requested description after entry",
                code="description_not_saved",
                retryable=True,
            )

    @staticmethod
    def _normalized_text(value: str) -> str:
        return " ".join(str(value or "").split())

    @classmethod
    def _value_aliases(cls, value: str) -> tuple[str, ...]:
        configured = cls._normalized_text(value)
        return cls._VALUE_ALIASES.get(configured, (configured,))

    def _open_advanced_details(self, page) -> None:
        deadline = time.monotonic() + min(10.0, self.timeout_ms / 1000)
        while time.monotonic() < deadline:
            if page.locator("#tags-container #text-input").count():
                return
            candidates = [
                page.get_by_role("button", name=re.compile(r"^(Tampilkan lebih banyak|Show more)$", re.I)).first,
                page.get_by_text("Tampilkan lebih banyak", exact=True).first,
                page.get_by_text("Show more", exact=True).first,
            ]
            for button in candidates:
                try:
                    if button.count() and button.is_visible(timeout=500):
                        button.evaluate("(el) => el.click()")
                        page.wait_for_timeout(500)
                        if page.locator("#tags-container #text-input").count():
                            return
                except Exception:
                    continue
            page.wait_for_timeout(250)
        raise BrowserPublishError("could not open YouTube advanced details", code="advanced_details_missing")

    def _set_tags(self, page, tags: list[str]) -> None:
        desired = []
        for raw in tags:
            value = self._normalized_text(str(raw).replace(",", " "))
            if value and value not in desired:
                desired.append(value[:60])
        clear = page.locator("#tags-container #clear-button").first
        if clear.count() and clear.is_visible(timeout=800) and clear.get_attribute("aria-disabled") != "true":
            clear.evaluate("(el) => el.click()")
            page.wait_for_timeout(250)
        input_box = page.locator("#tags-container #text-input").first
        input_box.wait_for(state="visible", timeout=self.timeout_ms)
        for tag in desired:
            input_box.fill(tag)
            input_box.press("Comma")
            page.wait_for_timeout(120)
        actual = [self._normalized_text(v) for v in page.locator("#tags-container ytcp-chip #chip-text").all_inner_texts()]
        if actual != desired:
            raise BrowserPublishError(
                f"YouTube Studio did not retain requested tags: {actual!r}",
                code="tags_not_saved",
                retryable=True,
            )

    def _select_visible_option(self, page, aliases: tuple[str, ...], *, field_name: str) -> None:
        options = page.locator("tp-yt-paper-item")
        for index in range(options.count()):
            option = options.nth(index)
            try:
                text = self._normalized_text(option.inner_text())
                if text in aliases and option.is_visible(timeout=500):
                    option.evaluate("(el) => el.click()")
                    page.wait_for_timeout(250)
                    return
            except Exception:
                continue
        raise BrowserPublishError(f"could not select YouTube {field_name}", code=f"{field_name}_option_missing")

    def _select_labeled_dropdown(self, page, labels: tuple[str, ...], value: str, *, field_name: str) -> None:
        pattern = re.compile("|".join(re.escape(label) for label in labels), re.I)
        trigger = page.locator("ytcp-dropdown-trigger").filter(has_text=pattern).first
        trigger.wait_for(state="visible", timeout=self.timeout_ms)
        aliases = self._value_aliases(value)
        if any(alias in self._normalized_text(trigger.inner_text()) for alias in aliases):
            return
        trigger.evaluate("(el) => el.click()")
        page.wait_for_timeout(200)
        self._select_visible_option(page, aliases, field_name=field_name)
        if not any(alias in self._normalized_text(trigger.inner_text()) for alias in aliases):
            raise BrowserPublishError(
                f"YouTube Studio did not retain requested {field_name}",
                code=f"{field_name}_not_saved",
                retryable=True,
            )

    def _select_category(self, page, value: str) -> None:
        trigger = page.locator("#category-container ytcp-dropdown-trigger").first
        trigger.wait_for(state="visible", timeout=self.timeout_ms)
        aliases = self._value_aliases(value)
        if any(alias in self._normalized_text(trigger.inner_text()) for alias in aliases):
            return
        trigger.evaluate("(el) => el.click()")
        page.wait_for_timeout(200)
        self._select_visible_option(page, aliases, field_name="category")
        if not any(alias in self._normalized_text(trigger.inner_text()) for alias in aliases):
            raise BrowserPublishError("YouTube Studio did not retain requested category", code="category_not_saved", retryable=True)

    def _fill_advanced_metadata(self, page, *, tags: list[str]) -> bool | None:
        self._open_advanced_details(page)
        self._set_tags(page, tags)
        if self.trust_channel_defaults:
            return None
        self._select_labeled_dropdown(page, self._ADVANCED_LABELS["video_language"], settings.youtube_video_language, field_name="video_language")
        metadata_pattern = re.compile(
            "|".join(re.escape(label) for label in self._ADVANCED_LABELS["metadata_language"]),
            re.I,
        )
        metadata_trigger = page.locator("ytcp-dropdown-trigger").filter(has_text=metadata_pattern).first
        metadata_language_set = bool(
            metadata_trigger.count() and metadata_trigger.is_visible(timeout=1000)
        )
        if metadata_language_set:
            self._select_labeled_dropdown(
                page,
                self._ADVANCED_LABELS["metadata_language"],
                settings.youtube_metadata_language,
                field_name="metadata_language",
            )
        self._select_category(page, settings.youtube_category)
        return metadata_language_set

    def _set_post_publish_metadata_language(self, page, video_id: str) -> None:
        page.goto(
            f"{self.studio_url}/video/{video_id}/edit",
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        self._dismiss_compatibility_warning(page)
        self._assert_authenticated(page)
        self._open_advanced_details(page)
        pattern = re.compile(
            "|".join(re.escape(label) for label in self._ADVANCED_LABELS["metadata_language"]),
            re.I,
        )
        trigger = page.locator("ytcp-dropdown-trigger").filter(has_text=pattern).first
        trigger.wait_for(state="visible", timeout=self.timeout_ms)
        aliases = self._value_aliases(settings.youtube_metadata_language)
        if any(alias in self._normalized_text(trigger.inner_text()) for alias in aliases):
            return
        self._select_labeled_dropdown(
            page,
            self._ADVANCED_LABELS["metadata_language"],
            settings.youtube_metadata_language,
            field_name="metadata_language",
        )
        save = page.locator("#save").first
        save.wait_for(state="visible", timeout=self.timeout_ms)
        deadline = time.monotonic() + min(20.0, self.timeout_ms / 1000)
        while save.get_attribute("aria-disabled") == "true":
            if time.monotonic() >= deadline:
                raise BrowserPublishError(
                    "YouTube Studio Save button stayed disabled after metadata language change",
                    code="metadata_language_save_disabled",
                    retryable=True,
                )
            page.wait_for_timeout(200)
        save.evaluate("(el) => el.click()")
        deadline = time.monotonic() + min(20.0, self.timeout_ms / 1000)
        while save.get_attribute("aria-disabled") != "true":
            if time.monotonic() >= deadline:
                raise BrowserPublishError(
                    "YouTube Studio did not finish saving title and description language",
                    code="metadata_language_save_timeout",
                    retryable=True,
                )
            page.wait_for_timeout(200)
        page.reload(wait_until="domcontentloaded", timeout=self.timeout_ms)
        self._open_advanced_details(page)
        pattern = re.compile(
            "|".join(re.escape(label) for label in self._ADVANCED_LABELS["metadata_language"]),
            re.I,
        )
        trigger = page.locator("ytcp-dropdown-trigger").filter(has_text=pattern).first
        trigger.wait_for(state="visible", timeout=self.timeout_ms)
        aliases = self._value_aliases(settings.youtube_metadata_language)
        if not any(alias in self._normalized_text(trigger.inner_text()) for alias in aliases):
            raise BrowserPublishError(
                "YouTube Studio did not persist title and description language",
                code="metadata_language_not_saved",
                retryable=True,
            )

    def _ensure_post_publish_thumbnail(self, page, video_id: str, thumbnail_path: Path) -> None:
        page.goto(
            f"{self.studio_url}/video/{video_id}/edit",
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        self._dismiss_compatibility_warning(page)
        self._assert_authenticated(page)
        uploader = page.locator("ytcp-thumbnail-uploader").first
        uploader.wait_for(state="attached", timeout=self.timeout_ms)
        page.wait_for_timeout(1200)
        preview = uploader.locator(".preview").first
        image = uploader.locator("#img-with-fallback").first
        if preview.count() and preview.is_visible(timeout=500) and image.count() and image.get_attribute("src"):
            return

        self._set_thumbnail(page, thumbnail_path)
        save = page.locator("#save").first
        save.wait_for(state="visible", timeout=self.timeout_ms)
        deadline = time.monotonic() + min(30.0, self.timeout_ms / 1000)
        while save.get_attribute("aria-disabled") == "true":
            if time.monotonic() >= deadline:
                raise BrowserPublishError(
                    "YouTube Studio Save button stayed disabled after thumbnail upload",
                    code="thumbnail_save_disabled",
                    retryable=True,
                )
            page.wait_for_timeout(200)
        save.evaluate("(el) => el.click()")
        while save.get_attribute("aria-disabled") != "true":
            if time.monotonic() >= deadline:
                raise BrowserPublishError(
                    "YouTube Studio did not finish saving the thumbnail",
                    code="thumbnail_save_timeout",
                    retryable=True,
                )
            page.wait_for_timeout(250)
        page.wait_for_timeout(1000)
        page.reload(wait_until="domcontentloaded", timeout=self.timeout_ms)
        uploader = page.locator("ytcp-thumbnail-uploader").first
        uploader.wait_for(state="attached", timeout=self.timeout_ms)
        deadline = time.monotonic() + min(10.0, self.timeout_ms / 1000)
        while time.monotonic() < deadline:
            preview = uploader.locator(".preview").first
            image = uploader.locator("#img-with-fallback").first
            if preview.count() and preview.is_visible(timeout=500) and image.count() and image.get_attribute("src"):
                return
            page.wait_for_timeout(300)
        raise BrowserPublishError(
            "YouTube Studio did not persist the custom thumbnail",
            code="thumbnail_not_saved",
            retryable=True,
        )

    def _set_thumbnail(self, page, thumbnail_path: Path | None) -> str:
        if thumbnail_path is None or not thumbnail_path.is_file():
            raise BrowserPublishError("thumbnail file is missing", code="thumbnail_missing")
        uploader = page.locator("ytcp-thumbnail-uploader").first
        image_input = uploader.locator("input[type='file'][accept*='image']").first
        if not image_input.count():
            raise BrowserPublishError("YouTube thumbnail upload control is missing", code="thumbnail_control_missing")
        try:
            select_button = uploader.locator("#select-button").first
            if select_button.count() and select_button.is_visible(timeout=800):
                with page.expect_file_chooser() as chooser_info:
                    select_button.click()
                chooser_info.value.set_files(str(thumbnail_path))
            else:
                image_input.set_input_files(str(thumbnail_path))
        except Exception as exc:
            raise BrowserPublishError("could not send thumbnail to YouTube Studio", code="thumbnail_upload_failed", retryable=True) from exc
        preview = uploader.locator(".preview").first
        uploading = uploader.locator(".uploading").first
        image = uploader.locator("#img-with-fallback").first
        deadline = time.monotonic() + min(45.0, self.timeout_ms / 1000)
        while time.monotonic() < deadline:
            retry = uploader.get_by_role("button", name=re.compile(r"^(Coba lagi|Retry)$", re.I)).first
            if retry.count() and retry.is_visible(timeout=300):
                raise BrowserPublishError("YouTube Studio rejected the thumbnail upload", code="thumbnail_upload_failed", retryable=True)
            preview_visible = preview.count() and preview.is_visible(timeout=300)
            uploading_visible = uploading.count() and uploading.is_visible(timeout=300)
            source = image.get_attribute("src") if image.count() else ""
            if preview_visible and not uploading_visible and source:
                return "uploaded"
            page.wait_for_timeout(300)
        raise BrowserPublishError("thumbnail upload did not finish before timeout", code="thumbnail_upload_timeout", retryable=True)

    def _try_set_thumbnail(self, page, thumbnail_path: Path | None) -> tuple[str, str]:
        """Best-effort thumbnail upload; video publishing must not depend on it."""
        try:
            return self._set_thumbnail(page, thumbnail_path), ""
        except BrowserPublishError as exc:
            detail = f"{exc.code}: {exc}"
            return "failed", detail[:1000]

    def _set_audience(self, page) -> None:
        radio = None
        for pattern in (
            r"not made for kids",
            r"tidak dibuat untuk anak-anak",
        ):
            try:
                candidate = page.get_by_role("radio", name=re.compile(pattern, re.I)).first
                if candidate.count() and candidate.is_visible(timeout=800):
                    radio = candidate
                    break
            except Exception:
                continue
        if radio is None:
            candidate = page.locator(
                "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']"
            ).first
            if candidate.count():
                radio = candidate
        if radio is None:
            raise BrowserPublishError(
                "could not find the YouTube audience control",
                code="audience_control_missing",
            )
        radio.evaluate("(el) => el.click()")
        deadline = time.monotonic() + min(8.0, self.timeout_ms / 1000)
        while time.monotonic() < deadline:
            if radio.get_attribute("aria-checked") == "true":
                return
            page.wait_for_timeout(200)
        raise BrowserPublishError(
            "YouTube Studio did not retain the Not made for kids selection",
            code="audience_not_saved",
            retryable=True,
        )

    @staticmethod
    def _visibility_radio(page, target: str):
        return page.locator(f"tp-yt-paper-radio-button[name='{target}']").first

    def _advance_wizard(self, page) -> None:
        # Step count changes over time. Advance until the actual Visibility
        # controls appear instead of assuming exactly three successful clicks.
        for _ in range(6):
            if any(
                self._visibility_radio(page, target).count()
                and self._visibility_radio(page, target).is_visible()
                for target in ("PRIVATE", "UNLISTED", "PUBLIC")
            ):
                return

            button = page.locator("#next-button").first
            button.wait_for(state="visible", timeout=self.timeout_ms)
            deadline = time.monotonic() + self.timeout_ms / 1000
            while button.get_attribute("aria-disabled") == "true":
                if time.monotonic() >= deadline:
                    raise BrowserPublishError(
                        "YouTube Studio Next button stayed disabled",
                        code="wizard_next_disabled",
                        retryable=True,
                    )
                page.wait_for_timeout(250)
            button.evaluate("(el) => el.click()")
            page.wait_for_timeout(650)

        raise BrowserPublishError(
            "YouTube Studio did not reach the Visibility step",
            code="visibility_step_missing",
            retryable=True,
        )

    def _set_visibility(self, page, privacy_status: str, scheduled_at: datetime | None) -> None:
        if scheduled_at is not None:
            raise BrowserPublishError(
                "scheduled publishing is not enabled until the Studio schedule UI is acceptance-tested",
                code="schedule_not_ready",
                action_required="manual_schedule",
            )
        mapping = {
            "private": "PRIVATE",
            "unlisted": "UNLISTED",
            "public": "PUBLIC",
        }
        target = mapping.get(str(privacy_status).casefold())
        if target is None:
            raise BrowserPublishError(f"unsupported visibility: {privacy_status}", code="bad_visibility")
        radio = self._visibility_radio(page, target)
        radio.wait_for(state="visible", timeout=self.timeout_ms)
        radio.evaluate("(el) => el.click()")
        deadline = time.monotonic() + min(8.0, self.timeout_ms / 1000)
        while time.monotonic() < deadline:
            if radio.get_attribute("aria-checked") == "true":
                return
            page.wait_for_timeout(200)
        raise BrowserPublishError(
            "YouTube Studio did not retain the requested visibility",
            code="visibility_not_saved",
            retryable=True,
        )

    @staticmethod
    def _extract_video_identity(page) -> tuple[str, str]:
        hrefs = page.locator("a").evaluate_all("els => els.map(e => e.href).filter(Boolean)")
        candidates = list(hrefs)
        with suppress(Exception):
            candidates.append(page.locator("body").inner_text())
        for candidate in candidates:
            match = re.search(
                r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/)|studio\.youtube\.com/video/)([A-Za-z0-9_-]{6,})",
                candidate,
            )
            if match:
                video_id = match.group(1)
                return video_id, f"https://www.youtube.com/shorts/{video_id}"
        return "", ""

    def _click_final_button(self, page) -> None:
        done = page.locator("#done-button").first
        done.wait_for(state="visible", timeout=self.timeout_ms)
        deadline = time.monotonic() + self.timeout_ms / 1000
        while done.get_attribute("aria-disabled") == "true":
            if time.monotonic() >= deadline:
                raise BrowserPublishError(
                    "YouTube Studio final Save/Publish button stayed disabled",
                    code="final_button_disabled",
                    retryable=True,
                )
            page.wait_for_timeout(250)
        done.evaluate("(el) => el.click()")
        page.wait_for_timeout(1800)

    @staticmethod
    def _row_matches_visibility(row_text: str, privacy_status: str) -> bool:
        expected_labels = {
            "private": ("Pribadi", "Private"),
            "unlisted": ("Tidak Publik", "Unlisted"),
            "public": ("Publik", "Public"),
        }
        normalized = " ".join(row_text.split())
        if "Draf" in normalized or "Draft" in normalized:
            return False
        return any(
            label in normalized
            for label in expected_labels[str(privacy_status).casefold()]
        )

    def _verify_content_row(self, page, *, title: str, privacy_status: str) -> tuple[str, str]:
        deadline = time.monotonic() + self.timeout_ms / 1000
        while time.monotonic() < deadline:
            page.goto(self.studio_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            self._dismiss_compatibility_warning(page)
            page.wait_for_timeout(700)
            content = page.get_by_role("menuitem", name=re.compile(r"^(Content|Konten)$", re.I)).first
            if content.count():
                content.click()
                page.wait_for_timeout(650)
            shorts = page.get_by_text("Shorts", exact=True).first
            if shorts.count():
                shorts.click()
                page.wait_for_timeout(900)
            title_locs = page.get_by_text(title, exact=True)
            for index in range(title_locs.count()):
                title_loc = title_locs.nth(index)
                row = title_loc.locator("xpath=ancestor::ytcp-video-row").first
                row_text = " ".join((row.inner_text() if row.count() else title_loc.inner_text()).split())
                if self._row_matches_visibility(row_text, privacy_status):
                    video_id, watch_url = self._extract_video_identity(row if row.count() else page)
                    return video_id, watch_url
            page.wait_for_timeout(1200)
        raise BrowserPublishError(
            "YouTube Studio did not confirm the uploaded Short with the requested visibility",
            code="publish_not_verified",
            retryable=True,
        )

    def publish(
        self,
        *,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        thumbnail_path: Path | None,
        privacy_status: str,
        scheduled_at: datetime | None,
    ) -> BrowserPublishResult:
        video_path = Path(video_path)
        if not video_path.is_file():
            raise BrowserPublishError("rendered video file is missing", code="file_missing")
        if not settings.youtube_browser_enabled:
            raise BrowserPublishError("browser publisher is disabled", code="browser_disabled")

        from playwright.sync_api import sync_playwright

        stages: list[str] = []
        diagnostics = self._diagnostic_dir()
        with self._single_browser(), sync_playwright() as p:
            context = self._launch_context(p)
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(self.timeout_ms)
            try:
                page.goto(self.studio_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self._dismiss_compatibility_warning(page)
                self._assert_authenticated(page)
                stages.append("authenticated")

                self._open_upload(page)
                file_input = page.locator("input[type='file']").first
                file_input.wait_for(state="attached", timeout=self.timeout_ms)
                file_input.set_input_files(str(video_path))
                stages.append("video_selected")

                page.locator("#title-textarea, ytcp-social-suggestions-textbox#title-textarea").first.wait_for(
                    state="visible", timeout=self.timeout_ms
                )
                self._fill_metadata(page, title=title, description=description)
                stages.append("metadata_filled")

                thumbnail_status, thumbnail_error = self._try_set_thumbnail(page, thumbnail_path)
                stages.append(f"thumbnail_{thumbnail_status}")
                if thumbnail_error:
                    stages.append("thumbnail_post_publish_retry_pending")
                self._set_audience(page)
                stages.append("audience_set")
                metadata_language_set = self._fill_advanced_metadata(page, tags=tags)
                stages.append("advanced_metadata_filled")
                if self.trust_channel_defaults:
                    stages.append("channel_defaults_trusted")
                elif metadata_language_set:
                    stages.append("metadata_language_set_during_upload")

                self._advance_wizard(page)
                stages.append("checks_advanced")
                self._set_visibility(page, privacy_status, scheduled_at)
                stages.append(f"visibility_{privacy_status}")

                candidate_video_id, candidate_watch_url = self._extract_video_identity(page)
                self._click_final_button(page)
                stages.append("publish_clicked")

                verified_video_id, verified_watch_url = self._verify_content_row(
                    page, title=title[:100], privacy_status=privacy_status
                )
                video_id = verified_video_id or candidate_video_id
                watch_url = verified_watch_url or candidate_watch_url
                stages.append("publish_verified")
                if thumbnail_path is not None and Path(thumbnail_path).is_file() and video_id:
                    try:
                        self._ensure_post_publish_thumbnail(page, video_id, Path(thumbnail_path))
                        thumbnail_status = "uploaded"
                        thumbnail_error = ""
                        stages.append("thumbnail_verified_post_publish")
                    except BrowserPublishError as exc:
                        thumbnail_status = "failed"
                        thumbnail_error = f"{exc.code}: {exc}"[:1000]
                        stages.append("thumbnail_manual_action_required")
                elif thumbnail_error:
                    stages.append("thumbnail_manual_action_required")

                metadata_warning = ""
                if not self.trust_channel_defaults and not metadata_language_set:
                    if not video_id:
                        metadata_warning = "metadata_language_post_publish: video id unavailable"
                        stages.append("metadata_language_manual_action_required")
                    else:
                        try:
                            self._set_post_publish_metadata_language(page, video_id)
                            stages.append("metadata_language_set_post_publish")
                        except BrowserPublishError as exc:
                            metadata_warning = f"{exc.code}: {exc}"[:1000]
                            stages.append("metadata_language_manual_action_required")
                stages.append("published")
                return BrowserPublishResult(
                    video_id=video_id,
                    watch_url=watch_url,
                    privacy_status=str(privacy_status),
                    upload_status="uploaded",
                    stages=stages,
                    thumbnail_status=thumbnail_status,
                    thumbnail_error=thumbnail_error,
                    metadata_warning=metadata_warning,
                )
            except BrowserPublishError:
                with suppress(Exception):
                    page.screenshot(path=str(diagnostics / "failure.png"), full_page=True)
                raise
            except Exception as exc:
                with suppress(Exception):
                    page.screenshot(path=str(diagnostics / "failure.png"), full_page=True)
                raise BrowserPublishError(
                    f"YouTube Studio automation failed at {stages[-1] if stages else 'startup'}: {type(exc).__name__}",
                    code="studio_automation_failed",
                    retryable=True,
                ) from exc
            finally:
                context.close()
