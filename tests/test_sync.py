"""The manual sync loop is complete, repeatable, and produces local-only outputs."""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
import io
import json
from pathlib import Path

import pytest

from isstech_replay.account_scope import account_database_path, account_runtime_dir
from isstech_replay.client import PaginationIncompleteError
from isstech_replay.config import Settings
from isstech_replay.models.purchase import (
    PurchaseApprovalStep,
    PurchaseListQuery,
    PurchaseListResult,
    PurchaseRequisitionDetail,
    PurchaseRequisitionSummary,
    PurchaseView,
)
from isstech_replay.models.procurement import (
    PROCUREMENT_STREAM_BY_WORKFLOW,
    ProcurementDocumentSummary,
    ProcurementListResult,
)
from isstech_replay.models.readonly_modules import (
    ReadonlyModuleKind,
    ReadonlyStreamSummary,
    ReadonlySyncBatchResult,
)
from isstech_replay.models.work_items import (
    ChangeKind,
    WorkItem,
    WorkItemRelation,
    WorkflowKind,
)
from isstech_replay.storage import WorkflowStorage
from isstech_replay.sync import (
    DetailScanIncompleteError,
    filter_account_purchase_requisitions,
    read_account_purchase_measurement,
    sync_purchase_requisitions,
)
from tools import sync_work_items as cli


OBSERVED_1 = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)
OBSERVED_2 = datetime(2026, 7, 16, 1, 0, tzinfo=UTC)


def _readonly_batch(
    *,
    status: str = "succeeded",
    dry_run: bool = False,
    observed_count: int = 3,
    changed_count: int = 2,
) -> ReadonlySyncBatchResult:
    return ReadonlySyncBatchResult(
        run_id="readonly-batch",
        status=status,
        dry_run=dry_run,
        started_at=OBSERVED_1.isoformat(),
        observed_at=OBSERVED_1.isoformat(),
        finished_at=OBSERVED_1.isoformat(),
        source_total_count=observed_count,
        observed_count=observed_count,
        snapshot_count=observed_count,
        history_rows_inserted=0 if dry_run else observed_count,
        changed_count=changed_count,
        streams=(
            ReadonlyStreamSummary(
                module=ReadonlyModuleKind.PAYMENT,
                run_id="readonly-payment",
                status=status,
                source_total_count=observed_count,
                observed_count=observed_count,
                snapshot_count=observed_count,
                changed_count=changed_count,
            ),
        ),
    )


def _result(
    *,
    approver: str = "USER_APPROVER",
    active_status: str = "审批中",
) -> PurchaseListResult:
    return PurchaseListResult(
        view=PurchaseView.SEARCH,
        items=(
            PurchaseRequisitionSummary(
                id="1",
                requisition_no="REF-1",
                project_no="PROJECT-1",
                project_name="REDACTED PROJECT ONE",
                creator_name="USER_REQUESTER",
                create_date="2026-07-01",
                status=active_status,
                next_approver=approver,
            ),
            PurchaseRequisitionSummary(
                id="2",
                requisition_no="REF-2",
                project_no="PROJECT-2",
                project_name="REDACTED PROJECT TWO",
                creator_name="USER_REQUESTER",
                create_date="2026-06-01",
                status="已完成",
                next_approver="",
            ),
        ),
        total_count=2,
        page=1,
        page_size=10,
        source_url="http://ipsapro.isstech.com/WebTP/PurchaseRequisition/SearchIndex",
    )


