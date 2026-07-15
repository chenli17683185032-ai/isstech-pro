"""Convert adapter-specific records into a stable local work-item contract."""

from __future__ import annotations

from datetime import date, datetime

from .models.purchase import PurchaseListResult
from .models.work_items import WorkItem, WorkflowKind
from .validation import require_path_segment


_PURCHASE_PENDING_STATUSES = {"审批中"}


def _waiting_days(value: str, *, today: date) -> int | None:
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
        if record.status not in _PURCHASE_PENDING_STATUSES or not record.next_approver:
            continue
        external_id = require_path_segment(record.id, "purchase requisition id")
        items.append(
            WorkItem(
                key=f"{WorkflowKind.PURCHASE_REQUISITION.value}:{external_id}",
                workflow=WorkflowKind.PURCHASE_REQUISITION,
                external_id=external_id,
                reference_no=record.requisition_no,
                project_no=record.project_no,
                title=record.project_name,
                applicant=record.creator_name,
                submitted_at=record.create_date,
                status=record.status,
                current_approver=record.next_approver,
                waiting_days=_waiting_days(record.create_date, today=current_date),
                source_url=(
                    f"{base_url.rstrip('/')}/WebTP/PurchaseRequisition/Detail/{external_id}"
                ),
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
