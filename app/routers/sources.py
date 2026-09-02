"""External source connectors exposed through the ManhwaShorts REST API."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.constants import ProjectStatus
from app.deps import CurrentUser, DbSession, OwnedProject
from app.models import SourceAsset
from app.routing import CommitRoute
from app.schemas import (
    SuwayomiImportOut,
    SuwayomiImportRequest,
    SuwayomiSearchItemOut,
    SuwayomiSearchRequest,
    SuwayomiStatusOut,
)
from app.services import ingest, suwayomi
from app.services import pipeline as pl

router = APIRouter(prefix="/api", tags=["sources"], route_class=CommitRoute)


def _ready_client() -> suwayomi.SuwayomiClient:
    state = suwayomi.ensure_sidecar()
    if not state.get("available"):
        raise HTTPException(status_code=503, detail=state.get("error") or "Suwayomi is unavailable")
    return suwayomi.client()


def _asset(project_id: str, result, rights: ingest.RightsDeclaration, order_index: int, source_name: str) -> SourceAsset:
    return SourceAsset(
        project_id=project_id,
        type=result.type,
        original_filename=result.original_filename,
        storage_key=result.storage_key,
        mime_type=result.mime_type,
        size_bytes=result.size_bytes,
        checksum=result.checksum,
        extracted_text=result.extracted_text,
        width=result.width,
        height=result.height,
        duration=result.audio_duration,
        source_name=rights.source_name or source_name,
        rights_owner=rights.rights_owner,
        license_type=rights.license_type,
        permission_reference=rights.permission_reference,
        permission_date=rights.permission_date,
        usage_limits=rights.usage_limits,
        attribution=rights.attribution,
        rights_status=rights.status,
        order_index=order_index,
        source_family=result.source_family,
        panel_bbox=result.panel_bbox or {},
        panel_quality=result.panel_quality or {},
        panel_decision=result.panel_decision,
        original_checksum=result.original_checksum,
        original_width=result.original_width,
        original_height=result.original_height,
        source_bounds_json={"x": result.source_bounds[0], "y": result.source_bounds[1], "width": result.source_bounds[2] - result.source_bounds[0], "height": result.source_bounds[3] - result.source_bounds[1]},
        strip_order=result.strip_order,
        region_order=result.region_order,
        trim_classification=result.trim_classification,
        coverage_map_hash=result.coverage_map_hash,
    )

@router.get("/sources/suwayomi/status", response_model=SuwayomiStatusOut)
def suwayomi_status(user: CurrentUser) -> dict:
    _ = user
    state = suwayomi.ensure_sidecar()
    return {
        "enabled": bool(settings.suwayomi_enabled),
        "available": bool(state.get("available")),
        "url": str(state.get("url") or ""),
        "sources": int(state.get("sources") or 0),
        "searchable_sources": int(state.get("searchable_sources") or 0),
        "needs_extension_setup": bool(state.get("needs_extension_setup")),
        "managed": bool(state.get("managed")),
        "installed": bool(state.get("installed", False)),
        "error": str(state.get("error") or ""),
    }


@router.post("/sources/suwayomi/search", response_model=list[SuwayomiSearchItemOut])
def suwayomi_search(payload: SuwayomiSearchRequest, user: CurrentUser) -> list[dict]:
    _ = user
    try:
        results = _ready_client().search(payload.title, payload.language, payload.source_id)
    except suwayomi.SuwayomiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [
        {
            "manga_id": int(item["id"]),
            "title": str(item.get("title") or ""),
            "source_id": str(item.get("sourceId") or ""),
            "source": str((item.get("source") or {}).get("displayName") or (item.get("source") or {}).get("name") or ""),
            "language": str((item.get("source") or {}).get("lang") or ""),
            "thumbnail_url": str(item.get("thumbnailUrl") or ""),
        }
        for item in results[:50]
    ]


@router.post("/projects/{project_id}/sources/suwayomi/import", response_model=SuwayomiImportOut)
def import_suwayomi_range(
    payload: SuwayomiImportRequest,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    if pl.latest_analysis(db, project.id) is not None:
        raise HTTPException(status_code=409, detail="Import sources before analysis; create a new project for a different chapter corpus.")
    connector = _ready_client()
    try:
        resolved = connector.resolve_range(
            payload.title,
            payload.chapter_from,
            payload.chapter_to,
            payload.language,
            payload.source_id,
        )
        pages = connector.download_range(resolved)
    except suwayomi.SuwayomiAmbiguousError as exc:
        raise HTTPException(status_code=409, detail={"code": "suwayomi.ambiguous", "candidates": exc.candidates}) from exc
    except suwayomi.SuwayomiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    rights = ingest.RightsDeclaration(**payload.rights.model_dump())
    source_label = str(resolved.source.get("displayName") or resolved.source.get("name") or "Suwayomi")
    provenance = f"Suwayomi / {source_label} / {resolved.manga.get('title', payload.title)}"
    existing_assets = pl.project_assets(db, project.id)
    existing = {asset.original_filename for asset in existing_assets}
    next_index = len(existing_assets)
    created: list[SourceAsset] = []
    duplicates = 0
    for page in pages:
        filename = suwayomi.provenance_filename(
            str(resolved.source.get("id") or resolved.manga.get("sourceId") or "unknown"),
            resolved.manga.get("id"),
            page.filename,
        )
        try:
            results = ingest.ingest_image_parts(project.id, filename, page.data)
        except ingest.IngestError as exc:
            raise HTTPException(status_code=422, detail=f"{page.filename}: {exc}") from exc
        for result in results:
            identity = result.original_filename
            if identity in existing:
                duplicates += 1
                continue
            row = _asset(project.id, result, rights, next_index, provenance)
            db.add(row)
            created.append(row)
            existing.add(identity)
            next_index += 1
    project.status = ProjectStatus.DRAFT
    pl.audit(db, "source.suwayomi.import", "project", project.id, user.id, manga_id=resolved.manga.get("id"), source_id=resolved.source.get("id"), chapters=[ch.get("chapterNumber") for ch in resolved.chapters], pages=len(pages), assets=len(created), duplicates=duplicates, rights=rights.status)
    db.flush()
    return {
        "status": "imported",
        "project_id": project.id,
        "manga_id": int(resolved.manga["id"]),
        "title": str(resolved.manga.get("title") or payload.title),
        "source_id": str(resolved.source.get("id") or resolved.manga.get("sourceId") or ""),
        "source": source_label,
        "language": str(resolved.source.get("lang") or ""),
        "chapters": [str(ch.get("chapterNumber")) for ch in resolved.chapters],
        "pages_downloaded": len(pages),
        "assets_created": len(created),
        "duplicates_skipped": duplicates,
        "asset_ids": [row.id for row in created],
        "rights_status": str(rights.status),
    }
