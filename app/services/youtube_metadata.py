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
_TAG_STOPWORDS = _TITLE_STOPWORDS | {
    "the", "game", "and", "for", "from", "with", "into", "recap", "manhwa",
    "shorts", "chapter", "this", "that", "as", "of", "to", "in", "a", "an",
}

def _title_words(value: str) -> set[str]:
    return {w for w in re.findall(r"[A-Za-z0-9']+", value.casefold()) if len(w) >= 4 and w not in _TITLE_STOPWORDS}

def _clip_title_at_word_boundary(value: str, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars].rstrip()
    if max_chars < len(text) and not text[max_chars].isspace():
        boundary = clipped.rfind(" ")
        if boundary > 0:
            clipped = clipped[:boundary]
    return clipped.rstrip(" .!?:;-|")


def _clean_core_title(value: str) -> str:
    text = " ".join(str(value or "").replace("#shorts", "").split()).strip(" .!?:;-|\"")
    return _clip_title_at_word_boundary(text, 86)

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
    core = _clip_title_at_word_boundary(_clean_core_title(core), max_core)
    title = f"{core}{suffix}" if core else f"{series} #shorts"
    return title[:100].rstrip()

def _story_excerpt(script_text: str, max_chars: int) -> str:
    text = " ".join(str(script_text or "").split()).strip()
    if len(text) <= max_chars:
        return text
    sentences = [row.strip() for row in re.split(r"(?<=[.!?])\s+", text) if row.strip()]
    chosen: list[str] = []
    for sentence in sentences:
        candidate = " ".join((*chosen, sentence))
        if len(candidate) > max_chars:
            break
        chosen.append(sentence)
    if chosen:
        return " ".join(chosen)
    clipped = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{clipped}…" if clipped else text[:max_chars]


def build_metadata(
    project_title: str,
    manhwa_title: str,
    chapter: str,
    script_text: str,
    attribution: str = "",
    language: str = "en",
) -> dict:
    """Build grounded YouTube metadata in one language from the approved recap."""
    llm_titles = _llm_hook_titles(project_title, manhwa_title, chapter, script_text)
    core = llm_titles[0] if llm_titles else _fallback_hook_title(script_text)
    title = _compose_video_title(core, manhwa_title, chapter, project_title)

    language_key = str(language or "en").casefold().split("-", 1)[0]
    if language_key == "id":
        chapter_label = f"Bab {chapter}" if chapter.strip() else ""
        recap_label = f"Rangkuman {manhwa_title.strip()} {chapter_label}".strip()
        rights_notice = (
            "Video ini adalah rangkuman dan komentar. Semua hak atas karya asli "
            "tetap milik pemegang hak masing-masing."
        )
        credit_label = "Kredit"
        raw_tags = ["manhwa", "rangkumanmanhwa", "shorts", "manhwaindonesia"]
    else:
        chapter_label = f"Chapter {chapter}" if chapter.strip() else ""
        recap_label = f"{manhwa_title.strip()} recap {chapter_label}".strip()
        rights_notice = (
            "This video is a recap and commentary. All rights to the original work "
            "remain with their respective rights holders."
        )
        credit_label = "Credits"
        raw_tags = ["manhwa", "manhwarecap", "shorts", "manhwashorts"]

    suffix_parts = [recap_label if manhwa_title.strip() else "", rights_notice]
    if attribution.strip():
        suffix_parts.append(f"{credit_label}: {attribution.strip()}")
    suffix = "\n\n".join(part for part in suffix_parts if part)
    story_limit = max(240, 4900 - len(suffix) - (2 if suffix else 0))
    story = _story_excerpt(script_text, story_limit)
    description = "\n\n".join(part for part in (story, suffix) if part)[:4900]

    for token in re.findall(r"[A-Za-z0-9]+", (manhwa_title or "").casefold()):
        cleaned = "".join(ch for ch in token if ch.isalnum())
        if len(cleaned) > 2 and cleaned not in _TAG_STOPWORDS and cleaned not in raw_tags:
            raw_tags.append(cleaned)
    return {"title": title, "description": description, "tags": raw_tags[:15]}
