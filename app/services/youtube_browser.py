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


class YouTubeStudioBrowserPublisher:
    name = "youtube_studio_browser"
    studio_url = "https://studio.youtube.com"

    def __init__(self, account_id: str | None = None) -> None:
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
                    if candidate.count() and candidate.is_visible(timeout=800):
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
                    if candidate.count() and candidate.is_visible(timeout=800):
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
                    if candidate.count() and candidate.is_visible(timeout=800):
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
        locator.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(value)
        page.keyboard.press("Tab")
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

    def _set_thumbnail(self, page, thumbnail_path: Path | None) -> str:
        if thumbnail_path is None or not thumbnail_path.is_file():
            return "not_available"
        image_inputs = page.locator("input[type='file'][accept*='image']")
        if not image_inputs.count():
            return "not_available"
        try:
            image_inputs.first.set_input_files(str(thumbnail_path))
            page.wait_for_timeout(1200)
            return "uploaded"
        except Exception:
            return "failed"

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
        radio.click()
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
            button.click()
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
        radio.click()
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
                r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/))([A-Za-z0-9_-]{6,})",
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
        done.click()
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
            title_loc = page.get_by_text(title, exact=True).first
            if title_loc.count():
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
        del tags  # Studio's tag field is hidden under advanced options; metadata remains packaged locally.
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

                thumbnail_status = self._set_thumbnail(page, thumbnail_path)
                stages.append(f"thumbnail_{thumbnail_status}")
                self._set_audience(page)
                stages.append("audience_set")

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
                stages.append("published")
                return BrowserPublishResult(
                    video_id=video_id,
                    watch_url=watch_url,
                    privacy_status=str(privacy_status),
                    upload_status="uploaded",
                    stages=stages,
                    thumbnail_status=thumbnail_status,
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
