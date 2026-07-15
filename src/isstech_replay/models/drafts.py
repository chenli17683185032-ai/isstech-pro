"""Human-reviewed workflow drafts and append-only local audit events."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .extraction import FieldEvidence, FieldIssue


class DraftState(StrEnum):
    EXTRACTED = "extracted"
    NEEDS_REVIEW = "needs_review"
    VALIDATED = "validated"
    READY = "ready"
    PREVIEWED = "previewed"
    SUBMITTED = "submitted"
    RECONCILING = "reconciling"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewDecision(StrEnum):
    NOT_PROPOSED = "not_proposed"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DraftField:
    field_name: str
    label: str
    required: bool
    source_field_id: int | None
    proposed_value: str | None
    confidence: float | None
    original_evidence: FieldEvidence | None
    original_evidence_valid: bool
    original_validation_issues: tuple[FieldIssue, ...]
    decision: ReviewDecision
    confirmed_value: str | None
    human_evidence: FieldEvidence | None
    reviewed_by: str | None
    reviewed_at: str | None


@dataclass(frozen=True, slots=True)
class DraftAuditEvent:
    id: int
    sequence: int
    event_type: str
    actor: str
    from_state: DraftState | None
    to_state: DraftState
    field_name: str | None
    details: dict[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class WorkflowDraft:
    id: str
    extraction_id: str
    material_id: str
    workflow: str
    profile: str
    state: DraftState
    version: int
    validation_issues: tuple[FieldIssue, ...]
    created_by: str
    created_at: str
    updated_at: str
    validated_at: str | None
    ready_at: str | None
    fields: tuple[DraftField, ...] = ()
    audit_events: tuple[DraftAuditEvent, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DraftCreateResult:
    draft: WorkflowDraft
    created: bool
