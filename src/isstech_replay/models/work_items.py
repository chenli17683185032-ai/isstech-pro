"""Normalized work items emitted by workflow-specific adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkflowKind(StrEnum):
    PURCHASE_REQUISITION = "purchase_requisition"


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
