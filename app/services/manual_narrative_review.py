"""Offline, provenance-labeled helpers for a manual narrative review bundle."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from PIL import Image

PROVENANCE_KIND = "codex_manual_vision_reference_v1"
INTERNAL_REVIEW_RIGHTS = "internal review only"
EXPECTED_SOURCE_ORDERS = tuple(range(24))
BUNDLE_FILES = (
    "source_ledger.json",
    "panel_understanding.json",
    "chapter_map.json",
    "narrative_review.json",
    "narration_spoken.txt",
    "display_cues.json",
    "qc_report.json",
)


class ManualReviewError(ValueError):
    """Safe, stable failure for invalid local review inputs."""

    def __init__(self, code: str, message: str = "manual review input is invalid") -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class SourceLedgerEntry:
    source_order: int
    source_asset_id: str
    panel_id: str
    review_path: str
    source_storage_path: str
    sha256: str
    width: int
    height: int
    rights_status: str
    included_in_story: bool
    exclusion_reason: str


@dataclass(frozen=True)
class ManualReviewLedger:
    provenance_kind: str
    production_evidence: bool
    production_analysis: bool
    publish_allowed: bool
    rights_status: str
    entries: tuple[SourceLedgerEntry, ...]
    ledger_sha256: str


@dataclass(frozen=True)
class ManualPanelObservation:
    source_order: int
    source_asset_id: str
    panel_id: str
    visible_summary: str
    visible_entities: tuple[str, ...]
    actions: tuple[str, ...]
    setting_or_continuity: str
    dialogue_present: bool
    dialogue_paraphrase: str
    uncertainties: tuple[str, ...]
    confidence: str
    evidence_status: str = "manual_visual_review"
    region_bounds: tuple[int, int, int, int] = (0, 0, 1, 1)
    dialogue_or_ocr: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManualNarrativeReview:
    panel_observations: tuple[ManualPanelObservation, ...]
    chapter_map: Mapping[str, object]
    passages: tuple[Mapping[str, object], ...]
    ending_kind: str
    unresolved_question: str
    spoken_text: str
    claims: tuple[Mapping[str, object], ...] = ()


def _fail(code: str, message: str = "manual review input is invalid") -> None:
    raise ManualReviewError(code, message)


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _nonempty(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(code)
    return value.strip()


def _positive_int(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail(code)
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        _fail("review.source_unreadable")
    raise AssertionError("unreachable")


def _resolved_review_path(review_path: str, base_dir: Path) -> Path:
    candidate = (base_dir / review_path).resolve()
    root = base_dir.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        _fail("review.source_path_invalid")
    if not candidate.is_file():
        _fail("review.source_missing")
    return candidate


def _read_dimensions(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, ValueError):
        _fail("review.source_dimensions_invalid")
    raise AssertionError("unreachable")


def _rights_status(item: Mapping[str, Any]) -> str:
    raw_rights = item.get("rights")
    rights = raw_rights if isinstance(raw_rights, Mapping) else {}
    values = " ".join(str(value) for value in rights.values()).casefold()
    declared = str(item.get("rights_status", "")).casefold()
    if INTERNAL_REVIEW_RIGHTS not in values and INTERNAL_REVIEW_RIGHTS not in declared:
        _fail("review.rights_invalid")
    return INTERNAL_REVIEW_RIGHTS


def _entry_from_manifest(item: Mapping[str, Any], base_dir: Path, index: int) -> SourceLedgerEntry:
    source_order = item.get("source_order")
    if isinstance(source_order, bool) or not isinstance(source_order, int):
        _fail("review.source_order_invalid")
    if source_order != index:
        _fail("review.source_order_invalid")

    source_asset_id = _nonempty(
        item.get("source_asset_id", item.get("asset_id")),
        "review.asset_lineage_invalid",
    )
    panel_id = _nonempty(
        item.get("panel_id", source_asset_id),
        "review.panel_lineage_invalid",
    )
    review_path = _nonempty(item.get("review_path"), "review.source_path_invalid")
    if Path(review_path).is_absolute():
        _fail("review.source_path_invalid")
    source_storage_path = _nonempty(
        item.get("source_storage_path", item.get("storage_path")),
        "review.storage_lineage_invalid",
    )
    declared_sha = _nonempty(
        item.get("sha256", item.get("checksum")),
        "review.source_checksum_invalid",
    ).casefold()
    if len(declared_sha) != 64 or any(character not in "0123456789abcdef" for character in declared_sha):
        _fail("review.source_checksum_invalid")
    width = _positive_int(item.get("width"), "review.source_dimensions_invalid")
    height = _positive_int(item.get("height"), "review.source_dimensions_invalid")
    included = source_order != 0
    if "included_in_story" in item and item["included_in_story"] is not included:
        _fail("review.title_scope_invalid")
    exclusion_reason = "title_front_matter" if source_order == 0 else ""
    if source_order == 0 and item.get("exclusion_reason", exclusion_reason) != exclusion_reason:
        _fail("review.title_scope_invalid")
    if source_order != 0 and item.get("exclusion_reason", ""):
        _fail("review.title_scope_invalid")

    path = _resolved_review_path(review_path, base_dir)
    if _sha256(path) != declared_sha:
        _fail("review.source_checksum_mismatch")
    if _read_dimensions(path) != (width, height):
        _fail("review.source_dimensions_mismatch")
    return SourceLedgerEntry(
        source_order=source_order,
        source_asset_id=source_asset_id,
        panel_id=panel_id,
        review_path=review_path.replace("\\", "/"),
        source_storage_path=source_storage_path,
        sha256=declared_sha,
        width=width,
        height=height,
        rights_status=_rights_status(item),
        included_in_story=included,
        exclusion_reason=exclusion_reason,
    )


def canonical_ledger_json(ledger: ManualReviewLedger, *, include_hash: bool = False) -> str:
    """Serialize the ledger deterministically, omitting its derived hash by default."""

    payload = asdict(ledger)
    payload["entries"] = [asdict(entry) for entry in ledger.entries]
    if not include_hash:
        payload.pop("ledger_sha256", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_source_ledger(
    ledger: ManualReviewLedger,
    *,
    base_dir: Path,
    expected_orders: Sequence[int] = EXPECTED_SOURCE_ORDERS,
) -> ManualReviewLedger:
    """Revalidate an immutable ledger against current local bytes and dimensions."""

    expected = tuple(expected_orders)
    if ledger.provenance_kind != PROVENANCE_KIND:
        _fail("review.provenance_invalid")
    if ledger.production_evidence or ledger.production_analysis or ledger.publish_allowed:
        _fail("review.provenance_invalid")
    if ledger.rights_status != INTERNAL_REVIEW_RIGHTS:
        _fail("review.rights_invalid")
    if tuple(entry.source_order for entry in ledger.entries) != expected:
        _fail("review.source_coverage_invalid")
    if len({entry.source_asset_id for entry in ledger.entries}) != len(ledger.entries):
        _fail("review.asset_lineage_invalid")
    if len({entry.panel_id for entry in ledger.entries}) != len(ledger.entries):
        _fail("review.panel_lineage_invalid")
    if ledger.entries[0].included_in_story or ledger.entries[0].exclusion_reason != "title_front_matter":
        _fail("review.title_scope_invalid")
    if any(not entry.included_in_story or entry.exclusion_reason for entry in ledger.entries[1:]):
        _fail("review.title_scope_invalid")
    for entry in ledger.entries:
        item = {
            "source_order": entry.source_order,
            "source_asset_id": entry.source_asset_id,
            "panel_id": entry.panel_id,
            "review_path": entry.review_path,
            "source_storage_path": entry.source_storage_path,
            "sha256": entry.sha256,
            "width": entry.width,
            "height": entry.height,
            "rights": {"permission_reference": INTERNAL_REVIEW_RIGHTS},
        }
        current = _entry_from_manifest(item, base_dir, entry.source_order)
        if current != entry:
            _fail("review.ledger_drift")
    expected_hash = hashlib.sha256(canonical_ledger_json(ledger).encode("utf-8")).hexdigest()
    if ledger.ledger_sha256 and ledger.ledger_sha256 != expected_hash:
        _fail("review.ledger_hash_mismatch")
    return replace(ledger, ledger_sha256=expected_hash)


def load_source_ledger(path: Path, *, base_dir: Path) -> ManualReviewLedger:
    """Load and verify a local manifest without opening historical remote paths."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("review.manifest_invalid")
    document = _mapping(payload, "review.manifest_invalid")
    assets = document.get("assets", document.get("entries"))
    if not isinstance(assets, list) or len(assets) != len(EXPECTED_SOURCE_ORDERS):
        _fail("review.source_coverage_invalid")
    if document.get("asset_count", len(assets)) != len(assets):
        _fail("review.source_coverage_invalid")
    declared_orders = document.get("source_order_coverage")
    if declared_orders is not None and tuple(declared_orders) != EXPECTED_SOURCE_ORDERS:
        _fail("review.source_coverage_invalid")
    entries = tuple(
        _entry_from_manifest(_mapping(item, "review.manifest_invalid"), base_dir, index)
        for index, item in enumerate(assets)
    )
    ledger = ManualReviewLedger(
        provenance_kind=PROVENANCE_KIND,
        production_evidence=False,
        production_analysis=False,
        publish_allowed=False,
        rights_status=INTERNAL_REVIEW_RIGHTS,
        entries=entries,
        ledger_sha256="",
    )
    return validate_source_ledger(ledger, base_dir=base_dir)


