"""Independent Payment/BizCase checkpoint and failure-isolation tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from isstech_replay.models.bizcase import BizCaseListResult, BizCaseRecord
from isstech_replay.models.payment import PaymentListResult, PaymentRecord
from isstech_replay.models.readonly_modules import ReadonlyModuleKind
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
    submitted_or_managed_count: int = 0,
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
        submitted_or_managed_ids=tuple(
            record.id for record in records[:submitted_or_managed_count]
        ),
    )


class FakeReadonlyClient:
    def __init__(
        self,
        *,
        payment_status: str = "已保存",
        bizcase_count: int = 2,
        bizcase_submitted_or_managed_count: int = 1,
        fail_payment: bool = False,
        fail_bizcase: bool = False,
        fail_identity: bool = False,
    ) -> None:
        self.payment_status = payment_status
        self.bizcase_count = bizcase_count
        self.bizcase_submitted_or_managed_count = (
            bizcase_submitted_or_managed_count
        )
        self.fail_payment = fail_payment
        self.fail_bizcase = fail_bizcase
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

    def list_personal_bizcases(self, *, max_pages: int) -> BizCaseListResult:
        assert max_pages == 20
        if self.fail_bizcase:
            raise RuntimeError("bizcase unavailable")
        return _bizcase_result(
            count=self.bizcase_count,
            submitted_or_managed_count=self.bizcase_submitted_or_managed_count,
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
    assert first.observed_count == 3
    assert first.changed_count == 3
    assert second.status == "succeeded"
    assert second.observed_count == 3
    assert second.changed_count == 0
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.PAYMENT)) == 1
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.BIZCASE)) == 2
    assert storage.table_count("readonly_module_runs") == 4
    assert storage.table_count("readonly_module_snapshots") == 6
    assert storage.table_count("readonly_module_current") == 3
    payment_payload = json.loads(
        storage.current_readonly_snapshots(ReadonlyModuleKind.PAYMENT)[0].payload_json
    )
    assert payment_payload["payment_no"] == "PAYMENT-REDACTED-1"
    assert payment_payload["scope_reasons"] == ["submitted_by_me"]
    bizcase_payloads = [
        json.loads(snapshot.payload_json)
        for snapshot in storage.current_readonly_snapshots(ReadonlyModuleKind.BIZCASE)
    ]
    assert [payload["submitted_or_managed"] for payload in bizcase_payloads] == [
        True,
        False,
    ]


def test_bizcase_snapshots_mark_only_exact_personal_projects() -> None:
    snapshots = readonly_snapshots(
        ReadonlyModuleKind.BIZCASE,
        _bizcase_result(
            project_numbers=(" PROJECT-1 ", "PROJECT-OTHER"),
            submitted_or_managed_count=2,
        ),
        observed_at="2026-07-16T01:00:00+00:00",
        project_numbers=("PROJECT-1",),
    )

    payloads = [json.loads(snapshot.payload_json) for snapshot in snapshots]
    assert payloads[0]["scope_reasons"] == ["my_project"]
    assert payloads[1]["scope_reasons"] == []
    assert payloads[0]["submitted_or_managed"] is True
    assert payloads[1]["submitted_or_managed"] is True


def test_bizcase_snapshots_reject_unknown_joint_evidence_identity() -> None:
    result = _bizcase_result(count=1)
    invalid = replace(
        result,
        submitted_or_managed_ids=("BC-REDACTED-999-V001",),
    )

    with pytest.raises(RuntimeError, match="joint evidence identity"):
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
    assert [stream.status for stream in result.streams] == ["failed", "succeeded"]
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.PAYMENT)) == 0
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.BIZCASE)) == 2


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
    assert [stream.status for stream in result.streams] == ["failed", "succeeded"]
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.PAYMENT)) == 1
    assert len(storage.current_readonly_snapshots(ReadonlyModuleKind.BIZCASE)) == 1
    failed_run = storage.get_readonly_run("partial-payment")
    assert failed_run is not None
    assert failed_run["status"] == "failed"
    assert failed_run["error_type"] == "RuntimeError"


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
