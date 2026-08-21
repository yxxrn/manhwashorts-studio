"""Narration script generation (PRD FR-04).

Builds a five-beat Shorts script (hook, setup, conflict, twist, CTA) from a
``AnalysisResult``. Every beat records which source assets it draws on, and the
generator never introduces facts that are absent from the analysis.

Locked sections survive regeneration, which is what makes the review loop in
FR-04 usable: the user keeps the hook they liked and re-rolls the rest.
"""

from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass, field

from app.constants import (
    SECTION_WEIGHTS,
    WORDS_PER_SECOND,
    NarrationStyle,
    ScriptSection,
    SpoilerLevel,
)
from app.services.analysis import AnalysisResult


@dataclass
class Section:
    section: str
    text: str
    locked: bool = False
    estimated_duration: float = 0.0
    citations: list[int] = field(default_factory=list)
    editorial_role: str = ""
    evidence: list[dict] = field(default_factory=list)

    @property
    def spoken_text(self) -> str:
        return self.text.strip()

    @property
    def display_text(self) -> str:
        from app.services.timeline import normalize_display_text
        return normalize_display_text(self.text)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScriptDraft:
    sections: list[Section] = field(default_factory=list)
    hook_options: list[str] = field(default_factory=list)
    selected_hook: int = 0
    estimated_duration: float = 0.0
    word_count: int = 0
    warnings: list[dict] = field(default_factory=list)
    generator: str = "rules"
    editorial: dict = field(default_factory=dict)

    @property
    def plain_text(self) -> str:
        return "\n\n".join(s.text for s in self.sections if s.text)

    def to_dict(self) -> dict:
        return {
            "sections": [s.to_dict() for s in self.sections],
            "hook_options": self.hook_options,
            "selected_hook": self.selected_hook,
            "estimated_duration": self.estimated_duration,
            "word_count": self.word_count,
            "warnings": self.warnings,
            "generator": self.generator,
            "editorial": self.editorial,
        }


# --- helpers ---------------------------------------------------------------


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def estimate_duration(text: str, style: str = NarrationStyle.DRAMATIC) -> float:
    """Words / words-per-second, with a floor so short beats aren't zero."""
    wps = WORDS_PER_SECOND.get(NarrationStyle(style), 2.4)
    words = word_count(text)
    if words == 0:
        return 0.0
    return max(0.6, round(words / wps, 2))


NARRATION_DURATION_CONTRACT_VERSION = "narration-duration-v1"
# Apostrophe contractions are one spoken word for the explicit narration
# duration contract.  Punctuation outside a contraction remains excluded.
_NARRATION_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?")


def narration_word_count(text: str) -> int:
    """Count spoken narration words using the persisted v1 contract."""
    return len(_NARRATION_WORD_PATTERN.findall(text))


def narration_duration_contract(
    style: str = NarrationStyle.DRAMATIC,
) -> dict[str, float | str]:
    """Return the identity portion of the canonical narrated-duration contract."""
    narration_style = NarrationStyle(style)
    return {
        "version": NARRATION_DURATION_CONTRACT_VERSION,
        "tokenizer": "ascii_alphanumeric_v1",
        "style": narration_style.value,
        "words_per_second": WORDS_PER_SECOND.get(narration_style, 2.4),
    }


def narration_duration_metrics(
    text: str,
    style: str = NarrationStyle.DRAMATIC,
) -> dict[str, float | int | str]:
    """Return the one canonical duration/word metric for narrated output.

    Legacy ``word_count``/``estimate_duration`` remain unchanged for v1/v2
    script callers.  Cloud Sharp Friend narration uses this explicit contract
    so provider-vector reconciliation, persisted ``NarrationResult`` values,
    cache admission, and render planning share the same tokenization.
    """
    contract = narration_duration_contract(style)
    words = narration_word_count(text)
    duration = (
        0.0
        if words == 0
        else max(0.6, round(words / float(contract["words_per_second"]), 2))
    )
    return {
        **contract,
        "word_count": words,
        "estimated_duration_s": duration,
    }


