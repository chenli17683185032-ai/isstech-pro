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
    PurchaseListQuery,
    PurchaseListResult,
    PurchaseRequisitionSummary,
    PurchaseView,
)
from isstech_replay.models.work_items import ChangeKind, WorkItem, WorkflowKind
from isstech_replay.storage import WorkflowStorage
from isstech_replay.sync import (
    filter_account_purchase_requisitions,
    sync_purchase_requisitions,
)
from tools import sync_work_items as cli


OBSERVED_1 = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)
OBSERVED_2 = datetime(2026, 7, 16, 1, 0, tzinfo=UTC)


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
    ) -> None:
        self.settings = Settings(base_url="http://ipsapro.isstech.com")
        self.result = result or _result()
        self.error = error
        self.calls: list[tuple[PurchaseView, int]] = []
        self.closed = False

    def get_portal_display_name(self) -> str:
        return "USER_REQUESTER"

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
