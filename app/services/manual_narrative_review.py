"""Offline, provenance-labeled helpers for a manual narrative review bundle."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
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
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
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
