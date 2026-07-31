"""Source material ingestion (PRD FR-02).

Accepts text, documents (TXT/MD/PDF/DOCX), and images. Every asset must carry
a rights declaration before it can reach publication; see
``app.services.policy``.

Deliberately absent: any form of remote fetching or scraping. Material only
enters the system through explicit user upload, per PRD section 8.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.constants import AssetType, LicenseType, RightsStatus
from app.services import storage


class IngestError(ValueError):
    """Raised when submitted material cannot be accepted."""


@dataclass
class RightsDeclaration:
    """User's assertion about how they are allowed to use a piece of material."""

    source_name: str = ""
    rights_owner: str = ""
    license_type: str = LicenseType.UNKNOWN
    permission_reference: str = ""
    permission_date: str = ""
    usage_limits: str = ""
    attribution: str = ""
    declared: bool = False

    @property
    def status(self) -> str:
        """Declared requires an owner and a concrete licence basis."""
        if not self.declared:
            return RightsStatus.UNDECLARED
        if self.license_type == LicenseType.UNKNOWN or not self.rights_owner.strip():
            return RightsStatus.UNDECLARED
        return RightsStatus.DECLARED


@dataclass
class IngestedAsset:
    """Normalised result of ingesting one piece of material."""

    type: str
    original_filename: str
    mime_type: str
    storage_key: str
    size_bytes: int
    checksum: str
    extracted_text: str = ""
    width: int = 0
    height: int = 0


# --- text extraction -------------------------------------------------------

_WS = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


def normalise_text(raw: str) -> str:
    """Collapse whitespace while preserving paragraph breaks."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


def extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        parts = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # pypdf raises a wide variety of errors
        raise IngestError(f"could not read PDF: {exc}") from exc
    text = normalise_text("\n\n".join(parts))
    if not text:
        raise IngestError(
            "no selectable text found in PDF (scanned images are not supported; "
            "paste the recap as text instead)"
        )
    return text


def extract_docx(data: bytes) -> str:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise IngestError(f"could not read DOCX: {exc}") from exc
    paragraphs = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            paragraphs.append(" | ".join(c.text for c in row.cells))
    text = normalise_text("\n".join(paragraphs))
    if not text:
        raise IngestError("DOCX contained no text")
    return text


def extract_document(filename: str, mime_type: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf" or mime_type == "application/pdf":
        return extract_pdf(data)
    if suffix == ".docx" or "wordprocessingml" in mime_type:
        return extract_docx(data)
    if suffix in {".txt", ".md", ".markdown"} or mime_type.startswith("text/"):
        try:
            return normalise_text(data.decode("utf-8"))
        except UnicodeDecodeError:
            return normalise_text(data.decode("latin-1", errors="replace"))
    raise IngestError(f"unsupported document type: {filename} ({mime_type})")


# --- validation ------------------------------------------------------------


def _check_size(data: bytes) -> None:
    if len(data) == 0:
        raise IngestError("file is empty")
    if len(data) > settings.max_upload_bytes:
        raise IngestError(
            f"file exceeds {settings.max_upload_mb} MB limit "
            f"({len(data) / 1024 / 1024:.1f} MB)"
        )


def _sniff_image(data: bytes) -> tuple[str, int, int]:
    """Verify the bytes really are an image and return (mime, w, h).

    Trusting a client-supplied Content-Type is not enough: Pillow re-parses
    the payload so a renamed executable cannot slip into the pipeline.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            fmt = (img.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise IngestError("file is not a valid image") from exc

    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(fmt, "")
    if mime not in settings.allowed_image_types:
        raise IngestError(f"unsupported image format: {fmt or 'unknown'} (use JPEG, PNG, or WebP)")
    if width < 200 or height < 200:
        raise IngestError(f"image too small ({width}x{height}); minimum 200x200")
    return mime, width, height


# --- ingest entry points ---------------------------------------------------


def ingest_text(project_id: str, text: str, title: str = "notes.txt") -> IngestedAsset:
    """Store a pasted recap or note as a text asset."""
    cleaned = normalise_text(text)
    if len(cleaned) < 40:
        raise IngestError("text is too short to summarise (minimum 40 characters)")
    data = cleaned.encode("utf-8")
    _check_size(data)
    obj = storage.put_bytes(f"projects/{project_id}/text", title, data)
    return IngestedAsset(
        type=AssetType.TEXT,
        original_filename=title,
        mime_type="text/plain",
        storage_key=obj.storage_key,
        size_bytes=obj.size_bytes,
        checksum=obj.checksum,
        extracted_text=cleaned,
    )


def ingest_document(project_id: str, filename: str, mime_type: str, data: bytes) -> IngestedAsset:
    _check_size(data)
    text = extract_document(filename, mime_type, data)
    obj = storage.put_bytes(f"projects/{project_id}/docs", filename, data)
    return IngestedAsset(
        type=AssetType.DOCUMENT,
        original_filename=filename,
        mime_type=mime_type or "application/octet-stream",
        storage_key=obj.storage_key,
        size_bytes=obj.size_bytes,
        checksum=obj.checksum,
        extracted_text=text,
    )


def ingest_image(project_id: str, filename: str, data: bytes) -> IngestedAsset:
    _check_size(data)
    mime, width, height = _sniff_image(data)
    obj = storage.put_bytes(f"projects/{project_id}/images", filename, data)
    return IngestedAsset(
        type=AssetType.IMAGE,
        original_filename=filename,
        mime_type=mime,
        storage_key=obj.storage_key,
        size_bytes=obj.size_bytes,
        checksum=obj.checksum,
        width=width,
        height=height,
    )


def ingest_upload(project_id: str, filename: str, mime_type: str, data: bytes) -> IngestedAsset:
    """Dispatch on declared type, then verify against real content."""
    suffix = Path(filename).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"} or mime_type.startswith("image/"):
        return ingest_image(project_id, filename, data)
    if suffix in {".txt", ".md", ".markdown", ".pdf", ".docx"} or (
        mime_type in settings.allowed_doc_types
    ):
        return ingest_document(project_id, filename, mime_type, data)
    raise IngestError(
        f"unsupported file type: {filename}. Accepted: JPG, PNG, WebP, TXT, MD, PDF, DOCX"
    )
