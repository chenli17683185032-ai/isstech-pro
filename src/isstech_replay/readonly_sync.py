"""Independent, failure-isolated synchronization for read-only business lists."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from uuid import uuid4

from .client import IsstechClient
from .models.bizcase import BizCaseListResult, BizCaseRecord
from .models.daily_expense import DailyExpenseListResult, DailyExpenseRecord
from .models.fee_application import FeeApplicationListResult, FeeApplicationRecord
from .models.payment import PaymentListResult, PaymentRecord
from .models.readonly_modules import (
    ReadonlyModuleKind,
    ReadonlySnapshot,
    ReadonlyStreamSummary,
    ReadonlySyncBatchResult,
    ReadonlySyncResult,
)
from .models.travel_application import (
    TravelApplicationListResult,
    TravelApplicationRecord,
)
from .models.work_items import (
    WorkItemRelation,
    WorkItemScopeReason,
)
from .parsers.portal import display_name_matches
from .storage import WorkflowStorage
from .sync import safe_error_message, utc_iso
from .work_items import personal_scope_reasons, personal_work_item_scope


READONLY_MODULES = (
    ReadonlyModuleKind.PAYMENT,
    ReadonlyModuleKind.BIZCASE,
    ReadonlyModuleKind.TRAVEL_APPLICATION,
    ReadonlyModuleKind.DAILY_EXPENSE,
    ReadonlyModuleKind.TRAVEL_REIMBURSEMENT,
    ReadonlyModuleKind.TRAVEL_SUBSIDY,
)


def _personal_project_numbers(storage: WorkflowStorage | None) -> tuple[str, ...]:
    if storage is None:
        return ()
    scoped = personal_work_item_scope(storage.current_snapshots())
    return tuple(
        sorted(
            {
                record.snapshot.project_no.strip()
                for record in scoped
                if WorkItemScopeReason.MY_PROJECT in record.scope_reasons
                and record.snapshot.project_no.strip()
            }
        )
    )


def _canonical_payload(payload: dict[str, object]) -> tuple[str, str]:
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return payload_json, hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _payment_payload(
    record: PaymentRecord,
    *,
    source_url: str,
    scope_reasons: tuple[WorkItemScopeReason, ...],
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "module": ReadonlyModuleKind.PAYMENT.value,
        "id": record.id,
        "payment_no": record.payment_no,
        "payment_type": record.payment_type,
        "applicant": record.applicant,
        "project_no": record.project_no,
        "project_name": record.project_name,
        "cost_center": record.cost_center,
        "payee_company": record.payee_company,
        "payer_company": record.payer_company,
        "amount": record.amount,
        "currency": record.currency,
        "status": record.status,
        "scope_reasons": [reason.value for reason in scope_reasons],
        "fields": record.field_dict(),
        "source_url": source_url,
    }


def _bizcase_payload(
    record: BizCaseRecord,
    *,
    source_url: str,
    scope_reasons: tuple[WorkItemScopeReason, ...],
    application_view_visible: bool,
) -> dict[str, object]:
    return {
        "schema_version": 4,
        "module": ReadonlyModuleKind.BIZCASE.value,
        "id": record.id,
        "ordinal": record.ordinal,
        "version_no": record.version_no,
        "bizcase_no": record.bizcase_no,
        "client_name": record.client_name,
        "profit_center_group": record.profit_center_group,
        "profit_center": record.profit_center,
        "project_no": record.project_no,
        "project_name": record.project_name,
        "revenue_recognition_type": record.revenue_recognition_type,
        "current_approver": record.current_approver,
        "scope_reasons": [reason.value for reason in scope_reasons],
        "application_view_visible": application_view_visible,
        "fields": record.field_dict(),
        "source_url": source_url,
    }


def _travel_application_payload(
    record: TravelApplicationRecord,
    *,
    source_url: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "module": ReadonlyModuleKind.TRAVEL_APPLICATION.value,
        "id": record.id,
        "ordinal": record.ordinal,
        "application_no": record.application_no,
        "project_name": record.project_name,
        "applicant": record.applicant,
        "application_date": record.application_date,
        "status": record.status,
        "amount": record.amount,
        "current_approver": record.current_approver,
        "scope_reasons": [WorkItemScopeReason.SUBMITTED_BY_ME.value],
        "fields": record.field_dict(),
        "source_url": source_url,
    }


def _daily_expense_payload(
    record: DailyExpenseRecord,
    *,
    source_url: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "module": ReadonlyModuleKind.DAILY_EXPENSE.value,
        "id": record.id,
        "ordinal": record.ordinal,
        "application_no": record.application_no,
        "project_name": record.project_name,
        "applicant": record.applicant,
        "application_date": record.application_date,
        "status": record.status,
        "amount": record.amount,
        "current_approver": record.current_approver,
        "scope_reasons": [WorkItemScopeReason.SUBMITTED_BY_ME.value],
        "fields": record.field_dict(),
        "source_url": source_url,
    }


def _fee_application_payload(
    record: FeeApplicationRecord,
    *,
    module: ReadonlyModuleKind,
    source_url: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "module": module.value,
        "id": record.id,
        "ordinal": record.ordinal,
        "application_no": record.application_no,
        "project_name": record.project_name,
        "applicant": record.applicant,
        "application_date": record.application_date,
        "status": record.status,
        "amount": record.amount,
        "current_approver": record.current_approver,
        "scope_reasons": [WorkItemScopeReason.SUBMITTED_BY_ME.value],
        "fields": record.field_dict(),
        "source_url": source_url,
    }


def readonly_snapshots(
    module: ReadonlyModuleKind,
    result: (
        PaymentListResult
        | BizCaseListResult
        | TravelApplicationListResult
        | DailyExpenseListResult
        | FeeApplicationListResult
    ),
    *,
    observed_at: str,
    display_name: str = "",
    project_numbers: tuple[str, ...] = (),
) -> tuple[ReadonlySnapshot, ...]:
    payloads: list[tuple[str, dict[str, object]]] = []
    if module is ReadonlyModuleKind.PAYMENT:
        if not isinstance(result, PaymentListResult):
            raise TypeError("Payment sync requires PaymentListResult")
        if not display_name.strip():
            raise ValueError("Payment personal scope requires a display name")
        for record in result.items:
            relations = (
                (WorkItemRelation.APPLICANT,)
                if display_name_matches(record.applicant, display_name)
                else ()
            )
            scope_reasons = personal_scope_reasons(
                project_no=record.project_no,
                relations=relations,
                my_project_numbers=project_numbers,
            )
            if not scope_reasons:
                raise RuntimeError("Payment result contains an unscoped record")
            payloads.append(
                (
                    record.id,
                    _payment_payload(
                        record,
                        source_url=result.source_url,
                        scope_reasons=scope_reasons,
                    ),
                )
            )
    elif module is ReadonlyModuleKind.BIZCASE:
        if not isinstance(result, BizCaseListResult):
            raise TypeError("BizCase sync requires BizCaseListResult")
        evidence_ids = result.application_visible_ids
        if len(set(evidence_ids)) != len(evidence_ids):
            raise RuntimeError("BizCase contains duplicate application visibility identity")
        record_ids = {record.id for record in result.items}
        if not set(evidence_ids).issubset(record_ids):
            raise RuntimeError("BizCase contains an unknown application visibility identity")
        evidence_id_set = set(evidence_ids)
        for record in result.items:
            scope_reasons = personal_scope_reasons(
                project_no=record.project_no,
                relations=(),
                my_project_numbers=project_numbers,
            )
            payloads.append(
                (
                    record.id,
                    _bizcase_payload(
                        record,
                        source_url=result.source_url,
                        scope_reasons=scope_reasons,
                        application_view_visible=record.id in evidence_id_set,
                    ),
                )
            )
    elif module is ReadonlyModuleKind.TRAVEL_APPLICATION:
        if not isinstance(result, TravelApplicationListResult):
            raise TypeError(
                "Travel application sync requires TravelApplicationListResult"
            )
        if not display_name.strip():
            raise ValueError("Travel application personal scope requires a display name")
        for record in result.items:
            if not display_name_matches(record.applicant, display_name):
                raise RuntimeError(
                    "Travel application result contains an unscoped record"
                )
            payloads.append(
                (
                    record.id,
                    _travel_application_payload(
                        record,
                        source_url=result.source_url,
                    ),
                )
            )
    elif module is ReadonlyModuleKind.DAILY_EXPENSE:
        if not isinstance(result, DailyExpenseListResult):
            raise TypeError("Daily expense sync requires DailyExpenseListResult")
        if not display_name.strip():
            raise ValueError("Daily expense personal scope requires a display name")
        for record in result.items:
            if not display_name_matches(record.applicant, display_name):
                raise RuntimeError("Daily expense result contains an unscoped record")
            payloads.append(
                (
                    record.id,
                    _daily_expense_payload(
                        record,
                        source_url=result.source_url,
                    ),
                )
            )
    else:
        if module not in {
            ReadonlyModuleKind.TRAVEL_REIMBURSEMENT,
            ReadonlyModuleKind.TRAVEL_SUBSIDY,
        } or not isinstance(result, FeeApplicationListResult):
            raise TypeError("Fee application sync requires FeeApplicationListResult")
        if not display_name.strip():
            raise ValueError("Fee application personal scope requires a display name")
        for record in result.items:
            if not display_name_matches(record.applicant, display_name):
                raise RuntimeError("Fee application result contains an unscoped record")
            payloads.append(
                (
                    record.id,
                    _fee_application_payload(
                        record,
                        module=module,
                        source_url=result.source_url,
                    ),
                )
            )

    snapshots = []
    for external_id, payload in payloads:
        payload_json, payload_hash = _canonical_payload(payload)
        snapshots.append(
            ReadonlySnapshot(
                module=module,
                external_id=external_id,
                observed_at=observed_at,
                payload_json=payload_json,
                payload_hash=payload_hash,
            )
        )
    return tuple(snapshots)


def sync_readonly_module(
    client: IsstechClient,
    module: ReadonlyModuleKind,
    *,
    storage: WorkflowStorage | None,
    max_pages: int = 20,
    dry_run: bool = False,
    observed_at: datetime | None = None,
    started_at: datetime | None = None,
    run_id: str | None = None,
    display_name: str | None = None,
    project_numbers: tuple[str, ...] = (),
) -> ReadonlySyncResult:
    if module not in READONLY_MODULES:
        raise ValueError(f"unsupported readonly module: {module}")
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if not dry_run and storage is None:
        raise ValueError("storage is required unless dry_run is enabled")
    actual_run_id = run_id or uuid4().hex
    started_text = utc_iso(started_at or datetime.now(UTC))
    run_started = False
    if not dry_run:
        assert storage is not None
        storage.start_readonly_run(
            run_id=actual_run_id,
            module=module,
            started_at=started_text,
            max_pages=max_pages,
        )
        run_started = True

    try:
        result: (
            PaymentListResult
            | BizCaseListResult
            | TravelApplicationListResult
            | DailyExpenseListResult
            | FeeApplicationListResult
        )
        identity = ""
        if module in {
            ReadonlyModuleKind.PAYMENT,
            ReadonlyModuleKind.TRAVEL_APPLICATION,
            ReadonlyModuleKind.DAILY_EXPENSE,
            ReadonlyModuleKind.TRAVEL_REIMBURSEMENT,
            ReadonlyModuleKind.TRAVEL_SUBSIDY,
        }:
            identity = (display_name or client.get_portal_display_name()).strip()
        if module is ReadonlyModuleKind.PAYMENT:
            result = client.list_personal_payment_records(
                display_name=identity,
                project_numbers=project_numbers,
                max_pages=max_pages,
            )
        elif module is ReadonlyModuleKind.BIZCASE:
            result = client.list_bizcases_with_application_visibility(
                max_pages=max_pages
            )
        elif module is ReadonlyModuleKind.TRAVEL_APPLICATION:
            result = client.list_personal_travel_applications(
                display_name=identity,
                max_pages=max_pages,
            )
        elif module is ReadonlyModuleKind.DAILY_EXPENSE:
            result = client.list_personal_daily_expenses(
                display_name=identity,
                max_pages=max_pages,
            )
        elif module is ReadonlyModuleKind.TRAVEL_REIMBURSEMENT:
            result = client.list_personal_travel_reimbursements(
                display_name=identity,
                max_pages=max_pages,
            )
        else:
            result = client.list_personal_travel_subsidies(
                display_name=identity,
                max_pages=max_pages,
            )
        observed_text = utc_iso(observed_at or datetime.now(UTC))
        snapshots = readonly_snapshots(
            module,
            result,
            observed_at=observed_text,
            display_name=identity,
            project_numbers=project_numbers,
        )
        if result.total_count != len(snapshots):
            raise RuntimeError(f"{module.value} normalization lost records")
        finished_text = utc_iso(datetime.now(UTC))
        if dry_run:
            return ReadonlySyncResult(
                run_id=actual_run_id,
                module=module,
                status="dry_run",
                dry_run=True,
                started_at=started_text,
                observed_at=observed_text,
                finished_at=finished_text,
                source_total_count=result.total_count,
                observed_count=len(snapshots),
                snapshot_count=len(snapshots),
                history_rows_inserted=0,
                changed_count=0,
            )

        assert storage is not None
        applied = storage.complete_readonly_run(
            run_id=actual_run_id,
            observed_at=observed_text,
            finished_at=finished_text,
            source_total_count=result.total_count,
            snapshots=snapshots,
        )
        run_started = False
        return ReadonlySyncResult(
            run_id=actual_run_id,
            module=module,
            status="succeeded",
            dry_run=False,
            started_at=started_text,
            observed_at=observed_text,
            finished_at=finished_text,
            source_total_count=result.total_count,
            observed_count=len(snapshots),
            snapshot_count=len(snapshots),
            history_rows_inserted=applied.history_rows_inserted,
            changed_count=applied.changed_count,
            database_path=str(storage.path),
        )
    except Exception as error:
        if run_started:
            assert storage is not None
            try:
                storage.fail_readonly_run(
                    run_id=actual_run_id,
                    finished_at=utc_iso(datetime.now(UTC)),
                    error_type=type(error).__name__,
                    error_message=safe_error_message(error),
                )
            except Exception as record_error:
                raise RuntimeError(
                    "readonly sync failed and its failure record could not be persisted"
                ) from record_error
        raise


def _stream_summary(result: ReadonlySyncResult) -> ReadonlyStreamSummary:
    return ReadonlyStreamSummary(
        module=result.module,
        run_id=result.run_id,
        status=result.status,
        source_total_count=result.source_total_count,
        observed_count=result.observed_count,
        snapshot_count=result.snapshot_count,
        history_rows_inserted=result.history_rows_inserted,
        changed_count=result.changed_count,
    )


def sync_readonly_modules(
    client: IsstechClient,
    *,
    storage: WorkflowStorage | None,
    max_pages: int = 20,
    dry_run: bool = False,
    modules: tuple[ReadonlyModuleKind, ...] = READONLY_MODULES,
    observed_at: datetime | None = None,
    started_at: datetime | None = None,
    run_id: str | None = None,
    scope_storage: WorkflowStorage | None = None,
) -> ReadonlySyncBatchResult:
    if not modules or len(set(modules)) != len(modules):
        raise ValueError("readonly modules must be non-empty and unique")
    if any(module not in READONLY_MODULES for module in modules):
        raise ValueError("readonly modules contain an unsupported module")
    if not dry_run and storage is None:
        raise ValueError("storage is required unless dry_run is enabled")

    batch_id = run_id or uuid4().hex
    batch_started = started_at or datetime.now(UTC)
    measurement_time = observed_at or datetime.now(UTC)
    summaries: list[ReadonlyStreamSummary] = []
    results: list[ReadonlySyncResult] = []
    project_numbers = _personal_project_numbers(scope_storage or storage)
    for module in modules:
        stream_run_id = f"{batch_id}-{module.value}"
        try:
            result = sync_readonly_module(
                client,
                module,
                storage=storage,
                max_pages=max_pages,
                dry_run=dry_run,
                observed_at=measurement_time,
                started_at=batch_started,
                run_id=stream_run_id,
                project_numbers=project_numbers,
            )
        except Exception as error:
            summaries.append(
                ReadonlyStreamSummary(
                    module=module,
                    run_id=stream_run_id,
                    status="failed",
                    error_type=type(error).__name__,
                    error_message=safe_error_message(error),
                )
            )
            continue
        results.append(result)
        summaries.append(_stream_summary(result))

    failures = sum(summary.status == "failed" for summary in summaries)
    status = (
        "dry_run"
        if dry_run and failures == 0
        else (
            "succeeded"
            if failures == 0
            else ("failed" if failures == len(summaries) else "partial")
        )
    )
    return ReadonlySyncBatchResult(
        run_id=batch_id,
        status=status,
        dry_run=dry_run,
        started_at=utc_iso(batch_started),
        observed_at=utc_iso(measurement_time),
        finished_at=utc_iso(datetime.now(UTC)),
        source_total_count=sum(result.source_total_count for result in results),
        observed_count=sum(result.observed_count for result in results),
        snapshot_count=sum(result.snapshot_count for result in results),
        history_rows_inserted=sum(result.history_rows_inserted for result in results),
        changed_count=sum(result.changed_count for result in results),
        streams=tuple(summaries),
        database_path=(str(storage.path) if storage is not None and not dry_run else None),
    )
