"""Workflow field profiles and evidence/threshold gates for provider output."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .models.extraction import (
    FieldIssue,
    FieldSpec,
    ProposedField,
    StructuredDocument,
)


DEFAULT_CONFIDENCE_THRESHOLD = 0.85


PURCHASE_REQUISITION_FIELDS = (
    FieldSpec(
        name="PR_PrjNo",
        label="项目编号",
        required=True,
        aliases=("项目号", "Project Number"),
    ),
    FieldSpec(
        name="PR_PrjName",
        label="项目名称",
        required=True,
        aliases=("Project Name",),
    ),
    FieldSpec(
        name="PR_ProcurementMethod",
        label="采购方式",
        required=True,
        aliases=("Procurement Method",),
    ),
    FieldSpec(
        name="PR_SigningEntity",
        label="签署主体",
        aliases=("签约主体", "Signing Entity"),
    ),
    FieldSpec(
        name="PR_Remark",
        label="备注",
        aliases=("说明", "Remark"),
    ),
)


FIELD_PROFILES = {"purchase_requisition": PURCHASE_REQUISITION_FIELDS}


@dataclass(frozen=True, slots=True)
class ProposalValidation:
    proposals: tuple[ProposedField, ...]
    issues: tuple[FieldIssue, ...]
    can_advance: bool


def field_profile(name: str) -> tuple[FieldSpec, ...]:
    try:
        return FIELD_PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown field profile: {name}") from exc


def validate_proposals(
    document: StructuredDocument,
    field_specs: tuple[FieldSpec, ...],
    proposals: tuple[ProposedField, ...],
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> ProposalValidation:
    if not 0 <= confidence_threshold <= 1:
        raise ValueError("confidence_threshold must be between 0 and 1")
    specs = {spec.name: spec for spec in field_specs}
    units = {(unit.kind, unit.index): unit for unit in document.units}
    accepted: list[ProposedField] = []
    issues: list[FieldIssue] = []
    seen: set[str] = set()

    for proposal in proposals:
        field_name = proposal.field_name
        if field_name not in specs:
            issues.append(
                FieldIssue("unknown_field", field_name, "provider returned an unrequested field")
            )
            continue
        if field_name in seen:
            issues.append(
                FieldIssue("duplicate_field", field_name, "provider returned the field more than once")
            )
            continue
        seen.add(field_name)
        accepted.append(proposal)
        if not proposal.proposed_value.strip():
            issues.append(FieldIssue("empty_value", field_name, "proposed value is empty"))
        if not math.isfinite(proposal.confidence) or not 0 <= proposal.confidence <= 1:
            issues.append(
                FieldIssue("invalid_confidence", field_name, "confidence must be finite and 0..1")
            )
        elif proposal.confidence < confidence_threshold:
            issues.append(
                FieldIssue(
                    "low_confidence",
                    field_name,
                    f"confidence is below threshold {confidence_threshold}",
                )
            )

        evidence = proposal.evidence
        if evidence is None:
            issues.append(FieldIssue("missing_evidence", field_name, "source evidence is required"))
            continue
        if evidence.material_id != document.material_id:
            issues.append(
                FieldIssue("wrong_material", field_name, "evidence material does not match")
            )
            continue
        unit = units.get((evidence.source_kind, evidence.source_index))
        if unit is None:
            issues.append(
                FieldIssue("unknown_source", field_name, "evidence source unit does not exist")
            )
            continue
        if evidence.source_label != unit.label:
            issues.append(
                FieldIssue("source_label_mismatch", field_name, "evidence source label differs")
            )
        excerpt = evidence.source_text.strip()
        if not excerpt or excerpt not in unit.text:
            issues.append(
                FieldIssue("source_text_mismatch", field_name, "source excerpt is not in the unit")
            )
        elif proposal.proposed_value.strip() not in excerpt:
            issues.append(
                FieldIssue("value_not_in_source", field_name, "proposed value is not in source excerpt")
            )

    for spec in field_specs:
        if spec.required and spec.name not in seen:
            issues.append(FieldIssue("missing_required", spec.name, "required field is missing"))
    for document_issue in document.issues:
        issues.append(FieldIssue("document_issue", "", document_issue))
    return ProposalValidation(
        proposals=tuple(accepted),
        issues=tuple(issues),
        can_advance=not issues,
    )
