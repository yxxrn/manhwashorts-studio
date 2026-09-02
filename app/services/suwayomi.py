"""Local Suwayomi GraphQL connector and sidecar lifecycle.

Suwayomi stays a separate localhost process.  ManhwaShorts owns source
resolution, page downloading, and ingestion so the rest of the pipeline never
depends on Suwayomi's filesystem layout.
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class SuwayomiError(RuntimeError):
    pass


class SuwayomiUnavailableError(SuwayomiError):
    pass


class SuwayomiAmbiguousError(SuwayomiError):
    def __init__(self, candidates: list[dict]):
        self.candidates = candidates
        super().__init__("multiple Suwayomi manga/source matches are equally suitable")


@dataclass(frozen=True)
class ResolvedRange:
    manga: dict
    source: dict
    chapters: tuple[dict, ...]


@dataclass(frozen=True)
class DownloadedPage:
    chapter_id: int
    chapter_number: str
    chapter_name: str
    page_index: int
    filename: str
    data: bytes


_sidecar_process: subprocess.Popen | None = None
_sidecar_log = None

SOURCES_QUERY = """query ManhwaShortsSources { sources { nodes { id name displayName lang } } }"""
SEARCH_MUTATION = """mutation ManhwaShortsSearch($input: FetchSourceMangaInput!) {
  fetchSourceManga(input: $input) { hasNextPage mangas { id title sourceId thumbnailUrl } }
}"""
MANGA_CHAPTERS_MUTATION = """mutation ManhwaShortsManga($id: Int!) {
  fetchMangaAndChapters(input: {id: $id, fetchManga: true, fetchChapters: true}) {
    manga { id title sourceId source { id name displayName lang } }
    chapters { id name mangaId scanlator sourceOrder chapterNumber }
  }
}"""
PAGES_MUTATION = """mutation ManhwaShortsPages($input: FetchChapterPagesInput!) {
  fetchChapterPages(input: $input) { chapter { id pageCount } pages }
}"""


def _norm_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _number(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _range_complete(chapters: list[dict], start: Decimal, end: Decimal) -> bool:
    selected = {_number(ch.get("chapterNumber")) for ch in chapters}
    selected.discard(None)
    if start == start.to_integral() and end == end.to_integral():
        return all(Decimal(n) in selected for n in range(int(start), int(end) + 1))
    return bool([n for n in selected if n is not None and start <= n <= end])


def _page_url(base_url: str, path: str, source_id: str) -> str:
    absolute = path if urlsplit(path).scheme else urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    parts = urlsplit(absolute)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["sourceId"] = str(source_id)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _managed_bind_target(base_url: str) -> tuple[str, int] | None:
    parts = urlsplit(base_url)
    if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost"}:
        return None
    if parts.username or parts.password or parts.query or parts.fragment or parts.path.rstrip("/"):
        return None
    try:
        port = parts.port or 80
    except ValueError:
        return None
    return "127.0.0.1", int(port)


def provenance_filename(source_id: str, manga_id: object, filename: str) -> str:
    def token(value: object) -> str:
        return re.sub(r"[^A-Za-z0-9.-]+", "_", str(value)).strip("_.")[:80] or "unknown"

    return f"s{token(source_id)}_m{token(manga_id)}__{Path(filename).name}"


def _extension(content_type: str, url: str) -> str:
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    mapped = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    if content_type in mapped:
        return mapped[content_type]
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"

class SuwayomiClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or settings.suwayomi_url).rstrip("/")
        self.timeout = float(timeout or settings.suwayomi_request_timeout)

    def _auth(self):
        password = settings.suwayomi_password.get_secret_value() if settings.suwayomi_password else None
        if settings.suwayomi_username and password is not None:
            return httpx.BasicAuth(settings.suwayomi_username, password)
        return None

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        try:
            response = httpx.post(
                f"{self.base_url}/api/graphql",
                json={"query": query, "variables": variables or {}},
                timeout=self.timeout,
                auth=self._auth(),
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SuwayomiUnavailableError(f"Suwayomi request failed: {exc}") from exc
        errors = body.get("errors") if isinstance(body, dict) else None
        if errors:
            message = "; ".join(str(item.get("message", item)) for item in errors[:3])
            raise SuwayomiError(f"Suwayomi GraphQL error: {message}")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise SuwayomiError("Suwayomi returned no GraphQL data")
        return data

    def status(self) -> dict:
        try:
            nodes = self.graphql(SOURCES_QUERY).get("sources", {}).get("nodes", [])
            searchable = [item for item in nodes if str(item.get("lang", "")).casefold() != "localsourcelang"]
            return {"available": True, "url": self.base_url, "sources": len(nodes), "searchable_sources": len(searchable), "needs_extension_setup": not searchable}
        except SuwayomiError as exc:
            return {"available": False, "url": self.base_url, "sources": 0, "searchable_sources": 0, "needs_extension_setup": False, "error": str(exc)}

    def sources(self, language: str | None = None) -> list[dict]:
        nodes = self.graphql(SOURCES_QUERY).get("sources", {}).get("nodes", [])
        result = [dict(item) for item in nodes if isinstance(item, dict)]
        if language:
            lang = language.casefold()
            result = [item for item in result if str(item.get("lang", "")).casefold() == lang]
        return result

    def search_source(self, source_id: str, title: str) -> list[dict]:
        data = self.graphql(
            SEARCH_MUTATION,
            {"input": {"type": "SEARCH", "source": str(source_id), "query": title, "page": 1}},
        )
        mangas = (data.get("fetchSourceManga") or {}).get("mangas") or []
        return [dict(item) for item in mangas if isinstance(item, dict)]

    def search(self, title: str, language: str | None = None, source_id: str | None = None) -> list[dict]:
        sources = self.sources(language)
        if source_id is not None:
            sources = [item for item in sources if str(item.get("id")) == str(source_id)]
        results: list[dict] = []
        for source in sources:
            try:
                mangas = self.search_source(str(source["id"]), title)
            except SuwayomiError as exc:
                logger.warning("Suwayomi source %s search failed: %s", source.get("name"), exc)
                continue
            for manga in mangas:
                results.append({**manga, "source": source})
        exact = _norm_title(title)
        results.sort(key=lambda item: (_norm_title(str(item.get("title", ""))) != exact, str(item.get("title", "")).casefold(), str(item.get("sourceId", ""))))
        return results

    def manga_and_chapters(self, manga_id: int) -> tuple[dict, list[dict]]:
        payload = self.graphql(MANGA_CHAPTERS_MUTATION, {"id": int(manga_id)}).get("fetchMangaAndChapters") or {}
        manga = payload.get("manga")
        if not isinstance(manga, dict):
            raise SuwayomiError("Suwayomi could not resolve manga metadata")
        chapters = [dict(item) for item in (payload.get("chapters") or []) if isinstance(item, dict)]
        return dict(manga), chapters

    def resolve_range(self, title: str, chapter_from: float, chapter_to: float, language: str | None = None, source_id: str | None = None) -> ResolvedRange:
        start, end = Decimal(str(chapter_from)), Decimal(str(chapter_to))
        if end < start:
            start, end = end, start
        candidates = self.search(title, language, source_id)
        exact_title = _norm_title(title)
        viable: list[tuple[int, ResolvedRange]] = []
        for candidate in candidates:
            if _norm_title(str(candidate.get("title", ""))) != exact_title:
                continue
            try:
                manga, chapters = self.manga_and_chapters(int(candidate["id"]))
            except (KeyError, TypeError, ValueError, SuwayomiError):
                continue
            selected = [
                ch for ch in chapters
                if (num := _number(ch.get("chapterNumber"))) is not None and start <= num <= end
            ]
            if not selected or not _range_complete(chapters, start, end):
                continue
            selected.sort(key=lambda ch: (_number(ch.get("chapterNumber")) or Decimal("Infinity"), int(ch.get("sourceOrder") or 0), int(ch.get("id") or 0)))
            source = manga.get("source") if isinstance(manga.get("source"), dict) else candidate.get("source", {})
            score = 100
            if language and str(source.get("lang", "")).casefold() == language.casefold():
                score += 10
            viable.append((score, ResolvedRange(manga=manga, source=dict(source), chapters=tuple(selected))))
        if not viable:
            raise SuwayomiError(f"no exact {title!r} source contains the complete chapter range {chapter_from:g}-{chapter_to:g}")
        viable.sort(key=lambda item: item[0], reverse=True)
        top_score = viable[0][0]
        top = [item[1] for item in viable if item[0] == top_score]
        if len(top) > 1 and source_id is None:
            raise SuwayomiAmbiguousError([
                {"manga_id": item.manga.get("id"), "title": item.manga.get("title"), "source_id": item.source.get("id"), "source": item.source.get("displayName") or item.source.get("name"), "language": item.source.get("lang")}
                for item in top
            ])
        return top[0]

    def chapter_pages(self, chapter_id: int) -> list[str]:
        data = self.graphql(PAGES_MUTATION, {"input": {"chapterId": int(chapter_id)}})
        pages = (data.get("fetchChapterPages") or {}).get("pages") or []
        if not isinstance(pages, list) or not pages:
            raise SuwayomiError(f"chapter {chapter_id} returned no pages")
        return [str(page) for page in pages]

    def download_range(self, resolved: ResolvedRange) -> list[DownloadedPage]:
        source_id = str(resolved.source.get("id") or resolved.manga.get("sourceId") or "")
        if not source_id:
            raise SuwayomiError("resolved manga has no source id")
        downloaded: list[DownloadedPage] = []
        auth = self._auth()
        with httpx.Client(timeout=self.timeout, auth=auth, follow_redirects=True) as client:
            for chapter in resolved.chapters:
                chapter_id = int(chapter["id"])
                number = _number(chapter.get("chapterNumber"))
                number_text = format(number, "f") if number is not None else str(chapter_id)
                for page_index, page_path in enumerate(self.chapter_pages(chapter_id), start=1):
                    url = _page_url(self.base_url, page_path, source_id)
                    try:
                        response = client.get(url)
                        response.raise_for_status()
                    except httpx.HTTPError as exc:
                        raise SuwayomiError(f"failed downloading chapter {number_text} page {page_index}: {exc}") from exc
                    ext = _extension(response.headers.get("content-type", ""), url)
                    filename = f"ch{number_text}__{page_index:04d}{ext}"
                    downloaded.append(DownloadedPage(chapter_id, number_text, str(chapter.get("name") or ""), page_index, filename, response.content))
        return downloaded


def client() -> SuwayomiClient:
    return SuwayomiClient()

def _ensure_managed_config(work: Path, host: str = "127.0.0.1", port: int = 4567) -> None:
    """Keep the bundled sidecar loopback-only and headless without erasing user stores."""
    config = work / "server.conf"
    text = config.read_text() if config.is_file() else ""
    begin, end = "# BEGIN MANHWASHORTS SIDECAR", "# END MANHWASHORTS SIDECAR"
    if begin in text and end in text:
        before, rest = text.split(begin, 1)
        _, after = rest.split(end, 1)
        text = before.rstrip() + "\n" + after.lstrip()
    block = """# BEGIN MANHWASHORTS SIDECAR
