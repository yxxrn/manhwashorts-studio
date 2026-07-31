"""Storage and resolution of BYOK credentials (v1.1).

Separated from ``app.services.providers`` on purpose: that module knows how to
talk to vendors, this one knows how to persist keys safely and decide which key
the pipeline should use. Neither depends on FastAPI, so both are testable
without HTTP.

Security posture:

- Keys are encrypted with Fernet before they touch the database, using the same
  key material that already protects OAuth tokens (``data/.fernet_key``).
- The plaintext key is only ever produced by ``reveal_secret``, which callers
  use immediately and never store. Nothing in this module logs it.
- API responses use ``key_hint`` (last 4 characters) so a user can tell two
  keys apart without the server ever handing one back.
- Deleting a credential removes the ciphertext row outright rather than marking
  it inactive, so "remove my key" means what it says.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import CredentialKind, CredentialStatus
from app.models import AuditLog, ProviderCredential
from app.security import decrypt_json, encrypt_json
from app.services import providers as pv


class CredentialError(RuntimeError):
    """Invalid credential input. Message is safe to show the user."""


def _now() -> datetime:
    return datetime.now(UTC)


def _hint(api_key: str) -> str:
    """Last four characters, for display only."""
    tail = api_key.strip()[-4:]
    return f"...{tail}" if tail else ""


def _audit(
    db: Session,
    actor_id: str,
    action: str,
    credential: ProviderCredential,
    **extra: object,
) -> None:
    """Record a credential action. Deliberately never includes the key.

    Flushes immediately: the session runs with ``autoflush=False``, so an added
    but unflushed audit row would be invisible to a later query in the same
    transaction, making the trail look empty when it is not.
    """
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type="provider_credential",
            entity_id=credential.id,
            detail={
                "kind": credential.kind,
                "provider": credential.provider,
                "key_hint": credential.key_hint,
                **extra,
            },
        )
    )
    db.flush()


def list_credentials(
    db: Session, workspace_id: str, kind: CredentialKind | str | None = None
) -> list[ProviderCredential]:
    """All stored credentials for a workspace, newest first."""
    stmt = select(ProviderCredential).where(ProviderCredential.workspace_id == workspace_id)
    if kind is not None:
        stmt = stmt.where(ProviderCredential.kind == str(CredentialKind(str(kind))))
    return list(db.scalars(stmt.order_by(ProviderCredential.created_at.desc())))


def get_credential(db: Session, workspace_id: str, credential_id: str) -> ProviderCredential:
    """Fetch one credential, enforcing workspace ownership."""
    row = db.get(ProviderCredential, credential_id)
    if row is None or row.workspace_id != workspace_id:
        raise CredentialError("credential not found")
    return row


def reveal_secret(credential: ProviderCredential) -> str:
    """Decrypt the API key for immediate use.

    Callers must pass the result straight to a provider adapter and never
    persist, log, or return it.
    """
    if not credential.encrypted_secret:
        raise CredentialError("this credential has no stored key")
    payload = decrypt_json(credential.encrypted_secret)
    key = str(payload.get("api_key") or "")
    if not key:
        raise CredentialError("stored credential is empty or corrupt")
    return key


def _pick_model(requested: str, available: list[str], previous: str = "") -> str:
    """Choose the model to store.

    Honours an explicit request when the provider actually offers it, otherwise
    keeps a still-valid previous choice, otherwise leaves it empty so the caller
    must choose. Silently substituting a different model would bill the user for
    something they did not pick.
    """
    requested = (requested or "").strip()
    if requested:
        if not available or requested in available:
            return requested
        raise CredentialError(
            f"model '{requested}' is not available on this key. "
            f"Pick one of the {len(available)} listed models."
        )
    if previous and previous in available:
        return previous
    return ""


def save_credential(
    db: Session,
    workspace_id: str,
    actor_id: str,
    kind: CredentialKind | str,
    provider: str,
    api_key: str,
    base_url: str | None = None,
    label: str = "",
    model: str = "",
    verify: bool = True,
) -> tuple[ProviderCredential, pv.VerificationResult]:
    """Create or update a credential, verifying it against the provider first.

    Verification is on by default because a key that cannot list models is a key
    that will fail mid-render, and finding that out at save time is far cheaper.
    Set ``verify=False`` only when the provider is unreachable by design (tests,
    air-gapped setups); the row is then marked ``unverified``.

    One row per (workspace, kind, provider): re-saving replaces the key rather
    than accumulating stale ciphertext.
    """
    kind = CredentialKind(str(kind))
    api_key = (api_key or "").strip()
    if not api_key:
        raise CredentialError("API key must not be empty")

    try:
        spec = pv.get_spec(kind, provider)
        resolved_base = pv.validate_base_url(base_url) or spec.default_base_url
    except pv.ProviderError as exc:
        raise CredentialError(str(exc)) from exc

    if spec.custom_endpoint and not resolved_base:
        raise CredentialError(f"{spec.label} needs a base URL")

    result = pv.VerificationResult(ok=False, message="not verified")
    if verify:
        result = pv.verify_credential(kind, provider, api_key, resolved_base)
        if not result.ok:
            # Do not persist a key we already know is broken.
            raise CredentialError(result.message)

    existing = db.scalars(
        select(ProviderCredential).where(
            ProviderCredential.workspace_id == workspace_id,
            ProviderCredential.kind == str(kind),
            ProviderCredential.provider == provider,
        )
    ).first()

    model_ids = [m.id for m in result.models]
    chosen_model = _pick_model(model, model_ids, existing.model if existing else "")

    row = existing or ProviderCredential(
        workspace_id=workspace_id, kind=str(kind), provider=provider
    )
    is_new = existing is None

    row.label = (label or spec.label)[:120]
    row.encrypted_secret = encrypt_json({"api_key": api_key})
    row.key_hint = _hint(api_key)
    row.base_url = resolved_base or None
    row.model = chosen_model
    row.available_models = [m.as_dict() for m in result.models]
    row.is_active = True

    if verify:
        row.status = CredentialStatus.VERIFIED
        row.status_message = result.message
        row.verified_at = _now()
    else:
        row.status = CredentialStatus.UNVERIFIED
        row.status_message = "saved without verification"
        row.verified_at = None

    if is_new:
        db.add(row)
        # First credential for a capability becomes the default automatically,
        # so a user who adds one key does not also have to select it.
        row.is_default = not _has_default(db, workspace_id, kind)

    db.flush()
    _audit(
        db,
        actor_id,
        "credential.saved" if is_new else "credential.updated",
        row,
        verified=verify,
        model_count=len(model_ids),
    )
    return row, result


def _has_default(db: Session, workspace_id: str, kind: CredentialKind) -> bool:
    return (
        db.scalars(
            select(ProviderCredential.id).where(
                ProviderCredential.workspace_id == workspace_id,
                ProviderCredential.kind == str(kind),
                ProviderCredential.is_default.is_(True),
                ProviderCredential.is_active.is_(True),
            )
        ).first()
        is not None
    )


def refresh_models(
    db: Session, workspace_id: str, credential_id: str, actor_id: str = ""
) -> tuple[ProviderCredential, pv.VerificationResult]:
    """Re-fetch the model list using the stored key.

    Also serves as a health check: providers add and retire models, and a key
    can be revoked upstream at any time. The row's status is updated either way
    so the UI can show a key that has gone bad.
    """
    row = get_credential(db, workspace_id, credential_id)
    api_key = reveal_secret(row)
    result = pv.verify_credential(row.kind, row.provider, api_key, row.base_url)

    if result.ok:
        row.available_models = [m.as_dict() for m in result.models]
        row.status = CredentialStatus.VERIFIED
        row.status_message = result.message
        row.verified_at = _now()
        # A model that disappeared upstream must not stay selected.
        if row.model and row.model not in [m.id for m in result.models]:
            row.status_message = (
                f"{result.message}; previously selected model "
                f"'{row.model}' is no longer offered"
            )
            row.model = ""
    else:
        row.status = CredentialStatus.INVALID
        row.status_message = result.message

    db.flush()
    _audit(db, actor_id, "credential.refreshed", row, ok=result.ok)
    return row, result


def select_model(
    db: Session, workspace_id: str, credential_id: str, model: str, actor_id: str = ""
) -> ProviderCredential:
    """Set which model this credential should use."""
    row = get_credential(db, workspace_id, credential_id)
    available = [m.get("id", "") for m in (row.available_models or [])]
    row.model = _pick_model(model, available, row.model)
    db.flush()
    _audit(db, actor_id, "credential.model_selected", row, model=row.model)
    return row


def set_default(
    db: Session, workspace_id: str, credential_id: str, actor_id: str = ""
) -> ProviderCredential:
    """Make one credential the active choice for its capability."""
    row = get_credential(db, workspace_id, credential_id)
    if row.status != CredentialStatus.VERIFIED:
        raise CredentialError("verify this key before making it the default")
    for other in list_credentials(db, workspace_id, row.kind):
        other.is_default = other.id == row.id
    db.flush()
    _audit(db, actor_id, "credential.set_default", row)
    return row


def delete_credential(
    db: Session, workspace_id: str, credential_id: str, actor_id: str = ""
) -> None:
    """Remove a credential and its ciphertext.

    If the deleted row was the default, promote another verified key for the
    same capability so the workspace does not silently fall back to local
    providers without explanation.
    """
    row = get_credential(db, workspace_id, credential_id)
    kind, provider, hint, was_default = row.kind, row.provider, row.key_hint, row.is_default

    db.delete(row)
    db.flush()

    if was_default:
        for candidate in list_credentials(db, workspace_id, kind):
            if candidate.status == CredentialStatus.VERIFIED and candidate.is_active:
                candidate.is_default = True
                break

    db.add(
        AuditLog(
            actor_id=actor_id,
            action="credential.deleted",
            entity_type="provider_credential",
            entity_id=credential_id,
            detail={"kind": kind, "provider": provider, "key_hint": hint},
        )
    )
    db.flush()


def active_credential(
    db: Session, workspace_id: str, kind: CredentialKind | str
) -> ProviderCredential | None:
    """The credential the pipeline should use for a capability, if any.

    Requires ``verified`` status and a chosen model: a key that has never been
    checked, or one with no model selected, is not something to spend a render
    on. Returning None means "use the local provider", which is always safe.
    """
    kind = CredentialKind(str(kind))
    row = db.scalars(
        select(ProviderCredential)
        .where(
            ProviderCredential.workspace_id == workspace_id,
            ProviderCredential.kind == str(kind),
            ProviderCredential.is_default.is_(True),
            ProviderCredential.is_active.is_(True),
            ProviderCredential.status == CredentialStatus.VERIFIED,
        )
        .order_by(ProviderCredential.updated_at.desc())
    ).first()
    if row is None or not row.model:
        return None
    return row


def mark_used(db: Session, credential: ProviderCredential) -> None:
    """Stamp last use so the UI can show which key is actually doing work."""
    credential.last_used_at = _now()
    db.flush()


def public_view(credential: ProviderCredential) -> dict[str, object]:
    """Serialise a credential for the API. Never includes the key."""
    return {
        "id": credential.id,
        "kind": credential.kind,
        "provider": credential.provider,
        "label": credential.label,
        "key_hint": credential.key_hint,
        "base_url": credential.base_url,
        "model": credential.model,
        "available_models": credential.available_models or [],
        "status": credential.status,
        "status_message": credential.status_message,
        "verified_at": credential.verified_at,
        "last_used_at": credential.last_used_at,
        "is_default": credential.is_default,
        "is_active": credential.is_active,
        "created_at": credential.created_at,
    }
