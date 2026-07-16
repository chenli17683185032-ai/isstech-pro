"""Procurement streams keep fixed schemas and fail closed on incomplete pages."""

from __future__ import annotations

from datetime import UTC, date, datetime
from html import escape
from pathlib import Path

import httpx
import pytest

from isstech_replay.client import IsstechClient, PaginationIncompleteError
from isstech_replay.models.procurement import (
    PROCUREMENT_STREAM_BY_WORKFLOW,
    PROCUREMENT_STREAMS,
    ProcurementDocumentSummary,
    ProcurementListResult,
    ProcurementStreamSpec,
)
from isstech_replay.models.purchase import PurchaseApprovalStep, PurchaseRequisitionDetail
from isstech_replay.models.work_items import WorkItemRelation, WorkflowKind
from isstech_replay.parsers.procurement import (
    parse_procurement_detail,
    parse_procurement_list,
)
from isstech_replay.policy import RequestClass
from isstech_replay.storage import WorkflowStorage, cached_workflow_detail
from isstech_replay.sync import sync_procurement_workflows


OBSERVED_1 = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)
OBSERVED_2 = datetime(2026, 7, 16, 1, 0, tzinfo=UTC)


def _page(
    spec: ProcurementStreamSpec,
    ids: tuple[str, ...],
    *,
    total: int,
    status: str = "审批通过",
) -> str:
    headers = "".join(f"<th>{escape(header)}</th>" for header in spec.headers)
    rows = []
    for index, external_id in enumerate(ids, start=1):
        values = {
            spec.reference_field: f"REF-REDACTED-{external_id}",
            spec.title_field: f"REDACTED TITLE {index}",
            spec.project_no_field: f"PROJECT-REDACTED-{index}",
            spec.applicant_field: "USER_REDACTED" if spec.applicant_field else "",
            spec.submitted_at_field: "2026-07-01" if spec.submitted_at_field else "",
            spec.status_field: status,
            spec.next_approver_field: "",
        }
        cells = [
            f'<td><a class="View" ajax-data="{escape(external_id)}">查看</a></td>'
        ]
        cells.extend(
            f"<td>{escape(values.get(header, f'REDACTED-{index}'))}</td>"
            for header in spec.headers[1:]
        )
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<table class="data-grid"><thead><tr>'
        + headers
        + "</tr></thead><tbody>"
        + "".join(rows)
        + f"</tbody></table><span>总共{total}条记录</span>"
    )


def _detail_page() -> str:
    return """
    <html><head><title>REDACTED DETAIL</title></head><body>
      <table>
        <tr><th>单据编号</th><td>REF-REDACTED</td></tr>
        <tr><th>项目编号</th><td>PROJECT-REDACTED</td></tr>
      </table>
      <table>
        <tr><th>序号</th><th>时间</th><th>审批人</th><th>职位</th><th>操作</th><th>批注</th></tr>
        <tr><td>1</td><td>2026-07-01</td><td>USER_A</td><td>ROLE_A</td><td>提交</td><td>REDACTED</td></tr>
      </table>
    </body></html>
    """


@pytest.mark.parametrize("spec", PROCUREMENT_STREAMS, ids=lambda spec: spec.workflow.value)
def test_fixed_schema_parser_normalizes_each_procurement_stream(
    spec: ProcurementStreamSpec,
) -> None:
    result = parse_procurement_list(_page(spec, ("1",), total=1), spec=spec)

    assert result.workflow is spec.workflow
    assert result.total_count == 1
    assert result.items[0].id == "1"
    assert result.items[0].reference_no == "REF-REDACTED-1"
    assert result.items[0].title == "REDACTED TITLE 1"
    assert dict(result.items[0].fields)[spec.status_field] == "审批通过"


def test_fixed_schema_parser_rejects_header_drift_and_missing_identity() -> None:
    spec = PROCUREMENT_STREAMS[1]
    drifted = _page(spec, ("1",), total=1).replace("<th>合同名称</th>", "<th>未知列</th>")
    with pytest.raises(ValueError, match="schema changed"):
        parse_procurement_list(drifted, spec=spec)

    missing = _page(spec, ("1",), total=1).replace(' ajax-data="1"', "")
    with pytest.raises(ValueError, match="stable identity"):
        parse_procurement_list(missing, spec=spec)