def derive_display_cues(spoken_text: str) -> tuple[dict[str, object], ...]:
    """Derive one punctuation-free display word from each spoken token."""

    from app.services.timeline import normalize_display_text

    cues: list[dict[str, object]] = []
    for spoken_token_index, token in enumerate(re.findall(r"\S+", str(spoken_text))):
        display_text = normalize_display_text(token)
        if not display_text:
            continue
        if " " in display_text or not display_text.isalnum() or display_text != display_text.upper():
            _fail("review.display_derivation_invalid")
        cues.append(
            {
                "spoken_token_index": spoken_token_index,
                "display_text": display_text,
                "timing_status": "not_rendered",
            }
        )
    return tuple(cues)


def _validate_bundle_provenance(bundle: Mapping[str, Any], ledger: ManualReviewLedger) -> None:
    if bundle.get("provenance_kind") != PROVENANCE_KIND:
        _fail("review.provenance_invalid")
    if bundle.get("production_evidence") is not False:
        _fail("review.provenance_invalid")
    if bundle.get("production_analysis") is not False:
        _fail("review.provenance_invalid")
    if bundle.get("publish_allowed") is not False:
        _fail("review.provenance_invalid")
    if bundle.get("rights_status") != INTERNAL_REVIEW_RIGHTS:
        _fail("review.rights_invalid")
    narrative = _mapping(bundle.get("narrative_review"), "review.narrative_invalid")
    if narrative.get("provenance_kind") != PROVENANCE_KIND:
        _fail("review.provenance_invalid")
    if narrative.get("source_ledger_sha256", ledger.ledger_sha256) != ledger.ledger_sha256:
        _fail("review.ledger_hash_mismatch")