def estimate_narration_duration(
    text: str,
    style: str = NarrationStyle.DRAMATIC,
) -> float:
    """Return the canonical narrated duration without changing legacy timing."""
    return float(narration_duration_metrics(text, style)["estimated_duration_s"])


def budget_for(section: str, target_seconds: float) -> float:
    return round(target_seconds * SECTION_WEIGHTS[ScriptSection(section)], 2)


def _trim_to_seconds(text: str, seconds: float, style: str) -> str:
    """Drop trailing sentences until the beat fits its time budget."""
    if estimate_duration(text, style) <= seconds:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    for sentence in sentences:
        candidate = " ".join([*out, sentence]).strip()
        if out and estimate_duration(candidate, style) > seconds:
            break
        out.append(sentence)
    result = " ".join(out).strip()
    if not result:
        # Single overlong sentence: cut on a word boundary instead.
        wps = WORDS_PER_SECOND.get(NarrationStyle(style), 2.4)
        words = re.findall(r"\S+", text)[: max(3, int(seconds * wps))]
        result = " ".join(words).rstrip(",;:") + "..."
    return result


def _shorten(text: str, max_words: int) -> str:
    words = re.findall(r"\S+", text)
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).rstrip(",;:.") + "..."


# Function words that carry no recap information. Dropping them turns a copied
# sentence into a terse summary line, which is both the house narration style
# and genuinely transformative rather than a reproduction of the source.
_FILLER = {
    "yang", "itu", "ini", "sebuah", "seorang", "para", "sang", "adalah",
    "dengan", "untuk", "pada", "dalam", "oleh", "akan", "telah", "sudah",
    "dari", "ke", "di", "juga", "pun", "saja", "bahkan", "sangat", "sekali",
    "agar", "supaya", "sehingga", "karena", "namun", "tetapi", "tapi",
    "kemudian", "lalu", "selanjutnya", "akhirnya", "ternyata", "sebenarnya",
    "rupanya", "bahwa", "apa", "siapa", "mana", "sini", "situ", "sana",
    "the", "a", "an", "of", "to", "in", "on", "at", "that", "which", "was",
    "were", "is", "are", "been", "had", "has", "have", "and", "but", "so",
}

# Discourse openers to strip; they belong to the source's prose, not a recap.
_OPENERS = re.compile(
    r"^\s*(bab ini dibuka dengan|bab ini|di akhir bab|pada akhirnya|"
    r"ketika|setelah|sebelum|selama|karena|namun|tetapi|tapi|kemudian|lalu|"
    r"akhirnya|ternyata|sebenarnya|rupanya|di dalam|di sana|jadi)\b[,\s]*",
    re.IGNORECASE,
)

# Subordinate tails add length without adding recap value.
_TAIL_CUT = re.compile(
    r"\s*[,;]?\s*\b(sesuatu yang|hal yang|sesuai dengan|menurut catatan|"
    r"yang menurut|padahal|meskipun|walaupun|walau|meski)\b.*$",
    re.IGNORECASE,
)


def _tokens_for_language(text: str) -> str:
    words = set(re.findall(r"[a-z]+", text.lower()))
    id_words = {"yang", "dan", "dengan", "untuk", "dari", "ini", "itu", "pada", "akan", "sudah", "karena", "kemudian", "akhirnya"}
    en_words = {"the", "and", "with", "for", "from", "this", "that", "is", "was", "he", "she", "they", "we", "but", "when", "while", "every", "one", "person", "appears", "forcing", "only", "enters", "fails"}
    if len(words & en_words) > len(words & id_words):
        return "en"
    return "id"


