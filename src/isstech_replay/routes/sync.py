"""Manual local snapshot synchronization over an authenticated read-only session."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from isstech_replay.errors import upstream_error
from isstech_replay.routes.deps import get_session
from isstech_replay.routes.work_items import WorkItemOut
from isstech_replay.session_store import SessionRecord
from isstech_replay.storage import WorkflowStorage, default_database_path
from isstech_replay.sync import safe_error_message, sync_purchase_requisitions


router = APIRouter(tags=["sync"])


class SyncEventOut(BaseModel):
    kind: str
    workflow: str
    external_id: str
    observed_at: str
    old_value: str | None = None
    new_value: str | None = None
    details: dict[str, str | None] = Field(default_factory=dict)


class SyncRunOut(BaseModel):
    run_id: str
    status: str
    dry_run: bool
    started_at: str
    observed_at: str
    finished_at: str
    source_total_count: int | None = None
    observed_count: int
    actionable_count: int
    snapshot_count: int
    history_rows_inserted: int
    event_count: int
    database_path: str | None = None
    events: list[SyncEventOut] = Field(default_factory=list)
    work_items: list[WorkItemOut] = Field(default_factory=list)


@router.post("/sync/work-items", response_model=SyncRunOut)
def sync_work_items(
    session: Annotated[SessionRecord, Depends(get_session)],
    max_pages: int = Query(default=20, ge=1, le=100),
    dry_run: bool = Query(default=False),
) -> SyncRunOut:
    storage = None if dry_run else WorkflowStorage(default_database_path())
    try:
        result = sync_purchase_requisitions(
            session.client,
            storage=storage,
            max_pages=max_pages,
            dry_run=dry_run,
        )
    except PermissionError as exc:
        raise upstream_error(str(exc), details={"code_hint": "AUTH_EXPIRED"}) from exc
    except Exception as exc:
        raise upstream_error(f"sync failed: {safe_error_message(exc)}") from exc

    return SyncRunOut(
        run_id=result.run_id,
        status=result.status,
        dry_run=result.dry_run,
        started_at=result.started_at,
        observed_at=result.observed_at,
        finished_at=result.finished_at,
        source_total_count=result.source_total_count,
        observed_count=result.observed_count,
        actionable_count=result.actionable_count,
        snapshot_count=result.snapshot_count,
        history_rows_inserted=result.history_rows_inserted,
        event_count=len(result.events),
        database_path=result.database_path,
        events=[
            SyncEventOut(
                kind=event.kind.value,
                workflow=event.adapter.value,
                external_id=event.external_id,
                observed_at=event.observed_at,
                old_value=event.old_value,
                new_value=event.new_value,
                details=event.details,
            )
            for event in result.events
        ],
        work_items=[
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
            )
            for item in result.work_items
        ],
    )
