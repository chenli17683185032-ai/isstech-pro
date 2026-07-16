"""Unified read-only work-item API across workflow adapters."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from isstech_replay.account_scope import account_database_path
from isstech_replay.errors import local_storage_error, not_found, upstream_error
from isstech_replay.models.work_items import (
    WorkItemCategory,
    WorkItemRelation,
    WorkItemScopeReason,
    WorkflowKind,
    WorkflowSnapshot,
)
from isstech_replay.routes.deps import get_session
from isstech_replay.session_store import SessionRecord
from isstech_replay.storage import WorkflowStorage, cached_workflow_detail
from isstech_replay.sync import safe_error_message, sync_procurement_workflows
from isstech_replay.work_items import personal_work_item_scope, visible_item_category

router = APIRouter(tags=["work-items"])


class WorkItemOut(BaseModel):
    key: str
    workflow: str
    workflow_label: str
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
    scope_reasons: list[WorkItemScopeReason] = Field(default_factory=list)


class WorkItemListOut(BaseModel):
    items: list[WorkItemOut]
    total_count: int
    follow_up_count: int
    approved_count: int
    other_count: int = 0
    synced_at: str


class CurrentWorkItemListOut(BaseModel):
    items: list[WorkItemOut]
    total_count: int
    follow_up_count: int
    approved_count: int
    other_count: int = 0
    synced_at: str | None = None
    source: str = "sqlite_current"
    ownership_scope: str = "personal_projects_and_submissions"
    source_total_count: int | None = None
    matched_count: int = 0
    my_project_count: int = 0
    submitted_by_me_count: int = 0
    workflow_counts: dict[str, int] = Field(default_factory=dict)
    source_counts: dict[str, int] = Field(default_factory=dict)


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
    approval_status: Literal[
        "available",
        "upstream_empty",
        "not_fetched",
        "fetch_failed",
    ] = "not_fetched"


def _snapshot_out(
    snapshot: WorkflowSnapshot,
    category: WorkItemCategory,
    scope_reasons: tuple[WorkItemScopeReason, ...] = (),
) -> WorkItemOut:
    return WorkItemOut(
        key=f"{snapshot.adapter.value}:{snapshot.external_id}",
        workflow=snapshot.adapter.value,
        workflow_label=snapshot.adapter.label,
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
        scope_reasons=list(scope_reasons),
    )


@router.get("/work-items/current", response_model=CurrentWorkItemListOut)
def list_current_work_items(
    session: Annotated[SessionRecord, Depends(get_session)],
) -> CurrentWorkItemListOut:
    try:
        storage = WorkflowStorage(account_database_path(session.username))
        snapshots = storage.current_snapshots()
        scoped = personal_work_item_scope(snapshots)
        categorized = [
            (
                record,
                visible_item_category(
                    record.snapshot.status,
                    has_current_approver=record.snapshot.actionable,
                ),
            )
            for record in scoped
        ]
        latest_runs = storage.latest_successful_runs_by_adapter()
        source_counts = {
            adapter.value: int(record["source_total_count"])
            for adapter, record in latest_runs.items()
            if record.get("source_total_count") is not None
        }
        observed_values = [
            str(record["observed_at"])
            for record in latest_runs.values()
            if record.get("observed_at") is not None
        ]
        synced_at = min(observed_values) if observed_values else None
        workflow_counts = {
            workflow.value: sum(
                record.snapshot.adapter is workflow for record in scoped
            )
            for workflow in WorkflowKind
        }
    except Exception as exc:
        raise local_storage_error(
            f"current work-item lookup failed: {type(exc).__name__}"
        ) from exc
    return CurrentWorkItemListOut(
        items=[
            _snapshot_out(record.snapshot, category, record.scope_reasons)
            for record, category in categorized
        ],
        total_count=len(categorized),
        follow_up_count=sum(
            category is WorkItemCategory.FOLLOW_UP for _, category in categorized
        ),
        approved_count=sum(
            category is WorkItemCategory.APPROVED for _, category in categorized
        ),
        other_count=sum(category is WorkItemCategory.OTHER for _, category in categorized),
        synced_at=synced_at,
        source_total_count=sum(source_counts.values()) if source_counts else None,
        matched_count=sum(bool(record.snapshot.relations) for record in scoped),
        my_project_count=sum(
            WorkItemScopeReason.MY_PROJECT in record.scope_reasons for record in scoped
        ),
        submitted_by_me_count=sum(
            WorkItemScopeReason.SUBMITTED_BY_ME in record.scope_reasons
            for record in scoped
        ),
        workflow_counts=workflow_counts,
        source_counts=source_counts,
    )


def _work_item_detail(
    workflow: WorkflowKind,
    external_id: str,
    session: SessionRecord,
) -> WorkItemDetailOut:
    normalized_id = external_id.strip()
    if not normalized_id:
        raise not_found("Work item not found in the current account scope")
    try:
        storage = WorkflowStorage(account_database_path(session.username))
        scoped_record = next(
            (
                record
                for record in personal_work_item_scope(storage.current_snapshots())
                if record.snapshot.adapter is workflow
                and record.snapshot.external_id == normalized_id
            ),
            None,
        )
    except Exception as exc:
        raise local_storage_error(
            f"work-item ownership lookup failed: {type(exc).__name__}"
        ) from exc

    snapshot = scoped_record.snapshot if scoped_record is not None else None
    category = (
        visible_item_category(
            snapshot.status,
            has_current_approver=snapshot.actionable,
        )
        if snapshot is not None
        else None
    )
    if snapshot is None or category is None:
        raise not_found("Work item not found in the current account scope")

    detail = cached_workflow_detail(snapshot)
    approval_status = detail.approval_status if detail is not None else "not_fetched"
    if detail is None or approval_status in {"not_fetched", "fetch_failed"}:
        try:
            detail = session.client.get_procurement_document_detail(
                workflow,
                normalized_id,
            )
            approval_status = (
                "available" if detail.approval_steps else "upstream_empty"
            )
        except PermissionError as exc:
            raise upstream_error(
                str(exc),
                details={"code_hint": "AUTH_EXPIRED"},
            ) from exc
        except Exception as exc:
            raise upstream_error(
                f"work-item detail failed: {safe_error_message(exc)}"
            ) from exc
    if detail is None:
        raise not_found("Cached work-item detail is unavailable")

    return WorkItemDetailOut(
        item=_snapshot_out(snapshot, category, scoped_record.scope_reasons),
        fields=detail.fields,
        html_title=detail.html_title,
        approval_status=approval_status,
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


@router.get(
    "/work-items/{workflow}/{external_id}/detail",
    response_model=WorkItemDetailOut,
)
def get_workflow_item_detail(
    workflow: WorkflowKind,
    external_id: str,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> WorkItemDetailOut:
    return _work_item_detail(workflow, external_id, session)


@router.get("/work-items/{external_id}/detail", response_model=WorkItemDetailOut)
def get_work_item_detail(
    external_id: str,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> WorkItemDetailOut:
    """Backward-compatible PurchaseRequisition detail route."""
    return _work_item_detail(WorkflowKind.PURCHASE_REQUISITION, external_id, session)


@router.get("/work-items", response_model=WorkItemListOut)
def list_work_items(
    session: Annotated[SessionRecord, Depends(get_session)],
    max_pages: int = Query(default=20, ge=1, le=100),
) -> WorkItemListOut:
    try:
        result = sync_procurement_workflows(
            session.client,
            storage=None,
            max_pages=max_pages,
            dry_run=True,
        )
        if result.status not in {"succeeded", "dry_run"}:
            failure = next(
                (stream for stream in result.streams if stream.status == "failed"),
                None,
            )
            message = (
                failure.error_message
                if failure is not None and failure.error_message
                else "one or more procurement streams were incomplete"
            )
            raise RuntimeError(message)
        items = result.work_items
    except PermissionError as exc:
        raise upstream_error(str(exc), details={"code_hint": "AUTH_EXPIRED"}) from exc
    except Exception as exc:
        raise upstream_error(f"work-item sync failed: {exc}") from exc

    return WorkItemListOut(
        items=[
            WorkItemOut(
                key=item.key,
                workflow=item.workflow.value,
                workflow_label=item.workflow.label,
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
        other_count=sum(item.category is WorkItemCategory.OTHER for item in items),
        synced_at=result.observed_at,
    )