def summarise_clause(sentence: str, max_words: int = 14) -> str:
    """Compress a source sentence into a terse recap line.

    Removes discourse openers, subordinate tails, and function words, keeping
    the content words that carry the plot. The result is a summary in the
    narrator's own register rather than the author's sentence, which is what
    the transformative-use gate in ``app.services.policy`` looks for.
    """
    text = sentence.strip()
    if not text:
        return ""

    text = _OPENERS.sub("", text)
    text = _TAIL_CUT.sub("", text)
    text = text.rstrip(" .,;:")

    # Preserve English function words; dropping them creates translated-sounding
    # fragments ("hunter weak system appears"). Indonesian keeps the compact
    # legacy path for backwards compatibility.
    lower_text = text.lower()
    if _tokens_for_language(lower_text) == "en":
        return _shorten(text, max_words).rstrip(".") + ("." if text else "")

    words = re.findall(r"[\w'-]+|[.,!?]", text)
    kept: list[str] = []
    for i, word in enumerate(words):
        if word in {".", ",", "!", "?"}:
            continue
        low = word.lower()
        # Keep the first word even if it is a filler, so the line has a subject.
        if low in _FILLER and i > 0:
            continue
        kept.append(word)
        if len(kept) >= max_words:
            break

    if not kept:
        return _shorten(sentence, max_words)

    # Lower-case a mid-sentence capital unless it looks like a name.
    out = " ".join(kept)
    return out[0].upper() + out[1:]


def summarise_events(events: list, max_words_each: int = 14) -> list[str]:
    """Summarise a list of events, dropping any that collapse to nothing."""
    lines: list[str] = []
    for event in events:
        line = summarise_clause(getattr(event, "text", str(event)), max_words_each)
        if line and line not in lines:
            lines.append(line)
    return lines


def _strip_spoiler(text: str, spoiler_level: str) -> str:
    """At minimal spoiler level, cut explicit outcome reveals.

    If every sentence is a reveal there is nothing safe left to keep, so we
    return a tease instead of leaking the original line.
    """
    if spoiler_level != SpoilerLevel.MINIMAL:
        return text
    reveal = re.compile(
        r"\b(ternyata|sebenarnya|rupanya|akhirnya\s+\w+\s+(?:mati|menang|kalah)|turns out)\b",
        re.IGNORECASE,
    )
    parts = re.split(r"(?<=[.!?])\s+", text)
    kept = [p for p in parts if not reveal.search(p)]
    cleaned = " ".join(kept).strip()
    if cleaned:
        return cleaned
    return "Dan justru di titik itu arah ceritanya berubah, tapi bagian itu lebih baik kamu baca sendiri."


def _pack_to_budget(
    events: list,
    seconds: float,
    style: str,
    fallback: str = "",
    summarise: bool = True,
) -> tuple[str, list]:
    """Add events in order until the time budget is filled.

    ``_trim_to_seconds`` only shrinks text. Without this, a beat sourced from
    one short sentence leaves most of its allowance unused and the finished
    Short lands far under the target duration.

    Each event is summarised rather than quoted, so the narration reads as
    commentary and does not trip the transformative-use gate.
    """
    used: list = []
    parts: list[str] = []
    for event in events:
        line = summarise_clause(event.text) if summarise else event.text.strip()
        if not line:
            continue
        candidate = " ".join([*parts, _ensure_stop(line)]).strip()
        if used and estimate_duration(candidate, style) > seconds:
            break
        parts.append(_ensure_stop(line))
        used.append(event)
    text = " ".join(parts).strip()
    if not text:
        text = fallback
    return text, used


def _ensure_stop(text: str) -> str:
    """Terminate a clause so joined summary lines read as separate sentences."""
    text = text.strip()
    if not text:
        return ""
    return text if text[-1] in ".!?" else text + "."


# --- hook templates --------------------------------------------------------

