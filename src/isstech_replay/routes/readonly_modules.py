"""Cached Payment and BizCase lists with explicit manual synchronization."""

from __future__ import annotations

from typing import Annotated
import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from isstech_replay.account_scope import account_database_path
from isstech_replay.errors import local_storage_error, upstream_error
from isstech_replay.models.readonly_modules import ReadonlyModuleKind
from isstech_replay.models.work_items import WorkItemScopeReason
from isstech_replay.readonly_sync import sync_readonly_modules
from isstech_replay.routes.deps import get_session
from isstech_replay.session_store import SessionRecord
from isstech_replay.storage import WorkflowStorage


router = APIRouter(tags=["readonly-modules"])


class PaymentRecordOut(BaseModel):
    id: str
    payment_no: str
    payment_type: str = ""
    applicant: str = ""
    project_no: str = ""
    project_name: str = ""
    cost_center: str = ""
    payee_company: str = ""
    payer_company: str = ""
    amount: str = ""
    currency: str = ""
    status: str = ""
    scope_reasons: list[WorkItemScopeReason] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)
    source_url: str = ""


class BizCaseRecordOut(BaseModel):
    id: str
    ordinal: int
    version_no: str
    bizcase_no: str = ""
    client_name: str = ""
    profit_center_group: str = ""
    profit_center: str = ""
    project_no: str = ""
    project_name: str = ""
    revenue_recognition_type: str = ""
    current_approver: str = ""
    scope_reasons: list[WorkItemScopeReason] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)
    source_url: str = ""


class TravelApplicationRecordOut(BaseModel):
    id: str
    ordinal: int
    application_no: str
    project_name: str = ""
    applicant: str = ""
    application_date: str = ""
    status: str = ""
    amount: str = ""
    current_approver: str = ""
    scope_reasons: list[WorkItemScopeReason] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)
    source_url: str = ""


class DailyExpenseRecordOut(BaseModel):
    id: str
    ordinal: int
    application_no: str
    project_name: str = ""
    applicant: str = ""
    application_date: str = ""
    status: str = ""
    amount: str = ""
    current_approver: str = ""
    scope_reasons: list[WorkItemScopeReason] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)
    source_url: str = ""


class FeeApplicationRecordOut(BaseModel):
    id: str
    ordinal: int
    application_no: str
    project_name: str = ""
    applicant: str = ""
    application_date: str = ""
    status: str = ""
    amount: str = ""
    current_approver: str = ""
    scope_reasons: list[WorkItemScopeReason] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)
    source_url: str = ""


class PaymentListOut(BaseModel):
    module: str = ReadonlyModuleKind.PAYMENT.value
    module_label: str = ReadonlyModuleKind.PAYMENT.label
    source: str = "sqlite_current"
    ownership_scope: str = "personal_submissions_projects_and_management"
    synced_at: str | None = None
    source_total_count: int = 0
    total_count: int = 0
    my_project_count: int = 0
    submitted_by_me_count: int = 0
    managed_by_me_count: int = 0
    items: list[PaymentRecordOut] = Field(default_factory=list)


class BizCaseListOut(BaseModel):
    module: str = ReadonlyModuleKind.BIZCASE.value
    module_label: str = ReadonlyModuleKind.BIZCASE.label
    source: str = "sqlite_current"
    ownership_scope: str = "personal_submissions_projects_and_management"
    synced_at: str | None = None
    source_total_count: int = 0
    total_count: int = 0
    my_project_count: int = 0
    submitted_by_me_count: int = 0
    managed_by_me_count: int = 0
    items: list[BizCaseRecordOut] = Field(default_factory=list)


class TravelApplicationListOut(BaseModel):
    module: str = ReadonlyModuleKind.TRAVEL_APPLICATION.value
    module_label: str = ReadonlyModuleKind.TRAVEL_APPLICATION.label
    source: str = "sqlite_current"
    ownership_scope: str = "personal_submissions_projects_and_management"
    synced_at: str | None = None
    source_total_count: int = 0
    total_count: int = 0
    my_project_count: int = 0
    submitted_by_me_count: int = 0
    managed_by_me_count: int = 0
    items: list[TravelApplicationRecordOut] = Field(default_factory=list)


class DailyExpenseListOut(BaseModel):
    module: str = ReadonlyModuleKind.DAILY_EXPENSE.value
    module_label: str = ReadonlyModuleKind.DAILY_EXPENSE.label
    source: str = "sqlite_current"
    ownership_scope: str = "personal_submissions_projects_and_management"
    synced_at: str | None = None
    source_total_count: int = 0
    total_count: int = 0
    my_project_count: int = 0
    submitted_by_me_count: int = 0
    managed_by_me_count: int = 0
    items: list[DailyExpenseRecordOut] = Field(default_factory=list)


