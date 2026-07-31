"""Story analysis: extract characters, events, and beats from source text.

PRD FR-03. Two implementations share one interface:

* ``RulesAnalyzer``  - deterministic heuristics, no network, always available.
* ``LLMAnalyzer``    - delegates to an OpenAI-compatible endpoint when configured.

Both are source-grounded: every extracted fact carries the index of the source
asset it came from, so the quality gate can verify claims trace back to
material the user supplied.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Protocol

from app.config import settings

# Words that look like names but are sentence-initial noise in Indonesian and
# English recaps. Filtered out of character detection.
_STOPNAMES = {
    "aku", "kamu", "dia", "mereka", "kami", "kita", "saya", "anda",
    "ini", "itu", "yang", "dan", "atau", "tapi", "tetapi", "namun",
    "setelah", "sebelum", "ketika", "saat", "karena", "sehingga", "jika",
    "kalau", "untuk", "dari", "dengan", "pada", "dalam", "oleh", "akan",
    "sudah", "belum", "tidak", "bukan", "adalah", "ada", "bab", "chapter",
    "the", "and", "but", "when", "after", "before", "because", "however",
    "he", "she", "they", "it", "his", "her", "their", "there", "then",
    "this", "that", "these", "those", "a", "an", "in", "on", "at", "to",
    "semua", "orang", "seseorang", "sang", "para", "seorang", "salah",
    "akhirnya", "kemudian", "lalu", "selain", "meski", "walau",
    "dungeon", "sistem", "level", "guild", "hunter", "system",
}

# Cues that mark a plot turn.
_TWIST_CUES = (
    "ternyata", "tak disangka", "tidak disangka", "mengejutkan", "rahasia",
    "sebenarnya", "kebenaran", "twist", "wahyu", "terungkap", "rupanya",
    "plot twist", "revealed", "turns out", "secretly",
)
_CLIFF_CUES = (
    "bersambung", "akhir bab", "muncul", "tiba-tiba", "menghilang", "kembali",
    "menantang", "mengancam", "tantangan", "cliffhanger", "to be continued",
    "suddenly", "appears", "next chapter",
)
_CONFLICT_CUES = (
    "melawan", "bertarung", "menyerang", "konflik", "musuh", "ancaman",
    "harus", "dipaksa", "gagal", "kalah", "bahaya", "berbahaya", "membunuh",
    "fight", "battle", "enemy", "threat", "must", "forced", "danger",
)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_PROPER = re.compile(r"\b([A-Z][a-zà-ÿA-Z']{2,})\b")
_LOCATION_HINT = re.compile(
    r"\b(?:di|ke|dari|in|at|inside)\s+((?:[A-Z][\w']+\s?){1,3})",
    re.UNICODE,
)


@dataclass
class Character:
    name: str
    mentions: int = 0
    role: str = ""
    aliases: list[str] = field(default_factory=list)
    source_index: int = 0


@dataclass
class StoryEvent:
    order: int
    text: str
    kind: str = "event"  # event | conflict | twist | cliffhanger
    source_index: int = 0


@dataclass
class AnalysisResult:
    characters: list[Character] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    events: list[StoryEvent] = field(default_factory=list)
    main_conflict: str = ""
    twist: str = ""
    cliffhanger: str = ""
    pronunciation_candidates: list[str] = field(default_factory=list)
    low_confidence_notes: list[str] = field(default_factory=list)
    generator: str = "rules"

    def to_dict(self) -> dict:
        return {
            "characters": [asdict(c) for c in self.characters],
            "locations": self.locations,
            "events": [asdict(e) for e in self.events],
            "main_conflict": self.main_conflict,
            "twist": self.twist,
            "cliffhanger": self.cliffhanger,
            "pronunciation_candidates": self.pronunciation_candidates,
            "low_confidence_notes": self.low_confidence_notes,
            "generator": self.generator,
        }


class Analyzer(Protocol):
    def analyze(self, sources: list[tuple[int, str]]) -> AnalysisResult: ...


def unwrap_paragraphs(text: str) -> str:
    """Join hard-wrapped lines back into continuous paragraphs.

    Pasted recaps are usually wrapped at ~75 columns. Splitting on those soft
    line breaks would cut sentences in half and produce fragment "events", so
    single newlines inside a paragraph are collapsed to spaces while blank
    lines are preserved as real paragraph boundaries.
    """
    paragraphs = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    joined = [re.sub(r"\s*\n\s*", " ", p).strip() for p in paragraphs]
    return "\n\n".join(p for p in joined if p)


def split_sentences(text: str) -> list[str]:
    """Split into sentences after repairing soft line wraps."""
    return [
        s.strip()
        for s in _SENT_SPLIT.split(unwrap_paragraphs(text))
        if len(s.strip()) > 12
    ]


def _score(sentence: str, cues: tuple[str, ...]) -> int:
    low = sentence.lower()
    return sum(1 for cue in cues if cue in low)


class RulesAnalyzer:
    """Heuristic extraction. Deterministic and offline."""

    name = "rules"

    def analyze(self, sources: list[tuple[int, str]]) -> AnalysisResult:
        result = AnalysisResult(generator=self.name)
        if not sources:
            result.low_confidence_notes.append("No source material supplied.")
            return result

        counts: dict[str, int] = {}
        first_seen: dict[str, int] = {}
        all_sentences: list[tuple[int, str]] = []
        locations: dict[str, None] = {}

        for src_index, text in sources:
            if not text.strip():
                continue
            for sentence in split_sentences(text):
                all_sentences.append((src_index, sentence))
                # Character candidates: proper nouns that are not sentence-initial
                # stopwords.
                for match in _PROPER.finditer(sentence):
                    word = match.group(1)
                    if word.lower() in _STOPNAMES or len(word) < 3:
                        continue
                    counts[word] = counts.get(word, 0) + 1
                    first_seen.setdefault(word, src_index)
            for match in _LOCATION_HINT.finditer(text):
                loc = match.group(1).strip()
                if loc and loc.lower() not in _STOPNAMES and len(loc) > 3:
                    locations.setdefault(loc, None)

        # A name mentioned once is more likely a stray capitalised word.
        named = sorted(
            ((w, c) for w, c in counts.items() if c >= 2),
            key=lambda kv: (-kv[1], kv[0]),
        )[:8]
        roles = ["protagonis", "tokoh pendukung", "tokoh pendukung", "antagonis"]
        for i, (word, count) in enumerate(named):
            result.characters.append(
                Character(
                    name=word,
                    mentions=count,
                    role=roles[i] if i < len(roles) else "tokoh",
                    source_index=first_seen.get(word, 0),
                )
            )

        result.locations = list(locations)[:6]

        # Events: keep source order, tag the ones that carry plot weight.
        for i, (src_index, sentence) in enumerate(all_sentences):
            kind = "event"
            if _score(sentence, _TWIST_CUES):
                kind = "twist"
            elif _score(sentence, _CONFLICT_CUES):
                kind = "conflict"
            elif _score(sentence, _CLIFF_CUES):
                kind = "cliffhanger"
            result.events.append(
                StoryEvent(order=i, text=sentence, kind=kind, source_index=src_index)
            )

        def best(cues: tuple[str, ...], prefer_late: bool = False) -> str:
            pool = all_sentences[len(all_sentences) // 2 :] if prefer_late else all_sentences
            pool = pool or all_sentences
            ranked = sorted(pool, key=lambda pair: -_score(pair[1], cues))
            top = ranked[0] if ranked else (0, "")
            return top[1] if _score(top[1], cues) > 0 else ""

        result.main_conflict = best(_CONFLICT_CUES)
        result.twist = best(_TWIST_CUES, prefer_late=True)
        result.cliffhanger = best(_CLIFF_CUES, prefer_late=True)

        if not result.main_conflict and all_sentences:
            mid = all_sentences[len(all_sentences) // 2][1]
            result.main_conflict = mid
            result.low_confidence_notes.append(
                "No explicit conflict cue found; used the middle sentence as a fallback. "
                "Please review."
            )
        if not result.twist:
            result.low_confidence_notes.append(
                "No twist detected in the source. The twist beat may need manual input."
            )
        if not result.cliffhanger and all_sentences:
            result.cliffhanger = all_sentences[-1][1]
        if not result.characters:
            result.low_confidence_notes.append(
                "No repeated character names detected. Add names manually for better narration."
            )

        # Names worth a pronunciation note: non-ASCII or unusual clusters.
        result.pronunciation_candidates = [
            c.name
            for c in result.characters
            if not c.name.isascii() or re.search(r"[^aeiouAEIOU\s]{3,}", c.name)
        ]
        return result


_LLM_SYSTEM = """You analyse manhwa chapter recaps supplied by the user.
Extract ONLY what the text states. Never invent characters, events, or outcomes.
If something is unclear, list it in low_confidence_notes instead of guessing.
Respond with strict JSON matching this schema:
{"characters":[{"name":str,"role":str,"aliases":[str]}],
 "locations":[str],
 "events":[{"text":str,"kind":"event|conflict|twist|cliffhanger"}],
 "main_conflict":str,"twist":str,"cliffhanger":str,
 "pronunciation_candidates":[str],"low_confidence_notes":[str]}"""


class LLMAnalyzer:
    """Source-grounded analysis via an OpenAI-compatible chat endpoint."""

    name = "llm"

    def __init__(self, fallback: Analyzer | None = None) -> None:
        self.fallback = fallback or RulesAnalyzer()

    def analyze(self, sources: list[tuple[int, str]]) -> AnalysisResult:
        import httpx

        if not settings.llm_base_url or not settings.llm_api_key:
            result = self.fallback.analyze(sources)
            result.low_confidence_notes.append(
                "LLM analysis not configured; used rule-based extraction."
            )
            return result

        joined = "\n\n".join(f"[source {i}]\n{t}" for i, t in sources if t.strip())[:12000]
        try:
            response = httpx.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.llm_model,
                    "messages": [
                        {"role": "system", "content": _LLM_SYSTEM},
                        {"role": "user", "content": joined},
                    ],
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                },
                timeout=settings.llm_timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
        except Exception as exc:
            result = self.fallback.analyze(sources)
            result.low_confidence_notes.append(
                f"LLM analysis failed ({type(exc).__name__}); used rule-based extraction."
            )
            return result

        return parse_llm_json(data, generator=self.name)


def parse_llm_json(data: dict, generator: str) -> AnalysisResult:
    """Convert a model's JSON response into an AnalysisResult.

    Shared by the env-configured and BYOK analyzers. Every field is length-capped
    and every enum value is validated, because this is untrusted input: a model
    can return any shape, and a malformed reply must not reach the database.
    """
    result = AnalysisResult(generator=generator)
    for c in data.get("characters", [])[:8]:
        if not isinstance(c, dict):
            continue
        aliases = c.get("aliases") or []
        result.characters.append(
            Character(
                name=str(c.get("name", ""))[:80],
                role=str(c.get("role", ""))[:40],
                aliases=[str(a)[:80] for a in aliases[:4]] if isinstance(aliases, list) else [],
            )
        )
    locations = data.get("locations") or []
    if isinstance(locations, list):
        result.locations = [str(x)[:80] for x in locations[:6]]

    for i, e in enumerate((data.get("events") or [])[:40]):
        if not isinstance(e, dict):
            continue
        kind = str(e.get("kind", "event"))
        result.events.append(
            StoryEvent(
                order=i,
                text=str(e.get("text", ""))[:500],
                kind=kind if kind in {"event", "conflict", "twist", "cliffhanger"} else "event",
            )
        )
    result.main_conflict = str(data.get("main_conflict", ""))[:500]
    result.twist = str(data.get("twist", ""))[:500]
    result.cliffhanger = str(data.get("cliffhanger", ""))[:500]

    candidates = data.get("pronunciation_candidates") or []
    if isinstance(candidates, list):
        result.pronunciation_candidates = [str(x)[:80] for x in candidates[:12]]
    notes = data.get("low_confidence_notes") or []
    if isinstance(notes, list):
        result.low_confidence_notes = [str(x)[:300] for x in notes[:10]]
    return result


def _strip_code_fence(text: str) -> str:
    """Remove ```json fences some models add despite being asked for raw JSON."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


class ByokAnalyzer:
    """Analysis using a user-supplied key and model (v1.1 BYOK).

    Falls back to rule-based extraction if the provider call fails, and records
    why in ``low_confidence_notes``. A failed API call should cost the user a
    slightly weaker analysis, not a dead pipeline; but it must be visible, never
    silent, so the note is mandatory.
    """

    name = "byok"

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        base_url: str | None = None,
        fallback: Analyzer | None = None,
        label: str = "",
    ) -> None:
        from app.services import providers as pv

        self._adapter = pv.get_llm_adapter(provider)
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self.fallback = fallback or RulesAnalyzer()
        self.name = f"byok:{provider}:{model}"
        self.label = label or provider

    def _degrade(self, sources: list[tuple[int, str]], reason: str) -> AnalysisResult:
        result = self.fallback.analyze(sources)
        result.low_confidence_notes.append(reason)
        return result

    def analyze(self, sources: list[tuple[int, str]]) -> AnalysisResult:
        from app.services.providers import ProviderError

        joined = "\n\n".join(f"[source {i}]\n{t}" for i, t in sources if t.strip())[:12000]
        if not joined:
            return self.fallback.analyze(sources)

        try:
            raw = self._adapter.chat_json(
                api_key=self._api_key,
                model=self._model,
                system=_LLM_SYSTEM,
                user=joined,
                base_url=self._base_url,
            )
            data = json.loads(_strip_code_fence(raw))
        except ProviderError as exc:
            return self._degrade(sources, f"{self.label} analysis failed ({exc}); used rules.")
        except (json.JSONDecodeError, TypeError):
            return self._degrade(
                sources, f"{self.label} returned invalid JSON; used rule-based extraction."
            )

        if not isinstance(data, dict):
            return self._degrade(
                sources, f"{self.label} returned an unexpected shape; used rules."
            )
        return parse_llm_json(data, generator=self.name)


def get_analyzer() -> Analyzer:
    """Return the analyzer selected by environment configuration.

    BYOK credentials are resolved per workspace by ``app.services.resolver`` and
    take precedence over this; this remains the fallback for setups configured
    entirely through environment variables.
    """
    if settings.llm_provider == "openai_compatible":
        return LLMAnalyzer()
    return RulesAnalyzer()