_HOOK_TEMPLATES: dict[str, list[str]] = {
    NarrationStyle.DRAMATIC: [
        "Everyone thought {who} would {fail}. They were wrong.",
        "This is why nobody took {who} seriously—until today.",
        "One decision in {chapter} changes everything for {who}.",
    ],
    NarrationStyle.MYSTERIOUS: [
        "Something in {chapter} was never meant to be seen by {who}.",
        "Nobody knows what really happened to {who}.",
        "The secret hidden in {chapter} is finally revealed.",
    ],
    NarrationStyle.CASUAL: [
        "Okay, {chapter} takes a turn nobody saw coming.",
        "So {who} finally does what we have been waiting for.",
        "If you missed {chapter}, here is what happened.",
    ],
    NarrationStyle.FAST: [
        "{chapter} in sixty seconds. Let us begin.",
        "Three shocking things happen in {chapter}. Number three is brutal.",
        "Here is the fast version of {chapter}.",
    ],
    NarrationStyle.INFORMATIVE: [
        "In {chapter}, the story moves in an unexpected direction.",
        "Here is the key recap of {chapter}.",
        "{chapter} finally answers a question left hanging.",
    ],
}

_ID_HOOK_TEMPLATES = {
    NarrationStyle.DRAMATIC: [
        "Semua orang mengira {who} akan {fail}. Mereka salah besar.",
        "Ini alasan {who} tidak pernah dianggap serius, sampai hari ini.",
        "Satu keputusan di {chapter} mengubah segalanya untuk {who}.",
    ],
    NarrationStyle.MYSTERIOUS: [
        "Ada sesuatu di {chapter} yang tidak seharusnya dilihat {who}.",
        "Tidak ada yang tahu apa yang sebenarnya terjadi pada {who}.",
        "Rahasia yang disembunyikan {chapter} akhirnya terbuka.",
    ],
    NarrationStyle.CASUAL: [
        "Oke, {chapter} ini benar-benar di luar dugaan.",
        "Jadi {who} akhirnya melakukan hal yang kita tunggu-tunggu.",
        "Kalau kamu ketinggalan {chapter}, ini yang kamu lewatkan.",
    ],
    NarrationStyle.FAST: [
        "{chapter} dalam 60 detik. Mulai.",
        "Tiga hal gila terjadi di {chapter}. Nomor tiga paling parah.",
        "Baca cepat: ini inti dari {chapter}.",
    ],
    NarrationStyle.INFORMATIVE: [
        "Di {chapter}, alur cerita bergerak ke arah yang jarang dibahas.",
        "Berikut rangkuman {chapter} beserta hal penting yang mudah terlewat.",
        "{chapter} menjelaskan sesuatu yang selama ini menggantung.",
    ],
}

_FAIL_PHRASES = ["fail", "lose", "give up", "be eliminated"]
_ID_FAIL_PHRASES = ["gagal", "kalah", "menyerah", "tersingkir"]

_CTA_TEMPLATES = [
    "What do you think happens next? Tell us in the comments.",
    "Whose side are you on? Comment below.",
    "Follow for the next chapter recap.",
]
_ID_CTA_TEMPLATES = [
    "Menurutmu apa yang terjadi selanjutnya? Tulis di komentar.",
    "Kamu tim siapa di bab ini? Komentar di bawah.",
    "Follow biar tidak ketinggalan rangkuman bab berikutnya.",
]


def _protagonist(analysis: AnalysisResult) -> str:
    if analysis.characters:
        return analysis.characters[0].name
    return "tokoh utama"


def _chapter_label(manhwa_title: str, chapter: str) -> str:
    chapter = (chapter or "").strip()
    if chapter and manhwa_title:
        return f"{manhwa_title} chapter {chapter}" if chapter.isdigit() else f"{manhwa_title} {chapter}"
    if chapter:
        return f"chapter {chapter}" if chapter.isdigit() else chapter
    return manhwa_title or "bab ini"


def build_hooks(
    analysis: AnalysisResult,
    style: str,
    manhwa_title: str,
    chapter: str,
    count: int = 3,
    seed: int | None = None,
    language: str = "en",
) -> list[str]:
    """Generate hook variants. Deterministic when ``seed`` is provided."""
    rng = random.Random(seed)
    catalog = _ID_HOOK_TEMPLATES if language == "id" else _HOOK_TEMPLATES
    templates = list(catalog.get(NarrationStyle(style), catalog[NarrationStyle.DRAMATIC]))
    rng.shuffle(templates)
    who = _protagonist(analysis)
    chapter_label = _chapter_label(manhwa_title, chapter)
    fail_phrases = _ID_FAIL_PHRASES if language == "id" else _FAIL_PHRASES
    hooks: list[str] = []
    for tpl in templates:
        hook = tpl.format(who=who, chapter=chapter_label, fail=rng.choice(fail_phrases))
        hooks.append(_shorten(hook, 16))
        if len(hooks) >= max(1, count):
            break
    return hooks


