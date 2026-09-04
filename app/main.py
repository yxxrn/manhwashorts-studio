"""FastAPI application entry point.

Security note for local deployment: this app binds to 127.0.0.1 by default and
authenticates every project route with a signed session cookie. It is not
hardened for direct exposure to the internet — there is no rate limiting on
login, no CSRF token on state-changing form posts, and no TLS. If you put it on
a public address, place it behind a reverse proxy that adds TLS and rate
limiting, and set MS_ENVIRONMENT=production so session cookies become Secure.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, settings
from app.db import init_db
from app.routers import auth, credentials, pipeline, projects, publish, sources
from app.schemas import EncoderCapabilityOut, HealthOut, LocalCapabilitiesOut
from app.services import encoders
from app.services import render as render_svc
from app.services import suwayomi as suwayomi_svc
from app.services import tts as tts_svc

logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("manhwashorts")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()

    # Fase 0.1: light cleanup on every start (cheap age-based only)
    try:
        from app.services import cleanup as cleanup_svc
        cleanup_svc.cleanup_on_startup()
    except Exception as exc:
        logger.warning("cleanup on startup failed: %s", exc)

    problems = render_svc.check_environment()
    if problems:
        for problem in problems:
            logger.warning("environment: %s", problem)
    else:
        logger.info("environment OK: ffmpeg, ffprobe, and subtitle font present")
    logger.info(
        "tts=%s llm=%s youtube=%s",
        settings.tts_provider,
        settings.llm_provider,
        "browser" if settings.youtube_browser_enabled else "disabled",
    )
    if settings.suwayomi_enabled and settings.suwayomi_auto_start:
        try:
            sidecar = suwayomi_svc.ensure_sidecar()
            if sidecar.get("available"):
                logger.info("Suwayomi source sidecar ready at %s", sidecar.get("url"))
            elif sidecar.get("installed"):
                logger.warning("Suwayomi sidecar is installed but unavailable: %s", sidecar.get("error", "unknown error"))
        except Exception as exc:
            logger.warning("Suwayomi sidecar startup skipped: %s", exc)
    try:
        yield
    finally:
        suwayomi_svc.stop_sidecar()


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description=(
        "Auto YouTube Shorts for manhwa recaps. Rights-aware and "
        "human-in-the-loop: nothing publishes without an explicit approval."
    ),
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(credentials.router)
app.include_router(projects.router)
app.include_router(pipeline.router)
app.include_router(publish.router)
app.include_router(sources.router)

TEMPLATES_DIR = BASE_DIR / "app" / "templates"
STATIC_DIR = BASE_DIR / "app" / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return a readable message instead of raw pydantic error objects."""
    messages = []
    for error in exc.errors():
        location = " -> ".join(str(p) for p in error.get("loc", ()) if p != "body")
        messages.append(f"{location}: {error.get('msg', 'invalid value')}" if location else error.get("msg", ""))
    return JSONResponse(status_code=422, content={"detail": "; ".join(messages) or "Invalid request."})


@app.get("/api/health", response_model=HealthOut, tags=["system"])
def health() -> dict:
    """Report whether the local environment can actually render.

    Fase 0.1 addition: includes lightweight disk usage (data/tmp + data/output)
    so operators can see growth without a separate endpoint.
    The import is lazy to keep the module light.
    """
    problems = render_svc.check_environment()
    provider = tts_svc.get_provider()
    encoder = encoders.select()

    # Fase 0.1: lightweight disk usage (lazy import)
    disk_usage: dict = {}
    try:
        from app.services import cleanup as cleanup_mod
        disk_usage = cleanup_mod.get_data_usage_cached()
    except Exception:
        pass

    return {
        "status": "ok" if not problems else "degraded",
        "version": settings.version,
        "environment": settings.environment,
        "ffmpeg": render_svc.ffmpeg_available(),
        "tts_provider": provider.name,
        "llm_provider": settings.llm_provider,
        "youtube_enabled": settings.youtube_browser_enabled,
        "problems": problems,
        "video_encoder": encoder.key,
        "gpu_encoding": encoder.hardware,
        "disk_usage": disk_usage,
    }


@app.get("/api/capabilities", response_model=LocalCapabilitiesOut, tags=["system"])
def capabilities() -> dict:
    """Small stable surface for local agents to discover orchestration support."""
    return {
        "api_version": settings.version,
        "local_only_default": settings.host in {"127.0.0.1", "localhost", "::1"},
        "auth": "session_cookie",
        "openapi_url": "/openapi.json",
        "orchestration": True,
        "approval_required": True,
        "render_async": True,
        "stages": ["analysis", "draft", "voice", "timeline", "quality", "render", "publish"],
        "source_connectors": ["suwayomi"] if settings.suwayomi_enabled else [],
        "publishers": ["youtube_studio_browser"] if settings.youtube_browser_enabled else [],
    }


@app.get("/api/encoders", response_model=EncoderCapabilityOut, tags=["system"])
def list_encoders() -> dict:
    """Which video encoders work on this machine, CPU and GPU.

    Each backend is probed by encoding one real frame, because every FFmpeg build
    advertises `h264_nvenc` whether or not an NVIDIA card is present. Results are
    cached for the process lifetime.
    """
    return encoders.describe()


@app.get("/api/voices", tags=["system"])
def list_voices() -> dict:
    """Available narration voices (FR-05)."""
    provider = tts_svc.get_provider().name
    if provider == "http" and settings.tts_http_protocol == "grok":
        return {
            "provider": provider,
            "model": settings.tts_http_model or tts_svc.GROK_TTS_DEFAULT_MODEL,
            "language": settings.tts_http_language,
            "default_voice_id": tts_svc.GROK_TTS_DEFAULT_VOICE_ID,
            "profiles": list(tts_svc.GROK_NARRATOR_PROFILES),
            "voices": [
                {"id": voice_id, "label": voice_id.title()}
                for voice_id in tts_svc.GROK_VOICE_IDS
            ],
        }
    return {
        "provider": provider,
        "voices": [
            {"id": key, "label": value["label"]}
            for key, value in tts_svc.VOICE_CATALOG.items()
        ],
    }


@app.get("/", response_class=HTMLResponse, tags=["ui"])
def dashboard(request: Request) -> HTMLResponse:
    """Single-page studio UI."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": settings.app_name,
            "version": settings.version,
            "max_duration": settings.max_short_seconds,
            "default_target_seconds": settings.default_target_seconds,
            "youtube_enabled": settings.youtube_browser_enabled,
        },
    )


def run() -> None:  # pragma: no cover - manual entry point
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
