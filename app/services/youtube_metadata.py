"""Grounded YouTube metadata generation shared by browser publishing."""

from __future__ import annotations

import json
import re

from app.config import settings

_GENERIC_TITLE_PHRASES = (
    "this changed everything", "no one saw this coming", "what happened next",
    "you won't believe", "nobody expected this", "the truth revealed",
)
_TITLE_STOPWORDS = {
    "this", "that", "with", "from", "into", "then", "when", "what", "why",
    "they", "their", "there", "have", "will", "just", "only", "after", "before",
    "yang", "dengan", "dari", "untuk", "ketika", "setelah", "sebelum", "mereka",
    "ternyata", "akhirnya", "chapter", "shorts",
}

def _title_words(value: str) -> set[str]:
    return {w for w in re.findall(r"[A-Za-z0-9']+", value.casefold()) if len(w) >= 4 and w not in _TITLE_STOPWORDS}

def _clean_core_title(value: str) -> str:
    text = " ".join(str(value or "").replace("#shorts", "").split()).strip(" .!?:;-|\"")
    return text[:86].rstrip(" .!?:;-|")

def _story_sentences(script_text: str) -> list[str]:
    text = " ".join(script_text.split())
    rows = [row.strip(" .") for row in re.split(r"(?<=[.!?])\s+", text) if row.strip()]
    return [row for row in rows if len(row.split()) >= 5]

def _fallback_hook_title(script_text: str) -> str:
    rows = _story_sentences(script_text)
    if not rows:
        return _clean_core_title(script_text) or "Manhwa Recap"
    signals = ("but", "until", "realized", "revealed", "discover", "secret", "betray", "attack", "danger", "forbidden", "namun", "sampai", "menyadari", "rahasia", "menemukan", "bahaya")
    def score(row: str) -> tuple[int, int]:
        lower = row.casefold()
        signal_score = sum(2 for word in signals if word in lower)
        length_score = 2 if 7 <= len(row.split()) <= 16 else 0
        return signal_score + length_score, -len(row)
    return _clean_core_title(max(rows[:8], key=score))

def _llm_hook_titles(project_title: str, manhwa_title: str, chapter: str, script_text: str) -> list[str]:
    if settings.llm_provider != "openai_compatible" or not settings.llm_base_url or not settings.llm_api_key:
        return []
    system = (
        "Write high-CTR YouTube Shorts titles for a manhwa recap. Lead with one concrete event, reveal, decision, threat, contradiction, or mystery from the supplied story. "
        "Do not summarize the chapter and do not invent facts, motives, identities, outcomes, or stakes. Avoid generic clickbait including THIS CHANGED EVERYTHING, NO ONE SAW THIS COMING, WHAT HAPPENED NEXT, and YOU WON'T BELIEVE. "
        "Use the same language as the recap. Return five distinct core titles, 6-13 words each, without the series name, chapter number, or #shorts. Strict JSON: {\"titles\":[string]}."
    )
    user = f"Series: {manhwa_title or project_title}\nChapter: {chapter}\nGrounded recap: {script_text[:6000]}"
    try:
        import httpx
        response = httpx.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}", "Content-Type": "application/json"},
            json={"model": settings.llm_model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "temperature": 0.75, "response_format": {"type": "json_object"}},
            timeout=min(30, settings.llm_timeout),
        )
        response.raise_for_status()
        payload = json.loads(response.json()["choices"][0]["message"]["content"])
    except Exception:
        return []
    story_words = _title_words(script_text)
    rows: list[str] = []
    for raw in payload.get("titles", [])[:5] if isinstance(payload, dict) else []:
        title = _clean_core_title(raw)
        lower = title.casefold()
        if not title or any(phrase in lower for phrase in _GENERIC_TITLE_PHRASES):
            continue
        if story_words and not (_title_words(title) & story_words):
            continue
        rows.append(title)
    return rows

def _compose_video_title(core: str, manhwa_title: str, chapter: str, project_title: str) -> str:
    series = (manhwa_title or project_title).strip()
    _ = chapter
    suffix = f" | {series}" if series else ""
    suffix += " #shorts"
    max_core = max(24, 100 - len(suffix))
    core = _clean_core_title(core)[:max_core].rstrip(" .!?:;-|")
    title = f"{core}{suffix}" if core else f"{series} #shorts"
    return title[:100].rstrip()

def build_metadata(
    project_title: str,
    manhwa_title: str,
    chapter: str,
    script_text: str,
    attribution: str = "",
) -> dict:
    """Build grounded, hook-first YouTube metadata from the approved recap."""
    llm_titles = _llm_hook_titles(project_title, manhwa_title, chapter, script_text)
    core = llm_titles[0] if llm_titles else _fallback_hook_title(script_text)
    title = _compose_video_title(core, manhwa_title, chapter, project_title)

    chapter_label = f"Chapter {chapter}" if chapter.strip() else ""
    first_lines = " ".join(script_text.split())[:340]
    parts = [first_lines]
    if manhwa_title.strip():
        parts.append(f"Recap {manhwa_title.strip()} {chapter_label}".strip())
    parts.append(
        "Video ini adalah rangkuman dan komentar. Semua hak atas karya asli "
        "tetap milik pemegang hak masing-masing."
    )
    if attribution.strip():
        parts.append(f"Kredit: {attribution.strip()}")
    description = "\n\n".join(p for p in parts if p)[:4900]

    raw_tags = ["manhwa", "manhwarecap", "shorts", "rangkumanmanhwa"]
    for token in (manhwa_title or "").lower().split():
        cleaned = "".join(ch for ch in token if ch.isalnum())
        if len(cleaned) > 2 and cleaned not in raw_tags:
            raw_tags.append(cleaned)
    return {"title": title, "description": description, "tags": raw_tags[:15]}