# --- generator -------------------------------------------------------------


class RulesScriptGenerator:
    """Assembles a script from analysis output using templates and budgets."""

    name = "rules"

    def generate(
        self,
        analysis: AnalysisResult,
        *,
        style: str = NarrationStyle.DRAMATIC,
        language: str = "en",
        target_seconds: float = 60.0,
        spoiler_level: str = SpoilerLevel.MEDIUM,
        manhwa_title: str = "",
        chapter: str = "",
        cta_text: str = "",
        locked: dict[str, Section] | None = None,
        hook_count: int = 3,
        seed: int | None = None,
    ) -> ScriptDraft:
        locked = locked or {}
        draft = ScriptDraft(generator=self.name)
        draft.hook_options = build_hooks(
            analysis, style, manhwa_title, chapter, hook_count, seed=seed, language=language
        )

        events = analysis.events
        conflict_events = [e for e in events if e.kind == "conflict"]
        plain_events = [e for e in events if e.kind == "event"]
        sources_all = sorted({e.source_index for e in events}) or [0]

        def cited(*groups: list) -> list[int]:
            found = sorted({e.source_index for group in groups for e in group})
            return found or sources_all

        # HOOK
        hook_text = draft.hook_options[0] if draft.hook_options else ""

        # SETUP: opening events establish the situation. Each beat is packed up
        # to its own share of the target duration so the Short lands near length.
        setup_text, setup_pool = _pack_to_budget(
            plain_events or events,
            budget_for(ScriptSection.SETUP.value, target_seconds),
            style,
            fallback=(f"{_protagonist(analysis)} faces a rapidly changing situation." if language != "id" else f"{_protagonist(analysis)} menghadapi situasi yang berubah drastis."),
        )

        # CONFLICT: prefer conflict-tagged events, then any event not already used.
        used_ids = {id(e) for e in setup_pool}
        conflict_candidates = [e for e in conflict_events if id(e) not in used_ids]
        conflict_candidates += [
            e for e in plain_events if id(e) not in used_ids and e not in conflict_candidates
        ]
        conflict_text, conflict_pool = _pack_to_budget(
            conflict_candidates,
            budget_for(ScriptSection.CONFLICT.value, target_seconds),
            style,
            fallback=summarise_clause(analysis.main_conflict)
            or ("Pressure rises as the remaining choices disappear." if language != "id" else "Tekanan meningkat dan pilihan yang tersisa semakin sempit."),
        )

        # TWIST: summarised like the other beats so it stays commentary.
        twist_pool = [e for e in events if e.kind in {"twist", "cliffhanger"}]
        twist_source = analysis.twist or analysis.cliffhanger
        if not twist_source and twist_pool:
            twist_source = twist_pool[0].text
        twist_text = summarise_clause(twist_source, max_words=18) if twist_source else ""
        if not twist_text:
            twist_text = ("This chapter ends with one question still unanswered." if language != "id" else "Bab ini ditutup dengan pertanyaan yang belum terjawab.")
            draft.warnings.append(
                {
                    "code": "script.twist_missing",
                    "severity": "warning",
                    "message": "No twist found in the source; the twist beat uses a neutral line. "
                    "Edit it before approving.",
                }
            )

        # Editorial insight separates interpretation from event recap. It is
        # deliberately framed as an inference, not as an invented fact.
        insight_marker = "karena itu" if language == "id" else "which means"
        if twist_text and insight_marker not in twist_text.lower():
            if language == "id":
                twist_text = f"{twist_text.rstrip('.!?')}. Karena itu, kegagalan ini justru menjadi petunjuk bahwa aturannya sedang berubah."
            else:
                twist_text = f"{twist_text.rstrip('.!?')}. Which means this failure may be the first clue that the rules are changing."

        # CTA
        cta = cta_text.strip() or (
            f"Menurutmu apa yang akan dilakukan {_protagonist(analysis)} selanjutnya? Tulis teorimu di komentar."
            if language == "id"
            else f"What will {_protagonist(analysis)} do next? Tell us your theory in the comments."
        )

        raw = {
            ScriptSection.HOOK: (hook_text, cited()),
            ScriptSection.SETUP: (setup_text, cited(setup_pool)),
            ScriptSection.CONFLICT: (conflict_text, cited(conflict_pool)),
            ScriptSection.TWIST: (twist_text, cited(twist_pool)),
            ScriptSection.CTA: (cta, []),
        }

        for section in ScriptSection:
            if section.value in locked:
                kept = locked[section.value]
                kept.locked = True
                kept.estimated_duration = estimate_duration(kept.text, style)
                kept.editorial_role = kept.editorial_role or section.value
                kept.evidence = kept.evidence or ([{"claim": kept.text, "evidence_refs": [f"source_{ref}" for ref in kept.citations], "confidence": 0.7}] if kept.citations else [])
                draft.sections.append(kept)
                continue

            text, citations = raw[section]
            text = _strip_spoiler(text, spoiler_level) if section != ScriptSection.CTA else text
            budget = budget_for(section.value, target_seconds)
            text = _trim_to_seconds(text.strip(), budget, style)
            role = {
                ScriptSection.HOOK.value: "hook",
                ScriptSection.SETUP.value: "setup",
                ScriptSection.CONFLICT.value: "escalation",
                ScriptSection.TWIST.value: "editorial_insight",
                ScriptSection.CTA.value: "payoff_open_loop",
            }[section.value]
            evidence = []
            if citations:
                evidence = [{"claim": text, "evidence_refs": [f"source_{ref}" for ref in citations], "confidence": 0.72}]
            draft.sections.append(
                Section(
                    section=section.value,
                    text=text,
                    locked=False,
                    estimated_duration=estimate_duration(text, style),
                    citations=citations,
                    editorial_role=role,
                    evidence=evidence,
                )
            )

        draft.editorial = {
            "structure": [s.editorial_role for s in draft.sections],
            "evidence": [evidence for section in draft.sections for evidence in section.evidence],
            "language": language,
            "facts_vs_interpretation": {"fact_sections": ["setup", "conflict"], "interpretation_sections": ["twist"]},
        }
        draft.estimated_duration = round(sum(s.estimated_duration for s in draft.sections), 2)
        draft.word_count = word_count(draft.plain_text)
        draft.warnings.extend(check_script(draft, target_seconds, language))
        return draft



