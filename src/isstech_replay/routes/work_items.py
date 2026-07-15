"""Unified read-only work-item API across workflow adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from isstech_replay.account_scope import account_database_path
from isstech_replay.errors import local_storage_error, not_found, upstream_error
from isstech_replay.models.work_items import (
    WorkItemCategory,
    WorkItemRelation,
    WorkflowKind,
    WorkflowSnapshot,
)
from isstech_replay.routes.deps import get_session
from isstech_replay.session_store import SessionRecord
from isstech_replay.storage import WorkflowStorage, cached_workflow_detail
from isstech_replay.sync import read_account_purchase_measurement, safe_error_message
from isstech_replay.work_items import purchase_center_items, purchase_item_category

router = APIRouter(tags=["work-items"])


class WorkItemOut(BaseModel):
    key: str
    workflow: str
    external_id: str
    reference_no: str = ""
    project_no: str = ""
    title: str = ""
    applicant: str = ""
    submitted_at: str = ""
    status: str = ""
    current_approver: str = ""
    waiting_days: int | None = None
    source_url: str = ""
    category: WorkItemCategory = WorkItemCategory.FOLLOW_UP
    relations: list[WorkItemRelation] = Field(default_factory=list)


class WorkItemListOut(BaseModel):
    items: list[WorkItemOut]
    total_count: int
    follow_up_count: int
    approved_count: int
    synced_at: str


class CurrentWorkItemListOut(BaseModel):
    items: list[WorkItemOut]
    total_count: int
    follow_up_count: int
    approved_count: int
    synced_at: str | None = None
    source: str = "sqlite_current"
    ownership_scope: str = "participant"
    source_total_count: int | None = None
    matched_count: int = 0


class WorkItemApprovalStepOut(BaseModel):
    sequence: str = ""
    timestamp: str = ""
    approver_name: str = ""
    role: str = ""
    action: str = ""
    comment: str = ""


class WorkItemDetailOut(BaseModel):
    item: WorkItemOut
    fields: dict[str, str] = Field(default_factory=dict)
    html_title: str = ""
    approval_steps: list[WorkItemApprovalStepOut] = Field(default_factory=list)


def _snapshot_out(
    snapshot: WorkflowSnapshot,
    category: WorkItemCategory,
) -> WorkItemOut:
    return WorkItemOut(
        key=f"{snapshot.adapter.value}:{snapshot.external_id}",
        workflow=snapshot.adapter.value,
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
        relations=list(snapshot.relations),
    )


@router.get("/work-items/current", response_model=CurrentWorkItemListOut)
def list_current_work_items(
    session: Annotated[SessionRecord, Depends(get_session)],
) -> CurrentWorkItemListOut:
    try:
        storage = WorkflowStorage(account_database_path(session.username))
        snapshots = storage.current_snapshots()
        categorized = [
            (snapshot, category)
            for snapshot in snapshots
            if (
                category := purchase_item_category(
                    snapshot.status,
                    has_current_approver=snapshot.actionable,
                )
            )
            is not None
        ]
        latest_run = storage.latest_successful_run()
        synced_at = (
            str(latest_run["observed_at"])
            if latest_run is not None and latest_run.get("observed_at") is not None
            else None
        )
        source_total_count = (
            int(latest_run["source_total_count"])
            if latest_run is not None and latest_run.get("source_total_count") is not None
            else None
        )
    except Exception as exc:
        raise local_storage_error(
            f"current work-item lookup failed: {type(exc).__name__}"
        ) from exc
    return CurrentWorkItemListOut(
        items=[_snapshot_out(snapshot, category) for snapshot, category in categorized],
        total_count=len(categorized),
        follow_up_count=sum(
            category is WorkItemCategory.FOLLOW_UP for _, category in categorized
        ),
        approved_count=sum(
            category is WorkItemCategory.APPROVED for _, category in categorized
        ),
        synced_at=synced_at,
        source_total_count=source_total_count,
        matched_count=len(snapshots),
    )


@router.get("/work-items/{external_id}/detail", response_model=WorkItemDetailOut)
def get_work_item_detail(
    external_id: str,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> WorkItemDetailOut:
    normalized_id = external_id.strip()
    if not normalized_id:
        raise not_found("Work item not found in the current account scope")
    try:
        storage = WorkflowStorage(account_database_path(session.username))
        snapshot = storage.get_current_snapshot(
            WorkflowKind.PURCHASE_REQUISITION,
            normalized_id,
        )
    except Exception as exc:
        raise local_storage_error(
            f"work-item ownership lookup failed: {type(exc).__name__}"
        ) from exc

    category = (
        purchase_item_category(
            snapshot.status,
            has_current_approver=snapshot.actionable,
        )
        if snapshot is not None
        else None
    )
    if snapshot is None or category is None:
        raise not_found("Work item not found in the current account scope")

    detail = cached_workflow_detail(snapshot)
    if detail is None:
        try:
            detail = session.client.get_purchase_requisition(normalized_id)
        except PermissionError as exc:
            raise upstream_error(
                str(exc),
                details={"code_hint": "AUTH_EXPIRED"},
            ) from exc
        except Exception as exc:
            raise upstream_error(
                f"work-item detail failed: {safe_error_message(exc)}"
            ) from exc

    return WorkItemDetailOut(
        item=_snapshot_out(snapshot, category),
        fields=detail.fields,
        html_title=detail.html_title,
        approval_steps=[
            WorkItemApprovalStepOut(
                sequence=step.sequence,
                timestamp=step.timestamp,
                approver_name=step.approver_name,
                role=step.role,
                action=step.action,
                comment=step.comment,
            )
            for step in detail.approval_steps
        ],
    )


@router.get("/work-items", response_model=WorkItemListOut)
def list_work_items(
    session: Annotated[SessionRecord, Depends(get_session)],
    max_pages: int = Query(default=20, ge=1, le=100),
) -> WorkItemListOut:
    try:
        measurement = read_account_purchase_measurement(
            session.client,
            max_pages=max_pages,
        )
        items = purchase_center_items(
            measurement.result,
            base_url=session.client.settings.base_url,
            relations_by_id={
                record.summary.id: record.relations
                for record in measurement.records
            },
        )
    except PermissionError as exc:
        raise upstream_error(str(exc), details={"code_hint": "AUTH_EXPIRED"}) from exc
    except Exception as exc:
        raise upstream_error(f"work-item sync failed: {exc}") from exc

    return WorkItemListOut(
        items=[
            WorkItemOut(
                key=item.key,
                workflow=item.workflow.value,
                external_id=item.external_id,
                reference_no=item.reference_no,
                project_no=item.project_no,
                title=item.title,
                applicant=item.applicant,
                submitted_at=item.submitted_at,
                status=item.status,
                current_approver=item.current_approver,
                waiting_days=item.waiting_days,
                source_url=item.source_url,
                category=item.category,
                relations=list(item.relations),
            )
            for item in items
        ],
        total_count=len(items),
        follow_up_count=sum(
            item.category is WorkItemCategory.FOLLOW_UP for item in items
        ),
        approved_count=sum(
            item.category is WorkItemCategory.APPROVED for item in items
        ),
        synced_at=datetime.now(UTC).isoformat(),
    )
