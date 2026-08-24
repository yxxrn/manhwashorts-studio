"""Small, deterministic terminal presentation helpers for the operator CLI."""

from __future__ import annotations

import os
import re
import sys
import urllib.parse
from collections.abc import Mapping

_ANSI = {
    "info": "36",  # cyan
    "success": "32",  # green
    "warning": "33",  # yellow
    "error": "31",  # red
    "muted": "90",  # gray
}
_BADGES = {
    "success": "[OK]",
    "warning": "[WARN]",
    "error": "[BLOCKED]",
    "info": "[INFO]",
    "muted": "[--]",
}
_KEY_PATTERN = re.compile(r"(?:sk-|xi-|AIza)[A-Za-z0-9_-]{8,}")


def color_enabled(*, stream: object | None = None, environ: Mapping[str, str] | None = None) -> bool:
    """Enable ANSI only for a TTY and never when ``NO_COLOR`` is present."""

    env = os.environ if environ is None else environ
    if "NO_COLOR" in env:
        return False
    target = sys.stdout if stream is None else stream
    isatty = getattr(target, "isatty", None)
    return bool(callable(isatty) and isatty())


def _paint(value: str, tone: str, color: bool) -> str:
    if not color:
        return value
    return f"\x1b[{_ANSI.get(tone, _ANSI['info'])}m{value}\x1b[0m"


def _redact(value: object, *, secret: str = "") -> str:
    text = str(value or "")
    if secret:
        text = text.replace(secret, "[redacted]")
    text = _KEY_PATTERN.sub("[redacted]", text)
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.query:
        text = urllib.parse.urlunsplit(parsed._replace(query="", fragment="")) + " [redacted]"
    return text


def _fit(value: str, width: int) -> str:
    width = max(24, int(width))
    if len(value) <= width:
        return value
    return value[: max(1, width - 3)].rstrip() + "..."


def render_status(
    label: str,
    value: object,
    *,
    tone: str = "info",
    color: bool = False,
    width: int = 72,
    secret: str = "",
) -> str:
    badge = _paint(_BADGES.get(tone, _BADGES["info"]), tone, color)
    safe_value = _fit(_redact(value, secret=secret), width - len(label) - 8)
    return f"{badge} {label}: {safe_value}"


def render_menu(
    *,
    provider: str = "not configured",
    model: str = "not selected",
    project: str = "none",
    job: str = "none",
    color: bool = False,
    width: int = 72,
) -> str:
    """Render the menu without animation; every line is width-bounded."""

    width = max(48, int(width))
    title = _paint("ManhwaShorts Studio | Operator", "info", color)
    lines = [
        _fit(title, width),
        _fit(render_status("Provider", provider, tone="success" if provider == "verified" else "warning", color=color, width=width), width),
        _fit(render_status("Model", model, tone="success" if model != "not selected" else "muted", color=color, width=width), width),
        _fit(render_status("Project", project, tone="info", color=color, width=width), width),
        _fit(render_status("Job", job, tone="warning" if job == "NEEDS_REVIEW" else "info", color=color, width=width), width),
        _fit("Review-only: voice/TTS/audio/publication disabled", width),
        "",
        "1) Setup/change cloud provider",
        "2) Test connection",
        "3) Fetch/select model",
        "4) Import/run one chapter folder",
        "5) Run batch parent folder",
        "6) Resume failed/pending jobs",
        "7) View status/review blockers",
        "8) Run explicitly approved production",
        "0) Exit / Keluar",
    ]
    return "\n".join(lines)


__all__ = ["color_enabled", "render_menu", "render_status"]