def test_generic_procurement_detail_parses_fields_and_approval_trail() -> None:
    detail = parse_procurement_detail(
        _detail_page(),
        workflow=WorkflowKind.PROCUREMENT_CONTRACT,
        external_id="detail-1",
    )

    assert detail.fields == {
        "单据编号": "REF-REDACTED",
        "项目编号": "PROJECT-REDACTED",
    }
    assert detail.html_title == "REDACTED DETAIL"
    assert len(detail.approval_steps) == 1
    assert detail.approval_steps[0].action == "提交"


@pytest.mark.parametrize(
    ("workflow", "expected_path"),
    [
        (
            WorkflowKind.PROCUREMENT_CONTRACT,
            "/WebTP/ProcurementContract/SearchDetail/detail-1",
        ),
        (
            WorkflowKind.PROCUREMENT_ORDER,
            "/WebTP/ProcurementOrder/SearchDetail/detail-1",
        ),
        (
            WorkflowKind.COST_CONFIRMATION,
            "/WebTP/CostConfirmation/Detail/detail-1",
        ),
        (
            WorkflowKind.CHECK_ACCEPTANCE,
            "/WebTP/CheckAcceptance/Detail/detail-1",
        ),
    ],
)
def test_client_uses_runtime_proven_procurement_detail_paths(
    workflow: WorkflowKind,
    expected_path: str,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, text=_detail_page(), request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        detail = client.get_procurement_document_detail(workflow, "detail-1")

    assert seen == [expected_path]
    assert len(detail.approval_steps) == 1


def test_client_collects_all_pages_and_posts_only_empty_new_stream_filters() -> None:
    spec = PROCUREMENT_STREAMS[1]
    seen: list[tuple[str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.content))
        page = int(request.url.path.rsplit("/", 2)[-2])
        ids = tuple(str(value) for value in (range(1, 11) if page == 1 else range(11, 13)))
        return httpx.Response(200, text=_page(spec, ids, total=12), request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        result = client.list_all_procurement_documents(
            WorkflowKind.PROCUREMENT_CONTRACT,
            max_pages=2,
            page_size=10,
        )

    assert len(result.items) == 12
    assert result.total_count == 12
    assert seen == [
        ("/WebTP/ProcurementContract/SearchIndex/0/1/False/1/10", b""),
        ("/WebTP/ProcurementContract/SearchIndex/0/1/False/2/10", b""),
    ]


def test_client_rejects_short_page_before_declared_total() -> None:
    spec = PROCUREMENT_STREAMS[4]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_page(spec, ("1", "2"), total=12),
            request=request,
        )

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PaginationIncompleteError, match="was short"):
            client.list_all_procurement_documents(
                WorkflowKind.CHECK_ACCEPTANCE,
                max_pages=2,
                page_size=10,
            )


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        (
            "POST",
            "/WebTP/CheckAcceptance/SearchIndex/0/1/False/2/50",
            RequestClass.ALLOW_LIVE,
        ),
        (
            "GET",
            "/WebTP/ProcurementContract/Delete/1",
            RequestClass.BUILD_ONLY,
        ),
        (
            "POST",
            "/WebTP/CostConfirmation/Approve/1",
            RequestClass.BUILD_ONLY,
        ),
        (
            "GET",
            "/WebTP/UnknownProcurement/SearchIndex",
            RequestClass.DENY,
        ),
        (
            "PUT",
            "/WebTP/ProcurementOrder/SearchIndex",
            RequestClass.DENY,
        ),
        (
            "GET",
            "/WebTP/ProcurementContract/SearchDetail/1",
            RequestClass.ALLOW_LIVE,
        ),
        (
            "GET",
            "/WebTP/ProcurementOrder/SearchDetail/1",
            RequestClass.ALLOW_LIVE,
        ),
        (
            "GET",
            "/WebTP/CostConfirmation/Detail/1",
            RequestClass.ALLOW_LIVE,
        ),
        (
            "GET",
            "/WebTP/CheckAcceptance/Detail/1",
            RequestClass.ALLOW_LIVE,
        ),
        (
            "POST",
            "/WebTP/CheckAcceptance/Detail/1",
            RequestClass.DENY,
        ),
    ],
)
def test_procurement_policy_allows_only_proven_reads(
    method: str,
    path: str,
    expected: RequestClass,
) -> None:
    with IsstechClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        assert client.classify(method, client._url(path)).request_class is expected