class TravelReimbursementListOut(BaseModel):
    module: str = ReadonlyModuleKind.TRAVEL_REIMBURSEMENT.value
    module_label: str = ReadonlyModuleKind.TRAVEL_REIMBURSEMENT.label
    source: str = "sqlite_current"
    ownership_scope: str = "personal_submissions_projects_and_management"
    synced_at: str | None = None
    source_total_count: int = 0
    total_count: int = 0
    my_project_count: int = 0
    submitted_by_me_count: int = 0
    managed_by_me_count: int = 0
    items: list[FeeApplicationRecordOut] = Field(default_factory=list)


class TravelSubsidyListOut(BaseModel):
    module: str = ReadonlyModuleKind.TRAVEL_SUBSIDY.value
    module_label: str = ReadonlyModuleKind.TRAVEL_SUBSIDY.label
    source: str = "sqlite_current"
    ownership_scope: str = "personal_submissions_projects_and_management"
    synced_at: str | None = None
    source_total_count: int = 0
    total_count: int = 0
    my_project_count: int = 0
    submitted_by_me_count: int = 0
    managed_by_me_count: int = 0
    items: list[FeeApplicationRecordOut] = Field(default_factory=list)


class ReadonlySyncStreamOut(BaseModel):
    module: str
    module_label: str
    run_id: str
    status: str
    source_total_count: int
    observed_count: int
    snapshot_count: int
    history_rows_inserted: int
    changed_count: int
    error_type: str | None = None
    error_message: str | None = None


class ReadonlySyncOut(BaseModel):
    run_id: str
    status: str
    dry_run: bool
    started_at: str
    observed_at: str
    finished_at: str
    source_total_count: int
    observed_count: int
    snapshot_count: int
    history_rows_inserted: int
    changed_count: int
    database_path: str | None = None
    streams: list[ReadonlySyncStreamOut] = Field(default_factory=list)


class ReadonlyRunOut(BaseModel):
    run_id: str
    module: str
    status: str
    started_at: str
    observed_at: str | None = None
    finished_at: str | None = None
    observed_count: int
    changed_count: int
    error_type: str | None = None
    error_message: str | None = None


def _storage(session: SessionRecord) -> WorkflowStorage:
    return WorkflowStorage(account_database_path(session.username))


