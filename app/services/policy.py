"""Rights and content policy gates (PRD section 8).

This module is the single place that decides whether a project may be
published. Keeping the logic here means the API, the worker, and the tests all
answer the question the same way.

Scope note: these are operational guardrails for recording permissions and
provenance. They are not a legal determination of fair use or fair dealing,
which depends on jurisdiction and context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import settings
from app.constants import AssetType, CheckSeverity, RightsStatus
from app.models import Project, SourceAsset


@dataclass
class PolicyFinding:
    """One policy observation. Errors block publication, warnings do not."""

    code: str
    severity: str
    message: str
    detail: dict = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == CheckSeverity.ERROR


def unrightsed_assets(assets: list[SourceAsset]) -> list[SourceAsset]:
    """Assets that have not been declared and so cannot be published."""
    return [a for a in assets if not a.is_publishable]


def check_rights(assets: list[SourceAsset]) -> list[PolicyFinding]:
    """Every asset used in a video needs a rights declaration."""
    findings: list[PolicyFinding] = []
    if not settings.require_rights_declaration:
        findings.append(
            PolicyFinding(
                "rights.enforcement_disabled",
                CheckSeverity.WARNING,
                "Rights enforcement is disabled in config; publication gate is weakened.",
            )
        )
        return findings

    missing = unrightsed_assets(assets)
    if missing:
        findings.append(
            PolicyFinding(
                "rights.undeclared_assets",
                CheckSeverity.ERROR,
                f"{len(missing)} asset(s) have no rights declaration. "
                "Declare the owner and licence basis, or remove them.",
                {"asset_ids": [a.id for a in missing]},
            )
        )

    rejected = [a for a in assets if a.rights_status == RightsStatus.REJECTED]
    if rejected:
        findings.append(
            PolicyFinding(
                "rights.rejected_assets",
                CheckSeverity.ERROR,
                f"{len(rejected)} asset(s) are marked as rejected and must not be used.",
                {"asset_ids": [a.id for a in rejected]},
            )
        )
    return findings


def check_panel_volume(assets: list[SourceAsset]) -> list[PolicyFinding]:
    """Discourage reproducing a whole chapter panel-by-panel.

    A recap that reprints most of the source is far less likely to read as
    transformative commentary.
    """
    images = [a for a in assets if a.type == AssetType.IMAGE]
    limit = settings.max_consecutive_panels_per_chapter
    if len(images) > limit:
        return [
            PolicyFinding(
                "policy.panel_volume",
                CheckSeverity.WARNING,
                f"{len(images)} panels from one chapter exceeds the configured "
                f"guideline of {limit}. Consider trimming to keep the video "
                "commentary-led rather than a reproduction.",
                {"panel_count": len(images), "limit": limit},
            )
        ]
    return []


_WATERMARK_MARKERS = ("asurascans", "discord.gg", "follow us", "continue reading", "read the novel")


def check_source_cleanliness(assets: list[SourceAsset]) -> list[PolicyFinding]:
    """Reject third-party marks; allow only explicit test fixtures."""
    hits = [
        a for a in assets
        if any(marker in f"{a.original_filename} {a.source_name} {a.extracted_text}".lower() for marker in _WATERMARK_MARKERS)
    ]
    if not hits:
        return []
    test_only = all("not_for_publication" in f"{a.source_name} {a.permission_reference}".lower() for a in hits)
    severity = CheckSeverity.WARNING if test_only else CheckSeverity.ERROR
    code = "source.test_only_watermark" if test_only else "source.third_party_watermark"
    return [PolicyFinding(code, severity, f"{len(hits)} source asset(s) contain third-party watermark/banner markers.", {"asset_ids": [a.id for a in hits], "test_only": test_only})]


_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text)]


def _shingles(tokens: list[str], n: int = 5) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(max(0, len(tokens) - n + 1))}


def similarity_ratio(script_text: str, source_text: str, n: int = 5) -> float:
    """Fraction of the script's n-gram shingles that appear verbatim in source.

    1.0 means the narration is a straight copy; near 0 means it is rewritten.
    """
    script_shingles = _shingles(_tokens(script_text), n)
    if not script_shingles:
        return 0.0
    source_shingles = _shingles(_tokens(source_text), n)
    if not source_shingles:
        return 0.0
    overlap = script_shingles & source_shingles
    return len(overlap) / len(script_shingles)


def check_transformative(script_text: str, source_text: str) -> list[PolicyFinding]:
    """Flag narration that merely reads the source material aloud."""
    if not script_text.strip() or not source_text.strip():
        return []
    ratio = similarity_ratio(script_text, source_text)
    if ratio >= 0.5:
        return [
            PolicyFinding(
                "policy.not_transformative",
                CheckSeverity.ERROR,
                f"{ratio:.0%} of the narration is copied verbatim from the source. "
                "Rewrite it as your own commentary before publishing.",
                {"similarity": round(ratio, 3)},
            )
        ]
    if ratio >= 0.25:
        return [
            PolicyFinding(
                "policy.high_similarity",
                CheckSeverity.WARNING,
                f"{ratio:.0%} of the narration matches the source wording. "
                "More paraphrasing would strengthen the recap.",
                {"similarity": round(ratio, 3)},
            )
        ]
    return []


def check_banned_words(script_text: str, banned: list[str]) -> list[PolicyFinding]:
    lowered = script_text.lower()
    hits = [w for w in banned if w.strip() and w.strip().lower() in lowered]
    if hits:
        return [
            PolicyFinding(
                "script.banned_words",
                CheckSeverity.ERROR,
                f"Script contains banned words: {', '.join(hits)}",
                {"words": hits},
            )
        ]
    return []


def check_citations(sections: list[dict]) -> list[PolicyFinding]:
    """Narration beats should trace back to source material."""
    factual = [s for s in sections if s.get("section") not in {"cta"}]
    uncited = [s["section"] for s in factual if not s.get("citations")]
    if uncited:
        return [
            PolicyFinding(
                "script.uncited_claims",
                CheckSeverity.WARNING,
                f"These sections have no source citation: {', '.join(uncited)}. "
                "Unsourced claims risk stating things the chapter never showed.",
                {"sections": uncited},
            )
        ]
    return []


def check_public_publish(privacy_status: str) -> list[PolicyFinding]:
    """Public publishing is opt-in via config, per PRD guardrail metrics."""
    if privacy_status == "public" and not settings.allow_public_publish:
        return [
            PolicyFinding(
                "publish.public_disabled",
                CheckSeverity.ERROR,
                "Public publishing is disabled. Upload as private or unlisted, "
                "review the video, then enable MS_ALLOW_PUBLIC_PUBLISH.",
            )
        ]
    return []


def evaluate_project(
    project: Project,
    assets: list[SourceAsset],
    script_text: str = "",
    sections: list[dict] | None = None,
) -> list[PolicyFinding]:
    """Run every policy gate relevant before render/publish."""
    source_text = "\n".join(a.extracted_text for a in assets if a.extracted_text)
    findings: list[PolicyFinding] = []
    findings += check_rights(assets)
    findings += check_panel_volume(assets)
    findings += check_source_cleanliness(assets)
    if script_text:
        findings += check_transformative(script_text, source_text)
        findings += check_banned_words(script_text, list(project.banned_words or []))
    if sections:
        findings += check_citations(sections)
    return findings


def blocking(findings: list[PolicyFinding]) -> list[PolicyFinding]:
    return [f for f in findings if f.blocking]