def _stream_result(
    workflow: WorkflowKind,
    *,
    status: str = "审批通过",
    external_id: str = "shared-id",
) -> ProcurementListResult:
    spec = PROCUREMENT_STREAM_BY_WORKFLOW[workflow]
    fields = {
        header: f"REDACTED-{index}"
        for index, header in enumerate(spec.headers[1:], start=1)
    }
    fields[spec.reference_field] = f"REF-{workflow.value}"
    fields[spec.title_field] = f"REDACTED {workflow.label}"
    fields[spec.project_no_field] = "PROJECT-REDACTED"
    fields[spec.status_field] = status
    fields[spec.next_approver_field] = "USER_APPROVER" if status == "审批中" else ""
    if spec.applicant_field:
        fields[spec.applicant_field] = "CURRENT USER"
    if spec.submitted_at_field:
        fields[spec.submitted_at_field] = "2026-07-01"
    record = ProcurementDocumentSummary(
        workflow=workflow,
        id=external_id,
        reference_no=fields[spec.reference_field],
        project_no=fields.get(spec.project_no_field, ""),
        title=fields[spec.title_field],
        applicant=fields.get(spec.applicant_field, "") if spec.applicant_field else "",
        submitted_at=(
            fields.get(spec.submitted_at_field, "") if spec.submitted_at_field else ""
        ),
        status=status,
        next_approver=fields[spec.next_approver_field],
        fields=tuple(fields.items()),
    )
    return ProcurementListResult(
        workflow=workflow,
        items=(record,),
        total_count=1,
        source_url=f"http://ipsapro.isstech.com{spec.search_path}",
    )


class FakeProcurementClient:
    def __init__(
        self,
        *,
        failure: WorkflowKind | None = None,
        status_by_workflow: dict[WorkflowKind, str] | None = None,
    ) -> None:
        self.settings = type("Settings", (), {"base_url": "http://ipsapro.isstech.com"})()
        self.failure = failure
        self.status_by_workflow = status_by_workflow or {}
        self.calls: list[WorkflowKind] = []

    def get_portal_display_name(self) -> str:
        return "CURRENT USER"

    def list_all_procurement_documents(
        self,
        workflow: WorkflowKind,
        *,
        max_pages: int,
        page_size: int,
    ) -> ProcurementListResult:
        assert max_pages == 20
        assert page_size == 50
        self.calls.append(workflow)
        if workflow is self.failure:
            raise PaginationIncompleteError(f"{workflow.value} incomplete")
        return _stream_result(
            workflow,
            status=self.status_by_workflow.get(workflow, "审批通过"),
        )


class ExpiredProcurementClient(FakeProcurementClient):
    def list_all_procurement_documents(
        self,
        workflow: WorkflowKind,
        *,
        max_pages: int,
        page_size: int,
    ) -> ProcurementListResult:
        del workflow, max_pages, page_size
        raise PermissionError("session expired")


class DetailProcurementClient(FakeProcurementClient):
    def __init__(self) -> None:
        super().__init__()
        self.detail_calls: list[WorkflowKind] = []

    def get_procurement_document_detail(
        self,
        workflow: WorkflowKind,
        external_id: str,
    ) -> PurchaseRequisitionDetail:
        self.detail_calls.append(workflow)
        fields = (
            {"PR_ProjectManagerName": "CURRENT USER"}
            if workflow is WorkflowKind.PURCHASE_REQUISITION
            else {"项目编号": "PROJECT-REDACTED"}
        )
        return PurchaseRequisitionDetail(
            id=external_id,
            fields=fields,
            html_title=workflow.label,
            approval_steps=(
                PurchaseApprovalStep(
                    sequence="1",
                    approver_name="USER_APPROVER",
                    action="提交",
                ),
            ),
        )


