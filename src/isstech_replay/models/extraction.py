"""Structured document units and evidence-backed field proposals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceKind(StrEnum):
    PAGE = "page"
    DOCUMENT = "document"
    SHEET = "sheet"
    SLIDE = "slide"


class ExtractionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DocumentUnit:
    kind: SourceKind
    index: int
    label: str
    text: str


@dataclass(frozen=True, slots=True)
class StructuredDocument:
    material_id: str
    material_sha256: str
    detected_mime_type: str
    parser_name: str
    parser_version: str
    units: tuple[DocumentUnit, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentParseResult:
    document: StructuredDocument
    document_path: str
    text_path: str
    unit_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    label: str
    required: bool = False
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FieldEvidence:
    material_id: str
    source_kind: SourceKind
    source_index: int
    source_label: str
    source_text: str


@dataclass(frozen=True, slots=True)
class ProposedField:
    field_name: str
    proposed_value: str
    confidence: float
    evidence: FieldEvidence | None


@dataclass(frozen=True, slots=True)
class FieldIssue:
    code: str
    field_name: str
    message: str


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    id: str
    material_id: str
    provider: str
    model: str
    extractor_version: str
    status: ExtractionStatus
    confidence_threshold: float
    can_advance: bool
    document_path: str
    result_path: str
    proposals: tuple[ProposedField, ...]
    issues: tuple[FieldIssue, ...]
    started_at: str
    finished_at: str
