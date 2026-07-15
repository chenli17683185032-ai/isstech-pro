"""Unified read-only work-item API across workflow adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from isstech_replay.account_scope import account_database_path
from isstech_replay.errors import local_storage_error, upstream_error
from isstech_replay.models.work_items import (
    WorkItemCategory,
    WorkflowSnapshot,
)
from isstech_replay.routes.deps import get_session
from isstech_replay.session_store import SessionRecord
from isstech_replay.storage import WorkflowStorage
from isstech_replay.sync import read_account_purchase_requisitions
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
        synced_at = storage.latest_successful_observed_at()
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
    )


@router.get("/work-items", response_model=WorkItemListOut)
def list_work_items(
    session: Annotated[SessionRecord, Depends(get_session)],
    max_pages: int = Query(default=20, ge=1, le=100),
) -> WorkItemListOut:
    try:
        result = read_account_purchase_requisitions(
            session.client,
            max_pages=max_pages,
        )
        items = purchase_center_items(
            result,
            base_url=session.client.settings.base_url,
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