def test_batch_sync_checkpoints_all_streams_without_cross_workflow_id_collision(
    tmp_path: Path,
) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    result = sync_procurement_workflows(
        FakeProcurementClient(),  # type: ignore[arg-type]
        storage=storage,
        observed_at=OBSERVED_1,
        started_at=OBSERVED_1,
        today=date(2026, 7, 15),
        run_id="batch-1",
    )

    assert result.status == "succeeded"
    assert result.observed_count == 5
    assert result.source_total_count == 5
    assert result.snapshot_count == 5
    assert len(result.streams) == 5
    assert {summary.status for summary in result.streams} == {"succeeded"}
    current = storage.current_snapshots()
    assert len(current) == 5
    assert {snapshot.adapter for snapshot in current} == set(WorkflowKind)
    assert {snapshot.external_id for snapshot in current} == {"shared-id"}
    acceptance = next(
        snapshot
        for snapshot in current
        if snapshot.adapter is WorkflowKind.CHECK_ACCEPTANCE
    )
    assert acceptance.relations == (WorkItemRelation.APPLICANT,)
    assert set(storage.latest_successful_runs_by_adapter()) == set(WorkflowKind)


def test_batch_sync_enriches_only_proven_personal_project_records_idempotently(
    tmp_path: Path,
) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    client = DetailProcurementClient()

    first = sync_procurement_workflows(
        client,  # type: ignore[arg-type]
        storage=storage,
        observed_at=OBSERVED_1,
        started_at=OBSERVED_1,
        run_id="detail-first",
    )
    second = sync_procurement_workflows(
        client,  # type: ignore[arg-type]
        storage=storage,
        observed_at=OBSERVED_2,
        started_at=OBSERVED_2,
        run_id="detail-second",
    )

    assert first.status == "succeeded"
    assert set(client.detail_calls) == set(WorkflowKind)
    assert second.events == ()
    assert all(
        cached_workflow_detail(snapshot).approval_status == "available"  # type: ignore[union-attr]
        for snapshot in storage.current_snapshots()
    )
    assert all(
        len(cached_workflow_detail(snapshot).approval_steps) == 1  # type: ignore[union-attr]
        for snapshot in storage.current_snapshots()
    )


def test_batch_sync_keeps_failed_stream_checkpoint_and_updates_other_streams(
    tmp_path: Path,
) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    sync_procurement_workflows(
        FakeProcurementClient(),  # type: ignore[arg-type]
        storage=storage,
        observed_at=OBSERVED_1,
        started_at=OBSERVED_1,
        run_id="batch-complete",
    )
    result = sync_procurement_workflows(
        FakeProcurementClient(
            failure=WorkflowKind.PROCUREMENT_CONTRACT,
            status_by_workflow={WorkflowKind.CHECK_ACCEPTANCE: "审批中"},
        ),  # type: ignore[arg-type]
        storage=storage,
        observed_at=OBSERVED_2,
        started_at=OBSERVED_2,
        run_id="batch-partial",
    )

    assert result.status == "partial"
    failed = next(
        summary
        for summary in result.streams
        if summary.workflow is WorkflowKind.PROCUREMENT_CONTRACT
    )
    assert failed.status == "failed"
    assert failed.error_type == "PaginationIncompleteError"
    contract = storage.current_snapshots(adapter=WorkflowKind.PROCUREMENT_CONTRACT)[0]
    acceptance = storage.current_snapshots(adapter=WorkflowKind.CHECK_ACCEPTANCE)[0]
    assert contract.observed_at == OBSERVED_1.isoformat()
    assert acceptance.observed_at == OBSERVED_2.isoformat()
    assert acceptance.status == "审批中"
    acceptance_detail = cached_workflow_detail(acceptance)
    assert acceptance_detail is not None
    assert acceptance_detail.fields["单据状态"] == "审批中"
    failed_run = storage.get_run("batch-partial-procurement_contract")
    assert failed_run is not None
    assert failed_run["status"] == "failed"


def test_batch_dry_run_creates_no_database(tmp_path: Path) -> None:
    database = tmp_path / "workflow.sqlite3"
    result = sync_procurement_workflows(
        FakeProcurementClient(),  # type: ignore[arg-type]
        storage=WorkflowStorage(database),
        dry_run=True,
        observed_at=OBSERVED_1,
        started_at=OBSERVED_1,
        run_id="batch-dry-run",
    )

    assert result.status == "dry_run"
    assert result.dry_run is True
    assert result.observed_count == 5
    assert not database.exists()


def test_batch_sync_does_not_downgrade_auth_expiry_to_partial_success() -> None:
    with pytest.raises(PermissionError, match="session expired"):
        sync_procurement_workflows(
            ExpiredProcurementClient(),  # type: ignore[arg-type]
            storage=None,
            dry_run=True,
            observed_at=OBSERVED_1,
            started_at=OBSERVED_1,
            run_id="batch-expired",
        )
