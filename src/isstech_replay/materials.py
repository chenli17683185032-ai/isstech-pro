"""Immutable local material ingestion with content-addressed deduplication."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import tempfile
from typing import BinaryIO
from uuid import uuid4
import zipfile

from .models.materials import IngestResult, Material, MaterialStatus
from .storage import DEFAULT_DATABASE_NAME, WorkflowStorage, default_data_dir
from .validation import require_path_segment


DEFAULT_MAX_MATERIAL_BYTES = 100 * 1024 * 1024
_UNINFORMATIVE_MIME_TYPES = {"", "application/octet-stream", "binary/octet-stream"}
_OFFICE_MIME_TYPES = {
    "word/": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xl/": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt/": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class MaterialIngestError(RuntimeError):
    """The source cannot become a complete, trusted local material record."""


class MaterialTooLargeError(MaterialIngestError):
    """The streaming size ceiling was reached."""


class MaterialIntegrityError(MaterialIngestError):
    """A content-addressed blob does not match its path or metadata."""


def material_max_bytes_from_env() -> int:
    value = int(os.getenv("ISSTECH_MAX_MATERIAL_BYTES", str(DEFAULT_MAX_MATERIAL_BYTES)))
    if value < 1:
        raise ValueError("ISSTECH_MAX_MATERIAL_BYTES must be positive")
    return value


def _normalize_filename(value: str) -> str:
    name = (value or "").strip()
    if not name or name in {".", ".."}:
        raise ValueError("original_name is required")
    if len(name.encode("utf-8")) > 255:
        raise ValueError("original_name exceeds 255 UTF-8 bytes")
    if "\x00" in name or "/" in name or "\\" in name:
        raise ValueError("original_name must not contain path separators")
    return name


def _normalize_mime(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _detect_zip_mime(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return "application/octet-stream"
    for prefix, mime_type in _OFFICE_MIME_TYPES.items():
        if any(name.startswith(prefix) for name in names):
            return mime_type
    return "application/zip"


def _detect_text_mime(path: Path, head: bytes) -> str | None:
    if b"\x00" in head:
        return None
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return None
    stripped = head.lstrip()
    if stripped.startswith((b"{", b"[")) and path.stat().st_size <= 5 * 1024 * 1024:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        else:
            return "application/json"
    return "text/plain"


def detect_mime_type(path: Path, head: bytes) -> str:
    if not head:
        return "application/x-empty"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        return _detect_zip_mime(path)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/x-ole-storage"
    return _detect_text_mime(path, head) or "application/octet-stream"


def _mime_compatible(expected: str, detected: str) -> bool:
    if expected == detected:
        return True
    if expected.startswith("text/") and detected == "text/plain":
        return True
    aliases = {
        ("image/jpg", "image/jpeg"),
        ("application/x-pdf", "application/pdf"),
    }
    return (expected, detected) in aliases


def _review_reasons(
    *,
    extension: str,
    declared_mime_type: str,
    detected_mime_type: str,
) -> tuple[str, ...]:
    reasons: list[str] = []
    expected_mime = _normalize_mime(mimetypes.guess_type(f"file{extension}")[0])
    if expected_mime and not _mime_compatible(expected_mime, detected_mime_type):
        reasons.append(
            f"extension {extension or '<none>'} expects {expected_mime}; "
            f"content is {detected_mime_type}"
        )
    if (
        declared_mime_type not in _UNINFORMATIVE_MIME_TYPES
        and not _mime_compatible(declared_mime_type, detected_mime_type)
    ):
        reasons.append(
            f"declared MIME {declared_mime_type}; content is {detected_mime_type}"
        )
    if detected_mime_type in {"application/octet-stream", "application/x-empty"}:
        reasons.append(f"content type requires review: {detected_mime_type}")
    return tuple(dict.fromkeys(reasons))


class MaterialService:
    def __init__(
        self,
        *,
        data_dir: str | Path | None = None,
        storage: WorkflowStorage | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.data_dir = Path(data_dir or default_data_dir()).expanduser()
        self.storage = storage or WorkflowStorage(self.data_dir / DEFAULT_DATABASE_NAME)
        self.max_bytes = (
            max_bytes if max_bytes is not None else material_max_bytes_from_env()
        )
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.material_root = self.data_dir / "materials"
        self.originals_root = self.material_root / "originals"
        self.derived_root = self.material_root / "derived"
        self.staging_root = self.material_root / ".staging"

    def _prepare_directories(self) -> None:
        for path in (
            self.data_dir,
            self.material_root,
            self.originals_root,
            self.derived_root,
            self.staging_root,
        ):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)

    def ingest_path(
        self,
        path: str | Path,
        *,
        declared_mime_type: str | None = None,
    ) -> IngestResult:
        source = Path(path)
        if source.is_symlink():
            raise ValueError("symbolic-link materials are not accepted")
        if not source.is_file():
            raise ValueError(f"material path is not a regular file: {source}")
        declared = declared_mime_type or mimetypes.guess_type(source.name)[0]
        with source.open("rb") as stream:
            return self.ingest_stream(
                stream,
                original_name=source.name,
                declared_mime_type=declared,
            )

    def ingest_stream(
        self,
        stream: BinaryIO,
        *,
        original_name: str,
        declared_mime_type: str | None = None,
    ) -> IngestResult:
        name = _normalize_filename(original_name)
        declared = _normalize_mime(declared_mime_type)
        extension = Path(name).suffix.lower()
        self._prepare_directories()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="material-",
            suffix=".part",
            dir=self.staging_root,
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        size_bytes = 0
        head = bytearray()
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise TypeError("material stream must return bytes")
                    size_bytes += len(chunk)
                    if size_bytes > self.max_bytes:
                        raise MaterialTooLargeError(
                            f"material exceeds configured size limit ({self.max_bytes} bytes)"
                        )
                    digest.update(chunk)
                    output.write(chunk)
                    if len(head) < 8192:
                        head.extend(chunk[: 8192 - len(head)])
                output.flush()
                os.fsync(output.fileno())

            sha256 = digest.hexdigest()
            detected = detect_mime_type(temporary, bytes(head))
            reasons = _review_reasons(
                extension=extension,
                declared_mime_type=declared,
                detected_mime_type=detected,
            )
            status = (
                MaterialStatus.NEEDS_REVIEW if reasons else MaterialStatus.READY
            )
            relative_blob = Path("materials") / "originals" / sha256 / "blob"
            final_blob = self.data_dir / relative_blob
            final_blob.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(final_blob.parent, 0o700)
            if final_blob.exists():
                self._verify_blob(final_blob, sha256=sha256, size_bytes=size_bytes)
            else:
                os.replace(temporary, final_blob)
            os.chmod(final_blob, 0o400)

            created_at = datetime.now(UTC).isoformat()
            material, deduplicated, blob_created = self.storage.register_material(
                material_id=uuid4().hex,
                sha256=sha256,
                size_bytes=size_bytes,
                original_path=relative_blob.as_posix(),
                original_name=name,
                declared_mime_type=declared,
                detected_mime_type=detected,
                extension=extension,
                status=status,
                review_reason="; ".join(reasons),
                created_at=created_at,
            )
            return IngestResult(
                material=material,
                deduplicated=deduplicated,
                blob_created=blob_created,
            )
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _verify_blob(path: Path, *, sha256: str, size_bytes: int) -> None:
        if not path.is_file() or path.stat().st_size != size_bytes:
            raise MaterialIntegrityError("existing material blob size does not match SHA path")
        if _sha256_file(path) != sha256:
            raise MaterialIntegrityError("existing material blob hash does not match SHA path")

    def resolve_original(self, material: Material) -> Path:
        root = self.data_dir.resolve()
        path = (self.data_dir / material.original_path).resolve()
        if not path.is_relative_to(root):
            raise MaterialIntegrityError("material original path escapes the data directory")
        self._verify_blob(path, sha256=material.sha256, size_bytes=material.size_bytes)
        return path

    def derived_directory(self, material_id: str) -> Path:
        return self.derived_root / require_path_segment(material_id, "material_id")

    def get(self, material_id: str) -> Material | None:
        return self.storage.get_material(material_id)

    def list(
        self,
        *,
        status: MaterialStatus | None = None,
        limit: int = 100,
    ) -> tuple[Material, ...]:
        return self.storage.list_materials(status=status, limit=limit)

    def ingest_paths(self, paths: Iterable[str | Path]) -> tuple[IngestResult, ...]:
        return tuple(self.ingest_path(path) for path in paths)
