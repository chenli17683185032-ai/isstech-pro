"""Human review service and guarded local workflow-draft state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from .extraction import DEFAULT_MAX_UNIT_CHARS, DocumentExtractionService
from .field_mapping import field_profile, validate_proposals
from .materials import MaterialService
from .models.drafts import (
    DraftCreateResult,
    DraftState,
    ReviewDecision,
    WorkflowDraft,
)
from .models.extraction import FieldEvidence, FieldIssue, ProposedField, SourceKind
from .storage import DraftStateConflict, DraftVersionConflict
from .validation import require_path_segment


class DraftNotFoundError(LookupError):
    """The requested local draft or extraction does not exist."""


MAX_CONFIRMED_VALUE_CHARS = 10_000
MAX_SOURCE_LABEL_CHARS = 500


@dataclass(frozen=True, slots=True)
class HumanEvidenceInput:
    source_kind: SourceKind
    source_index: int
    source_label: str
    source_text: str


def _utc_now(value: datetime | None = None) -> str:
    actual = value or datetime.now(UTC)
    if actual.tzinfo is None:
        raise ValueError("review timestamps must be timezone-aware")
    return actual.astimezone(UTC).isoformat()


def _reviewer(value: str) -> str:
    reviewer = value.strip()
    if not reviewer:
        raise ValueError("reviewer identity is required")
    if len(reviewer) > 200 or any(character in reviewer for character in "\r\n\x00"):
        raise ValueError("reviewer identity is invalid")
    return reviewer


class WorkflowReviewService:
    def __init__(self, material_service: MaterialService) -> None:
        self.material_service = material_service
        self.storage = material_service.storage

    def create_draft(
        self,
        extraction_id: str,
        *,
        reviewer: str,
        draft_id: str | None = None,
        now: datetime | None = None,
    ) -> DraftCreateResult:
        actor = _reviewer(reviewer)
        extraction = self.storage.get_extraction(extraction_id)
        if extraction is None:
            raise DraftNotFoundError("extraction not found")
        profile = str(extraction["profile"])
        specs = field_profile(profile)
        workflow = self._workflow_for_profile(profile)
        actual_id = require_path_segment(draft_id or uuid4().hex, "draft_id")
        return self.storage.create_workflow_draft(
            draft_id=actual_id,
            extraction_id=extraction_id,
            workflow=workflow,
            field_specs=specs,
            actor=actor,
            created_at=_utc_now(now),
        )

    def get_draft(self, draft_id: str) -> WorkflowDraft:
        draft = self.storage.get_workflow_draft(draft_id)
        if draft is None:
            raise DraftNotFoundError("workflow draft not found")
        return draft

    def review_field(
        self,
        draft_id: str,
        field_name: str,
        *,
        decision: ReviewDecision,
        confirmed_value: str | None,
        evidence: HumanEvidenceInput | None,
        reviewer: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> WorkflowDraft:
        actor = _reviewer(reviewer)
        draft = self.get_draft(draft_id)
        specs = {spec.name for spec in field_profile(draft.profile)}
        if field_name not in specs:
            raise ValueError(f"field is not in the draft profile: {field_name}")
        human_evidence = None
        if evidence is not None:
            if evidence.source_index < 1:
                raise ValueError("evidence source_index must be positive")
            if not evidence.source_label or len(evidence.source_label) > MAX_SOURCE_LABEL_CHARS:
                raise ValueError("evidence source_label is invalid")
            if not evidence.source_text or len(evidence.source_text) > DEFAULT_MAX_UNIT_CHARS:
                raise ValueError("evidence source_text is invalid")
            human_evidence = FieldEvidence(
                material_id=draft.material_id,
                source_kind=evidence.source_kind,
                source_index=evidence.source_index,
                source_label=evidence.source_label,
                source_text=evidence.source_text,
            )
        if confirmed_value is not None and len(confirmed_value) > MAX_CONFIRMED_VALUE_CHARS:
            raise ValueError("confirmed value exceeds configured character limit")
        return self.storage.review_workflow_draft_field(
            draft_id=draft_id,
            field_name=field_name,
            decision=decision,
            confirmed_value=confirmed_value,
            human_evidence=human_evidence,
            actor=actor,
            reviewed_at=_utc_now(now),
            expected_version=expected_version,
        )

    def validate_draft(
        self,
        draft_id: str,
        *,
        reviewer: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> WorkflowDraft:
        actor = _reviewer(reviewer)
        draft = self.get_draft(draft_id)
        if draft.version != expected_version:
            raise DraftVersionConflict(
                f"stale draft version {expected_version}; current version is {draft.version}"
            )
        if draft.state not in {DraftState.EXTRACTED, DraftState.NEEDS_REVIEW}:
            raise DraftStateConflict(
                f"validation is not allowed from draft state {draft.state.value}"
            )
        specs = field_profile(draft.profile)
        parsed = DocumentExtractionService(self.material_service).parse(draft.material_id)
        proposals: list[ProposedField] = []
        review_issues: list[FieldIssue] = []
        for field in draft.fields:
            if field.decision is ReviewDecision.CONFIRMED:
                proposals.append(
                    ProposedField(
                        field_name=field.field_name,
                        proposed_value=field.confirmed_value or "",
                        confidence=1.0,
                        evidence=field.human_evidence or field.original_evidence,
                    )
                )
            elif field.decision is ReviewDecision.PENDING and (
                field.required or field.source_field_id is not None
            ):
                review_issues.append(
                    FieldIssue(
                        "review_pending",
                        field.field_name,
                        "field still requires an explicit human decision",
                    )
                )
            elif field.decision is ReviewDecision.REJECTED and field.required:
                review_issues.append(
                    FieldIssue(
                        "required_rejected",
                        field.field_name,
                        "required field cannot be rejected",
                    )
                )
        validation = validate_proposals(
            parsed.document,
            specs,
            tuple(proposals),
            confidence_threshold=0,
        )
        issues = self._unique_issues((*validation.issues, *review_issues))
        return self.storage.apply_workflow_draft_validation(
            draft_id=draft_id,
            issues=issues,
            actor=actor,
            validated_at=_utc_now(now),
            expected_version=expected_version,
        )

    def mark_ready(
        self,
        draft_id: str,
        *,
        reviewer: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> WorkflowDraft:
        return self.storage.mark_workflow_draft_ready(
            draft_id=draft_id,
            actor=_reviewer(reviewer),
            ready_at=_utc_now(now),
            expected_version=expected_version,
        )

    @staticmethod
    def _workflow_for_profile(profile: str) -> str:
        if profile == "purchase_requisition":
            return "purchase_requisition"
        raise ValueError(f"profile has no workflow mapping: {profile}")

    @staticmethod
    def _unique_issues(issues: tuple[FieldIssue, ...]) -> tuple[FieldIssue, ...]:
        output: list[FieldIssue] = []
        seen: set[tuple[str, str, str]] = set()
        for issue in issues:
            identity = (issue.code, issue.field_name, issue.message)
            if identity not in seen:
                seen.add(identity)
                output.append(issue)
        return tuple(output)
