"""Local material references and immutable content blobs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MaterialStatus(StrEnum):
    READY = "ready"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, slots=True)
class Material:
    id: str
    sha256: str
    size_bytes: int
    original_name: str
    declared_mime_type: str
    detected_mime_type: str
    extension: str
    status: MaterialStatus
    review_reason: str
    original_path: str
    created_at: str


@dataclass(frozen=True, slots=True)
class IngestResult:
    material: Material
    deduplicated: bool
    blob_created: bool
