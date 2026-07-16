"""Convert adapter-specific records into a stable local work-item contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from .models.purchase import PurchaseListResult, PurchaseRequisitionSummary
from .models.work_items import (
    WorkItem,
    WorkItemCategory,
    WorkItemRelation,
    WorkItemScopeReason,
    WorkflowKind,
    WorkflowSnapshot,
)
from .validation import require_path_segment


_PURCHASE_PENDING_STATUSES = {"审批中"}
_PURCHASE_APPROVED_STATUSES = {"审批通过", "已完成"}
_SUBMISSION_RELATIONS = {
    WorkItemRelation.APPLICANT,
    WorkItemRelation.SUBMITTER,
}


@dataclass(frozen=True, slots=True)
class PersonalWorkflowSnapshot:
    snapshot: WorkflowSnapshot
    scope_reasons: tuple[WorkItemScopeReason, ...]


def personal_work_item_scope(
    snapshots: Iterable[WorkflowSnapshot],
) -> tuple[PersonalWorkflowSnapshot, ...]:
    """Derive personal submissions and project records from complete source rows."""
    source = tuple(snapshots)
    my_project_numbers = {
        snapshot.project_no.strip()
        for snapshot in source
        if snapshot.project_no.strip()
        and WorkItemRelation.PROJECT_MANAGER in snapshot.relations
    }
    scoped: list[PersonalWorkflowSnapshot] = []
    for snapshot in source:
        reasons: list[WorkItemScopeReason] = []
        project_no = snapshot.project_no.strip()
        if project_no and project_no in my_project_numbers:
            reasons.append(WorkItemScopeReason.MY_PROJECT)
        if _SUBMISSION_RELATIONS.intersection(snapshot.relations):
            reasons.append(WorkItemScopeReason.SUBMITTED_BY_ME)
        if reasons:
            scoped.append(
                PersonalWorkflowSnapshot(
                    snapshot=snapshot,
                    scope_reasons=tuple(reasons),
                )
            )
    return tuple(scoped)


def is_purchase_active(status: str) -> bool:
    return status in _PURCHASE_PENDING_STATUSES


def is_purchase_approved(status: str) -> bool:
    return status in _PURCHASE_APPROVED_STATUSES


def purchase_item_category(
    status: str,
    *,
    has_current_approver: bool,
) -> WorkItemCategory | None:
    if is_purchase_active(status) and has_current_approver:
        return WorkItemCategory.FOLLOW_UP
    if is_purchase_approved(status):
        return WorkItemCategory.APPROVED
    return None


def visible_item_category(
    status: str,
    *,
    has_current_approver: bool,
) -> WorkItemCategory:
    return purchase_item_category(
        status,
        has_current_approver=has_current_approver,
    ) or WorkItemCategory.OTHER


def snapshot_center_item(snapshot: WorkflowSnapshot) -> WorkItem:
    category = visible_item_category(
        snapshot.status,
        has_current_approver=snapshot.actionable,
    )
    return WorkItem(
        key=f"{snapshot.adapter.value}:{snapshot.external_id}",
        workflow=snapshot.adapter,
        external_id=snapshot.external_id,
        reference_no=snapshot.reference_no,
        project_no=snapshot.project_no,
        title=snapshot.title,
        applicant=snapshot.applicant,
        submitted_at=snapshot.submitted_at,
        status=snapshot.status,
        current_approver=(
            snapshot.current_approver
            if category is WorkItemCategory.FOLLOW_UP
            else ""
        ),
        waiting_days=(
            snapshot.waiting_days
            if category is WorkItemCategory.FOLLOW_UP
            else None
        ),
        source_url=snapshot.source_url,
        category=category,
        relations=snapshot.relations,
    )


def _purchase_item(
    record: PurchaseRequisitionSummary,
    *,
    category: WorkItemCategory,
    base_url: str,
    today: date,
    relations: tuple[WorkItemRelation, ...] = (),
) -> WorkItem:
    external_id = require_path_segment(record.id, "purchase requisition id")
    return WorkItem(
        key=f"{WorkflowKind.PURCHASE_REQUISITION.value}:{external_id}",
        workflow=WorkflowKind.PURCHASE_REQUISITION,
        external_id=external_id,
        reference_no=record.requisition_no,
        project_no=record.project_no,
        title=record.project_name,
        applicant=record.creator_name,
        submitted_at=record.create_date,
        status=record.status,
        current_approver=(
            record.next_approver if category is WorkItemCategory.FOLLOW_UP else ""
        ),
        waiting_days=(
            waiting_days_since(record.create_date, today=today)
            if category is WorkItemCategory.FOLLOW_UP
            else None
        ),
        source_url=(
            f"{base_url.rstrip('/')}/WebTP/PurchaseRequisition/Detail/{external_id}"
        ),
        category=category,
        relations=relations,
    )


def waiting_days_since(value: str, *, today: date) -> int | None:
    try:
        submitted = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    if submitted > today:
        return None
    return (today - submitted).days


def purchase_follow_up_items(
    result: PurchaseListResult,
    *,
    base_url: str,
    today: date | None = None,
    relations_by_id: dict[str, tuple[WorkItemRelation, ...]] | None = None,
) -> tuple[WorkItem, ...]:
    """Return only records that are actively waiting on a named approver."""
    current_date = today or date.today()
    items = []
    for record in result.items:
        category = purchase_item_category(
            record.status,
            has_current_approver=bool(record.next_approver),
        )
        if category is not WorkItemCategory.FOLLOW_UP:
            continue
        items.append(
            _purchase_item(
                record,
                category=category,
                base_url=base_url,
                today=current_date,
                relations=(relations_by_id or {}).get(record.id, ()),
            )
        )
    return tuple(
        sorted(
            items,
            key=lambda item: (
                -(item.waiting_days if item.waiting_days is not None else -1),
                item.reference_no,
                item.external_id,
            ),
        )
    )


def purchase_center_items(
    result: PurchaseListResult,
    *,
    base_url: str,
    today: date | None = None,
    relations_by_id: dict[str, tuple[WorkItemRelation, ...]] | None = None,
) -> tuple[WorkItem, ...]:
    current_date = today or date.today()
    items = []
    for record in result.items:
        category = purchase_item_category(
            record.status,
            has_current_approver=bool(record.next_approver),
        )
        if category is None:
            continue
        items.append(
            _purchase_item(
                record,
                category=category,
                base_url=base_url,
                today=current_date,
                relations=(relations_by_id or {}).get(record.id, ()),
            )
        )
    return tuple(
        sorted(
            items,
            key=lambda item: (
                0 if item.category is WorkItemCategory.FOLLOW_UP else 1,
                -(item.waiting_days if item.waiting_days is not None else -1),
                item.reference_no,
                item.external_id,
            ),
        )
    )