class FakeClient:
    def __init__(
        self,
        result: PurchaseListResult | None = None,
        error: Exception | None = None,
        *,
        display_name: str = "USER_REQUESTER",
        details: dict[
            str,
            PurchaseRequisitionDetail | list[PurchaseRequisitionDetail | Exception],
        ]
        | None = None,
    ) -> None:
        self.settings = Settings(base_url="http://ipsapro.isstech.com")
        self.result = result or _result()
        self.error = error
        self.display_name = display_name
        self.details = details or {}
        self.calls: list[tuple[PurchaseView, int]] = []
        self.detail_calls: list[str] = []
        self.closed = False

    def get_portal_display_name(self) -> str:
        return self.display_name

    def list_all_purchase_requisitions(
        self,
        query: object,
        *,
        max_pages: int,
    ) -> PurchaseListResult:
        assert isinstance(query, PurchaseListQuery)
        self.calls.append((query.view, max_pages))
        if self.error is not None:
            raise self.error
        return self.result

    def get_purchase_requisition(self, external_id: str) -> PurchaseRequisitionDetail:
        self.detail_calls.append(external_id)
        configured = self.details.get(external_id)
        if isinstance(configured, list):
            if not configured:
                raise AssertionError(f"no configured detail result left for {external_id}")
            configured = configured.pop(0)
        if isinstance(configured, Exception):
            raise configured
        return configured or PurchaseRequisitionDetail(id=external_id)

    def list_all_procurement_documents(
        self,
        workflow: WorkflowKind,
        *,
        max_pages: int,
        page_size: int,
    ) -> ProcurementListResult:
        del max_pages, page_size
        if self.error is not None:
            raise self.error
        if workflow is not WorkflowKind.PURCHASE_REQUISITION:
            return ProcurementListResult(
                workflow=workflow,
                items=(),
                total_count=0,
            )
        spec = PROCUREMENT_STREAM_BY_WORKFLOW[workflow]
        items = []
        for record in self.result.items:
            fields = (
                ("申请单编号", record.requisition_no),
                ("项目编号", record.project_no),
                ("项目名称", record.project_name),
                ("申请人", record.creator_name),
                ("申请时间", record.create_date),
                ("单据状态", record.status),
                ("下一级审批人", record.next_approver),
            )
            assert tuple(name for name, _ in fields) == spec.headers[1:]
            items.append(
                ProcurementDocumentSummary(
                    workflow=workflow,
                    id=record.id,
                    reference_no=record.requisition_no,
                    project_no=record.project_no,
                    title=record.project_name,
                    applicant=record.creator_name,
                    submitted_at=record.create_date,
                    status=record.status,
                    next_approver=record.next_approver,
                    fields=fields,
                )
            )
        return ProcurementListResult(
            workflow=workflow,
            items=tuple(items),
            total_count=len(items),
            source_url=self.result.source_url,
        )

    def close(self) -> None:
        self.closed = True


