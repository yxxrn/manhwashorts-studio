"""Prepare and finalize an ignored, offline manual chapter narrative review."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.manual_narrative_review import (  # noqa: E402
    INTERNAL_REVIEW_RIGHTS,
    PROVENANCE_KIND,
    ManualNarrativeReview,
    ManualReviewError,
    ManualReviewLedger,
    SourceLedgerEntry,
    build_review_qc,
    canonical_ledger_json,
    derive_display_cues,
    load_source_ledger,
    validate_manual_narrative,
    validate_panel_observations,
    write_review_bundle,
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )


def _safe_output_root(path: Path) -> Path:
    output = path.resolve()
    if output == ROOT or ROOT in output.parents:
        relative = output.relative_to(ROOT)
        if not relative.parts or relative.parts[0].casefold() != "data":
            raise ManualReviewError("review.output_root_invalid")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _ledger_template(ledger: ManualReviewLedger) -> list[dict[str, object]]:
    return [
        {
            "source_order": entry.source_order,
            "source_asset_id": entry.source_asset_id,
            "panel_id": entry.panel_id,
            "review_path": entry.review_path,
            "visible_summary": (
                "Title/front matter; excluded from story claims."
                if entry.source_order == 0
                else ""
            ),
            "visible_entities": [],
            "actions": [],
            "setting_or_continuity": "",
            "dialogue_present": False,
            "dialogue_paraphrase": "",
            "uncertainties": [],
            "confidence": "low",
            "evidence_status": "manual_visual_review",
        }
        for entry in ledger.entries
    ]


def _prepare(manifest_path: Path, output_path: Path) -> None:
    manifest = manifest_path.resolve()
    ledger = load_source_ledger(manifest, base_dir=manifest.parent)
    output = _safe_output_root(output_path)
    (output / "source_ledger.json").write_text(
        canonical_ledger_json(ledger, include_hash=True), encoding="utf-8", newline="\n"
    )
    _write_json(output / "panel_observations_template.json", _ledger_template(ledger))
    (output / "source_root.txt").write_text(str(manifest.parent), encoding="utf-8", newline="\n")


def _load_prepared_ledger(bundle_root: Path) -> ManualReviewLedger:
    try:
        payload = json.loads((bundle_root / "source_ledger.json").read_text(encoding="utf-8"))
        source_root = Path((bundle_root / "source_root.txt").read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ManualReviewError("review.bundle_missing") from None
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ManualReviewError("review.bundle_files_invalid")
    try:
        ledger = ManualReviewLedger(
            provenance_kind=payload["provenance_kind"],
            production_evidence=payload["production_evidence"],
            production_analysis=payload["production_analysis"],
            publish_allowed=payload["publish_allowed"],
            rights_status=payload["rights_status"],
            entries=tuple(SourceLedgerEntry(**entry) for entry in entries),
            ledger_sha256=payload["ledger_sha256"],
        )
    except (KeyError, TypeError):
        raise ManualReviewError("review.bundle_files_invalid") from None
    from app.services.manual_narrative_review import validate_source_ledger

    return validate_source_ledger(ledger, base_dir=source_root)


def _normalized_passages(value: Any) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        raise ManualReviewError("narrative.passage_invalid")
    passages = []
    for passage in value:
        if not isinstance(passage, dict):
            raise ManualReviewError("narrative.passage_invalid")
        evidence = passage.get("evidence_panel_ids", passage.get("evidence_refs"))
        text = passage.get("text", passage.get("spoken_text"))
        passages.append(
            {
                "passage_id": passage.get("passage_id"),
                "editorial_role": passage.get("editorial_role"),
                "text": text,
                "claim_ids": passage.get("claim_ids"),
                "evidence_panel_ids": evidence,
                "qualification": passage.get("qualification", ""),
            }
        )
    return tuple(passages)


def _finalize(
    bundle_root: Path,
    observations_path: Path,
    chapter_map_path: Path,
    narrative_path: Path,
) -> None:
    bundle = bundle_root.resolve()
    ledger = _load_prepared_ledger(bundle)
    try:
        observations = json.loads(observations_path.read_text(encoding="utf-8"))
        chapter_map = json.loads(chapter_map_path.read_text(encoding="utf-8"))
        narrative = json.loads(narrative_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ManualReviewError("review.input_invalid") from None
    typed = validate_panel_observations(ledger, observations)
    claims = narrative.get("claims", ()) if isinstance(narrative, dict) else ()
    if not isinstance(claims, list):
        raise ManualReviewError("review.evidence_missing")
    spoken_text = narrative.get("spoken_text", narrative.get("narration_spoken"))
    if not isinstance(narrative, dict):
        raise ManualReviewError("review.narrative_invalid")
    review = ManualNarrativeReview(
        panel_observations=typed,
        chapter_map=chapter_map,
        passages=_normalized_passages(narrative.get("passages", [])),
        ending_kind=narrative.get("ending_kind"),
        unresolved_question=narrative.get("unresolved_question", ""),
        spoken_text=spoken_text,
        claims=tuple(claims),
    )
    validate_manual_narrative(review, ledger=ledger)
    qc = build_review_qc(
        review,
        ledger=ledger,
        display_cues=derive_display_cues(review.spoken_text),
    )
    safe_observations = [
        {key: value for key, value in asdict(item).items() if key != "dialogue_or_ocr"}
        for item in typed
    ]
    review_payload = dict(narrative)
    review_payload.update(
        {
            "provenance_kind": PROVENANCE_KIND,
            "source_ledger_sha256": ledger.ledger_sha256,
            "approval_state": "PENDING_EDITORIAL_REVIEW",
        }
    )
    output_bundle = {
        "provenance_kind": PROVENANCE_KIND,
        "production_evidence": False,
        "production_analysis": False,
        "publish_allowed": False,
        "rights_status": INTERNAL_REVIEW_RIGHTS,
        "panel_understanding": safe_observations,
        "chapter_map": chapter_map,
        "narrative_review": review_payload,
        "narration_spoken": spoken_text,
        "display_cues": list(derive_display_cues(review.spoken_text)),
        "qc_report": asdict(qc),
    }
    write_review_bundle(bundle, output_bundle, ledger=ledger)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--bundle", type=Path, required=True)
    finalize.add_argument("--observations", type=Path, required=True)
    finalize.add_argument("--chapter-map", type=Path, required=True)
    finalize.add_argument("--narrative", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            _prepare(args.manifest, args.output)
        else:
            _finalize(args.bundle, args.observations, args.chapter_map, args.narrative)
    except ManualReviewError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