server.ip = "{host}"
server.port = {port}
server.initialOpenInBrowserEnabled = false
server.systemTrayEnabled = false
server.webUIEnabled = false
server.kcefEnabled = false
server.authMode = "none"
# END MANHWASHORTS SIDECAR
"""
    config.write_text(text.rstrip() + ("\n\n" if text.strip() else "") + block.format(host=host, port=port))


def ensure_sidecar() -> dict:
    """Connect to an existing server or start the bundled JAR when available."""
    global _sidecar_process, _sidecar_log
    if not settings.suwayomi_enabled:
        return {"available": False, "url": settings.suwayomi_url, "sources": 0, "managed": False, "installed": False, "error": "Suwayomi connector is disabled"}
    current = client().status()
    if current["available"]:
        return {**current, "managed": False, "installed": Path(settings.suwayomi_jar_path).is_file()}
    jar = Path(settings.suwayomi_jar_path)
    if not settings.suwayomi_auto_start or not jar.is_file():
        return {**current, "managed": False, "installed": jar.is_file()}
    bind = _managed_bind_target(settings.suwayomi_url)
    if bind is None:
        return {
            **current,
            "managed": False,
            "installed": True,
            "error": current.get("error") or "bundled Suwayomi auto-start requires a loopback HTTP URL without a subpath",
        }
    if _sidecar_process is None or _sidecar_process.poll() is not None:
        work = settings.data_dir / "suwayomi"
        work.mkdir(parents=True, exist_ok=True)
        _ensure_managed_config(work, *bind)
        log_path = work / "sidecar.log"
        _sidecar_log = log_path.open("ab")
        try:
            _sidecar_process = subprocess.Popen(
                [
                    settings.suwayomi_java_bin,
                    f"-Dsuwayomi.tachidesk.config.server.rootDir={work}",
                    "-jar",
                    str(jar),
                ],
                cwd=work,
                stdout=_sidecar_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            _sidecar_log.close()
            _sidecar_log = None
            raise SuwayomiUnavailableError(f"could not start bundled Suwayomi: {exc}") from exc
    deadline = time.monotonic() + float(settings.suwayomi_start_timeout)
    while time.monotonic() < deadline:
        current = client().status()
        if current["available"]:
            return {**current, "managed": True, "installed": True}
        if _sidecar_process.poll() is not None:
            break
        time.sleep(0.5)
    return {**current, "managed": True, "installed": True, "error": current.get("error") or "Suwayomi did not become ready"}


def stop_sidecar() -> None:
    """Stop only the process started by this ManhwaShorts process."""
    global _sidecar_process, _sidecar_log
    if _sidecar_process is not None and _sidecar_process.poll() is None:
        _sidecar_process.terminate()
        try:
            _sidecar_process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            _sidecar_process.kill()
            _sidecar_process.wait(timeout=3)
    _sidecar_process = None
    if _sidecar_log is not None:
        _sidecar_log.close()
        _sidecar_log = None