def _current_payloads(
    storage: WorkflowStorage,
    module: ReadonlyModuleKind,
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    payloads = []
    for snapshot in storage.current_readonly_snapshots(module):
        try:
            payload = json.loads(snapshot.payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid cached {module.value} payload") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"invalid cached {module.value} payload")
        payloads.append(payload)
    return payloads, storage.latest_readonly_successful_run(module)


def _payload_scope_reasons(
    payload: dict[str, object],
) -> tuple[WorkItemScopeReason, ...]:
    raw_reasons = payload.get("scope_reasons")
    if raw_reasons is None:
        return ()
    if not isinstance(raw_reasons, list) or any(
        not isinstance(value, str) for value in raw_reasons
    ):
        raise ValueError("invalid cached personal scope reasons")
    try:
        found = {WorkItemScopeReason(value) for value in raw_reasons}
    except ValueError as exc:
        raise ValueError("unknown cached personal scope reason") from exc
    return tuple(reason for reason in WorkItemScopeReason if reason in found)


def _personal_payloads(
    payloads: list[dict[str, object]],
    *,
    asserted_scope_reasons: dict[
        str, tuple[WorkItemScopeReason, ...]
    ] | None = None,
) -> tuple[list[dict[str, object]], dict[WorkItemScopeReason, int]]:
    personal = []
    counts = {reason: 0 for reason in WorkItemScopeReason}
    assertions = asserted_scope_reasons or {}
    for payload in payloads:
        external_id = payload.get("id")
        if not isinstance(external_id, str) or not external_id:
            raise ValueError("invalid cached readonly module identity")
        found = {*_payload_scope_reasons(payload), *assertions.get(external_id, ())}
        reasons = tuple(reason for reason in WorkItemScopeReason if reason in found)
        if not reasons:
            continue
        personal_payload = dict(payload)
        personal_payload["scope_reasons"] = [reason.value for reason in reasons]
        personal.append(personal_payload)
        for reason in reasons:
            counts[reason] += 1
    return personal, counts


@router.post("/readonly-modules/sync", response_model=ReadonlySyncOut)
def sync_modules(
    session: Annotated[SessionRecord, Depends(get_session)],
    max_pages: int = Query(default=20, ge=1, le=100),
    dry_run: bool = Query(default=False),
) -> ReadonlySyncOut:
    scope_storage = _storage(session)
    storage = None if dry_run else scope_storage
    try:
        result = sync_readonly_modules(
            session.client,
            storage=storage,
            max_pages=max_pages,
            dry_run=dry_run,
            scope_storage=scope_storage,
        )
    except PermissionError as exc:
        raise upstream_error(str(exc), details={"code_hint": "AUTH_EXPIRED"}) from exc
    except Exception as exc:
        raise upstream_error(f"readonly module sync failed: {type(exc).__name__}") from exc
    return ReadonlySyncOut(
        run_id=result.run_id,
        status=result.status,
        dry_run=result.dry_run,
        started_at=result.started_at,
        observed_at=result.observed_at,
        finished_at=result.finished_at,
        source_total_count=result.source_total_count,
        observed_count=result.observed_count,
        snapshot_count=result.snapshot_count,
        history_rows_inserted=result.history_rows_inserted,
        changed_count=result.changed_count,
        database_path=result.database_path,
        streams=[
            ReadonlySyncStreamOut(
                module=stream.module.value,
                module_label=stream.module.label,
                run_id=stream.run_id,
                status=stream.status,
                source_total_count=stream.source_total_count,
                observed_count=stream.observed_count,
                snapshot_count=stream.snapshot_count,
                history_rows_inserted=stream.history_rows_inserted,
                changed_count=stream.changed_count,
                error_type=stream.error_type,
                error_message=stream.error_message,
            )
            for stream in result.streams
        ],
    )


@router.get("/readonly-modules/payment", response_model=PaymentListOut)
def list_payment_records(
    session: Annotated[SessionRecord, Depends(get_session)],
) -> PaymentListOut:
    try:
        cached, latest = _current_payloads(_storage(session), ReadonlyModuleKind.PAYMENT)
        payloads, counts = _personal_payloads(cached)
        items = [PaymentRecordOut.model_validate(payload) for payload in payloads]
    except Exception as exc:
        raise local_storage_error(f"payment cache read failed: {type(exc).__name__}") from exc
    return PaymentListOut(
        synced_at=str(latest["observed_at"]) if latest and latest["observed_at"] else None,
        source_total_count=int(latest["source_total_count"] or 0) if latest else 0,
        total_count=len(items),
        my_project_count=counts[WorkItemScopeReason.MY_PROJECT],
        submitted_by_me_count=counts[WorkItemScopeReason.SUBMITTED_BY_ME],
        managed_by_me_count=counts[WorkItemScopeReason.MANAGED_BY_ME],
        items=items,
    )


@router.get("/readonly-modules/bizcases", response_model=BizCaseListOut)
def list_bizcases(
    session: Annotated[SessionRecord, Depends(get_session)],
) -> BizCaseListOut:
    try:
        storage = _storage(session)
        cached, latest = _current_payloads(storage, ReadonlyModuleKind.BIZCASE)
        payloads, counts = _personal_payloads(
            cached,
            asserted_scope_reasons=storage.readonly_scope_assertions(
                ReadonlyModuleKind.BIZCASE
            ),
        )
        items = sorted(
            (BizCaseRecordOut.model_validate(payload) for payload in payloads),
            key=lambda item: item.ordinal,
        )
    except Exception as exc:
        raise local_storage_error(f"BizCase cache read failed: {type(exc).__name__}") from exc
    return BizCaseListOut(
        synced_at=str(latest["observed_at"]) if latest and latest["observed_at"] else None,
        source_total_count=int(latest["source_total_count"] or 0) if latest else 0,
        total_count=len(items),
        my_project_count=counts[WorkItemScopeReason.MY_PROJECT],
        submitted_by_me_count=counts[WorkItemScopeReason.SUBMITTED_BY_ME],
        managed_by_me_count=counts[WorkItemScopeReason.MANAGED_BY_ME],
        items=items,
    )


@router.get(
    "/readonly-modules/travel-applications",
    response_model=TravelApplicationListOut,
)
def list_travel_applications(
    session: Annotated[SessionRecord, Depends(get_session)],
) -> TravelApplicationListOut:
    try:
        cached, latest = _current_payloads(
            _storage(session),
            ReadonlyModuleKind.TRAVEL_APPLICATION,
        )
        payloads, counts = _personal_payloads(cached)
        items = sorted(
            (TravelApplicationRecordOut.model_validate(payload) for payload in payloads),
            key=lambda item: (item.application_date, item.application_no),
            reverse=True,
        )
    except Exception as exc:
        raise local_storage_error(
            f"travel application cache read failed: {type(exc).__name__}"
        ) from exc
    return TravelApplicationListOut(
        synced_at=str(latest["observed_at"]) if latest and latest["observed_at"] else None,
        source_total_count=int(latest["source_total_count"] or 0) if latest else 0,
        total_count=len(items),
        my_project_count=counts[WorkItemScopeReason.MY_PROJECT],
        submitted_by_me_count=counts[WorkItemScopeReason.SUBMITTED_BY_ME],
        managed_by_me_count=counts[WorkItemScopeReason.MANAGED_BY_ME],
        items=items,
    )


@router.get(
    "/readonly-modules/daily-expenses",
    response_model=DailyExpenseListOut,
)
def list_daily_expenses(
    session: Annotated[SessionRecord, Depends(get_session)],
) -> DailyExpenseListOut:
    try:
        cached, latest = _current_payloads(
            _storage(session),
            ReadonlyModuleKind.DAILY_EXPENSE,
        )
        payloads, counts = _personal_payloads(cached)
        items = sorted(
            (DailyExpenseRecordOut.model_validate(payload) for payload in payloads),
            key=lambda item: (item.application_date, item.application_no),
            reverse=True,
        )
    except Exception as exc:
        raise local_storage_error(
            f"daily expense cache read failed: {type(exc).__name__}"
        ) from exc
    return DailyExpenseListOut(
        synced_at=str(latest["observed_at"]) if latest and latest["observed_at"] else None,
        source_total_count=int(latest["source_total_count"] or 0) if latest else 0,
        total_count=len(items),
        my_project_count=counts[WorkItemScopeReason.MY_PROJECT],
        submitted_by_me_count=counts[WorkItemScopeReason.SUBMITTED_BY_ME],
        managed_by_me_count=counts[WorkItemScopeReason.MANAGED_BY_ME],
        items=items,
    )


def _fee_application_list(
    session: SessionRecord,
    module: ReadonlyModuleKind,
) -> tuple[list[FeeApplicationRecordOut], dict[WorkItemScopeReason, int], dict[str, object] | None]:
    cached, latest = _current_payloads(_storage(session), module)
    payloads, counts = _personal_payloads(cached)
    items = sorted(
        (FeeApplicationRecordOut.model_validate(payload) for payload in payloads),
        key=lambda item: (item.application_date, item.application_no),
        reverse=True,
    )
    return items, counts, latest


@router.get(
    "/readonly-modules/travel-reimbursements",
    response_model=TravelReimbursementListOut,
)
def list_travel_reimbursements(
    session: Annotated[SessionRecord, Depends(get_session)],
) -> TravelReimbursementListOut:
    try:
        items, counts, latest = _fee_application_list(
            session,
            ReadonlyModuleKind.TRAVEL_REIMBURSEMENT,
        )
    except Exception as exc:
        raise local_storage_error(
            f"travel reimbursement cache read failed: {type(exc).__name__}"
        ) from exc
    return TravelReimbursementListOut(
        synced_at=str(latest["observed_at"]) if latest and latest["observed_at"] else None,
        source_total_count=int(latest["source_total_count"] or 0) if latest else 0,
        total_count=len(items),
        my_project_count=counts[WorkItemScopeReason.MY_PROJECT],
        submitted_by_me_count=counts[WorkItemScopeReason.SUBMITTED_BY_ME],
        managed_by_me_count=counts[WorkItemScopeReason.MANAGED_BY_ME],
        items=items,
    )


@router.get(
    "/readonly-modules/travel-subsidies",
    response_model=TravelSubsidyListOut,
)
def list_travel_subsidies(
    session: Annotated[SessionRecord, Depends(get_session)],
) -> TravelSubsidyListOut:
    try:
        items, counts, latest = _fee_application_list(
            session,
            ReadonlyModuleKind.TRAVEL_SUBSIDY,
        )
    except Exception as exc:
        raise local_storage_error(
            f"travel subsidy cache read failed: {type(exc).__name__}"
        ) from exc
    return TravelSubsidyListOut(
        synced_at=str(latest["observed_at"]) if latest and latest["observed_at"] else None,
        source_total_count=int(latest["source_total_count"] or 0) if latest else 0,
        total_count=len(items),
        my_project_count=counts[WorkItemScopeReason.MY_PROJECT],
        submitted_by_me_count=counts[WorkItemScopeReason.SUBMITTED_BY_ME],
        managed_by_me_count=counts[WorkItemScopeReason.MANAGED_BY_ME],
        items=items,
    )


@router.get("/readonly-modules/runs", response_model=list[ReadonlyRunOut])
def list_readonly_runs(
    session: Annotated[SessionRecord, Depends(get_session)],
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ReadonlyRunOut]:
    try:
        records = _storage(session).list_readonly_runs(limit=limit)
        return [ReadonlyRunOut.model_validate(record) for record in records]
    except Exception as exc:
        raise local_storage_error(
            f"readonly sync run list failed: {type(exc).__name__}"
        ) from exc
