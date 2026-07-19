"""Account-scoped daily briefing records for the local follow-up assistant."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .work_items import WorkItemScopeReason


class AssistantBriefSource(StrEnum):
    MODEL = "model"
    FALLBACK = "fallback"


class WaitingBasis(StrEnum):
    SUBMISSION_DATE_ESTIMATE = "submission_date_estimate"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AssistantCandidate:
    item_key: str
    category: str
    reference: str
    title: str
    project: str
    status: str
    current_approver: str
    submitted_at: str
    waiting_days: int | None
    waiting_basis: WaitingBasis
    destination: str
    target: str | None
    scope_reasons: tuple[WorkItemScopeReason, ...] = ()


@dataclass(frozen=True, slots=True)
class AssistantBriefItem:
    item_key: str
    category: str
    reference: str
    title: str
    project: str
    status: str
    current_approver: str
    waiting_days: int | None
    waiting_basis: WaitingBasis
    destination: str
    target: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class AssistantPreference:
    preference_id: int
    created_at: str
    text: str


@dataclass(frozen=True, slots=True)
class AssistantBrief:
    brief_id: str
    business_date: str
    snapshot_hash: str
    preference_version: int
    generated_at: str
    source: AssistantBriefSource
    provider: str
    model: str
    provider_configured: bool
    fallback_code: str | None
    summary: str
    items: tuple[AssistantBriefItem, ...]
    candidate_count: int