def test_sync_persists_all_records_and_returns_actionable_items(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    client = FakeClient()
    result = sync_purchase_requisitions(
        client,  # type: ignore[arg-type]
        storage=storage,
        max_pages=20,
        observed_at=OBSERVED_1,
        today=date(2026, 7, 15),
        run_id="run-1",
    )

    assert client.calls == [
        (PurchaseView.SEARCH, 20),
    ]
    assert result.status == "succeeded"
    assert result.observed_count == 2
    assert result.snapshot_count == 2
    assert result.history_rows_inserted == 2
    assert result.actionable_count == 1
    assert len(result.work_items) == 1
    assert [event.kind for event in result.events] == [ChangeKind.NEW, ChangeKind.NEW]
    assert storage.table_count("workflow_current") == 2
    assert storage.get_run("run-1")["status"] == "succeeded"  # type: ignore[index]


def test_account_identity_filter_excludes_other_applicants() -> None:
    owned = PurchaseRequisitionSummary(
        id="owned-1",
        requisition_no="REF-OWNED",
        project_no="PROJECT-OWNED",
        project_name="OWNED PROJECT",
        creator_name="ACCOUNT USER",
        create_date="2026-07-01",
        status="审批中",
    )
    unrelated = PurchaseRequisitionSummary(
        id="global-2",
        requisition_no="REF-GLOBAL",
        creator_name="OTHER USER",
        status="审批中",
        next_approver="GLOBAL APPROVER",
    )

    filtered = filter_account_purchase_requisitions(
        PurchaseListResult(
            view=PurchaseView.SEARCH,
            items=(owned, unrelated),
            total_count=2,
        ),
        display_name=" account user ",
    )

    assert [item.id for item in filtered.items] == ["owned-1"]
    assert filtered.total_count == 2


def test_sync_preserves_global_candidate_count_after_account_filter(
    tmp_path: Path,
) -> None:
    owned = _result().items[0]
    unrelated = PurchaseRequisitionSummary(
        id="global-2",
        requisition_no="REF-GLOBAL",
        creator_name="OTHER USER",
        status="审批中",
        next_approver="GLOBAL APPROVER",
    )
    source = PurchaseListResult(
        view=PurchaseView.SEARCH,
        items=(owned, unrelated),
        total_count=78,
    )
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")

    result = sync_purchase_requisitions(
        FakeClient(source),  # type: ignore[arg-type]
        storage=storage,
        observed_at=OBSERVED_1,
        today=date(2026, 7, 15),
        run_id="filtered-run",
    )

    assert result.source_total_count == 78
    assert result.observed_count == 1
    assert result.snapshot_count == 1
    run = storage.get_run("filtered-run")
    assert run is not None
    assert run["source_total_count"] == 78
    assert run["observed_count"] == 1


def test_measurement_keeps_each_proven_participant_relation() -> None:
    identity = "ACCOUNT_1"
    summaries = (
        PurchaseRequisitionSummary(id="applicant", creator_name=identity),
        PurchaseRequisitionSummary(id="submitter", creator_name="OTHER"),
        PurchaseRequisitionSummary(id="project-manager", creator_name="OTHER"),
        PurchaseRequisitionSummary(id="procurement-manager", creator_name="OTHER"),
        PurchaseRequisitionSummary(id="approver", creator_name="OTHER"),
        PurchaseRequisitionSummary(id="unrelated", creator_name="OTHER"),
    )
    source = PurchaseListResult(
        view=PurchaseView.SEARCH,
        items=summaries,
        total_count=len(summaries),
    )
    details = {
        "submitter": PurchaseRequisitionDetail(
            id="submitter",
            approval_steps=(
                PurchaseApprovalStep(approver_name=identity, action="提交"),
            ),
        ),
        "project-manager": PurchaseRequisitionDetail(
            id="project-manager",
            fields={"PR_ProjectManagerName": f"Current User ({identity})"},
        ),
        "procurement-manager": PurchaseRequisitionDetail(
            id="procurement-manager",
            fields={"PR_ProcurementManagerName": identity},
        ),
        "approver": PurchaseRequisitionDetail(
            id="approver",
            approval_steps=(
                PurchaseApprovalStep(approver_name=identity, action="同意"),
            ),
        ),
    }

    measurement = read_account_purchase_measurement(
        FakeClient(source, display_name=identity, details=details),  # type: ignore[arg-type]
        max_pages=20,
    )

    assert {
        record.summary.id: record.relations for record in measurement.records
    } == {
        "applicant": (WorkItemRelation.APPLICANT,),
        "submitter": (WorkItemRelation.SUBMITTER,),
        "project-manager": (WorkItemRelation.PROJECT_MANAGER,),
        "procurement-manager": (WorkItemRelation.PROCUREMENT_MANAGER,),
        "approver": (WorkItemRelation.APPROVER,),
    }
    assert measurement.result.total_count == len(summaries)


def test_detail_read_retries_once_then_keeps_complete_measurement() -> None:
    source = PurchaseListResult(
        view=PurchaseView.SEARCH,
        items=(PurchaseRequisitionSummary(id="retry", creator_name="ACCOUNT_1"),),
        total_count=1,
    )
    client = FakeClient(
        source,
        display_name="ACCOUNT_1",
        details={
            "retry": [
                RuntimeError("temporary detail failure"),
                PurchaseRequisitionDetail(id="retry", fields={"Field": "Value"}),
            ]
        },
    )

    measurement = read_account_purchase_measurement(  # type: ignore[arg-type]
        client,
        max_pages=20,
    )

    assert [record.summary.id for record in measurement.records] == ["retry"]
    assert client.detail_calls == ["retry", "retry"]


def test_detail_read_failure_aborts_run_and_preserves_previous_current(
    tmp_path: Path,
) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    sync_purchase_requisitions(
        FakeClient(),  # type: ignore[arg-type]
        storage=storage,
        observed_at=OBSERVED_1,
        run_id="complete-run",
    )
    failing = FakeClient(
        details={
            "1": [RuntimeError("first"), RuntimeError("second")],
        }
    )

    with pytest.raises(DetailScanIncompleteError, match="item 1/2"):
        sync_purchase_requisitions(
            failing,  # type: ignore[arg-type]
            storage=storage,
            observed_at=OBSERVED_2,
            run_id="failed-detail-run",
        )

    assert failing.detail_calls == ["1", "1"]
    assert {item.external_id for item in storage.current_snapshots()} == {"1", "2"}
    assert storage.get_run("failed-detail-run")["status"] == "failed"  # type: ignore[index]


def test_detail_scan_limit_fails_before_any_detail_request() -> None:
    summaries = tuple(
        PurchaseRequisitionSummary(id=str(index), creator_name="ACCOUNT_1")
        for index in range(501)
    )
    client = FakeClient(
        PurchaseListResult(
            view=PurchaseView.SEARCH,
            items=summaries,
            total_count=len(summaries),
        ),
        display_name="ACCOUNT_1",
    )

    with pytest.raises(DetailScanIncompleteError, match="exceeds limit 500"):
        read_account_purchase_measurement(client, max_pages=100)  # type: ignore[arg-type]

    assert client.detail_calls == []


def test_same_state_next_day_updates_age_without_change_event(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    first = FakeClient()
    second = FakeClient()
    sync_purchase_requisitions(
        first,  # type: ignore[arg-type]
        storage=storage,
        observed_at=OBSERVED_1,
        today=date(2026, 7, 15),
        run_id="run-1",
    )
    replay = sync_purchase_requisitions(
        second,  # type: ignore[arg-type]
        storage=storage,
        observed_at=OBSERVED_2,
        today=date(2026, 7, 16),
        run_id="run-2",
    )

    assert replay.events == ()
    assert storage.table_count("workflow_snapshots") == 4
    actionable = storage.current_snapshots(actionable_only=True)
    assert len(actionable) == 1
    assert actionable[0].waiting_days == 15


def test_sync_failure_is_recorded_without_partial_snapshot_or_secret(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    marker = "TEST_TICKET_VALUE"
    client = FakeClient(
        error=PaginationIncompleteError(
            "pagination stopped with " + ".iPSA" + "=" + marker
        )
    )

    with pytest.raises(PaginationIncompleteError):
        sync_purchase_requisitions(
            client,  # type: ignore[arg-type]
            storage=storage,
            observed_at=OBSERVED_1,
            run_id="run-failed",
        )

    run = storage.get_run("run-failed")
    assert run is not None
    assert run["status"] == "failed"
    assert run["error_type"] == "PaginationIncompleteError"
    assert marker not in str(run["error_message"])
    assert "<redacted>" in str(run["error_message"])
    assert storage.table_count("workflow_snapshots") == 0
    assert storage.table_count("workflow_events") == 0


def test_dry_run_does_not_create_database_or_run_files(tmp_path: Path) -> None:
    database = tmp_path / "workflow.sqlite3"
    result = sync_purchase_requisitions(
        FakeClient(),  # type: ignore[arg-type]
        storage=WorkflowStorage(database),
        dry_run=True,
        observed_at=OBSERVED_1,
        today=date(2026, 7, 15),
        run_id="dry-run",
    )
    assert result.status == "dry_run"
    assert result.snapshot_count == 2
    assert result.history_rows_inserted == 0
    assert result.database_path is None
    assert not database.exists()


def test_csv_export_escapes_formulas_and_contains_no_auth_fields() -> None:
    item = WorkItem(
        key="purchase_requisition:1",
        workflow=WorkflowKind.PURCHASE_REQUISITION,
        external_id="1",
        reference_no="=FORMULA",
        project_no="PROJECT-1",
        title="REDACTED PROJECT",
        applicant="USER_REQUESTER",
        submitted_at="2026-07-01",
        status="审批中",
        current_approver="USER_APPROVER",
        waiting_days=14,
        source_url="http://ipsapro.isstech.com/WebTP/PurchaseRequisition/Detail/1",
    )
    rendered = cli.render_work_items_csv((item,))
    rows = list(csv.DictReader(io.StringIO(rendered.lstrip("\ufeff"))))
    assert rows[0]["reference_no"] == "'=FORMULA"
    assert "emp_Password" not in rendered
    assert ".iPSA" not in rendered
    assert "cookie" not in rendered.lower()


def test_cli_requires_environment_credentials_without_creating_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ISSTECH_USERNAME", raising=False)
    monkeypatch.delenv("ISSTECH_PASSWORD", raising=False)
    assert cli.main(["--data-dir", str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []


def test_cli_records_login_failure_without_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "TEST_LOGIN_SECRET"
    monkeypatch.setenv("ISSTECH_USERNAME", "TEST_USER")
    monkeypatch.setenv("ISSTECH_PASSWORD", marker)

    def fail_login(username: str, password: str):
        del username
        raise RuntimeError("password=" + password)

    monkeypatch.setattr(cli, "login_with_settings", fail_login)
    assert cli.main(["--data-dir", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert marker not in captured.err
    assert "<redacted>" in captured.err

    storage = WorkflowStorage(
        account_database_path(
            "TEST_USER",
            base_database_path=tmp_path / "workflow-center.sqlite3",
        )
    )
    runs = storage.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert marker not in str(runs[0]["error_message"])


def test_cli_writes_database_summary_and_csv_with_restrictive_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_client = FakeClient()
    monkeypatch.setenv("ISSTECH_USERNAME", "TEST_USER")
    monkeypatch.setenv("ISSTECH_PASSWORD", "TEST_PASSWORD")
    monkeypatch.setattr(
        cli,
        "login_with_settings",
        lambda username, password: (fake_client, object()),
    )
    readonly_calls = []

    def sync_readonly(client, **kwargs):
        assert client is fake_client
        assert kwargs["storage"] is kwargs["scope_storage"]
        assert kwargs["dry_run"] is False
        readonly_calls.append(kwargs)
        return _readonly_batch()

    monkeypatch.setattr(cli, "sync_readonly_modules", sync_readonly)

    exit_code = cli.main(
        [
            "--data-dir",
            str(tmp_path),
            "--json",
            "--csv",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "succeeded"
    assert payload["actionable_count"] == 1
    assert payload["procurement_observed_count"] == 2
    assert payload["readonly_observed_count"] == 3
    assert payload["readonly_changed_count"] == 2
    assert payload["observed_count"] == 5
    assert payload["readonly_modules"]["run_id"] == "readonly-batch"
    assert len(readonly_calls) == 1
    assert fake_client.closed is True

    scoped_dir = account_runtime_dir(tmp_path, "TEST_USER")
    database = scoped_dir / "workflow-center.sqlite3"
    summaries = list((scoped_dir / "runs").glob("*/summary.json"))
    exports = list((scoped_dir / "exports").glob("*-work-items.csv"))
    assert database.is_file()
    assert "TEST_USER" not in str(scoped_dir)
    assert len(summaries) == 1
    assert len(exports) == 1
    assert summaries[0].stat().st_mode & 0o777 == 0o600
    assert exports[0].stat().st_mode & 0o777 == 0o600
    assert json.loads(summaries[0].read_text())["status"] == "succeeded"


def test_cli_combined_dry_run_does_not_create_account_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_client = FakeClient()
    login_calls = []
    monkeypatch.setenv("ISSTECH_USERNAME", "TEST_USER")
    monkeypatch.setenv("ISSTECH_PASSWORD", "TEST_PASSWORD")

    def login(username: str, password: str):
        login_calls.append((username, password))
        return fake_client, object()

    def sync_readonly(client, **kwargs):
        assert client is fake_client
        assert kwargs["storage"] is None
        assert kwargs["scope_storage"] is None
        assert kwargs["dry_run"] is True
        return _readonly_batch(
            status="dry_run",
            dry_run=True,
            observed_count=1,
            changed_count=0,
        )

    monkeypatch.setattr(cli, "login_with_settings", login)
    monkeypatch.setattr(cli, "sync_readonly_modules", sync_readonly)

    exit_code = cli.main(["--data-dir", str(tmp_path), "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "dry_run"
    assert payload["observed_count"] == 3
    assert login_calls == [("TEST_USER", "TEST_PASSWORD")]
    assert fake_client.closed is True
    assert list(tmp_path.iterdir()) == []


def test_cli_returns_nonzero_and_keeps_summary_when_readonly_is_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_client = FakeClient()
    monkeypatch.setenv("ISSTECH_USERNAME", "TEST_USER")
    monkeypatch.setenv("ISSTECH_PASSWORD", "TEST_PASSWORD")
    monkeypatch.setattr(
        cli,
        "login_with_settings",
        lambda username, password: (fake_client, object()),
    )
    monkeypatch.setattr(
        cli,
        "sync_readonly_modules",
        lambda client, **kwargs: _readonly_batch(
            status="partial",
            observed_count=1,
            changed_count=0,
        ),
    )

    exit_code = cli.main(["--data-dir", str(tmp_path), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["status"] == "partial"
    assert "status=partial" in captured.err
    scoped_dir = account_runtime_dir(tmp_path, "TEST_USER")
    summaries = list((scoped_dir / "runs").glob("*/summary.json"))
    assert len(summaries) == 1
    assert json.loads(summaries[0].read_text())["status"] == "partial"
    storage = WorkflowStorage(scoped_dir / "workflow-center.sqlite3")
    assert storage.table_count("workflow_current") == 2
