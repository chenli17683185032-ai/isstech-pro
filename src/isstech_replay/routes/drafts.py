"""Authenticated local human review and workflow-draft state transitions."""

from __future__ import annotations

from typing import Annotated, Any, Literal, NoReturn

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from isstech_replay.errors import (
    bad_request,
    conflict,
    local_storage_error,
    not_found,
    parse_error,
)
from isstech_replay.extraction import DEFAULT_MAX_UNIT_CHARS, DocumentExtractionError
from isstech_replay.materials import MaterialService
from isstech_replay.models.drafts import (
    DraftField,
    ReviewDecision,
    WorkflowDraft,
)
from isstech_replay.models.extraction import FieldEvidence, FieldIssue, SourceKind
from isstech_replay.routes.deps import get_session
from isstech_replay.session_store import SessionRecord
from isstech_replay.storage import (
    DraftStateConflict,
    DraftVersionConflict,
    StorageError,
    WorkflowStorage,
    default_data_dir,
    default_database_path,
)
from isstech_replay.sync import safe_error_message
from isstech_replay.workflow_state import (
    DraftNotFoundError,
    HumanEvidenceInput,
    MAX_CONFIRMED_VALUE_CHARS,
    MAX_SOURCE_LABEL_CHARS,
    WorkflowReviewService,
)


router = APIRouter(tags=["drafts"])


class FieldIssueOut(BaseModel):
    code: str
    field_name: str
    message: str


class FieldEvidenceOut(BaseModel):
    material_id: str
    source_kind: str
    source_index: int
    source_label: str
    source_text: str


class DraftFieldOut(BaseModel):
    field_name: str
    label: str
    required: bool
    proposed_value: str | None = None
    confidence: float | None = None
    original_evidence: FieldEvidenceOut | None = None
    original_evidence_valid: bool
    original_validation_issues: list[FieldIssueOut] = Field(default_factory=list)
    decision: str
    confirmed_value: str | None = None
    human_evidence: FieldEvidenceOut | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None


class DraftAuditEventOut(BaseModel):
    sequence: int
    event_type: str
    actor: str
    from_state: str | None = None
    to_state: str
    field_name: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class DraftOut(BaseModel):
    draft_id: str
    extraction_id: str
    material_id: str
    workflow: str
    profile: str
    state: str
    version: int
    validation_issues: list[FieldIssueOut] = Field(default_factory=list)
    created_by: str
    created_at: str
    updated_at: str
    validated_at: str | None = None
    ready_at: str | None = None
    fields: list[DraftFieldOut] = Field(default_factory=list)
    audit_events: list[DraftAuditEventOut] = Field(default_factory=list)


class DraftCreateOut(BaseModel):
    created: bool
    draft: DraftOut


class ReviewEvidenceIn(BaseModel):
    source_kind: SourceKind
    source_index: int = Field(ge=1)
    source_label: str = Field(min_length=1, max_length=MAX_SOURCE_LABEL_CHARS)
    source_text: str = Field(min_length=1, max_length=DEFAULT_MAX_UNIT_CHARS)


class FieldReviewIn(BaseModel):
    decision: Literal["confirmed", "rejected"]
    confirmed_value: str | None = Field(default=None, max_length=MAX_CONFIRMED_VALUE_CHARS)
    evidence: ReviewEvidenceIn | None = None
    expected_version: int = Field(ge=1)


class VersionedActionIn(BaseModel):
    expected_version: int = Field(ge=1)


def _material_service(request: Request) -> MaterialService:
    factory = getattr(request.app.state, "material_service_factory", None)
    if factory is not None:
        return factory()
    return MaterialService(
        data_dir=default_data_dir(),
        storage=WorkflowStorage(default_database_path()),
    )


def _service(material_service: MaterialService) -> WorkflowReviewService:
    return WorkflowReviewService(material_service)


def _issue_out(issue: FieldIssue) -> FieldIssueOut:
    return FieldIssueOut(
        code=issue.code,
        field_name=issue.field_name,
        message=issue.message,
    )


def _evidence_out(evidence: FieldEvidence | None) -> FieldEvidenceOut | None:
    if evidence is None:
        return None
    return FieldEvidenceOut(
        material_id=evidence.material_id,
        source_kind=evidence.source_kind.value,
        source_index=evidence.source_index,
        source_label=evidence.source_label,
        source_text=evidence.source_text,
    )


def _field_out(field: DraftField) -> DraftFieldOut:
    return DraftFieldOut(
        field_name=field.field_name,
        label=field.label,
        required=field.required,
        proposed_value=field.proposed_value,
        confidence=field.confidence,
        original_evidence=_evidence_out(field.original_evidence),
        original_evidence_valid=field.original_evidence_valid,
        original_validation_issues=[
            _issue_out(issue) for issue in field.original_validation_issues
        ],
        decision=field.decision.value,
        confirmed_value=field.confirmed_value,
        human_evidence=_evidence_out(field.human_evidence),
        reviewed_by=field.reviewed_by,
        reviewed_at=field.reviewed_at,
    )


