"""One complete measurement-to-snapshot workflow for PurchaseRequisition."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
import hashlib
import json
import re
from typing import Any
from uuid import uuid4

from .client import IsstechClient
from .models.purchase import (
    PurchaseListQuery,
    PurchaseListResult,
    PurchaseRequisitionDetail,
    PurchaseRequisitionSummary,
    PurchaseView,
)
from .models.work_items import (
    SyncResult,
    WorkItemRelation,
    WorkflowKind,
    WorkflowSnapshot,
)
from .parsers.portal import display_name_matches
from .storage import WorkflowStorage
from .validation import require_path_segment
from .work_items import (
    is_purchase_active,
    purchase_follow_up_items,
    waiting_days_since,
)


_COOKIE_VALUE_RE = re.compile(
    r"(?i)(\.iPSA|emp_Password|password|authorization|cookie|ticket|token)"
    r"=([^;&\s]+)"
)
_DETAIL_SCAN_LIMIT = 500
_DETAIL_READ_ATTEMPTS = 2
_SUBMIT_ACTIONS = {"提交", "发起", "申请"}


class DetailScanIncompleteError(RuntimeError):
    """A complete account-relevance measurement could not be proven."""


@dataclass(frozen=True, slots=True)
class AccountPurchaseRecord:
    summary: PurchaseRequisitionSummary
    detail: PurchaseRequisitionDetail
    relations: tuple[WorkItemRelation, ...]


@dataclass(frozen=True, slots=True)
class AccountPurchaseMeasurement:
    result: PurchaseListResult
    records: tuple[AccountPurchaseRecord, ...]


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("sync timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def safe_error_message(error: BaseException) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ")
    return _COOKIE_VALUE_RE.sub(lambda match: f"{match.group(1)}=<redacted>", message)[:1000]


def _snapshot_payload(
    *,
    external_id: str,
    reference_no: str,
    project_no: str,
    title: str,
    applicant: str,
    submitted_at: str,
    status: str,
    current_node: str,
    current_approver: str,
    source_url: str,
    active: bool,
    actionable: bool,
    relations: tuple[WorkItemRelation, ...],
    detail: PurchaseRequisitionDetail,
) -> tuple[str, str]:
    payload = {
        "payload_version": 2,
        "actionable": actionable,
        "active": active,
        "adapter": WorkflowKind.PURCHASE_REQUISITION.value,
        "applicant": applicant,
        "current_approver": current_approver,
        "current_node": current_node,
        "external_id": external_id,
        "project_no": project_no,
        "reference_no": reference_no,
        "source_url": source_url,
        "status": status,
        "submitted_at": submitted_at,
        "title": title,
        "relations": [relation.value for relation in relations],
        "detail": {
            "fields": detail.fields,
            "html_title": detail.html_title,
            "approval_steps": [asdict(step) for step in detail.approval_steps],
        },
    }
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    return payload_json, payload_hash


def purchase_snapshots(
    measurement: AccountPurchaseMeasurement,
    *,
    base_url: str,
    observed_at: str,
    today: date,
) -> tuple[WorkflowSnapshot, ...]:
    snapshots: list[WorkflowSnapshot] = []
    for measured in measurement.records:
        record = measured.summary
        external_id = require_path_segment(record.id, "purchase requisition id")
        active = is_purchase_active(record.status)
        actionable = active and bool(record.next_approver)
        source_url = (
            f"{base_url.rstrip('/')}/WebTP/PurchaseRequisition/Detail/{external_id}"
        )
        # Status is the minimum observed node signal until Detail exposes a stable node name.
        current_node = record.status
        payload_json, payload_hash = _snapshot_payload(
            external_id=external_id,
            reference_no=record.requisition_no,
            project_no=record.project_no,
            title=record.project_name,
            applicant=record.creator_name,
            submitted_at=record.create_date,
            status=record.status,
            current_node=current_node,
            current_approver=record.next_approver,
            source_url=source_url,
            active=active,
            actionable=actionable,
            relations=measured.relations,
            detail=measured.detail,
        )
        snapshots.append(
            WorkflowSnapshot(
                adapter=WorkflowKind.PURCHASE_REQUISITION,
                external_id=external_id,
                observed_at=observed_at,
                reference_no=record.requisition_no,
                project_no=record.project_no,
                title=record.project_name,
                applicant=record.creator_name,
                submitted_at=record.create_date,
                status=record.status,
                current_node=current_node,
                current_approver=record.next_approver,
                waiting_days=waiting_days_since(record.create_date, today=today),
                source_url=source_url,
                active=active,
                actionable=actionable,
                relations=measured.relations,
                payload_json=payload_json,
                payload_hash=payload_hash,
            )
        )
    return tuple(snapshots)


def filter_account_purchase_requisitions(
    search_result: PurchaseListResult,
    *,
    display_name: str,
) -> PurchaseListResult:
    """Filter the global SearchIndex by the authenticated Portal identity."""
    owned = tuple(
        record
        for record in search_result.items
        if record.creator_name
        and display_name_matches(record.creator_name, display_name)
    )

    return PurchaseListResult(
        view=PurchaseView.SEARCH,
        items=owned,
        total_text=None,
        # Preserve the complete SearchIndex count so the persisted run can explain
        # how many globally visible candidates were reduced to this owned subset.
        total_count=search_result.total_count,
        page=1,
        page_size=search_result.page_size,
        source_url=search_result.source_url,
    )


def _purchase_relations(
    summary: PurchaseRequisitionSummary,
    detail: PurchaseRequisitionDetail,
    *,
    display_name: str,
) -> tuple[WorkItemRelation, ...]:
    relations: set[WorkItemRelation] = set()
    if display_name_matches(summary.creator_name, display_name):
        relations.add(WorkItemRelation.APPLICANT)
    if display_name_matches(
        detail.fields.get("PR_ProjectManagerName", ""),
        display_name,
    ):
        relations.add(WorkItemRelation.PROJECT_MANAGER)
    if display_name_matches(
        detail.fields.get("PR_ProcurementManagerName", ""),
        display_name,
    ):
        relations.add(WorkItemRelation.PROCUREMENT_MANAGER)
    for step in detail.approval_steps:
        if not display_name_matches(step.approver_name, display_name):
            continue
        action = step.action.strip()
        if action in _SUBMIT_ACTIONS:
            relations.add(WorkItemRelation.SUBMITTER)
        elif action:
            relations.add(WorkItemRelation.APPROVER)
    return tuple(relation for relation in WorkItemRelation if relation in relations)


def _read_detail_with_retry(
    client: IsstechClient,
    external_id: str,
    *,
    position: int,
    total: int,
) -> PurchaseRequisitionDetail:
    last_error: Exception | None = None
    for _ in range(_DETAIL_READ_ATTEMPTS):
        try:
            return client.get_purchase_requisition(external_id)
        except Exception as error:
            last_error = error
    raise DetailScanIncompleteError(
        f"detail scan failed at item {position}/{total} after "
        f"{_DETAIL_READ_ATTEMPTS} attempts"
    ) from last_error


def read_account_purchase_measurement(
    client: IsstechClient,
    *,
    max_pages: int,
) -> AccountPurchaseMeasurement:
    display_name = client.get_portal_display_name()
    search_result = client.list_all_purchase_requisitions(
        PurchaseListQuery(view=PurchaseView.SEARCH),
        max_pages=max_pages,
    )
    if len(search_result.items) > _DETAIL_SCAN_LIMIT:
        raise DetailScanIncompleteError(
            f"detail scan candidate count exceeds limit {_DETAIL_SCAN_LIMIT}"
        )
    records = []
    total = len(search_result.items)
    for position, summary in enumerate(search_result.items, start=1):
        detail = _read_detail_with_retry(
            client,
            summary.id,
            position=position,
            total=total,
        )
        relations = _purchase_relations(
            summary,
            detail,
            display_name=display_name,
        )
        if relations:
            records.append(
                AccountPurchaseRecord(
                    summary=summary,
                    detail=detail,
                    relations=relations,
                )
            )
    result = PurchaseListResult(
        view=PurchaseView.SEARCH,
        items=tuple(record.summary for record in records),
        total_text=None,
        total_count=search_result.total_count,
        page=1,
        page_size=search_result.page_size,
        source_url=search_result.source_url,
    )
    return AccountPurchaseMeasurement(result=result, records=tuple(records))


def read_account_purchase_requisitions(
    client: IsstechClient,
    *,
    max_pages: int,
) -> PurchaseListResult:
    return read_account_purchase_measurement(client, max_pages=max_pages).result


def sync_purchase_requisitions(
    client: IsstechClient,
    *,
    storage: WorkflowStorage | None,
    max_pages: int = 20,
    dry_run: bool = False,
    observed_at: datetime | None = None,
    started_at: datetime | None = None,
    today: date | None = None,
    run_id: str | None = None,
) -> SyncResult:
    """Fetch a complete list, normalize it, then atomically persist one measurement."""
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if not dry_run and storage is None:
        raise ValueError("storage is required unless dry_run is enabled")

    adapter = WorkflowKind.PURCHASE_REQUISITION
    actual_run_id = run_id or uuid4().hex
    started_text = utc_iso(started_at or datetime.now(UTC))
    run_started = False
    if not dry_run:
        assert storage is not None
        storage.start_run(
            run_id=actual_run_id,
            adapter=adapter,
            started_at=started_text,
            max_pages=max_pages,
        )
        run_started = True

    try:
        measurement = read_account_purchase_measurement(client, max_pages=max_pages)
        result = measurement.result
        observed_datetime = observed_at or datetime.now(UTC)
        observed_text = utc_iso(observed_datetime)
        effective_today = today or date.today()
        snapshots = purchase_snapshots(
            measurement,
            base_url=client.settings.base_url,
            observed_at=observed_text,
            today=effective_today,
        )
        work_items = purchase_follow_up_items(
            result,
            base_url=client.settings.base_url,
            today=effective_today,
            relations_by_id={
                record.summary.id: record.relations for record in measurement.records
            },
        )
        if len(snapshots) != len(result.items):
            raise RuntimeError("snapshot normalization lost records")
        finished_at = utc_iso(datetime.now(UTC))

        if dry_run:
            return SyncResult(
                run_id=actual_run_id,
                status="dry_run",
                dry_run=True,
                started_at=started_text,
                observed_at=observed_text,
                finished_at=finished_at,
                source_total_count=result.total_count,
                observed_count=len(result.items),
                actionable_count=len(work_items),
                snapshot_count=len(snapshots),
                history_rows_inserted=0,
                work_items=work_items,
            )

        assert storage is not None
        applied = storage.complete_run(
            run_id=actual_run_id,
            observed_at=observed_text,
            finished_at=finished_at,
            source_total_count=result.total_count,
            snapshots=snapshots,
            actionable_count=len(work_items),
        )
        run_started = False
        return SyncResult(
            run_id=actual_run_id,
            status="succeeded",
            dry_run=False,
            started_at=started_text,
            observed_at=observed_text,
            finished_at=finished_at,
            source_total_count=result.total_count,
            observed_count=len(result.items),
            actionable_count=len(work_items),
            snapshot_count=len(snapshots),
            history_rows_inserted=applied.history_rows_inserted,
            events=applied.events,
            work_items=work_items,
            database_path=str(storage.path),
        )
    except Exception as error:
        if run_started:
            assert storage is not None
            failed_at = utc_iso(datetime.now(UTC))
            try:
                storage.fail_run(
                    run_id=actual_run_id,
                    finished_at=failed_at,
                    error_type=type(error).__name__,
                    error_message=safe_error_message(error),
                )
            except Exception as record_error:
                raise RuntimeError(
                    "sync failed and the failure record could not be persisted"
                ) from record_error
        raise


def sync_result_dict(result: SyncResult) -> dict[str, Any]:
    """Return a JSON-serializable summary without snapshots or upstream secrets."""
    return asdict(result)
