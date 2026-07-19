"""Independent Payment/BizCase checkpoint and failure-isolation tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from isstech_replay.models.bizcase import BizCaseListResult, BizCaseRecord
from isstech_replay.models.daily_expense import (
    DailyExpenseListResult,
    DailyExpenseRecord,
)
from isstech_replay.models.fee_application import (
    FeeApplicationListResult,
    FeeApplicationRecord,
)
from isstech_replay.models.payment import PaymentListResult, PaymentRecord
from isstech_replay.models.readonly_modules import ReadonlyModuleKind
from isstech_replay.models.travel_application import (
    TravelApplicationListResult,
    TravelApplicationRecord,
)
from isstech_replay.readonly_sync import readonly_snapshots, sync_readonly_modules
from isstech_replay.storage import SCHEMA_VERSION, WorkflowStorage


T1 = datetime(2026, 7, 16, 1, 0, tzinfo=UTC)
T2 = datetime(2026, 7, 16, 2, 0, tzinfo=UTC)
T3 = datetime(2026, 7, 16, 3, 0, tzinfo=UTC)


def _payment_result(*, status: str = "已保存") -> PaymentListResult:
    record = PaymentRecord(
        id="PAY-1",
        payment_no="PAYMENT-REDACTED-1",
        payment_type="TYPE-A",
        applicant="USER-A",
        project_no="PROJECT-1",
        project_name="PROJECT REDACTED 1",
        currency="CNY",
        status=status,
        fields=(("付款单编号", "PAYMENT-REDACTED-1"), ("状态", status)),
    )
    return PaymentListResult(
        items=(record,),
        total_count=1,
        page_count=1,
        current_page=1,
        source_url="http://ipsapro.isstech.com/WebPMS/Payment/index",
    )


def _bizcase_result(
    *,
    count: int = 2,
    project_numbers: tuple[str, ...] = (),
    application_visible_count: int = 0,
) -> BizCaseListResult:
    records = tuple(
        BizCaseRecord(
            id=f"BC-REDACTED-{index:03d}-V001",
            ordinal=index,
            version_no=f"BC-REDACTED-{index:03d}-V001",
            bizcase_no=f"BC-REDACTED-{index:03d}",
            client_name=f"CLIENT-{index}",
            project_no=(
                project_numbers[index - 1]
                if index <= len(project_numbers)
                else ""
            ),
            project_name=f"PROJECT REDACTED {index}",
            current_approver="APPROVED",
            fields=(("BizCase编号", f"BC-REDACTED-{index:03d}"),),
        )
        for index in range(1, count + 1)
    )
    return BizCaseListResult(
        items=records,
        total_count=count,
        page_count=1,
        source_url="http://ipsapro.isstech.com/WebPMP/Main.aspx?thUrl=REDACTED",
        application_visible_ids=tuple(
            record.id for record in records[:application_visible_count]
        ),
    )


def _travel_result(
    *,
    count: int = 2,
    applicant: str = "USER-A",
) -> TravelApplicationListResult:
    records = tuple(
        TravelApplicationRecord(
            id=f"ELA-REDACTED-{index:03d}",
            ordinal=index,
            application_no=f"ELA-REDACTED-{index:03d}",
            project_name=f"PROJECT REDACTED {index}",
            applicant=applicant,
            application_date=f"2026-07-{index:02d}",
            status="已通过",
            amount="￥0.00",
            fields=(("申请人", applicant),),
        )
        for index in range(1, count + 1)
    )
    return TravelApplicationListResult(
        items=records,
        total_count=count,
        page_count=1,
        source_url="http://ipsapro.isstech.com/WebPSAOA/Fee/REDACTED",
    )


def _daily_expense_result(
    *,
    count: int = 2,
    applicant: str = "USER-A",
) -> DailyExpenseListResult:
    records = tuple(
        DailyExpenseRecord(
            id=f"DEA-REDACTED-{index:03d}",
            ordinal=index,
            application_no=f"DEA-REDACTED-{index:03d}",
            project_name=f"PROJECT REDACTED {index}",
            applicant=applicant,
            application_date=f"2026-07-{index:02d}",
            status="已提交",
            amount="￥0.00",
            fields=(("申请人", applicant),),
        )
        for index in range(1, count + 1)
    )
    return DailyExpenseListResult(
        items=records,
        total_count=count,
        page_count=1,
        source_url="http://ipsapro.isstech.com/WebPSAOA/Fee/REDACTED",
    )


def _fee_application_result(
    *,
    count: int = 2,
    prefix: str,
    applicant: str = "USER-A",
) -> FeeApplicationListResult:
    application_prefix = "EEA" if prefix == "R" else "ESA"
    records = tuple(
        FeeApplicationRecord(
            id=f"{application_prefix}-REDACTED-{index:03d}",
            ordinal=index,
            application_no=f"{application_prefix}-REDACTED-{index:03d}",
            project_name=f"PROJECT REDACTED {index}",
            applicant=applicant,
            application_date=f"2026-07-{index:02d}",
            status="已提交",
            amount="￥0.00",
            fields=(("申请人", applicant),),
        )
        for index in range(1, count + 1)
    )
    return FeeApplicationListResult(
        items=records,
        total_count=count,
        page_count=1,
        source_url="http://ipsapro.isstech.com/WebPSAOA/Fee/REDACTED",
    )


class FakeReadonlyClient:
    def __init__(
        self,
        *,
        payment_status: str = "已保存",
        bizcase_count: int = 2,
        bizcase_application_visible_count: int = 1,
        travel_count: int = 2,
        daily_expense_count: int = 2,
        travel_reimbursement_count: int = 2,
        travel_subsidy_count: int = 2,
        fail_payment: bool = False,
        fail_bizcase: bool = False,
        fail_travel: bool = False,
        fail_daily_expense: bool = False,
        fail_travel_reimbursement: bool = False,
        fail_travel_subsidy: bool = False,
        fail_identity: bool = False,
    ) -> None:
        self.payment_status = payment_status
        self.bizcase_count = bizcase_count
        self.bizcase_application_visible_count = (
            bizcase_application_visible_count
        )
        self.travel_count = travel_count
        self.daily_expense_count = daily_expense_count
        self.travel_reimbursement_count = travel_reimbursement_count
        self.travel_subsidy_count = travel_subsidy_count
        self.fail_payment = fail_payment
        self.fail_bizcase = fail_bizcase
        self.fail_travel = fail_travel
        self.fail_daily_expense = fail_daily_expense
        self.fail_travel_reimbursement = fail_travel_reimbursement
        self.fail_travel_subsidy = fail_travel_subsidy
        self.fail_identity = fail_identity

    def get_portal_display_name(self) -> str:
        if self.fail_identity:
            raise RuntimeError("identity unavailable")
        return "USER-A"

    def list_personal_payment_records(
        self,
        *,
        display_name: str,
        project_numbers: tuple[str, ...],
        max_pages: int,
    ) -> PaymentListResult:
        assert display_name == "USER-A"
        assert project_numbers == ()
        assert max_pages == 20
        if self.fail_payment:
            raise RuntimeError("payment unavailable")
        return _payment_result(status=self.payment_status)

    def list_bizcases_with_application_visibility(
        self,
        *,
        max_pages: int,
    ) -> BizCaseListResult:
        assert max_pages == 20
        if self.fail_bizcase:
            raise RuntimeError("bizcase unavailable")
        return _bizcase_result(
            count=self.bizcase_count,
            application_visible_count=self.bizcase_application_visible_count,
        )

    def list_personal_travel_applications(
        self,
        *,
        display_name: str,
        max_pages: int,
    ) -> TravelApplicationListResult:
        assert display_name == "USER-A"
        assert max_pages == 20
        if self.fail_travel:
            raise RuntimeError("travel application unavailable")
        return _travel_result(count=self.travel_count, applicant=display_name)

    def list_personal_daily_expenses(
        self,
        *,
        display_name: str,
        max_pages: int,
    ) -> DailyExpenseListResult:
        assert display_name == "USER-A"
        assert max_pages == 20
        if self.fail_daily_expense:
            raise RuntimeError("daily expense unavailable")
        return _daily_expense_result(
            count=self.daily_expense_count,
            applicant=display_name,
        )

    def list_personal_travel_reimbursements(
        self,
        *,
        display_name: str,
        max_pages: int,
    ) -> FeeApplicationListResult:
        assert display_name == "USER-A"
        assert max_pages == 20
        if self.fail_travel_reimbursement:
            raise RuntimeError("travel reimbursement unavailable")
        return _fee_application_result(
            count=self.travel_reimbursement_count,
            prefix="R",
            applicant=display_name,
        )

    def list_personal_travel_subsidies(
        self,
        *,
        display_name: str,
        max_pages: int,
    ) -> FeeApplicationListResult:
        assert display_name == "USER-A"
        assert max_pages == 20
        if self.fail_travel_subsidy:
            raise RuntimeError("travel subsidy unavailable")
        return _fee_application_result(
            count=self.travel_subsidy_count,
            prefix="S",
            applicant=display_name,
        )


def test_readonly_sync_is_idempotent_and_keeps_modules_separate(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    first = sync_readonly_modules(
        FakeReadonlyClient(),  # type: ignore[arg-type]
        storage=storage,
        observed_at=T1,
        started_at=T1,
        run_id="batch-1",
    )
    second = sync_readonly_modules(
        FakeReadonlyClient(),  # type: ignore[arg-type]
        storage=storage,
        observed_at=T2,
        started_at=T2,
        run_id="batch-2",
    )

    assert storage.schema_version() == SCHEMA_VERSION
    assert first.status == "succeeded"
    assert [stream.module for stream in first.streams] == [
        ReadonlyModuleKind.PAYMENT,
        ReadonlyModuleKind.BIZCASE,
        ReadonlyModuleKind.TRAVEL_APPLICATION,
        ReadonlyModuleKind.DAILY_EXPENSE,
        ReadonlyModuleKind.TRAVEL_REIMBURSEMENT,
        ReadonlyModuleKind.TRAVEL_SUBSIDY,
    ]
    assert first.observed_count == 11
    assert first.changed_count == 11
    assert second.status == "succeeded"
    assert second.observed_count == 11
    assert second.changed_count == 0
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.PAYMENT)) == 1
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.BIZCASE)) == 2
    assert len(
        storage.current_readonly_snapshots(ReadonlyModuleKind.TRAVEL_APPLICATION)
    ) == 2
    assert len(
        storage.current_readonly_snapshots(ReadonlyModuleKind.DAILY_EXPENSE)
    ) == 2
    assert len(
        storage.current_readonly_snapshots(ReadonlyModuleKind.TRAVEL_REIMBURSEMENT)
    ) == 2
    assert len(
        storage.current_readonly_snapshots(ReadonlyModuleKind.TRAVEL_SUBSIDY)
    ) == 2
    assert storage.table_count("readonly_module_runs") == 12
    assert storage.table_count("readonly_module_snapshots") == 22
    assert storage.table_count("readonly_module_current") == 11
    payment_payload = json.loads(
        storage.current_readonly_snapshots(ReadonlyModuleKind.PAYMENT)[0].payload_json
    )
    assert payment_payload["payment_no"] == "PAYMENT-REDACTED-1"
    assert payment_payload["scope_reasons"] == ["submitted_by_me"]
    bizcase_payloads = [
        json.loads(snapshot.payload_json)
        for snapshot in storage.current_readonly_snapshots(ReadonlyModuleKind.BIZCASE)
    ]
    assert [payload["application_view_visible"] for payload in bizcase_payloads] == [
        True,
        False,
    ]
    travel_payloads = [
        json.loads(snapshot.payload_json)
        for snapshot in storage.current_readonly_snapshots(
            ReadonlyModuleKind.TRAVEL_APPLICATION
        )
    ]
    assert all(
        payload["scope_reasons"] == ["submitted_by_me"]
        for payload in travel_payloads
    )
    daily_expense_payloads = [
        json.loads(snapshot.payload_json)
        for snapshot in storage.current_readonly_snapshots(
            ReadonlyModuleKind.DAILY_EXPENSE
        )
    ]
    assert all(
        payload["scope_reasons"] == ["submitted_by_me"]
        for payload in daily_expense_payloads
    )


def test_bizcase_snapshots_mark_only_exact_personal_projects() -> None:
    snapshots = readonly_snapshots(
        ReadonlyModuleKind.BIZCASE,
        _bizcase_result(
            project_numbers=(" PROJECT-1 ", "PROJECT-OTHER"),
            application_visible_count=2,
        ),
        observed_at="2026-07-16T01:00:00+00:00",
        project_numbers=("PROJECT-1",),
    )

    payloads = [json.loads(snapshot.payload_json) for snapshot in snapshots]
    assert payloads[0]["scope_reasons"] == ["my_project"]
    assert payloads[1]["scope_reasons"] == []
    assert payloads[0]["application_view_visible"] is True
    assert payloads[1]["application_view_visible"] is True


def test_bizcase_snapshots_reject_unknown_application_visibility_identity() -> None:
    result = _bizcase_result(count=1)
    invalid = replace(
        result,
        application_visible_ids=("BC-REDACTED-999-V001",),
    )

    with pytest.raises(RuntimeError, match="application visibility identity"):
        readonly_snapshots(
            ReadonlyModuleKind.BIZCASE,
            invalid,
            observed_at="2026-07-16T01:00:00+00:00",
        )


def test_identity_failure_does_not_block_bizcase_checkpoint(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")

    result = sync_readonly_modules(
        FakeReadonlyClient(fail_identity=True),  # type: ignore[arg-type]
        storage=storage,
        observed_at=T1,
        started_at=T1,
        run_id="identity-failure",
    )

    assert result.status == "partial"
    assert [stream.status for stream in result.streams] == [
        "failed",
        "succeeded",
        "failed",
        "failed",
        "failed",
        "failed",
    ]
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.PAYMENT)) == 0
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.BIZCASE)) == 2
    assert len(
        storage.current_readonly_snapshots(ReadonlyModuleKind.TRAVEL_APPLICATION)
    ) == 0
    assert len(
        storage.current_readonly_snapshots(ReadonlyModuleKind.DAILY_EXPENSE)
    ) == 0


def test_readonly_stream_failure_preserves_its_last_successful_snapshot(
    tmp_path: Path,
) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    sync_readonly_modules(
        FakeReadonlyClient(),  # type: ignore[arg-type]
        storage=storage,
        observed_at=T1,
        started_at=T1,
        run_id="baseline",
    )
    result = sync_readonly_modules(
        FakeReadonlyClient(fail_payment=True, bizcase_count=1),  # type: ignore[arg-type]
        storage=storage,
        observed_at=T3,
        started_at=T3,
        run_id="partial",
    )

    assert result.status == "partial"
    assert [stream.status for stream in result.streams] == [
        "failed",
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.PAYMENT)) == 1
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.BIZCASE)) == 1
    failed_run = storage.get_readonly_run("partial-payment")
    assert failed_run is not None
    assert failed_run["status"] == "failed"
    assert failed_run["error_type"] == "RuntimeError"


def test_fee_stream_failure_preserves_only_its_previous_checkpoint(
    tmp_path: Path,
) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    modules = (
        ReadonlyModuleKind.TRAVEL_REIMBURSEMENT,
        ReadonlyModuleKind.TRAVEL_SUBSIDY,
    )
    sync_readonly_modules(
        FakeReadonlyClient(),  # type: ignore[arg-type]
        storage=storage,
        modules=modules,
        observed_at=T1,
        started_at=T1,
        run_id="fee-baseline",
    )

    result = sync_readonly_modules(
        FakeReadonlyClient(
            travel_reimbursement_count=1,
            fail_travel_subsidy=True,
        ),  # type: ignore[arg-type]
        storage=storage,
        modules=modules,
        observed_at=T3,
        started_at=T3,
        run_id="fee-partial",
    )

    assert result.status == "partial"
    assert [stream.status for stream in result.streams] == ["succeeded", "failed"]
    assert len(
        storage.current_readonly_snapshots(ReadonlyModuleKind.TRAVEL_REIMBURSEMENT)
    ) == 1
    assert len(
        storage.current_readonly_snapshots(ReadonlyModuleKind.TRAVEL_SUBSIDY)
    ) == 2
    failed_run = storage.get_readonly_run("fee-partial-travel_subsidy")
    assert failed_run is not None
    assert failed_run["status"] == "failed"


def test_readonly_changed_count_includes_updates_and_removals(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    sync_readonly_modules(
        FakeReadonlyClient(),  # type: ignore[arg-type]
        storage=storage,
        observed_at=T1,
        started_at=T1,
        run_id="baseline",
    )
    changed = sync_readonly_modules(
        FakeReadonlyClient(payment_status="审批拒绝", bizcase_count=1),  # type: ignore[arg-type]
        storage=storage,
        observed_at=T2,
        started_at=T2,
        run_id="changed",
    )

    assert changed.changed_count == 2
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.BIZCASE)) == 1