def _draft_out(draft: WorkflowDraft) -> DraftOut:
    return DraftOut(
        draft_id=draft.id,
        extraction_id=draft.extraction_id,
        material_id=draft.material_id,
        workflow=draft.workflow,
        profile=draft.profile,
        state=draft.state.value,
        version=draft.version,
        validation_issues=[_issue_out(issue) for issue in draft.validation_issues],
        created_by=draft.created_by,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        validated_at=draft.validated_at,
        ready_at=draft.ready_at,
        fields=[_field_out(field) for field in draft.fields],
        audit_events=[
            DraftAuditEventOut(
                sequence=event.sequence,
                event_type=event.event_type,
                actor=event.actor,
                from_state=event.from_state.value if event.from_state else None,
                to_state=event.to_state.value,
                field_name=event.field_name,
                details=event.details,
                created_at=event.created_at,
            )
            for event in draft.audit_events
        ],
    )


def _raise_api_error(error: Exception) -> NoReturn:
    if isinstance(error, DraftNotFoundError):
        raise not_found(str(error)) from error
    if isinstance(error, (DraftVersionConflict, DraftStateConflict)):
        raise conflict(str(error)) from error
    if isinstance(error, DocumentExtractionError):
        raise parse_error(safe_error_message(error)) from error
    if isinstance(error, ValueError):
        raise bad_request(safe_error_message(error)) from error
    if isinstance(error, (StorageError, OSError)):
        raise local_storage_error(f"draft storage failed: {type(error).__name__}") from error
    raise local_storage_error(f"draft operation failed: {type(error).__name__}") from error


@router.post(
    "/extractions/{extraction_id}/drafts",
    response_model=DraftCreateOut,
    status_code=status.HTTP_201_CREATED,
)
def create_draft(
    extraction_id: str,
    session: Annotated[SessionRecord, Depends(get_session)],
    material_service: Annotated[MaterialService, Depends(_material_service)],
    response: Response,
) -> DraftCreateOut:
    try:
        result = _service(material_service).create_draft(
            extraction_id,
            reviewer=session.username,
        )
    except Exception as error:
        _raise_api_error(error)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return DraftCreateOut(created=result.created, draft=_draft_out(result.draft))


@router.get("/drafts/{draft_id}", response_model=DraftOut)
def get_draft(
    draft_id: str,
    _session: Annotated[SessionRecord, Depends(get_session)],
    material_service: Annotated[MaterialService, Depends(_material_service)],
) -> DraftOut:
    try:
        return _draft_out(_service(material_service).get_draft(draft_id))
    except Exception as error:
        _raise_api_error(error)


@router.put("/drafts/{draft_id}/fields/{field_name}", response_model=DraftOut)
def review_field(
    draft_id: str,
    field_name: str,
    body: FieldReviewIn,
    session: Annotated[SessionRecord, Depends(get_session)],
    material_service: Annotated[MaterialService, Depends(_material_service)],
) -> DraftOut:
    evidence = (
        HumanEvidenceInput(
            source_kind=body.evidence.source_kind,
            source_index=body.evidence.source_index,
            source_label=body.evidence.source_label,
            source_text=body.evidence.source_text,
        )
        if body.evidence is not None
        else None
    )
    try:
        draft = _service(material_service).review_field(
            draft_id,
            field_name,
            decision=ReviewDecision(body.decision),
            confirmed_value=body.confirmed_value,
            evidence=evidence,
            reviewer=session.username,
            expected_version=body.expected_version,
        )
        return _draft_out(draft)
    except Exception as error:
        _raise_api_error(error)


@router.post("/drafts/{draft_id}/validate", response_model=DraftOut)
def validate_draft(
    draft_id: str,
    body: VersionedActionIn,
    session: Annotated[SessionRecord, Depends(get_session)],
    material_service: Annotated[MaterialService, Depends(_material_service)],
) -> DraftOut:
    try:
        draft = _service(material_service).validate_draft(
            draft_id,
            reviewer=session.username,
            expected_version=body.expected_version,
        )
        return _draft_out(draft)
    except Exception as error:
        _raise_api_error(error)


@router.post("/drafts/{draft_id}/ready", response_model=DraftOut)
def mark_ready(
    draft_id: str,
    body: VersionedActionIn,
    session: Annotated[SessionRecord, Depends(get_session)],
    material_service: Annotated[MaterialService, Depends(_material_service)],
) -> DraftOut:
    try:
        draft = _service(material_service).mark_ready(
            draft_id,
            reviewer=session.username,
            expected_version=body.expected_version,
        )
        return _draft_out(draft)
    except Exception as error:
        _raise_api_error(error)
