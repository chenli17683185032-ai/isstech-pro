"""Bounded daily prioritization over already-cached personal workflow snapshots."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import re
from typing import TYPE_CHECKING
import unicodedata
from uuid import uuid4

import httpx

from .ai.briefing import BriefingProvider, BriefingProviderError, ModelBriefing
from .models.assistant import (
    AssistantBrief,
    AssistantBriefItem,
    AssistantBriefSource,
    AssistantCandidate,
    WaitingBasis,
)
from .models.readonly_modules import ReadonlyModuleKind
from .models.work_items import WorkItemScopeReason
from .work_items import personal_work_item_scope

if TYPE_CHECKING:
    from .storage import WorkflowStorage


APPROVED_STATUSES = frozenset({"审批通过", "已通过", "已完成"})
MAX_ASSISTANT_CANDIDATES = 100
MAX_ASSISTANT_ITEMS = 5
_HIGH_RISK_STATUS_RE = re.compile(r"拒绝|退回|失败|驳回|撤回")
_READONLY_TARGETS = {
    ReadonlyModuleKind.PAYMENT: "payment",
    ReadonlyModuleKind.BIZCASE: "bizcases",
    ReadonlyModuleKind.TRAVEL_APPLICATION: "travelApplications",
    ReadonlyModuleKind.DAILY_EXPENSE: "dailyExpenses",
    ReadonlyModuleKind.TRAVEL_REIMBURSEMENT: "travelReimbursements",
    ReadonlyModuleKind.TRAVEL_SUBSIDY: "travelSubsidies",
}


class AssistantDataError(RuntimeError):
    """Cached assistant input is malformed or outside its account contract."""


def collect_assistant_candidates(
    storage: WorkflowStorage,
    *,
    today: date,
) -> tuple[AssistantCandidate, ...]:
    candidates: list[AssistantCandidate] = []
    for scoped in personal_work_item_scope(storage.current_snapshots()):
        snapshot = scoped.snapshot
        status = snapshot.status.strip()
        if not status or status in APPROVED_STATUSES:
            continue
        candidates.append(
            AssistantCandidate(
                item_key=f"{snapshot.adapter.value}:{snapshot.external_id}",
                category=snapshot.adapter.label,
                reference=snapshot.reference_no or snapshot.external_id,
                title=snapshot.title or "未命名单据",
                project=snapshot.project_no,
                status=status,
                current_approver=snapshot.current_approver,
                submitted_at=snapshot.submitted_at,
                waiting_days=_waiting_days(
                    snapshot.submitted_at,
                    today=today,
                    stored=snapshot.waiting_days,
                ),
                waiting_basis=WaitingBasis.SUBMISSION_DATE_ESTIMATE,
                destination="work-items",
                target=None,
                scope_reasons=scoped.scope_reasons,
            )
        )

    for module in ReadonlyModuleKind:
        assertions = storage.readonly_scope_assertions(module)
        for snapshot in storage.current_readonly_snapshots(module):
            try:
                payload = json.loads(snapshot.payload_json)
            except json.JSONDecodeError as exc:
                raise AssistantDataError(
                    f"invalid cached assistant payload for {module.value}"
                ) from exc
            if not isinstance(payload, dict):
                raise AssistantDataError(
                    f"invalid cached assistant payload for {module.value}"
                )
            reasons = _readonly_scope_reasons(
                payload,
                asserted=assertions.get(snapshot.external_id, ()),
            )
            if not reasons:
                continue
            status = _payload_text(payload, "status")
            if not status or status in APPROVED_STATUSES:
                continue
            submitted_at = _payload_text(payload, "application_date")
            candidates.append(
                AssistantCandidate(
                    item_key=f"{module.value}:{snapshot.external_id}",
                    category=module.label,
                    reference=_first_payload_text(
                        payload,
                        "payment_no",
                        "application_no",
                        "bizcase_no",
                    )
                    or snapshot.external_id,
                    title=_first_payload_text(
                        payload,
                        "project_name",
                        "payee_company",
                        "client_name",
                    )
                    or "未命名单据",
                    project=_payload_text(payload, "project_no"),
                    status=status,
                    current_approver=_payload_text(payload, "current_approver"),
                    submitted_at=submitted_at,
                    waiting_days=_waiting_days(submitted_at, today=today),
                    waiting_basis=(
                        WaitingBasis.SUBMISSION_DATE_ESTIMATE
                        if submitted_at
                        else WaitingBasis.UNKNOWN
                    ),
                    destination="readonly-modules",
                    target=_READONLY_TARGETS[module],
                    scope_reasons=reasons,
                )
            )
    return tuple(sorted(candidates, key=lambda candidate: candidate.item_key))


def generate_assistant_brief(
    storage: WorkflowStorage,
    *,
    now: datetime | None = None,
    provider: BriefingProvider | None = None,
) -> AssistantBrief:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("assistant generation timestamp must be timezone-aware")
    today = current.astimezone().date()
    generated_at = current.astimezone(UTC).isoformat()
    candidates = collect_assistant_candidates(storage, today=today)
    preferences = storage.list_assistant_preferences(limit=50)
    preference_version = storage.assistant_preference_version()
    preference_texts = tuple(preference.text for preference in preferences)
    ranked = _rank_candidates(candidates, preference_texts)
    selected = ranked[:MAX_ASSISTANT_ITEMS]
    source = AssistantBriefSource.FALLBACK
    fallback_code: str | None = "provider_not_configured"
    summary = _fallback_summary(candidates, selected)
    reasons = {
        candidate.item_key: _fallback_reason(candidate, preference_texts)
        for candidate in selected
    }
    provider_name = ""
    provider_model = ""
    provider_configured = provider is not None

    if provider is not None and candidates:
        provider_name = provider.name
        provider_model = provider.model
        try:
            model_result = provider.prioritize(
                ranked[:MAX_ASSISTANT_CANDIDATES],
                preference_texts,
            )
            selected, reasons = _merge_model_priorities(
                model_result,
                ranked,
                preference_texts,
            )
            summary = model_result.summary
            source = AssistantBriefSource.MODEL
            fallback_code = None
        except Exception as exc:
            fallback_code = _fallback_code(exc)

    items = tuple(
        AssistantBriefItem(
            item_key=candidate.item_key,
            category=candidate.category,
            reference=candidate.reference,
            title=candidate.title,
            project=candidate.project,
            status=candidate.status,
            current_approver=candidate.current_approver,
            waiting_days=candidate.waiting_days,
            waiting_basis=candidate.waiting_basis,
            destination=candidate.destination,
            target=candidate.target,
            reason=reasons[candidate.item_key],
        )
        for candidate in selected
    )
    brief = AssistantBrief(
        brief_id=uuid4().hex,
        business_date=today.isoformat(),
        snapshot_hash=_snapshot_hash(candidates),
        preference_version=preference_version,
        generated_at=generated_at,
        source=source,
        provider=provider_name,
        model=provider_model,
        provider_configured=provider_configured,
        fallback_code=fallback_code,
        summary=summary,
        items=items,
        candidate_count=len(candidates),
    )
    retention_cutoff = (today - timedelta(days=90)).isoformat()
    return storage.save_assistant_brief(brief, retention_cutoff=retention_cutoff)


def _rank_candidates(
    candidates: tuple[AssistantCandidate, ...],
    preferences: tuple[str, ...],
) -> tuple[AssistantCandidate, ...]:
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                -_candidate_score(candidate, preferences),
                -(candidate.waiting_days if candidate.waiting_days is not None else -1),
                candidate.item_key,
            ),
        )[:MAX_ASSISTANT_CANDIDATES]
    )


def _candidate_score(
    candidate: AssistantCandidate,
    preferences: tuple[str, ...],
) -> int:
    score = min(candidate.waiting_days or 0, 365) * 10
    if _matches_preference(candidate, preferences):
        score += 10_000
    if _HIGH_RISK_STATUS_RE.search(candidate.status):
        score += 5_000
    if candidate.current_approver:
        score += 20
    return score


def _merge_model_priorities(
    result: ModelBriefing,
    ranked: tuple[AssistantCandidate, ...],
    preferences: tuple[str, ...],
) -> tuple[tuple[AssistantCandidate, ...], dict[str, str]]:
    by_key = {candidate.item_key: candidate for candidate in ranked}
    selected: list[AssistantCandidate] = []
    reasons: dict[str, str] = {}
    for priority in result.priorities:
        candidate = by_key.get(priority.item_key)
        if candidate is None or candidate in selected:
            continue
        selected.append(candidate)
        reasons[candidate.item_key] = priority.reason
        if len(selected) == MAX_ASSISTANT_ITEMS:
            break
    for candidate in ranked:
        if len(selected) >= MAX_ASSISTANT_ITEMS:
            break
        if candidate in selected:
            continue
        selected.append(candidate)
        reasons[candidate.item_key] = _fallback_reason(candidate, preferences)
    return tuple(selected), reasons


def _fallback_summary(
    candidates: tuple[AssistantCandidate, ...],
    selected: tuple[AssistantCandidate, ...],
) -> str:
    if not candidates:
        return "当前没有需要催办的未审批单据。"
    known_waits = [
        candidate.waiting_days
        for candidate in candidates
        if candidate.waiting_days is not None
    ]
    longest = max(known_waits) if known_waits else None
    if longest is None:
        return f"当前有 {len(candidates)} 条未审批，建议先处理前 {len(selected)} 条。"
    return (
        f"当前有 {len(candidates)} 条未审批，建议先处理前 {len(selected)} 条；"
        f"最长已等待 {longest} 天。"
    )


def _fallback_reason(
    candidate: AssistantCandidate,
    preferences: tuple[str, ...],
) -> str:
    if _matches_preference(candidate, preferences):
        return "符合你设置的优先级偏好"
    if _HIGH_RISK_STATUS_RE.search(candidate.status):
        return f"状态为{candidate.status}，建议优先处理"
    if candidate.waiting_days is not None and candidate.waiting_days >= 14:
        return f"按申请日期估算已等待 {candidate.waiting_days} 天"
    if candidate.waiting_days is not None:
        return f"按申请日期估算已等待 {candidate.waiting_days} 天"
    if candidate.current_approver:
        return f"当前审批人：{candidate.current_approver}"
    return "当前仍未审批"


def _matches_preference(
    candidate: AssistantCandidate,
    preferences: tuple[str, ...],
) -> bool:
    fields = (
        candidate.category,
        candidate.reference,
        candidate.title,
        candidate.project,
        candidate.current_approver,
    )
    normalized_fields = tuple(
        value for raw in fields if len(value := _normalize_text(raw)) >= 2
    )
    return any(
        field in normalized_preference
        for preference in preferences
        if (normalized_preference := _normalize_text(preference))
        for field in normalized_fields
    )


def _snapshot_hash(candidates: tuple[AssistantCandidate, ...]) -> str:
    payload = [
        {
            **asdict(candidate),
            "waiting_basis": candidate.waiting_basis.value,
            "scope_reasons": [reason.value for reason in candidate.scope_reasons],
        }
        for candidate in candidates
    ]
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _readonly_scope_reasons(
    payload: dict[str, object],
    *,
    asserted: tuple[WorkItemScopeReason, ...],
) -> tuple[WorkItemScopeReason, ...]:
    raw = payload.get("scope_reasons", [])
    if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
        raise AssistantDataError("cached readonly scope reasons are invalid")
    try:
        found = {WorkItemScopeReason(value) for value in raw}
    except ValueError as exc:
        raise AssistantDataError("cached readonly scope reason is unknown") from exc
    found.update(asserted)
    return tuple(reason for reason in WorkItemScopeReason if reason in found)


def _payload_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AssistantDataError(f"cached assistant field is not text: {key}")
    return value.strip()


def _first_payload_text(payload: dict[str, object], *keys: str) -> str:
    return next((value for key in keys if (value := _payload_text(payload, key))), "")


def _waiting_days(
    value: str,
    *,
    today: date,
    stored: int | None = None,
) -> int | None:
    parsed = _parse_date(value)
    measured = (today - parsed).days if parsed is not None and parsed <= today else None
    known = [days for days in (stored, measured) if days is not None and days >= 0]
    return max(known) if known else None


def _parse_date(value: str) -> date | None:
    normalized = value.strip()
    if not normalized:
        return None
    candidates = (normalized, normalized[:10])
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
        except ValueError:
            pass
        for pattern in ("%Y/%m/%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(candidate, pattern).date()
            except ValueError:
                continue
    return None


def _normalize_text(value: str) -> str:
    return "".join(
        unicodedata.normalize("NFKC", value).casefold().split()
    )


def _fallback_code(error: Exception) -> str:
    if isinstance(error, httpx.TimeoutException):
        return "provider_timeout"
    if isinstance(error, httpx.HTTPStatusError):
        return (
            "provider_rate_limited"
            if error.response.status_code == 429
            else "provider_http_error"
        )
    if isinstance(error, BriefingProviderError):
        return "provider_invalid_response"
    if isinstance(error, ValueError):
        return "provider_config_invalid"
    return "provider_unavailable"
