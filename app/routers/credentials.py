"""BYOK credential routes (v1.1).

Bring your own key: the user supplies API keys for analysis, highlights, script
rewriting, and narration, and the app fetches the model list from that key so the
choices offered are exactly what the key can reach.

Security contract for every route in this file:

* The plaintext key is accepted on input, used immediately, encrypted, and then
  dropped. It is never returned by any response and never written to a log.
* Responses expose ``key_hint`` (last four characters) only.
* All routes are workspace-scoped through ``CurrentWorkspace``, so one account
  cannot read or modify another's credentials.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.constants import CredentialKind
from app.deps import CurrentUser, CurrentWorkspace, DbSession
from app.routing import CommitRoute
from app.schemas import (
    ActiveProvidersOut,
    CredentialCreate,
    CredentialOut,
    CredentialTestOut,
    CredentialTestRequest,
    MessageOut,
    ModelSelectRequest,
    ProviderCatalogOut,
)
from app.services import credentials as cred_svc
from app.services import providers as pv
from app.services import resolver as resolver_svc

router = APIRouter(prefix="/api/credentials", tags=["credentials"], route_class=CommitRoute)


def _bad_request(exc: Exception) -> HTTPException:
    """Credential problems are user input errors, not server faults."""
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/providers", response_model=ProviderCatalogOut)
def list_providers() -> dict:
    """Catalogue of supported providers, for building the settings form.

    Unauthenticated on purpose: it is static public metadata with no user data,
    and the UI needs it to render the form before anything is saved.
    """
    return pv.catalog()


@router.get("", response_model=list[CredentialOut])
def list_credentials(db: DbSession, workspace: CurrentWorkspace) -> list[dict]:
    """Stored credentials for this workspace. Keys are never included."""
    return [
        cred_svc.public_view(row)
        for row in cred_svc.list_credentials(db, workspace.id)
    ]


@router.get("/active", response_model=ActiveProvidersOut)
def active_providers(db: DbSession, workspace: CurrentWorkspace) -> dict:
    """Which provider each stage will actually use, and why.

    Exposed so the user is never guessing whether a render will hit their paid
    key or fall back to the offline engine.
    """
    return resolver_svc.describe_all(db, workspace.id)


@router.post("/test", response_model=CredentialTestOut)
def test_credential(
    payload: CredentialTestRequest,
    workspace: CurrentWorkspace,
    user: CurrentUser,
) -> dict:
    """Verify a key and fetch its models without saving anything.

    Lets the user confirm a key works, and pick a model, before committing it to
    storage. Returns ok=false with a readable message rather than an error status,
    because a rejected key is an expected outcome the form needs to display.
    """
    result = pv.verify_credential(
        payload.kind, payload.provider, payload.api_key, payload.base_url
    )
    return {
        "ok": result.ok,
        "message": result.message,
        "models": [m.as_dict() for m in result.models],
    }


@router.post("", response_model=CredentialOut, status_code=status.HTTP_201_CREATED)
def save_credential(
    payload: CredentialCreate,
    db: DbSession,
    workspace: CurrentWorkspace,
    user: CurrentUser,
) -> dict:
    """Store an API key, verifying it against the provider first.

    Re-posting the same kind+provider replaces the stored key rather than
    creating a duplicate, so rotating a key is the same action as adding one.
    """
    try:
        row, _ = cred_svc.save_credential(
            db,
            workspace_id=workspace.id,
            actor_id=user.id,
            kind=payload.kind,
            provider=payload.provider,
            api_key=payload.api_key,
            base_url=payload.base_url,
            label=payload.label,
            model=payload.model,
            verify=payload.verify,
        )
    except cred_svc.CredentialError as exc:
        raise _bad_request(exc) from None
    return cred_svc.public_view(row)


@router.post("/{credential_id}/refresh", response_model=CredentialOut)
def refresh_models(
    credential_id: str,
    db: DbSession,
    workspace: CurrentWorkspace,
    user: CurrentUser,
) -> dict:
    """Re-fetch the model list with the stored key.

    Doubles as a health check: providers retire models and keys get revoked
    upstream, and both show up here as a status change.
    """
    try:
        row, _ = cred_svc.refresh_models(db, workspace.id, credential_id, user.id)
    except cred_svc.CredentialError as exc:
        raise _bad_request(exc) from None
    return cred_svc.public_view(row)


@router.post("/{credential_id}/model", response_model=CredentialOut)
def select_model(
    credential_id: str,
    payload: ModelSelectRequest,
    db: DbSession,
    workspace: CurrentWorkspace,
    user: CurrentUser,
) -> dict:
    """Choose which model this credential uses."""
    try:
        row = cred_svc.select_model(db, workspace.id, credential_id, payload.model, user.id)
    except cred_svc.CredentialError as exc:
        raise _bad_request(exc) from None
    return cred_svc.public_view(row)


@router.post("/{credential_id}/default", response_model=CredentialOut)
def set_default(
    credential_id: str,
    db: DbSession,
    workspace: CurrentWorkspace,
    user: CurrentUser,
) -> dict:
    """Make this credential the active one for its capability."""
    try:
        row = cred_svc.set_default(db, workspace.id, credential_id, user.id)
    except cred_svc.CredentialError as exc:
        raise _bad_request(exc) from None
    return cred_svc.public_view(row)


@router.delete("/{credential_id}", response_model=MessageOut)
def delete_credential(
    credential_id: str,
    db: DbSession,
    workspace: CurrentWorkspace,
    user: CurrentUser,
) -> dict:
    """Delete a credential and its stored ciphertext.

    The row is removed, not flagged inactive: "remove my key" should mean the
    key is gone.
    """
    try:
        cred_svc.delete_credential(db, workspace.id, credential_id, user.id)
    except cred_svc.CredentialError as exc:
        raise _bad_request(exc) from None
    return {"detail": "Credential deleted. The stored key has been removed."}


@router.get("/kinds", response_model=list[str])
def list_kinds() -> list[str]:
    """Capabilities that accept a bring-your-own key."""
    return [str(k) for k in CredentialKind]
