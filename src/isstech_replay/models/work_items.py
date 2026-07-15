"""Normalized work items emitted by workflow-specific adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class WorkflowKind(StrEnum):
    PURCHASE_REQUISITION = "purchase_requisition"


class WorkItemCategory(StrEnum):
    FOLLOW_UP = "follow_up"
    APPROVED = "approved"


class ChangeKind(StrEnum):
    NEW = "new"
    NODE_CHANGED = "node_changed"
    COMPLETED = "completed"
    ASSIGNEE_CHANGED = "assignee_changed"


@dataclass(frozen=True, slots=True)
class WorkItem:
    key: str
    workflow: WorkflowKind
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


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    adapter: WorkflowKind
    external_id: str
    observed_at: str
    reference_no: str = ""
    project_no: str = ""
    title: str = ""
    applicant: str = ""
    submitted_at: str = ""
    status: str = ""
    current_node: str = ""
    current_approver: str = ""
    waiting_days: int | None = None
    source_url: str = ""
    active: bool = False
    actionable: bool = False
    payload_json: str = ""
    payload_hash: str = ""


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    kind: ChangeKind
    adapter: WorkflowKind
    external_id: str
    observed_at: str
    old_value: str | None = None
    new_value: str | None = None
    details: dict[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SyncResult:
    run_id: str
    status: str
    dry_run: bool
    started_at: str
    observed_at: str
    finished_at: str
    source_total_count: int | None
    observed_count: int
    actionable_count: int
    snapshot_count: int
    history_rows_inserted: int
    events: tuple[ChangeEvent, ...] = ()
    work_items: tuple[WorkItem, ...] = ()
    database_path: str | None = None
