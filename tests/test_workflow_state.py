"""Human review is versioned, auditable, evidence-backed, and state-guarded."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
import sqlite3

import pytest

from isstech_replay.ai.provider import RuleBasedExtractionProvider
from isstech_replay.extraction import FieldExtractionService
from isstech_replay.materials import MaterialService
from isstech_replay.models.drafts import DraftState, ReviewDecision, WorkflowDraft
from isstech_replay.models.extraction import SourceKind
from isstech_replay.storage import DraftStateConflict, DraftVersionConflict
from isstech_replay.workflow_state import HumanEvidenceInput, WorkflowReviewService


T0 = datetime(2026, 7, 15, 7, 0, tzinfo=UTC)
COMPLETE_TEXT = "\n".join(
    (
        "项目编号：PRJ-001",
        "项目名称：REDACTED PROJECT",
        "采购方式：公开询价",
    )
)


def _material_service(tmp_path: Path) -> MaterialService:
    return MaterialService(data_dir=tmp_path / "data")


def _extraction(
    materials: MaterialService,
    *,
    text: str = COMPLETE_TEXT,
    provider=None,
    extraction_id: str = "extraction-1",
):
    ingested = materials.ingest_stream(
        BytesIO(text.encode()),
        original_name=f"{extraction_id}.txt",
        declared_mime_type="text/plain",
    )
    result = FieldExtractionService(
        materials,
        provider or RuleBasedExtractionProvider(),
    ).extract(ingested.material.id, extraction_id=extraction_id)
    return ingested.material, result


def _field(draft: WorkflowDraft, name: str):
    return next(field for field in draft.fields if field.field_name == name)


def _confirm_proposed_fields(
    service: WorkflowReviewService,
    draft: WorkflowDraft,
    *,
    reviewer: str = "REVIEWER_A",
    start: datetime = T0,
) -> WorkflowDraft:
    current = draft
    offset = 1
    for field in current.fields:
        if field.source_field_id is None or field.decision is not ReviewDecision.PENDING:
            continue
        current = service.review_field(
            current.id,
            field.field_name,
            decision=ReviewDecision.CONFIRMED,
            confirmed_value=field.proposed_value,
            evidence=None,
            reviewer=reviewer,
            expected_version=current.version,
            now=start + timedelta(minutes=offset),
        )
        offset += 1
    return current


def test_draft_creation_is_idempotent_and_profile_complete(tmp_path: Path) -> None:
    materials = _material_service(tmp_path)
    _, extraction = _extraction(materials)
    service = WorkflowReviewService(materials)

    first = service.create_draft(
        extraction.id,
        reviewer="REVIEWER_A",
        draft_id="draft-1",
        now=T0,
    )
    second = service.create_draft(
        extraction.id,
        reviewer="REVIEWER_B",
        draft_id="unused-draft-id",
        now=T0 + timedelta(minutes=1),
    )

    assert first.created is True
    assert second.created is False
    assert second.draft.id == first.draft.id
    assert first.draft.state is DraftState.EXTRACTED
    assert first.draft.version == 1
    assert len(first.draft.fields) == 5
    assert {
        field.decision
        for field in first.draft.fields
        if field.source_field_id is not None
    } == {ReviewDecision.PENDING}
    assert {
        field.decision
        for field in first.draft.fields
        if not field.required and field.source_field_id is None
    } == {ReviewDecision.NOT_PROPOSED}
    assert [event.event_type for event in first.draft.audit_events] == ["draft_created"]
    assert first.draft.audit_events[0].actor == "REVIEWER_A"


def test_review_preserves_ai_proposal_and_records_reviewer(tmp_path: Path) -> None:
    materials = _material_service(tmp_path)
    _, extraction = _extraction(materials)
    service = WorkflowReviewService(materials)
    draft = service.create_draft(
        extraction.id,
        reviewer="REVIEWER_A",
        draft_id="draft-review",
        now=T0,
    ).draft
    original = _field(draft, "PR_PrjNo")

    updated = service.review_field(
        draft.id,
        "PR_PrjNo",
        decision=ReviewDecision.CONFIRMED,
        confirmed_value="PRJ-001",
        evidence=None,
        reviewer="REVIEWER_A",
        expected_version=1,
        now=T0 + timedelta(minutes=1),
    )

    reviewed = _field(updated, "PR_PrjNo")
    assert reviewed.proposed_value == original.proposed_value == "PRJ-001"
    assert reviewed.original_evidence == original.original_evidence
    assert reviewed.decision is ReviewDecision.CONFIRMED
    assert reviewed.confirmed_value == "PRJ-001"
    assert reviewed.reviewed_by == "REVIEWER_A"
    assert updated.version == 2
    stored = materials.storage.get_extraction(extraction.id)
    assert stored is not None
    source = next(field for field in stored["fields"] if field["field_name"] == "PR_PrjNo")
    assert source["proposed_value"] == "PRJ-001"
    assert source["review_status"] == "confirmed"
    assert source["confirmed_value"] == "PRJ-001"
    assert [event.event_type for event in updated.audit_events] == [
        "draft_created",
        "field_reviewed",
    ]
    assert updated.audit_events[-1].actor == "REVIEWER_A"


def test_validation_feedback_then_validated_then_ready(tmp_path: Path) -> None:
    materials = _material_service(tmp_path)
    _, extraction = _extraction(materials)
    service = WorkflowReviewService(materials)
    draft = service.create_draft(
        extraction.id,
        reviewer="REVIEWER_A",
        draft_id="draft-state",
        now=T0,
    ).draft

    failed = service.validate_draft(
        draft.id,
        reviewer="REVIEWER_A",
        expected_version=draft.version,
        now=T0 + timedelta(minutes=1),
    )
    assert failed.state is DraftState.NEEDS_REVIEW
    assert failed.version == 2
    assert {issue.code for issue in failed.validation_issues} >= {
        "missing_required",
        "review_pending",
    }

    reviewed = _confirm_proposed_fields(
        service,
        failed,
        start=T0 + timedelta(minutes=1),
    )
    validated = service.validate_draft(
        reviewed.id,
        reviewer="REVIEWER_A",
        expected_version=reviewed.version,
        now=T0 + timedelta(minutes=10),
    )
    ready = service.mark_ready(
        validated.id,
        reviewer="REVIEWER_A",
        expected_version=validated.version,
        now=T0 + timedelta(minutes=11),
    )

    assert validated.state is DraftState.VALIDATED
    assert validated.validation_issues == ()
    assert validated.validated_at is not None
    assert ready.state is DraftState.READY
    assert ready.ready_at is not None
    assert [event.event_type for event in ready.audit_events][-2:] == [
        "validation_passed",
        "draft_ready",
    ]
    assert [event.sequence for event in ready.audit_events] == list(
        range(1, ready.version + 1)
    )


def test_missing_required_ai_field_can_be_human_supplied_with_exact_evidence(
    tmp_path: Path,
) -> None:
    text = "\n".join(
        (
            "项目代号取值 PRJ-001",
            "项目名称：REDACTED PROJECT",
            "采购方式：公开询价",
        )
    )
    materials = _material_service(tmp_path)
    _, extraction = _extraction(materials, text=text, extraction_id="extraction-missing")
    service = WorkflowReviewService(materials)
    draft = service.create_draft(
        extraction.id,
        reviewer="REVIEWER_A",
        draft_id="draft-missing",
        now=T0,
    ).draft
    missing = _field(draft, "PR_PrjNo")
    assert draft.state is DraftState.NEEDS_REVIEW
    assert missing.source_field_id is None
    assert missing.decision is ReviewDecision.PENDING

    reviewed = service.review_field(
        draft.id,
        "PR_PrjNo",
        decision=ReviewDecision.CONFIRMED,
        confirmed_value="PRJ-001",
        evidence=HumanEvidenceInput(
            source_kind=SourceKind.DOCUMENT,
            source_index=1,
            source_label="Document",
            source_text="项目代号取值 PRJ-001",
        ),
        reviewer="REVIEWER_A",
        expected_version=draft.version,
        now=T0 + timedelta(minutes=1),
    )
    reviewed = _confirm_proposed_fields(
        service,
        reviewed,
        start=T0 + timedelta(minutes=1),
    )
    validated = service.validate_draft(
        draft.id,
        reviewer="REVIEWER_A",
        expected_version=reviewed.version,
        now=T0 + timedelta(minutes=10),
    )

    assert validated.state is DraftState.VALIDATED
    supplied = _field(validated, "PR_PrjNo")
    assert supplied.proposed_value is None
    assert supplied.confirmed_value == "PRJ-001"
    assert supplied.human_evidence is not None


def test_bad_human_evidence_keeps_draft_in_review(tmp_path: Path) -> None:
    text = "项目代号取值 PRJ-001\n项目名称：REDACTED PROJECT\n采购方式：公开询价"
    materials = _material_service(tmp_path)
    _, extraction = _extraction(materials, text=text, extraction_id="extraction-bad-source")
    service = WorkflowReviewService(materials)
    draft = service.create_draft(
        extraction.id,
        reviewer="REVIEWER_A",
        draft_id="draft-bad-source",
        now=T0,
    ).draft
    draft = service.review_field(
        draft.id,
        "PR_PrjNo",
        decision=ReviewDecision.CONFIRMED,
        confirmed_value="PRJ-001",
        evidence=HumanEvidenceInput(
            SourceKind.DOCUMENT,
            1,
            "Document",
            "不存在的来源 PRJ-001",
        ),
        reviewer="REVIEWER_A",
        expected_version=draft.version,
        now=T0 + timedelta(minutes=1),
    )
    draft = _confirm_proposed_fields(service, draft)

    validated = service.validate_draft(
        draft.id,
        reviewer="REVIEWER_A",
        expected_version=draft.version,
        now=T0 + timedelta(minutes=10),
    )

    assert validated.state is DraftState.NEEDS_REVIEW
    assert any(
        issue.code == "source_text_mismatch" and issue.field_name == "PR_PrjNo"
        for issue in validated.validation_issues
    )


def test_human_confirmation_resolves_low_confidence_but_not_evidence(tmp_path: Path) -> None:
    class LowConfidenceProvider(RuleBasedExtractionProvider):
        name = "low_confidence_test"

        def propose(self, document, field_specs):
            proposals = super().propose(document, field_specs)
            return tuple(
                replace(proposal, confidence=0.5)
                if proposal.field_name == "PR_PrjNo"
                else proposal
                for proposal in proposals
            )

    materials = _material_service(tmp_path)
    _, extraction = _extraction(
        materials,
        provider=LowConfidenceProvider(),
        extraction_id="extraction-low-confidence",
    )
    service = WorkflowReviewService(materials)
    draft = service.create_draft(
        extraction.id,
        reviewer="REVIEWER_A",
        draft_id="draft-low-confidence",
        now=T0,
    ).draft
    assert draft.state is DraftState.NEEDS_REVIEW

    reviewed = _confirm_proposed_fields(service, draft)
    validated = service.validate_draft(
        draft.id,
        reviewer="REVIEWER_A",
        expected_version=reviewed.version,
        now=T0 + timedelta(minutes=10),
    )

    assert validated.state is DraftState.VALIDATED
    assert _field(validated, "PR_PrjNo").confidence == 0.5


def test_stale_version_and_invalid_state_transitions_are_rejected(tmp_path: Path) -> None:
    materials = _material_service(tmp_path)
    _, extraction = _extraction(materials)
    service = WorkflowReviewService(materials)
    draft = service.create_draft(
        extraction.id,
        reviewer="REVIEWER_A",
        draft_id="draft-conflict",
        now=T0,
    ).draft

    with pytest.raises(DraftStateConflict, match="ready"):
        service.mark_ready(
            draft.id,
            reviewer="REVIEWER_A",
            expected_version=draft.version,
            now=T0 + timedelta(minutes=1),
        )
    updated = service.review_field(
        draft.id,
        "PR_PrjNo",
        decision=ReviewDecision.CONFIRMED,
        confirmed_value="PRJ-001",
        evidence=None,
        reviewer="REVIEWER_A",
        expected_version=draft.version,
        now=T0 + timedelta(minutes=1),
    )
    with pytest.raises(DraftVersionConflict, match="stale"):
        service.review_field(
            draft.id,
            "PR_PrjName",
            decision=ReviewDecision.CONFIRMED,
            confirmed_value="REDACTED PROJECT",
            evidence=None,
            reviewer="REVIEWER_B",
            expected_version=draft.version,
            now=T0 + timedelta(minutes=2),
        )
    assert service.get_draft(draft.id).version == updated.version


def test_rejected_required_field_cannot_validate_or_become_ready(tmp_path: Path) -> None:
    materials = _material_service(tmp_path)
    _, extraction = _extraction(materials, extraction_id="extraction-rejected")
    service = WorkflowReviewService(materials)
    draft = service.create_draft(
        extraction.id,
        reviewer="REVIEWER_A",
        draft_id="draft-rejected",
        now=T0,
    ).draft
    draft = service.review_field(
        draft.id,
        "PR_PrjNo",
        decision=ReviewDecision.REJECTED,
        confirmed_value=None,
        evidence=None,
        reviewer="REVIEWER_A",
        expected_version=draft.version,
        now=T0 + timedelta(minutes=1),
    )
    draft = _confirm_proposed_fields(service, draft)

    checked = service.validate_draft(
        draft.id,
        reviewer="REVIEWER_A",
        expected_version=draft.version,
        now=T0 + timedelta(minutes=10),
    )

    assert checked.state is DraftState.NEEDS_REVIEW
    assert {issue.code for issue in checked.validation_issues} >= {
        "missing_required",
        "required_rejected",
    }
    with pytest.raises(DraftStateConflict, match="ready"):
        service.mark_ready(
            checked.id,
            reviewer="REVIEWER_A",
            expected_version=checked.version,
            now=T0 + timedelta(minutes=11),
        )


def test_validated_draft_fields_are_locked_and_audit_is_append_only(tmp_path: Path) -> None:
    materials = _material_service(tmp_path)
    _, extraction = _extraction(materials)
    service = WorkflowReviewService(materials)
    draft = service.create_draft(
        extraction.id,
        reviewer="REVIEWER_A",
        draft_id="draft-audit",
        now=T0,
    ).draft
    draft = _confirm_proposed_fields(service, draft)
    validated = service.validate_draft(
        draft.id,
        reviewer="REVIEWER_A",
        expected_version=draft.version,
        now=T0 + timedelta(minutes=10),
    )

    with pytest.raises(DraftStateConflict, match="field review"):
        service.review_field(
            validated.id,
            "PR_PrjNo",
            decision=ReviewDecision.REJECTED,
            confirmed_value=None,
            evidence=None,
            reviewer="REVIEWER_A",
            expected_version=validated.version,
            now=T0 + timedelta(minutes=11),
        )
    connection = sqlite3.connect(materials.storage.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE draft_audit_events SET actor = 'TAMPERED' WHERE draft_id = ?",
                (validated.id,),
            )
    finally:
        connection.close()
    assert service.get_draft(validated.id).audit_events[-1].actor == "REVIEWER_A"
