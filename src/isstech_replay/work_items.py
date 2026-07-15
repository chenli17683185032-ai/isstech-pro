"""Convert adapter-specific records into a stable local work-item contract."""

from __future__ import annotations

from datetime import date, datetime

from .models.purchase import PurchaseListResult, PurchaseRequisitionSummary
from .models.work_items import WorkItem, WorkItemCategory, WorkflowKind
from .validation import require_path_segment


_PURCHASE_PENDING_STATUSES = {"审批中"}
_PURCHASE_APPROVED_STATUSES = {"审批通过", "已完成"}


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


def _purchase_item(
    record: PurchaseRequisitionSummary,
    *,
    category: WorkItemCategory,
    base_url: str,
    today: date,
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