_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "image_path",
        "audio_path",
        "video_path",
        "media_path",
        "database_path",
        "db_path",
        "credential",
        "api_key",
        "authorization",
    }
)


def _reject_media_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_PAYLOAD_KEYS:
                _fail("review.media_payload_forbidden")
            _reject_media_payload(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _reject_media_payload(child)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        _fail("review.bundle_write_failed")


def write_review_bundle(
    root: Path, bundle: Mapping[str, object], *, ledger: ManualReviewLedger
) -> Path:
    """Write the sanitized review files atomically under an ignored local root."""

    _validate_bundle_provenance(bundle, ledger)
    _reject_media_payload(bundle)
    spoken_text = bundle.get("narration_spoken")
    if not isinstance(spoken_text, str) or not spoken_text.strip():
        _fail("review.narration_invalid")
    derived_cues = derive_display_cues(spoken_text)
    supplied_cues = bundle.get("display_cues")
    if supplied_cues is not None and supplied_cues != list(derived_cues):
        _fail("review.display_derivation_invalid")

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        _fail("review.bundle_write_failed")
    narrative = dict(_mapping(bundle["narrative_review"], "review.narrative_invalid"))
    narrative["source_ledger_sha256"] = ledger.ledger_sha256
    files: dict[str, str] = {
        "source_ledger.json": canonical_ledger_json(ledger, include_hash=True),
        "panel_understanding.json": _canonical_json(bundle.get("panel_understanding", [])),
        "chapter_map.json": _canonical_json(bundle.get("chapter_map", {})),
        "narrative_review.json": _canonical_json(narrative),
        "narration_spoken.txt": spoken_text,
        "display_cues.json": _canonical_json(list(derived_cues)),
        "qc_report.json": _canonical_json(bundle.get("qc_report", {})),
    }
    for filename in BUNDLE_FILES:
        _write_atomic(root / filename, files[filename])
    try:
        result = read_review_bundle(root, ledger=ledger)
    except ManualReviewError:
        raise
    except Exception:
        _fail("review.bundle_round_trip_invalid")
    if result["narration_spoken"] != spoken_text:
        _fail("review.bundle_round_trip_invalid")
    return root


def read_review_bundle(root: Path, *, ledger: ManualReviewLedger) -> dict[str, object]:
    """Read exactly one sanitized bundle and verify its immutable input hash."""

    try:
        names = {path.name for path in root.iterdir() if path.is_file()}
    except OSError:
        _fail("review.bundle_missing")
    if names != set(BUNDLE_FILES):
        _fail("review.bundle_files_invalid")
    try:
        source_ledger = json.loads((root / "source_ledger.json").read_text(encoding="utf-8"))
        panel_understanding = json.loads(
            (root / "panel_understanding.json").read_text(encoding="utf-8")
        )
        chapter_map = json.loads((root / "chapter_map.json").read_text(encoding="utf-8"))
        narrative_review = json.loads(
            (root / "narrative_review.json").read_text(encoding="utf-8")
        )
        display_cues = json.loads((root / "display_cues.json").read_text(encoding="utf-8"))
        qc_report = json.loads((root / "qc_report.json").read_text(encoding="utf-8"))
        narration_spoken = (root / "narration_spoken.txt").read_text(encoding="utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("review.bundle_files_invalid")
    expected_source = json.loads(canonical_ledger_json(ledger, include_hash=True))
    if source_ledger != expected_source:
        _fail("review.ledger_hash_mismatch")
    metadata = {
        "provenance_kind": narrative_review.get("provenance_kind"),
        "production_evidence": False,
        "production_analysis": False,
        "publish_allowed": False,
        "rights_status": INTERNAL_REVIEW_RIGHTS,
        "narrative_review": narrative_review,
    }
    _validate_bundle_provenance(metadata, ledger)
    expected_cues = list(derive_display_cues(narration_spoken))
    if display_cues != expected_cues:
        _fail("review.display_derivation_invalid")
    return {
        "provenance_kind": PROVENANCE_KIND,
        "production_evidence": False,
        "production_analysis": False,
        "publish_allowed": False,
        "rights_status": INTERNAL_REVIEW_RIGHTS,
        "source_ledger": source_ledger,
        "panel_understanding": panel_understanding,
        "chapter_map": chapter_map,
        "narrative_review": narrative_review,
        "narration_spoken": narration_spoken,
        "display_cues": display_cues,
        "qc_report": qc_report,
    }


def _string_tuple(value: Any, code: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail(code)
    result = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(result) != len(value) or (not allow_empty and not result):
        _fail(code)
    return result


def validate_panel_observations(
    ledger: ManualReviewLedger,
    observations: Sequence[Mapping[str, object]],
) -> tuple[ManualPanelObservation, ...]:
    """Validate one cautious, manual observation for every immutable ledger entry."""

    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        _fail("review.panel_coverage_invalid")
    if len(observations) != len(ledger.entries):
        _fail("review.panel_coverage_invalid")
    result: list[ManualPanelObservation] = []
    for index, raw_value in enumerate(observations):
        raw = _mapping(raw_value, "review.panel_observation_invalid")
        entry = ledger.entries[index]
        if raw.get("source_order") != entry.source_order:
            _fail("review.panel_coverage_invalid")
        if raw.get("source_asset_id") != entry.source_asset_id:
            _fail("review.panel_lineage_invalid")
        if raw.get("panel_id") != entry.panel_id:
            _fail("review.evidence_foreign")
        summary = _nonempty(raw.get("visible_summary"), "review.observation_summary_missing")
        entities = _string_tuple(raw.get("visible_entities", []), "review.observation_entities_invalid")
        actions = _string_tuple(raw.get("actions", []), "review.observation_actions_invalid")
        setting = _nonempty(
            raw.get("setting_or_continuity"), "review.observation_continuity_missing"
        )
        dialogue_present = raw.get("dialogue_present")
        if not isinstance(dialogue_present, bool):
            _fail("review.observation_dialogue_invalid")
        dialogue_paraphrase = str(raw.get("dialogue_paraphrase", ""))
        if dialogue_present and not dialogue_paraphrase.strip():
            _fail("review.dialogue_paraphrase_missing")
        if not dialogue_present and dialogue_paraphrase.strip():
            _fail("review.source_text_leak")
        uncertainties = _string_tuple(
            raw.get("uncertainties", []), "review.observation_uncertainty_invalid"
        )
        confidence = _nonempty(raw.get("confidence"), "review.observation_confidence_invalid")
        if confidence not in {"high", "medium", "low"}:
            _fail("review.observation_confidence_invalid")
        evidence_status = raw.get("evidence_status")
        if evidence_status != "manual_visual_review":
            _fail("review.evidence_status_invalid")
        dialogue_or_ocr = _string_tuple(
            raw.get("dialogue_or_ocr", []), "review.dialogue_evidence_invalid"
        )
        if index == 0:
            if entry.included_in_story or entry.exclusion_reason != "title_front_matter":
                _fail("review.title_scope_invalid")
        elif not actions:
            _fail("review.observation_actions_missing")
        result.append(
            ManualPanelObservation(
                source_order=entry.source_order,
                source_asset_id=entry.source_asset_id,
                panel_id=entry.panel_id,
                visible_summary=summary,
                visible_entities=entities,
                actions=actions,
                setting_or_continuity=setting,
                dialogue_present=dialogue_present,
                dialogue_paraphrase=dialogue_paraphrase.strip(),
                uncertainties=uncertainties,
                confidence=confidence,
                region_bounds=(0, 0, entry.width, entry.height),
                dialogue_or_ocr=dialogue_or_ocr,
            )
        )
    if tuple(item.source_order for item in result) != EXPECTED_SOURCE_ORDERS:
        _fail("review.panel_coverage_invalid")
    return tuple(result)


def _positive_orders(value: Any, code: str) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        _fail(code)
    orders = tuple(value)
    if not orders or any(isinstance(item, bool) or not isinstance(item, int) for item in orders):
        _fail(code)
    if len(set(orders)) != len(orders) or any(item not in range(1, 24) for item in orders):
        _fail(code)
    return orders


def _panel_id_tuple(
    value: Any, expected: set[str], code: str
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail(code)
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item in expected:
            result.append(item)
        elif isinstance(item, int) and not isinstance(item, bool) and 0 <= item < 24:
            candidate = f"panel-{item:02d}"
            if candidate not in expected:
                _fail(code)
            result.append(candidate)
        else:
            _fail(code)
    if not result or len(set(result)) != len(result):
        _fail(code)
    return tuple(result)


def validate_chapter_map(
    observations: Sequence[ManualPanelObservation],
    chapter_map: Mapping[str, object],
) -> None:
    """Require a qualified causal map covering every story order exactly once."""

    if not isinstance(chapter_map, Mapping):
        _fail("review.chapter_map_invalid")
    beats = chapter_map.get("beats")
    if not isinstance(beats, list) or not beats:
        _fail("review.chapter_map_invalid")
    beat_ids: set[str] = set()
    covered: list[int] = []
    for raw_value in beats:
        beat = _mapping(raw_value, "review.chapter_map_invalid")
        beat_id = _nonempty(beat.get("beat_id"), "review.chapter_map_invalid")
        if beat_id in beat_ids:
            _fail("review.chapter_map_invalid")
        beat_ids.add(beat_id)
        orders = _positive_orders(beat.get("panel_orders"), "review.chapter_map_invalid")
        evidence_refs = _positive_orders(beat.get("evidence_refs"), "review.chapter_map_invalid")
        if not set(orders) <= set(evidence_refs):
            _fail("review.chapter_map_invalid")
        for field in ("visible_change", "stakes", "qualification"):
            _nonempty(beat.get(field), "review.chapter_map_invalid")
        covered.extend(orders)
    if set(covered) != set(range(1, 24)):
        _fail("review.chapter_map_invalid")
    story_spine = _mapping(chapter_map.get("story_spine"), "review.chapter_map_invalid")
    for field in (
        "who_wants_what",
        "obstacle",
        "decision",
        "consequence",
        "changed_stakes",
        "unresolved_question",
    ):
        _nonempty(story_spine.get(field), "review.chapter_map_invalid")
    causal_chain = chapter_map.get("causal_chain")
    if not isinstance(causal_chain, list) or not causal_chain:
        _fail("review.chapter_map_invalid")
    for raw_value in causal_chain:
        link = _mapping(raw_value, "review.chapter_map_invalid")
        if link.get("from_beat") not in beat_ids or link.get("to_beat") not in beat_ids:
            _fail("review.chapter_map_invalid")
        _nonempty(link.get("relationship"), "review.chapter_map_invalid")
        _positive_orders(link.get("evidence_refs"), "review.chapter_map_invalid")
    coverage = _mapping(chapter_map.get("coverage"), "review.chapter_map_invalid")
    if tuple(coverage.get("story_orders_required", ())) != tuple(range(1, 24)):
        _fail("review.chapter_map_invalid")
    if tuple(coverage.get("story_orders_covered", ())) != tuple(range(1, 24)):
        _fail("review.chapter_map_invalid")


def _passage_text(passage: Mapping[str, object]) -> str:
    return _nonempty(passage.get("text", passage.get("spoken_text")), "narrative.text_invalid")


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)


def _has_four_word_sequence(left: str, right: str) -> bool:
    left_words = _words(left)
    right_words = _words(right)
    ngrams = {
        tuple(right_words[index : index + 4])
        for index in range(max(0, len(right_words) - 3))
    }
    return any(
        tuple(left_words[index : index + 4]) in ngrams
        for index in range(max(0, len(left_words) - 3))
    )


def build_manual_analyzer_projection(
    observations: Sequence[ManualPanelObservation],
    chapter_map: Mapping[str, object],
    review: ManualNarrativeReview,
) -> dict[str, object]:
    """Build a sanitized six-key v3 document for local validation only."""

    expected = tuple(item.panel_id for item in observations)
    if len(expected) != 24 or len(set(expected)) != 24:
        _fail("review.panel_lineage_invalid")
    coverage_hash = hashlib.sha256("|".join(expected).encode("utf-8")).hexdigest()
    projected_observations = []
    for index, observation in enumerate(observations):
        projected_observations.append(
            {
                "panel_id": observation.panel_id,
                "source_asset_id": observation.source_asset_id,
                "strip_region_id": observation.panel_id,
                "source_index": index,
                "region_bounds": {
                    "x": observation.region_bounds[0],
                    "y": observation.region_bounds[1],
                    "width": observation.region_bounds[2],
                    "height": observation.region_bounds[3],
                },
                "coverage_map_version": PROVENANCE_KIND,
                "coverage_map_hash": coverage_hash,
                "visible_facts": [observation.visible_summary, *observation.actions],
                "dialogue_or_ocr": list(observation.dialogue_or_ocr),
                "inferences": [observation.setting_or_continuity],
                "uncertainties": list(observation.uncertainties),
                "evidence_refs": list(expected),
            }
        )
    claims = [dict(claim) for claim in review.claims]
    if not claims:
        _fail("review.evidence_missing")
    for claim in claims:
        if set(claim) != {
            "claim_id",
            "claim_type",
            "text",
            "qualification",
            "evidence_panel_ids",
        }:
            _fail("review.evidence_missing")
    causal_links = []
    for link in chapter_map["causal_chain"]:
        refs = list(link["evidence_refs"])
        causal_links.append(
            {
                "from_panel_id": expected[refs[0]],
                "to_panel_id": expected[refs[-1]],
                "reason": link["relationship"],
                "evidence_panel_ids": [expected[ref] for ref in refs],
            }
        )
    entities = []
    for entity_index, observation in enumerate(observations):
        name = observation.visible_entities[0] if observation.visible_entities else "visible subject"
        entities.append(
            {
                "entity_id": f"manual-entity-{entity_index}",
                "canonical_name": name,
                "aliases": [],
                "panel_ids": list(expected),
            }
        )
    passages = []
    for passage in review.passages:
        passages.append(
            {
                "passage_id": passage["passage_id"],
                "editorial_role": passage["editorial_role"],
                "text": _passage_text(passage),
                "claim_ids": list(passage["claim_ids"]),
                "evidence_panel_ids": list(passage["evidence_panel_ids"]),
            }
        )
    return {
        "observations": projected_observations,
        "continuity_ledger": {
            "chunks": [{"chunk_id": "manual-all-panels", "panel_ids": list(expected)}],
            "entities": entities,
            "motives": [],
            "state_changes": [],
            "causal_links": causal_links,
            "reconciled_after_final_chunk": True,
        },
        "evidence_graph": {"claims": claims},
        "coverage_manifest": {
            "total_panels": 24,
            "processed_panels": 24,
            "panel_ids": list(expected),
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
        },
        "narrative_outline": {
            "story_spine": dict(chapter_map["story_spine"]),
            "ending_kind": review.ending_kind,
        },
        "script_passages": passages,
    }


def validate_manual_narrative(
    review: ManualNarrativeReview,
    *,
    ledger: ManualReviewLedger,
) -> None:
    """Validate the manual narrative through the explicit Sharp Friend v3 contract."""

    observations = review.panel_observations
    if len(observations) != 24 or tuple(item.source_order for item in observations) != EXPECTED_SOURCE_ORDERS:
        _fail("review.panel_coverage_invalid")
    validate_chapter_map(observations, review.chapter_map)
    if not 4 <= len(review.passages) <= 6:
        _fail("narrative.passage_count_invalid")
    if len({passage.get("passage_id") for passage in review.passages}) != len(review.passages):
        _fail("narrative.passage_invalid")
    if len({passage.get("editorial_role") for passage in review.passages}) != len(review.passages):
        _fail("narrative.passage_invalid")
    claim_map = {str(claim.get("claim_id")): claim for claim in review.claims}
    if len(claim_map) != len(review.claims) or not claim_map:
        _fail("narrative.unsupported_claim")
    for claim in review.claims:
        claim_type = claim.get("claim_type")
        _nonempty(claim.get("text"), "narrative.unsupported_claim")
        claim_refs = claim.get("evidence_panel_ids")
        if not claim_refs:
            _fail("review.evidence_missing")
        _panel_id_tuple(claim_refs, {item.panel_id for item in observations}, "review.evidence_foreign")
        if claim_type == "interpretation" and not str(claim.get("qualification", "")).strip():
            _fail("narrative.interpretation_unqualified")
    passage_texts = []
    for passage in review.passages:
        text = _passage_text(passage)
        passage_texts.append(text)
        claim_ids = _string_tuple(passage.get("claim_ids"), "review.evidence_missing", allow_empty=False)
        raw_evidence_refs = passage.get("evidence_panel_ids")
        if not raw_evidence_refs:
            _fail("review.evidence_missing")
        evidence_refs = _panel_id_tuple(
            raw_evidence_refs,
            {item.panel_id for item in observations},
            "review.evidence_foreign",
        )
        if not set(claim_ids) <= set(claim_map):
            _fail("narrative.unsupported_claim")
        required = set().union(*(set(claim_map[item]["evidence_panel_ids"]) for item in claim_ids))
        if not required <= set(evidence_refs):
            _fail("review.evidence_missing")
    for observation in observations:
        if any(_has_four_word_sequence(text, dialogue) for text in passage_texts for dialogue in observation.dialogue_or_ocr):
            _fail("narrative.balloon_dialogue_copied")
    spoken_text = _nonempty(review.spoken_text, "narrative.text_invalid")
    if spoken_text != " ".join(passage_texts):
        _fail("narrative.display_derivation_invalid")
    lowered = spoken_text.casefold()
    if re.search(r"\b(?:subscribe|follow for more|please like|comment below)\b", lowered):
        _fail("narrative.cta")
    if any(marker in lowered for marker in ("epic battle", "unstoppable attack", "insane power")):
        _fail("narrative.generic_hype")
    final_text = passage_texts[-1].rstrip()
    unresolved = _nonempty(review.unresolved_question, "narrative.ending_invalid")
    if review.ending_kind == "open_question":
        if not final_text.endswith("?"):
            _fail("narrative.ending_invalid")
    elif (
        review.ending_kind not in {"cliffhanger", "consequence", "open_question"}
        or (
            review.ending_kind in {"cliffhanger", "consequence"}
            and final_text.endswith("?")
        )
    ):
        _fail("narrative.ending_invalid")
    if not derive_display_cues(spoken_text):
        _fail("narrative.display_derivation_invalid")
    projection = build_manual_analyzer_projection(observations, review.chapter_map, review)
    projection["narrative_outline"]["story_spine"]["unresolved_question"] = unresolved
    try:
        from app.services.analyzer_contract import validate_analyzer_output

        validate_analyzer_output(
            projection,
            expected_panel_ids=tuple(item.panel_id for item in observations),
            narrative_profile_id="sharp_friend_v1",
        )
    except Exception as exc:
        if isinstance(exc, ManualReviewError):
            raise
        _fail("narrative.contract_invalid")


REVIEW_STATES = frozenset(
    {
        "DRAFT",
        "QC_BLOCKED",
        "PENDING_EDITORIAL_REVIEW",
        "APPROVED_REFERENCE_ONLY",
        "REJECTED",
        "REVISED",
    }
)


@dataclass(frozen=True)
class ReviewQCReport:
    blocking_findings: tuple[str, ...]
    warnings: tuple[str, ...]
    metrics: Mapping[str, float | int]
    report_sha256: str
    review_state: str


def _qc_hash(
    blocking_findings: Sequence[str],
    warnings: Sequence[str],
    metrics: Mapping[str, float | int],
    review_state: str,
) -> str:
    payload = {
        "blocking_findings": list(blocking_findings),
        "warnings": list(warnings),
        "metrics": dict(metrics),
        "review_state": review_state,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def build_review_qc(
    review: ManualNarrativeReview,
    *,
    ledger: ManualReviewLedger,
    display_cues: Sequence[Mapping[str, object]],
) -> ReviewQCReport:
    """Produce deterministic, non-rewriting QC for a manual review revision."""

    blockers: set[str] = set()
    warnings: set[str] = set()
    try:
        validate_manual_narrative(review, ledger=ledger)
    except ManualReviewError as exc:
        blockers.add(exc.code)
    expected_cues = list(derive_display_cues(review.spoken_text))
    if list(display_cues) != expected_cues:
        blockers.add("narrative.display_derivation_invalid")

    passages = [
        {
            "text": _passage_text(passage),
            "claim_ids": list(passage.get("claim_ids", [])),
            "evidence_panel_ids": list(passage.get("evidence_panel_ids", [])),
        }
        for passage in review.passages
    ]
    claims = {str(claim.get("claim_id")): claim for claim in review.claims}
    total_words = sum(len(_words(item["text"])) for item in passages)
    metrics: dict[str, float | int] = {
        "passage_count": len(review.passages),
        "total_words": total_words,
        "estimated_duration_s": round(total_words * 60 / 150, 3),
        "display_cue_count": len(expected_cues),
    }
    if not 90 <= total_words <= 125:
        warnings.add("narrative.word_count_target_warning")
    if not blockers:
        try:
            from app.services import editorial_qc, narrative_identity

            profile = narrative_identity.get_narrative_identity("sharp_friend_v1")
            naturalness = editorial_qc.screen_narrative_naturalness(passages, claims, profile)
            warnings.update(naturalness.warnings)
            metrics.update(
                {
                    "sentence_length_p10": naturalness.sentence_length_p10,
                    "sentence_length_p50": naturalness.sentence_length_p50,
                    "sentence_length_p90": naturalness.sentence_length_p90,
                    "sentence_length_variance": naturalness.sentence_length_variance,
                    "repeated_sentence_ratio": naturalness.repeated_normalized_sentence_ratio,
                    "repeated_opening_ratio": naturalness.repeated_opening_ngram_ratio,
                    "connector_diversity_count": naturalness.connector_diversity_count,
                    "causal_transition_coverage": naturalness.causal_transition_coverage,
                    "contraction_count": naturalness.contraction_count,
                    "claim_evidence_coverage_ratio": naturalness.claim_evidence_coverage_ratio,
                    "qualified_interpretation_coverage_ratio": naturalness.qualified_interpretation_coverage_ratio,
                }
            )
        except Exception:
            blockers.add("narrative.qc_invalid")
    ordered_blockers = tuple(sorted(blockers))
    ordered_warnings = tuple(sorted(warnings))
    state = "PENDING_EDITORIAL_REVIEW" if not ordered_blockers else "QC_BLOCKED"
    report_hash = _qc_hash(ordered_blockers, ordered_warnings, metrics, state)
    return ReviewQCReport(
        blocking_findings=ordered_blockers,
        warnings=ordered_warnings,
        metrics=metrics,
        report_sha256=report_hash,
        review_state=state,
    )


def _require_clear_qc(bundle: Mapping[str, object]) -> str:
    qc = _mapping(bundle.get("qc_report"), "review.approval_invalid")
    if qc.get("review_state") != "PENDING_EDITORIAL_REVIEW":
        _fail("review.approval_invalid")
    if qc.get("blocking_findings"):
        _fail("review.approval_invalid")
    revision_sha = _nonempty(bundle.get("revision_sha256"), "review.approval_invalid")
    report_sha = _nonempty(qc.get("report_sha256"), "review.approval_invalid")
    if revision_sha != report_sha:
        _fail("review.approval_invalid")
    return revision_sha


def approve_reference_review(
    bundle: Mapping[str, object], *, reviewer: str, reviewed_at: str
) -> dict[str, object]:
    """Return an explicit reference-only approval without touching production state."""

    revision_sha = _require_clear_qc(bundle)
    _nonempty(reviewer, "review.approval_invalid")
    _nonempty(reviewed_at, "review.approval_invalid")
    approved = dict(bundle)
    approved.update(
        {
            "approval_state": "APPROVED_REFERENCE_ONLY",
            "reviewer": reviewer.strip(),
            "reviewed_at": reviewed_at.strip(),
            "revision_sha256": revision_sha,
            "production_evidence": False,
            "production_analysis": False,
            "publish_allowed": False,
            "approval_scope": "reference discussion only; not SCRIPT_APPROVED",
        }
    )
    return approved


def reject_reference_review(
    bundle: Mapping[str, object], *, reviewer: str, reason: str
) -> dict[str, object]:
    """Return a rejected revision with an explicit human reason."""

    _nonempty(reviewer, "review.rejection_invalid")
    _nonempty(reason, "review.rejection_invalid")
    rejected = dict(bundle)
    rejected.update(
        {
            "approval_state": "REJECTED",
            "reviewer": reviewer.strip(),
            "rejection_reason": reason.strip(),
            "publish_allowed": False,
        }
    )
    return rejected


def revise_reference_review(
    bundle: Mapping[str, object], *, revision_id: str
) -> dict[str, object]:
    """Create a new revision marker and clear all prior human approval fields."""

    _nonempty(revision_id, "review.revision_invalid")
    revised = dict(bundle)
    for key in ("reviewer", "reviewed_at", "rejection_reason", "approval_scope"):
        revised.pop(key, None)
    revised.update(
        {
            "approval_state": "REVISED",
            "revision_id": revision_id.strip(),
            "publish_allowed": False,
        }
    )
    return revised
