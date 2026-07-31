"""Project and source-asset routes (PRD FR-01, FR-02)."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select

from app.constants import LicenseType, ProjectStatus
from app.deps import CurrentUser, CurrentWorkspace, DbSession, OwnedProject
from app.models import Project, SourceAsset
from app.schemas import (
    AssetOut,
    AssetRightsUpdate,
    MessageOut,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    TextAssetCreate,
)
from app.services import ingest, storage
from app.services.pipeline import audit, project_assets

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: DbSession,
    workspace: CurrentWorkspace,
    user: CurrentUser,
) -> Project:
    project = Project(
        workspace_id=workspace.id,
        title=payload.title.strip(),
        manhwa_title=payload.manhwa_title.strip(),
        chapter=payload.chapter.strip(),
        content_type=payload.content_type,
        language=payload.language,
        spoiler_level=payload.spoiler_level,
        narration_style=payload.narration_style,
        target_duration=payload.target_duration,
        voice_id=payload.voice_id,
        series_name=payload.series_name.strip(),
        cta_text=payload.cta_text.strip(),
        banned_words=[w.strip() for w in payload.banned_words if w.strip()],
        pronunciations=payload.pronunciations,
        template=payload.template,
    )
    db.add(project)
    db.flush()
    audit(db, "project.create", "project", project.id, user.id, title=project.title)
    return project


@router.get("", response_model=list[ProjectOut])
def list_projects(
    db: DbSession,
    workspace: CurrentWorkspace,
    include_archived: bool = False,
) -> list[Project]:
    stmt = select(Project).where(Project.workspace_id == workspace.id)
    if not include_archived:
        stmt = stmt.where(Project.archived == False)  # noqa: E712
    return list(db.scalars(stmt.order_by(Project.created_at.desc())))


@router.get("/{project_id}", response_model=ProjectOut)
def get_project_route(project: OwnedProject) -> Project:
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    payload: ProjectUpdate,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> Project:
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field in {"title", "manhwa_title", "chapter", "series_name", "cta_text"} and value:
            value = str(value).strip()
        setattr(project, field, value)
    audit(db, "project.update", "project", project.id, user.id, fields=sorted(changes))
    db.flush()
    return project


@router.post("/{project_id}/duplicate", response_model=ProjectOut, status_code=201)
def duplicate_project(project: OwnedProject, db: DbSession, user: CurrentUser) -> Project:
    """Copy settings and source assets into a fresh draft (FR-01)."""
    clone = Project(
        workspace_id=project.workspace_id,
        title=f"{project.title} (copy)",
        manhwa_title=project.manhwa_title,
        chapter=project.chapter,
        content_type=project.content_type,
        language=project.language,
        spoiler_level=project.spoiler_level,
        narration_style=project.narration_style,
        target_duration=project.target_duration,
        voice_id=project.voice_id,
        series_name=project.series_name,
        cta_text=project.cta_text,
        banned_words=list(project.banned_words or []),
        pronunciations=dict(project.pronunciations or {}),
        template=project.template,
        status=ProjectStatus.DRAFT,
    )
    db.add(clone)
    db.flush()

    # Assets are content-addressed, so the copies share storage keys and carry
    # their rights declarations with them.
    for asset in project_assets(db, project.id):
        db.add(
            SourceAsset(
                project_id=clone.id,
                type=asset.type,
                original_filename=asset.original_filename,
                storage_key=asset.storage_key,
                mime_type=asset.mime_type,
                size_bytes=asset.size_bytes,
                checksum=asset.checksum,
                extracted_text=asset.extracted_text,
                width=asset.width,
                height=asset.height,
                source_name=asset.source_name,
                rights_owner=asset.rights_owner,
                license_type=asset.license_type,
                permission_reference=asset.permission_reference,
                permission_date=asset.permission_date,
                usage_limits=asset.usage_limits,
                rights_status=asset.rights_status,
                attribution=asset.attribution,
                order_index=asset.order_index,
            )
        )
    audit(db, "project.duplicate", "project", clone.id, user.id, source=project.id)
    db.flush()
    return clone


@router.delete("/{project_id}", response_model=MessageOut)
def delete_project(project: OwnedProject, db: DbSession, user: CurrentUser) -> dict:
    """Delete a project and every asset unique to it (privacy: right to erase)."""
    assets = project_assets(db, project.id)
    keys = {a.storage_key for a in assets if a.storage_key}
    project_id = project.id
    db.delete(project)
    db.flush()

    # Only remove blobs no other project still references.
    removed = 0
    for key in keys:
        still_used = db.scalars(
            select(SourceAsset).where(SourceAsset.storage_key == key).limit(1)
        ).first()
        if still_used is None and storage.delete(key):
            removed += 1

    audit(db, "project.delete", "project", project_id, user.id, blobs_removed=removed)
    return {"detail": f"Project deleted. {removed} stored file(s) removed."}


# --- assets ----------------------------------------------------------------


@router.get("/{project_id}/assets", response_model=list[AssetOut])
def list_assets(project: OwnedProject, db: DbSession) -> list[SourceAsset]:
    return project_assets(db, project.id)


@router.post("/{project_id}/assets/text", response_model=AssetOut, status_code=201)
def add_text_asset(
    payload: TextAssetCreate,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> SourceAsset:
    try:
        result = ingest.ingest_text(project.id, payload.text, payload.title)
    except ingest.IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    rights = ingest.RightsDeclaration(**payload.rights.model_dump())
    asset = SourceAsset(
        project_id=project.id,
        type=result.type,
        original_filename=result.original_filename,
        storage_key=result.storage_key,
        mime_type=result.mime_type,
        size_bytes=result.size_bytes,
        checksum=result.checksum,
        extracted_text=result.extracted_text,
        source_name=rights.source_name,
        rights_owner=rights.rights_owner,
        license_type=rights.license_type,
        permission_reference=rights.permission_reference,
        permission_date=rights.permission_date,
        usage_limits=rights.usage_limits,
        attribution=rights.attribution,
        rights_status=rights.status,
        order_index=len(project_assets(db, project.id)),
    )
    db.add(asset)
    audit(db, "asset.add_text", "asset", asset.id, user.id, rights=asset.rights_status)
    db.flush()
    return asset


@router.post("/{project_id}/assets/upload", response_model=list[AssetOut], status_code=201)
async def upload_assets(
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
    files: list[UploadFile] = File(...),
    rights_owner: str = Form(""),
    source_name: str = Form(""),
    license_type: str = Form(LicenseType.UNKNOWN),
    permission_reference: str = Form(""),
    permission_date: str = Form(""),
    usage_limits: str = Form(""),
    attribution: str = Form(""),
    declared: bool = Form(False),
) -> list[SourceAsset]:
    """Upload images or documents with one shared rights declaration."""
    if not files:
        raise HTTPException(status_code=422, detail="No files were uploaded.")

    try:
        license_value = LicenseType(license_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown license_type. Use one of: "
            f"{', '.join(x.value for x in LicenseType)}",
        ) from exc

    rights = ingest.RightsDeclaration(
        source_name=source_name,
        rights_owner=rights_owner,
        license_type=license_value,
        permission_reference=permission_reference,
        permission_date=permission_date,
        usage_limits=usage_limits,
        attribution=attribution,
        declared=declared,
    )

    created: list[SourceAsset] = []
    start_index = len(project_assets(db, project.id))
    for offset, upload in enumerate(files):
        data = await upload.read()
        try:
            result = ingest.ingest_upload(
                project.id, upload.filename or "upload", upload.content_type or "", data
            )
        except ingest.IngestError as exc:
            raise HTTPException(
                status_code=422, detail=f"{upload.filename}: {exc}"
            ) from exc

        asset = SourceAsset(
            project_id=project.id,
            type=result.type,
            original_filename=result.original_filename,
            storage_key=result.storage_key,
            mime_type=result.mime_type,
            size_bytes=result.size_bytes,
            checksum=result.checksum,
            extracted_text=result.extracted_text,
            width=result.width,
            height=result.height,
            source_name=rights.source_name,
            rights_owner=rights.rights_owner,
            license_type=rights.license_type,
            permission_reference=rights.permission_reference,
            permission_date=rights.permission_date,
            usage_limits=rights.usage_limits,
            attribution=rights.attribution,
            rights_status=rights.status,
            order_index=start_index + offset,
        )
        db.add(asset)
        created.append(asset)

    audit(
        db, "asset.upload", "project", project.id, user.id,
        count=len(created), rights=rights.status,
    )
    db.flush()
    return created


@router.patch("/{project_id}/assets/{asset_id}/rights", response_model=AssetOut)
def update_rights(
    asset_id: str,
    payload: AssetRightsUpdate,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> SourceAsset:
    """Declare or correct an asset's rights basis (FR-02, section 8)."""
    asset = db.get(SourceAsset, asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(status_code=404, detail="Asset not found.")

    rights = ingest.RightsDeclaration(**payload.model_dump())
    asset.source_name = rights.source_name
    asset.rights_owner = rights.rights_owner
    asset.license_type = rights.license_type
    asset.permission_reference = rights.permission_reference
    asset.permission_date = rights.permission_date
    asset.usage_limits = rights.usage_limits
    asset.attribution = rights.attribution
    asset.rights_status = rights.status
    audit(
        db, "asset.rights_update", "asset", asset.id, user.id,
        rights_status=asset.rights_status,
    )
    db.flush()
    return asset


@router.delete("/{project_id}/assets/{asset_id}", response_model=MessageOut)
def delete_asset(
    asset_id: str,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    asset = db.get(SourceAsset, asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(status_code=404, detail="Asset not found.")
    key = asset.storage_key
    db.delete(asset)
    db.flush()

    still_used = db.scalars(
        select(SourceAsset).where(SourceAsset.storage_key == key).limit(1)
    ).first()
    if still_used is None:
        storage.delete(key)
    audit(db, "asset.delete", "asset", asset_id, user.id)
    return {"detail": "Asset deleted."}
