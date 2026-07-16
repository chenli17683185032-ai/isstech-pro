"""Normalized work items emitted by workflow-specific adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class WorkflowKind(StrEnum):
    PURCHASE_REQUISITION = "purchase_requisition"
    PROCUREMENT_CONTRACT = "procurement_contract"
    PROCUREMENT_ORDER = "procurement_order"
    COST_CONFIRMATION = "cost_confirmation"
    CHECK_ACCEPTANCE = "check_acceptance"

    @property
    def label(self) -> str:
        return {
            WorkflowKind.PURCHASE_REQUISITION: "采购立项",
            WorkflowKind.PROCUREMENT_CONTRACT: "采购合同",
            WorkflowKind.PROCUREMENT_ORDER: "采购订单",
            WorkflowKind.COST_CONFIRMATION: "成本确认",
            WorkflowKind.CHECK_ACCEPTANCE: "采购验收",
        }[self]


class WorkItemCategory(StrEnum):
    FOLLOW_UP = "follow_up"
    APPROVED = "approved"
    OTHER = "other"


class WorkItemRelation(StrEnum):
    APPLICANT = "applicant"
    SUBMITTER = "submitter"
    PROJECT_MANAGER = "project_manager"
    PROCUREMENT_MANAGER = "procurement_manager"
    APPROVER = "approver"


class WorkItemScopeReason(StrEnum):
    MY_PROJECT = "my_project"
    SUBMITTED_BY_ME = "submitted_by_me"


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
    relations: tuple[WorkItemRelation, ...] = ()


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
    relations: tuple[WorkItemRelation, ...] = ()
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


@dataclass(frozen=True, slots=True)
class StreamSyncSummary:
    workflow: WorkflowKind
    run_id: str
    status: str
    source_total_count: int | None = None
    observed_count: int = 0
    actionable_count: int = 0
    snapshot_count: int = 0
    history_rows_inserted: int = 0
    event_count: int = 0
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class SyncBatchResult:
    run_id: str
    status: str
    dry_run: bool
    started_at: str
    observed_at: str
    finished_at: str
    source_total_count: int
    observed_count: int
    actionable_count: int
    snapshot_count: int
    history_rows_inserted: int
    streams: tuple[StreamSyncSummary, ...]
    events: tuple[ChangeEvent, ...] = ()
    work_items: tuple[WorkItem, ...] = ()
    database_path: str | None = None