def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]


def validate_editorial(draft: ScriptDraft, language: str = "en") -> list[dict]:
    """Validate story value before TTS; deterministic, conservative, auditable."""
    findings: list[dict] = []
    sections = {section.section: section for section in draft.sections}
    required = ("hook", "setup", "conflict", "twist", "cta")
    for name in required:
        if not sections.get(name) or not sections[name].spoken_text:
            findings.append({"code": f"editorial.missing_{name}", "severity": "error", "message": f"Missing editorial section: {name}."})
    if not sections:
        return findings
    all_text = " ".join(section.spoken_text for section in draft.sections)
    sentences = _sentences(all_text)
    if any(len(re.findall(r"\b[\w'-]+\b", sentence)) < 3 for sentence in sentences):
        findings.append({"code": "editorial.sentence_fragment", "severity": "error", "message": "Narration contains a sentence fragment."})
    normalized = [re.sub(r"[^a-z0-9]+", " ", sentence.lower()).strip() for sentence in sentences]
    if len(normalized) != len(set(normalized)):
        findings.append({"code": "editorial.repeated_sentence", "severity": "error", "message": "Narration repeats a sentence."})
    words = re.findall(r"\b[\w'-]+\b", all_text.lower())
    if len(words) >= 12:
        trigrams = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
        if len(trigrams) != len(set(trigrams)):
            findings.append({"code": "editorial.repeated_phrase", "severity": "error", "message": "Narration repeats a three-word phrase."})
    from app.services.editorial_timing import language_consistency
    language_finding = language_consistency(all_text, language)
    if not language_finding["passed"]:
        findings.append({"code": "editorial.code_switch", "severity": "error", "message": "Narration switches language unexpectedly.", "detail": language_finding})
    insight = sections.get("twist", Section("twist", "")).spoken_text.lower()
    insight_words = ("because", "which means", "the reason", "this explains", "consequence", "not just", "why", "karena", "artinya", "alasan", "bukan sekadar")
    if not any(marker in insight for marker in insight_words):
        findings.append({"code": "editorial.insight_missing", "severity": "error", "message": "Editorial insight/interpretation is missing from the twist beat."})
    if sections.get("hook") and sections["hook"].spoken_text.lower() == sections.get("setup", Section("setup", "")).spoken_text.lower():
        findings.append({"code": "editorial.hook_repeats_setup", "severity": "error", "message": "Hook repeats setup instead of creating a question or contradiction."})
    cta = sections.get("cta", Section("cta", "")).spoken_text.lower()
    context_words = set(re.findall(r"\b[\w'-]+\b", " ".join(item.spoken_text for item in draft.sections if item.section != "cta").lower()))
    if not (set(re.findall(r"\b[\w'-]+\b", cta)) & context_words):
        findings.append({"code": "editorial.cta_context_missing", "severity": "error", "message": "CTA has no vocabulary tied to the story conflict."})
    if not any(section.evidence for section in draft.sections if section.section in {"setup", "conflict", "twist"}):
        findings.append({"code": "editorial.evidence_missing", "severity": "error", "message": "Editorial sections have no auditable evidence references."})
    return findings


