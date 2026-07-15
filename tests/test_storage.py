"""SQLite snapshots remain versioned, atomic, ordered, and event-idempotent."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from isstech_replay.models.work_items import ChangeKind, WorkflowKind, WorkflowSnapshot
from isstech_replay.storage import (
    SCHEMA_VERSION,
    SnapshotOrderError,
    UnsupportedSchemaVersion,
    WorkflowStorage,
)


T1 = "2026-07-15T01:00:00+00:00"
T2 = "2026-07-15T02:00:00+00:00"
T3 = "2026-07-15T03:00:00+00:00"


def _snapshot(
    observed_at: str,
    *,
    external_id: str = "1",
    status: str = "审批中",
    node: str = "NODE_A",
    approver: str = "USER_A",
    active: bool = True,
    actionable: bool = True,
) -> WorkflowSnapshot:
    payload = {
        "actionable": actionable,
        "active": active,
        "approver": approver,
        "external_id": external_id,
        "node": node,
        "status": status,
    }
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    return WorkflowSnapshot(
        adapter=WorkflowKind.PURCHASE_REQUISITION,
        external_id=external_id,
        observed_at=observed_at,
        reference_no=f"REF-{external_id}",
        project_no="PROJECT-REDACTED",
        title="REDACTED PROJECT",
        applicant="USER_REQUESTER",
        submitted_at="2026-07-01",
        status=status,
        current_node=node,
        current_approver=approver,
        waiting_days=14,
        source_url=(
            f"http://ipsapro.isstech.com/WebTP/PurchaseRequisition/Detail/{external_id}"
        ),
        active=active,
        actionable=actionable,
        payload_json=payload_json,
        payload_hash=payload_hash,
    )


def _apply(
    storage: WorkflowStorage,
    run_id: str,
    observed_at: str,
    snapshots: tuple[WorkflowSnapshot, ...],
):
    storage.start_run(
        run_id=run_id,
        adapter=WorkflowKind.PURCHASE_REQUISITION,
        started_at=observed_at,
        max_pages=20,
    )
    return storage.complete_run(
        run_id=run_id,
        observed_at=observed_at,
        finished_at=observed_at,
        source_total_count=len(snapshots),
        snapshots=snapshots,
        actionable_count=sum(snapshot.actionable for snapshot in snapshots),
    )


def test_schema_initializes_once_with_restrictive_permissions(tmp_path: Path) -> None:
    database = tmp_path / "state" / "workflow.sqlite3"
    storage = WorkflowStorage(database)
    assert storage.schema_version() == SCHEMA_VERSION
    assert database.stat().st_mode & 0o777 == 0o600
    assert storage.table_count("sync_runs") == 0
    assert storage.schema_version() == SCHEMA_VERSION


def test_new_snapshot_then_unchanged_replay_has_no_duplicate_event(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    first = _apply(storage, "run-1", T1, (_snapshot(T1),))
    second = _apply(storage, "run-2", T2, (_snapshot(T2),))

    assert [event.kind for event in first.events] == [ChangeKind.NEW]
    assert second.events == ()
    assert storage.table_count("sync_runs") == 2
    assert storage.table_count("workflow_snapshots") == 2
    assert storage.table_count("workflow_current") == 1
    assert storage.table_count("workflow_events") == 1


def test_exact_observation_replay_reuses_history_without_events(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    _apply(storage, "run-1", T1, (_snapshot(T1),))
    replay = _apply(storage, "run-2", T1, (_snapshot(T1),))

    assert replay.history_rows_inserted == 0
    assert replay.events == ()
    assert storage.table_count("workflow_snapshots") == 1
    assert storage.get_run("run-2")["status"] == "succeeded"  # type: ignore[index]


def test_node_change_produces_exactly_one_node_event(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    _apply(storage, "run-1", T1, (_snapshot(T1),))
    changed = _apply(storage, "run-2", T2, (_snapshot(T2, node="NODE_B"),))

    assert len(changed.events) == 1
    assert changed.events[0].kind is ChangeKind.NODE_CHANGED
    assert changed.events[0].old_value == "NODE_A"
    assert changed.events[0].new_value == "NODE_B"


def test_assignee_change_produces_one_assignee_event(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    _apply(storage, "run-1", T1, (_snapshot(T1),))
    changed = _apply(storage, "run-2", T2, (_snapshot(T2, approver="USER_B"),))

    assert [event.kind for event in changed.events] == [ChangeKind.ASSIGNEE_CHANGED]


def test_active_to_terminal_produces_completed_without_noise(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    _apply(storage, "run-1", T1, (_snapshot(T1),))
    completed = _apply(
        storage,
        "run-2",
        T2,
        (
            _snapshot(
                T2,
                status="已完成",
                node="已完成",
                approver="",
                active=False,
                actionable=False,
            ),
        ),
    )

    assert [event.kind for event in completed.events] == [ChangeKind.COMPLETED]
    current = storage.current_snapshots()
    assert len(current) == 1
    assert current[0].active is False
    assert storage.current_snapshots(actionable_only=True) == ()


def test_snapshot_transaction_rolls_back_before_failed_run_record(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    valid = _snapshot(T1, external_id="1")
    invalid = replace(_snapshot(T1, external_id="2"), payload_hash="not-a-hash")
    storage.start_run(
        run_id="run-failed",
        adapter=WorkflowKind.PURCHASE_REQUISITION,
        started_at=T1,
        max_pages=20,
    )

    with pytest.raises(ValueError, match="payload_hash"):
        storage.complete_run(
            run_id="run-failed",
            observed_at=T1,
            finished_at=T1,
            source_total_count=2,
            snapshots=(valid, invalid),
            actionable_count=2,
        )
    assert storage.table_count("workflow_snapshots") == 0
    assert storage.table_count("workflow_current") == 0
    assert storage.table_count("workflow_events") == 0

    storage.fail_run(
        run_id="run-failed",
        finished_at=T1,
        error_type="ValueError",
        error_message="REDACTED test failure",
    )
    run = storage.get_run("run-failed")
    assert run is not None
    assert run["status"] == "failed"


def test_stale_snapshot_cannot_replace_current_state(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    _apply(storage, "run-new", T2, (_snapshot(T2),))
    storage.start_run(
        run_id="run-stale",
        adapter=WorkflowKind.PURCHASE_REQUISITION,
        started_at=T3,
        max_pages=20,
    )
    with pytest.raises(SnapshotOrderError):
        storage.complete_run(
            run_id="run-stale",
            observed_at=T1,
            finished_at=T3,
            source_total_count=1,
            snapshots=(_snapshot(T1, node="OLD_NODE"),),
            actionable_count=1,
        )
    assert storage.current_snapshots()[0].current_node == "NODE_A"


def test_future_schema_version_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 99")
    connection.close()

    with pytest.raises(UnsupportedSchemaVersion, match="newer than supported"):
        WorkflowStorage(database).initialize()


def test_incomplete_current_schema_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "incomplete.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sync_runs (run_id TEXT PRIMARY KEY)")
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.close()

    with pytest.raises(UnsupportedSchemaVersion, match="schema is incomplete"):
        WorkflowStorage(database).initialize()
