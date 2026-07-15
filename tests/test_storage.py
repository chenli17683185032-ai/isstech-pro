"""SQLite snapshots remain versioned, atomic, ordered, and event-idempotent."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from isstech_replay.models.work_items import (
    ChangeKind,
    WorkItemRelation,
    WorkflowKind,
    WorkflowSnapshot,
)
from isstech_replay.storage import (
    SCHEMA_VERSION,
    SnapshotOrderError,
    UnsupportedSchemaVersion,
    WorkflowStorage,
    cached_workflow_detail,
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


def _with_payload(
    snapshot: WorkflowSnapshot,
    payload: dict[str, object],
) -> WorkflowSnapshot:
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return replace(
        snapshot,
        payload_json=payload_json,
        payload_hash=hashlib.sha256(payload_json.encode()).hexdigest(),
    )


def test_schema_initializes_once_with_restrictive_permissions(tmp_path: Path) -> None:
    database = tmp_path / "state" / "workflow.sqlite3"
    storage = WorkflowStorage(database)
    assert storage.schema_version() == SCHEMA_VERSION
    assert database.stat().st_mode & 0o777 == 0o600
    assert storage.table_count("sync_runs") == 0
    assert storage.schema_version() == SCHEMA_VERSION


def test_fresh_workspace_readers_initialize_schema_concurrently(tmp_path: Path) -> None:
    database = tmp_path / "state" / "workflow.sqlite3"
    workers = 8
    barrier = threading.Barrier(workers)

    def read_empty_workspace(index: int) -> tuple[object, ...]:
        storage = WorkflowStorage(database)
        barrier.wait(timeout=5)
        readers = (
            storage.list_runs,
            storage.current_snapshots,
            storage.list_extractions,
            storage.list_workflow_draft_summaries,
        )
        return readers[index % len(readers)]()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = tuple(executor.map(read_empty_workspace, range(workers)))

    assert results == ((),) * workers
    assert WorkflowStorage(database).schema_version() == SCHEMA_VERSION


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


def test_successful_empty_measurement_preserves_sync_freshness(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    _apply(storage, "run-empty", T1, ())

    assert storage.current_snapshots(actionable_only=True) == ()
    assert storage.latest_successful_observed_at() == T1
    assert storage.latest_successful_run()["run_id"] == "run-empty"  # type: ignore[index]


def test_current_snapshot_lookup_is_scoped_by_adapter_and_external_id(
    tmp_path: Path,
) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    _apply(storage, "run-1", T1, (_snapshot(T1, external_id="owned-1"),))

    found = storage.get_current_snapshot(
        WorkflowKind.PURCHASE_REQUISITION,
        "owned-1",
    )

    assert found is not None
    assert found.external_id == "owned-1"
    assert (
        storage.get_current_snapshot(
            WorkflowKind.PURCHASE_REQUISITION,
            "missing",
        )
        is None
    )


def test_payload_v2_restores_relations_and_cached_detail(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    snapshot = _with_payload(
        _snapshot(T1, external_id="participant-1"),
        {
            "payload_version": 2,
            "relations": ["applicant", "project_manager", "unknown", "applicant"],
            "detail": {
                "fields": {"PR_PrjName": "REDACTED PROJECT"},
                "html_title": "REDACTED DETAIL",
                "approval_steps": [
                    {
                        "sequence": "1",
                        "timestamp": "2026-07-01 09:00",
                        "approver_name": "USER_REQUESTER",
                        "role": "项目经理",
                        "action": "提交",
                        "comment": "REDACTED",
                    }
                ],
            },
        },
    )
    _apply(storage, "v2-run", T1, (snapshot,))

    restored = storage.get_current_snapshot(
        WorkflowKind.PURCHASE_REQUISITION,
        "participant-1",
    )
    assert restored is not None
    assert restored.relations == (
        WorkItemRelation.APPLICANT,
        WorkItemRelation.PROJECT_MANAGER,
    )
    detail = cached_workflow_detail(restored)
    assert detail is not None
    assert detail.fields == {"PR_PrjName": "REDACTED PROJECT"}
    assert detail.html_title == "REDACTED DETAIL"
    assert detail.approval_steps[0].action == "提交"


def test_payload_v1_remains_readable_without_relations_or_cached_detail(
    tmp_path: Path,
) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    _apply(storage, "v1-run", T1, (_snapshot(T1),))

    restored = storage.current_snapshots()[0]

    assert restored.relations == ()
    assert cached_workflow_detail(restored) is None


def test_complete_measurement_reconciles_current_without_deleting_history(
    tmp_path: Path,
) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    _apply(
        storage,
        "run-1",
        T1,
        (
            _snapshot(T1, external_id="1"),
            _snapshot(T1, external_id="2"),
        ),
    )

    _apply(storage, "run-2", T2, (_snapshot(T2, external_id="2"),))

    assert [snapshot.external_id for snapshot in storage.current_snapshots()] == ["2"]
    assert storage.table_count("workflow_snapshots") == 3


def test_successful_empty_measurement_clears_adapter_current(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    _apply(storage, "run-1", T1, (_snapshot(T1),))

    _apply(storage, "run-empty", T2, ())

    assert storage.current_snapshots() == ()
    assert storage.table_count("workflow_snapshots") == 1


def test_source_candidate_count_may_exceed_owned_snapshot_count(tmp_path: Path) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    snapshot = _snapshot(T1, external_id="owned-1")
    storage.start_run(
        run_id="filtered-run",
        adapter=WorkflowKind.PURCHASE_REQUISITION,
        started_at=T1,
        max_pages=20,
    )

    storage.complete_run(
        run_id="filtered-run",
        observed_at=T1,
        finished_at=T1,
        source_total_count=78,
        snapshots=(snapshot,),
        actionable_count=1,
    )

    run = storage.get_run("filtered-run")
    assert run is not None
    assert run["source_total_count"] == 78
    assert run["observed_count"] == 1


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


def test_version_one_database_migrates_without_losing_runs(tmp_path: Path) -> None:
    database = tmp_path / "version-one.sqlite3"
    connection = sqlite3.connect(database)
    schema = (
        Path(__file__).parents[1] / "src" / "isstech_replay" / "schema.sql"
    ).read_text(encoding="utf-8")
    connection.executescript(schema)
    connection.execute(
        "INSERT INTO sync_runs "
        "(run_id, adapter, status, started_at, max_pages) "
        "VALUES ('existing-run', 'purchase_requisition', 'running', ?, 20)",
        (T1,),
    )
    connection.commit()
    connection.close()

    storage = WorkflowStorage(database)
    assert storage.schema_version() == SCHEMA_VERSION
    assert storage.get_run("existing-run")["status"] == "running"  # type: ignore[index]
    assert storage.table_count("materials") == 0
    assert storage.table_count("extraction_runs") == 0


def test_version_two_database_migrates_without_losing_materials(tmp_path: Path) -> None:
    database = tmp_path / "version-two.sqlite3"
    package_root = Path(__file__).parents[1] / "src" / "isstech_replay"
    connection = sqlite3.connect(database)
    connection.executescript((package_root / "schema.sql").read_text(encoding="utf-8"))
    connection.executescript(
        (package_root / "migration_002_materials.sql").read_text(encoding="utf-8")
    )
    sha256 = "a" * 64
    connection.execute(
        "INSERT INTO material_blobs "
        "(sha256, size_bytes, original_path, detected_mime_type, created_at) "
        "VALUES (?, 8, ?, 'text/plain', ?)",
        (sha256, f"materials/originals/{sha256}/blob", T1),
    )
    connection.execute(
        "INSERT INTO materials "
        "(material_id, sha256, original_name, declared_mime_type, "
        "detected_mime_type, extension, ingest_status, review_reason, created_at) "
        "VALUES ('existing-material', ?, 'existing.txt', 'text/plain', "
        "'text/plain', '.txt', 'ready', '', ?)",
        (sha256, T1),
    )
    connection.commit()
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    connection.close()

    storage = WorkflowStorage(database)

    assert storage.schema_version() == SCHEMA_VERSION
    material = storage.get_material("existing-material")
    assert material is not None
    assert material.original_name == "existing.txt"
    assert storage.table_count("materials") == 1
    assert storage.table_count("extraction_runs") == 0


def test_version_three_database_migrates_without_losing_extractions(tmp_path: Path) -> None:
    database = tmp_path / "version-three.sqlite3"
    package_root = Path(__file__).parents[1] / "src" / "isstech_replay"
    connection = sqlite3.connect(database)
    for script in (
        "schema.sql",
        "migration_002_materials.sql",
        "migration_003_extraction.sql",
    ):
        connection.executescript((package_root / script).read_text(encoding="utf-8"))
    sha256 = "b" * 64
    connection.execute(
        "INSERT INTO material_blobs "
        "(sha256, size_bytes, original_path, detected_mime_type, created_at) "
        "VALUES (?, 8, ?, 'text/plain', ?)",
        (sha256, f"materials/originals/{sha256}/blob", T1),
    )
    connection.execute(
        "INSERT INTO materials "
        "(material_id, sha256, original_name, declared_mime_type, "
        "detected_mime_type, extension, ingest_status, review_reason, created_at) "
        "VALUES ('material-v3', ?, 'existing.txt', 'text/plain', "
        "'text/plain', '.txt', 'ready', '', ?)",
        (sha256, T1),
    )
    connection.execute(
        "INSERT INTO extraction_runs "
        "(extraction_id, material_id, profile, provider, model, extractor_version, "
        "status, confidence_threshold, can_advance, started_at, finished_at, "
        "field_count) VALUES ('extraction-v3', 'material-v3', "
        "'purchase_requisition', 'local_rules', 'label_value_lines', 'version-1', "
        "'succeeded', 0.85, 1, ?, ?, 1)",
        (T1, T2),
    )
    connection.execute(
        "INSERT INTO extracted_fields "
        "(extraction_id, field_name, proposed_value, confidence, required, "
        "evidence_valid) VALUES "
        "('extraction-v3', 'PR_PrjNo', 'PRJ-001', 0.98, 1, 1)"
    )
    connection.commit()
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    connection.close()

    storage = WorkflowStorage(database)

    assert storage.schema_version() == SCHEMA_VERSION
    extraction = storage.get_extraction("extraction-v3")
    assert extraction is not None
    assert extraction["status"] == "succeeded"
    assert len(extraction["fields"]) == 1
    assert storage.table_count("workflow_drafts") == 0
    assert storage.table_count("draft_audit_events") == 0