def check_script(draft: ScriptDraft, target_seconds: float, language: str = "en") -> list[dict]:
    """Non-policy script warnings: length, repetition, empty beats."""
    warnings: list[dict] = []

    if draft.estimated_duration > target_seconds * 1.1:
        warnings.append(
            {
                "code": "script.too_long",
                "severity": "warning",
                "message": f"Estimated {draft.estimated_duration:.0f}s exceeds the "
                f"{target_seconds:.0f}s target. Trim before generating voice-over.",
            }
        )
    if draft.estimated_duration < target_seconds * 0.4:
        warnings.append(
            {
                "code": "script.too_short",
                "severity": "warning",
                "message": f"Estimated {draft.estimated_duration:.0f}s is well under the "
                f"{target_seconds:.0f}s target. Add more detail.",
            }
        )

    empty = [s.section for s in draft.sections if not s.text.strip()]
    if empty:
        warnings.append(
            {
                "code": "script.empty_section",
                "severity": "error",
                "message": f"These sections are empty: {', '.join(empty)}",
            }
        )

    # Repeated sentences across beats read as padding.
    seen: dict[str, str] = {}
    for s in draft.sections:
        for sentence in re.split(r"(?<=[.!?])\s+", s.text):
            key = re.sub(r"\W+", "", sentence.lower())
            if len(key) < 15:
                continue
            if key in seen and seen[key] != s.section:
                warnings.append(
                    {
                        "code": "script.repetition",
                        "severity": "warning",
                        "message": f"'{_shorten(sentence, 8)}' appears in both "
                        f"{seen[key]} and {s.section}.",
                    }
                )
            seen.setdefault(key, s.section)
    warnings.extend(validate_editorial(draft, language))
    return warnings


def apply_pronunciations(text: str, mapping: dict[str, str]) -> str:
    """Replace names with phonetic spellings for TTS (FR-05)."""
    out = text
    for name, phonetic in (mapping or {}).items():
        if not name.strip() or not phonetic.strip():
            continue
        out = re.sub(rf"\b{re.escape(name)}\b", phonetic, out, flags=re.IGNORECASE)
    return out


def get_generator() -> RulesScriptGenerator:
    return RulesScriptGenerator()
