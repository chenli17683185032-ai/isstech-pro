"""Versioned SQLite storage for workflow measurements and change events."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from .models.drafts import (
    DraftAuditEvent,
    DraftCreateResult,
    DraftField,
    DraftState,
    ReviewDecision,
    WorkflowDraft,
)
from .models.extraction import (
    ExtractionResult,
    ExtractionStatus,
    FieldEvidence,
    FieldIssue,
    FieldSpec,
    SourceKind,
)
from .models.materials import Material, MaterialArtifact, MaterialStatus
from .models.purchase import PurchaseApprovalStep
from .models.readonly_modules import ReadonlyModuleKind, ReadonlySnapshot
from .models.work_items import (
    ChangeEvent,
    ChangeKind,
    WorkItemRelation,
    WorkItemScopeReason,
    WorkflowKind,
    WorkflowSnapshot,
)


SCHEMA_VERSION = 6
DEFAULT_DATA_DIR = Path("data")
DEFAULT_DATABASE_NAME = "workflow-center.sqlite3"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_WORKFLOW_TABLES = {
    "sync_runs",
    "workflow_snapshots",
    "workflow_current",
    "workflow_events",
}
_MATERIAL_TABLES = {"material_blobs", "materials", "material_artifacts"}
_EXTRACTION_TABLES = {"extraction_runs", "extracted_fields"}
_DRAFT_TABLES = {"workflow_drafts", "draft_fields", "draft_audit_events"}
_READONLY_MODULE_TABLES = {
    "readonly_module_runs",
    "readonly_module_snapshots",
    "readonly_module_current",
    "readonly_scope_assertions",
}
_REQUIRED_TABLES = (
    _WORKFLOW_TABLES
    | _MATERIAL_TABLES
    | _EXTRACTION_TABLES
    | _DRAFT_TABLES
    | _READONLY_MODULE_TABLES
)
_REQUIRED_TABLES_BY_VERSION = {
    1: _WORKFLOW_TABLES,
    2: _WORKFLOW_TABLES | _MATERIAL_TABLES,
    3: _WORKFLOW_TABLES | _MATERIAL_TABLES | _EXTRACTION_TABLES,
    4: _WORKFLOW_TABLES | _MATERIAL_TABLES | _EXTRACTION_TABLES | _DRAFT_TABLES,
    5: _REQUIRED_TABLES - {"readonly_scope_assertions"},
    6: _REQUIRED_TABLES,
}
_MIGRATIONS = {
    1: "migration_002_materials.sql",
    2: "migration_003_extraction.sql",
    3: "migration_004_review.sql",
    4: "migration_005_readonly_modules.sql",
    5: "migration_006_readonly_scope.sql",
}
_INITIALIZATION_THREAD_LOCK = threading.Lock()
_INITIALIZATION_LOCK_TIMEOUT_SECONDS = 10.0
_INITIALIZATION_LOCK_POLL_SECONDS = 0.05


class StorageError(RuntimeError):
    """Base class for workflow storage failures."""


class UnsupportedSchemaVersion(StorageError):
    """The database requires a migration this binary does not know."""


class SnapshotConflictError(StorageError):
    """The same observation key was reused with different state."""


class SnapshotOrderError(StorageError):
    """An older measurement attempted to replace a newer current state."""


class DraftVersionConflict(StorageError):
    """A stale reviewer attempted to overwrite a newer draft version."""


class DraftStateConflict(StorageError):
    """A draft action is invalid from its current state."""


@dataclass(frozen=True, slots=True)
class StorageApplyResult:
    history_rows_inserted: int
    events: tuple[ChangeEvent, ...]


@dataclass(frozen=True, slots=True)
class ReadonlyStorageApplyResult:
    history_rows_inserted: int
    changed_count: int


@dataclass(frozen=True, slots=True)
class CachedWorkflowDetail:
    fields: dict[str, str]
    html_title: str
    approval_steps: tuple[PurchaseApprovalStep, ...]
    approval_status: str


def _workflow_payload(payload_json: str) -> dict[str, object]:
    try:
        payload = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _payload_relations(payload: dict[str, object]) -> tuple[WorkItemRelation, ...]:
    if payload.get("payload_version") not in {2, 3}:
        return ()
    raw_relations = payload.get("relations")
    if not isinstance(raw_relations, list):
        return ()
    found: set[WorkItemRelation] = set()
    for value in raw_relations:
        if not isinstance(value, str):
            continue
        try:
            found.add(WorkItemRelation(value))
        except ValueError:
            continue
    return tuple(relation for relation in WorkItemRelation if relation in found)


def cached_workflow_detail(
    snapshot: WorkflowSnapshot,
) -> CachedWorkflowDetail | None:
    payload = _workflow_payload(snapshot.payload_json)
    if payload.get("payload_version") not in {2, 3}:
        return None
    detail = payload.get("detail")
    if not isinstance(detail, dict):
        return None
    raw_fields = detail.get("fields")
    html_title = detail.get("html_title")
    raw_steps = detail.get("approval_steps")
    approval_status = detail.get("approval_status")
    if (
        not isinstance(raw_fields, dict)
        or not isinstance(html_title, str)
        or not isinstance(raw_steps, list)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in raw_fields.items()
        )
    ):
        return None

    if approval_status not in {
        "available",
        "upstream_empty",
        "not_fetched",
        "fetch_failed",
    }:
        approval_status = "available" if raw_steps else "not_fetched"

    step_fields = (
        "sequence",
        "timestamp",
        "approver_name",
        "role",
        "action",
        "comment",
    )
    steps: list[PurchaseApprovalStep] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            return None
        values = {field: raw_step.get(field, "") for field in step_fields}
        if any(not isinstance(value, str) for value in values.values()):
            return None
        steps.append(PurchaseApprovalStep(**values))
    return CachedWorkflowDetail(
        fields=dict(raw_fields),
        html_title=html_title,
        approval_steps=tuple(steps),
        approval_status=approval_status,
    )


@contextmanager
def _schema_initialization_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".initialize.lock")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(lock_path, 0o600)
    deadline = time.monotonic() + _INITIALIZATION_LOCK_TIMEOUT_SECONDS
    acquired = False
    try:
        while not acquired:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise StorageError(
                        f"database schema initialization lock timed out: {path}"
                    ) from exc
                time.sleep(_INITIALIZATION_LOCK_POLL_SECONDS)
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def default_data_dir() -> Path:
    return Path(os.getenv("ISSTECH_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()


def default_database_path() -> Path:
    configured = os.getenv("ISSTECH_DATABASE_PATH")
    if configured:
        return Path(configured).expanduser()
    return default_data_dir() / DEFAULT_DATABASE_NAME


class WorkflowStorage:
    """Open short SQLite connections so CLI and local API can share one file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        os.chmod(self.path, 0o600)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with _INITIALIZATION_THREAD_LOCK:
            with _schema_initialization_lock(self.path):
                self._initialize_locked()

    def _initialize_locked(self) -> None:
        connection = self._connect()
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise UnsupportedSchemaVersion(
                    f"database schema version {version} is newer than supported "
                    f"version {SCHEMA_VERSION}"
                )
            if version == 0:
                existing = self._existing_tables(connection)
                if existing:
                    raise UnsupportedSchemaVersion(
                        "database has unversioned tables; refusing automatic initialization"
                    )
                schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
                connection.executescript(schema)
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])

            while version < SCHEMA_VERSION:
                self._verify_tables(connection, version)
                migration_name = _MIGRATIONS.get(version)
                if migration_name is None:
                    raise UnsupportedSchemaVersion(
                        f"no migration from schema version {version}"
                    )
                migration = Path(__file__).with_name(migration_name).read_text(
                    encoding="utf-8"
                )
                connection.executescript(migration)
                new_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if new_version <= version:
                    raise UnsupportedSchemaVersion(
                        f"migration {migration_name} did not advance schema version"
                    )
                version = new_version

            self._verify_tables(connection, SCHEMA_VERSION)
        finally:
            connection.close()

    @staticmethod
    def _existing_tables(connection: sqlite3.Connection) -> set[str]:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }

    @classmethod
    def _verify_tables(cls, connection: sqlite3.Connection, version: int) -> None:
        required = _REQUIRED_TABLES_BY_VERSION.get(version)
        if required is None:
            raise UnsupportedSchemaVersion(f"unknown schema version {version}")
        missing = sorted(required - cls._existing_tables(connection))
        if missing:
            raise UnsupportedSchemaVersion(
                "database schema is incomplete; missing: " + ", ".join(missing)
            )

    def schema_version(self) -> int:
        self.initialize()
        connection = self._connect()
        try:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])
        finally:
            connection.close()

    def start_run(
        self,
        *,
        run_id: str,
        adapter: WorkflowKind,
        started_at: str,
        max_pages: int,
    ) -> None:
        if not run_id:
            raise ValueError("run_id is required")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT INTO sync_runs "
                    "(run_id, adapter, status, started_at, max_pages) "
                    "VALUES (?, ?, 'running', ?, ?)",
                    (run_id, adapter.value, started_at, max_pages),
                )
        finally:
            connection.close()

    def fail_run(
        self,
        *,
        run_id: str,
        finished_at: str,
        error_type: str,
        error_message: str,
    ) -> None:
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "UPDATE sync_runs SET status = 'failed', finished_at = ?, "
                    "error_type = ?, error_message = ? "
                    "WHERE run_id = ? AND status = 'running'",
                    (finished_at, error_type[:120], error_message[:1000], run_id),
                )
                if cursor.rowcount != 1:
                    raise StorageError(f"run is missing or not running: {run_id}")
        finally:
            connection.close()

    def complete_run(
        self,
        *,
        run_id: str,
        observed_at: str,
        finished_at: str,
        source_total_count: int | None,
        snapshots: tuple[WorkflowSnapshot, ...],
        actionable_count: int,
    ) -> StorageApplyResult:
        self.initialize()
        connection = self._connect()
        events: list[ChangeEvent] = []
        history_rows_inserted = 0
        seen: set[tuple[str, str]] = set()
        try:
            with connection:
                run = connection.execute(
                    "SELECT adapter, status FROM sync_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if run is None or run["status"] != "running":
                    raise StorageError(f"run is missing or not running: {run_id}")
                if actionable_count != sum(snapshot.actionable for snapshot in snapshots):
                    raise StorageError("actionable_count does not match snapshots")
                if source_total_count is not None and source_total_count < len(snapshots):
                    raise StorageError(
                        f"source_total_count={source_total_count} is smaller than "
                        f"snapshot_count={len(snapshots)}"
                    )
                newer_current = connection.execute(
                    "SELECT external_id, last_seen_at FROM workflow_current "
                    "WHERE adapter = ? AND last_seen_at > ? LIMIT 1",
                    (run["adapter"], observed_at),
                ).fetchone()
                if newer_current is not None:
                    raise SnapshotOrderError(
                        f"measurement {observed_at} predates current "
                        f"{newer_current['last_seen_at']} for "
                        f"{run['adapter']}/{newer_current['external_id']}"
                    )

                for snapshot in snapshots:
                    self._validate_snapshot(snapshot, observed_at=observed_at)
                    adapter = snapshot.adapter.value
                    if adapter != run["adapter"]:
                        raise StorageError(
                            f"snapshot adapter {adapter} does not match run {run['adapter']}"
                        )
                    identity = (adapter, snapshot.external_id)
                    if identity in seen:
                        raise SnapshotConflictError(
                            f"duplicate snapshot in one run: {adapter}/{snapshot.external_id}"
                        )
                    seen.add(identity)

                    current = connection.execute(
                        "SELECT * FROM workflow_current "
                        "WHERE adapter = ? AND external_id = ?",
                        identity,
                    ).fetchone()
                    if current is not None and snapshot.observed_at < current["last_seen_at"]:
                        raise SnapshotOrderError(
                            f"snapshot {snapshot.observed_at} predates current "
                            f"{current['last_seen_at']} for {adapter}/{snapshot.external_id}"
                        )

                    existing = connection.execute(
                        "SELECT payload_hash FROM workflow_snapshots "
                        "WHERE adapter = ? AND external_id = ? AND observed_at = ?",
                        (adapter, snapshot.external_id, snapshot.observed_at),
                    ).fetchone()
                    if existing is not None and existing["payload_hash"] != snapshot.payload_hash:
                        raise SnapshotConflictError(
                            "same adapter/external_id/observed_at has a different payload"
                        )
                    if existing is None:
                        self._insert_snapshot(connection, run_id, snapshot)
                        history_rows_inserted += 1

                    derived = self._derive_events(current, snapshot)
                    for event in derived:
                        self._insert_event(connection, run_id, current, snapshot, event)
                        events.append(event)
                    self._upsert_current(connection, run_id, snapshot)

                current_ids = [external_id for _, external_id in seen]
                if current_ids:
                    placeholders = ",".join("?" for _ in current_ids)
                    connection.execute(
                        "DELETE FROM workflow_current WHERE adapter = ? "
                        f"AND external_id NOT IN ({placeholders})",
                        (run["adapter"], *current_ids),
                    )
                else:
                    connection.execute(
                        "DELETE FROM workflow_current WHERE adapter = ?",
                        (run["adapter"],),
                    )

                cursor = connection.execute(
                    "UPDATE sync_runs SET status = 'succeeded', observed_at = ?, "
                    "finished_at = ?, source_total_count = ?, observed_count = ?, "
                    "actionable_count = ?, snapshot_count = ?, history_rows_inserted = ?, "
                    "event_count = ?, error_type = NULL, error_message = NULL "
                    "WHERE run_id = ? AND status = 'running'",
                    (
                        observed_at,
                        finished_at,
                        source_total_count,
                        len(snapshots),
                        actionable_count,
                        len(snapshots),
                        history_rows_inserted,
                        len(events),
                        run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StorageError(f"run completion lost state: {run_id}")
        finally:
            connection.close()
        return StorageApplyResult(
            history_rows_inserted=history_rows_inserted,
            events=tuple(events),
        )

    @staticmethod
    def _validate_snapshot(snapshot: WorkflowSnapshot, *, observed_at: str) -> None:
        if not snapshot.external_id.strip():
            raise ValueError("snapshot external_id is required")
        if snapshot.observed_at != observed_at:
            raise ValueError("all snapshots in a run must share observed_at")
        if not _HASH_RE.fullmatch(snapshot.payload_hash):
            raise ValueError("snapshot payload_hash must be lowercase SHA-256")
        actual_hash = hashlib.sha256(snapshot.payload_json.encode("utf-8")).hexdigest()
        if actual_hash != snapshot.payload_hash:
            raise ValueError("snapshot payload_hash does not match payload_json")
        try:
            payload = json.loads(snapshot.payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError("snapshot payload_json is invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("snapshot payload_json must be an object")

    @staticmethod
    def _insert_snapshot(
        connection: sqlite3.Connection,
        run_id: str,
        snapshot: WorkflowSnapshot,
    ) -> None:
        connection.execute(
            "INSERT INTO workflow_snapshots "
            "(run_id, adapter, external_id, observed_at, reference_no, project_no, "
            "title, applicant, submitted_at, status, current_node, current_approver, "
            "waiting_days, source_url, active, actionable, payload_json, payload_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                snapshot.adapter.value,
                snapshot.external_id,
                snapshot.observed_at,
                snapshot.reference_no,
                snapshot.project_no,
                snapshot.title,
                snapshot.applicant,
                snapshot.submitted_at,
                snapshot.status,
                snapshot.current_node,
                snapshot.current_approver,
                snapshot.waiting_days,
                snapshot.source_url,
                int(snapshot.active),
                int(snapshot.actionable),
                snapshot.payload_json,
                snapshot.payload_hash,
            ),
        )

    @staticmethod
    def _derive_events(
        current: sqlite3.Row | None,
        snapshot: WorkflowSnapshot,
    ) -> tuple[ChangeEvent, ...]:
        adapter = snapshot.adapter
        common = {
            "adapter": adapter,
            "external_id": snapshot.external_id,
            "observed_at": snapshot.observed_at,
        }
        if current is None:
            return (
                ChangeEvent(
                    kind=ChangeKind.NEW,
                    old_value=None,
                    new_value=snapshot.status,
                    details={
                        "status": snapshot.status,
                        "current_node": snapshot.current_node,
                        "current_approver": snapshot.current_approver,
                    },
                    **common,
                ),
            )
        if current["payload_hash"] == snapshot.payload_hash:
            return ()

        if bool(current["active"]) and not snapshot.active:
            return (
                ChangeEvent(
                    kind=ChangeKind.COMPLETED,
                    old_value=current["status"],
                    new_value=snapshot.status,
                    details={
                        "old_status": current["status"],
                        "new_status": snapshot.status,
                    },
                    **common,
                ),
            )

        events: list[ChangeEvent] = []
        if current["current_node"] != snapshot.current_node:
            events.append(
                ChangeEvent(
                    kind=ChangeKind.NODE_CHANGED,
                    old_value=current["current_node"],
                    new_value=snapshot.current_node,
                    details={
                        "old_node": current["current_node"],
                        "new_node": snapshot.current_node,
                    },
                    **common,
                )
            )
        if current["current_approver"] != snapshot.current_approver:
            events.append(
                ChangeEvent(
                    kind=ChangeKind.ASSIGNEE_CHANGED,
                    old_value=current["current_approver"],
                    new_value=snapshot.current_approver,
                    details={
                        "old_approver": current["current_approver"],
                        "new_approver": snapshot.current_approver,
                    },
                    **common,
                )
            )
        return tuple(events)

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        run_id: str,
        current: sqlite3.Row | None,
        snapshot: WorkflowSnapshot,
        event: ChangeEvent,
    ) -> None:
        connection.execute(
            "INSERT INTO workflow_events "
            "(run_id, adapter, external_id, event_type, observed_at, old_value, "
            "new_value, details_json, old_payload_hash, new_payload_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                event.adapter.value,
                event.external_id,
                event.kind.value,
                event.observed_at,
                event.old_value,
                event.new_value,
                json.dumps(event.details, ensure_ascii=False, sort_keys=True),
                current["payload_hash"] if current is not None else None,
                snapshot.payload_hash,
            ),
        )

    @staticmethod
    def _upsert_current(
        connection: sqlite3.Connection,
        run_id: str,
        snapshot: WorkflowSnapshot,
    ) -> None:
        connection.execute(
            "INSERT INTO workflow_current "
            "(adapter, external_id, first_seen_at, last_seen_at, last_run_id, "
            "reference_no, project_no, title, applicant, submitted_at, status, "
            "current_node, current_approver, waiting_days, source_url, active, "
            "actionable, payload_json, payload_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(adapter, external_id) DO UPDATE SET "
            "last_seen_at = excluded.last_seen_at, last_run_id = excluded.last_run_id, "
            "reference_no = excluded.reference_no, project_no = excluded.project_no, "
            "title = excluded.title, applicant = excluded.applicant, "
            "submitted_at = excluded.submitted_at, status = excluded.status, "
            "current_node = excluded.current_node, "
            "current_approver = excluded.current_approver, "
            "waiting_days = excluded.waiting_days, source_url = excluded.source_url, "
            "active = excluded.active, actionable = excluded.actionable, "
            "payload_json = excluded.payload_json, payload_hash = excluded.payload_hash",
            (
                snapshot.adapter.value,
                snapshot.external_id,
                snapshot.observed_at,
                snapshot.observed_at,
                run_id,
                snapshot.reference_no,
                snapshot.project_no,
                snapshot.title,
                snapshot.applicant,
                snapshot.submitted_at,
                snapshot.status,
                snapshot.current_node,
                snapshot.current_approver,
                snapshot.waiting_days,
                snapshot.source_url,
                int(snapshot.active),
                int(snapshot.actionable),
                snapshot.payload_json,
                snapshot.payload_hash,
            ),
        )

    def start_readonly_run(
        self,
        *,
        run_id: str,
        module: ReadonlyModuleKind,
        started_at: str,
        max_pages: int,
    ) -> None:
        if not run_id:
            raise ValueError("run_id is required")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT INTO readonly_module_runs "
                    "(run_id, module, status, started_at, max_pages) "
                    "VALUES (?, ?, 'running', ?, ?)",
                    (run_id, module.value, started_at, max_pages),
                )
        finally:
            connection.close()

    def fail_readonly_run(
        self,
        *,
        run_id: str,
        finished_at: str,
        error_type: str,
        error_message: str,
    ) -> None:
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "UPDATE readonly_module_runs SET status = 'failed', finished_at = ?, "
                    "error_type = ?, error_message = ? "
                    "WHERE run_id = ? AND status = 'running'",
                    (finished_at, error_type[:120], error_message[:1000], run_id),
                )
                if cursor.rowcount != 1:
                    raise StorageError(f"readonly run is missing or not running: {run_id}")
        finally:
            connection.close()

    def complete_readonly_run(
        self,
        *,
        run_id: str,
        observed_at: str,
        finished_at: str,
        source_total_count: int,
        snapshots: tuple[ReadonlySnapshot, ...],
    ) -> ReadonlyStorageApplyResult:
        if source_total_count != len(snapshots):
            raise StorageError("readonly source_total_count must equal snapshot count")
        self.initialize()
        connection = self._connect()
        history_rows_inserted = 0
        changed_count = 0
        seen: set[str] = set()
        try:
            with connection:
                run = connection.execute(
                    "SELECT module, status FROM readonly_module_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if run is None or run["status"] != "running":
                    raise StorageError(f"readonly run is missing or not running: {run_id}")
                newer_current = connection.execute(
                    "SELECT external_id, last_seen_at FROM readonly_module_current "
                    "WHERE module = ? AND last_seen_at > ? LIMIT 1",
                    (run["module"], observed_at),
                ).fetchone()
                if newer_current is not None:
                    raise SnapshotOrderError(
                        f"measurement {observed_at} predates readonly current "
                        f"{newer_current['last_seen_at']} for "
                        f"{run['module']}/{newer_current['external_id']}"
                    )
                previous_rows = connection.execute(
                    "SELECT external_id, payload_hash FROM readonly_module_current "
                    "WHERE module = ?",
                    (run["module"],),
                ).fetchall()
                previous = {row["external_id"]: row["payload_hash"] for row in previous_rows}

                for snapshot in snapshots:
                    self._validate_readonly_snapshot(snapshot, observed_at=observed_at)
                    if snapshot.module.value != run["module"]:
                        raise StorageError(
                            f"readonly snapshot module {snapshot.module.value} does not match "
                            f"run {run['module']}"
                        )
                    if snapshot.external_id in seen:
                        raise SnapshotConflictError(
                            f"duplicate readonly snapshot: {run['module']}/{snapshot.external_id}"
                        )
                    seen.add(snapshot.external_id)
                    existing = connection.execute(
                        "SELECT payload_hash FROM readonly_module_snapshots "
                        "WHERE module = ? AND external_id = ? AND observed_at = ?",
                        (run["module"], snapshot.external_id, observed_at),
                    ).fetchone()
                    if existing is not None and existing["payload_hash"] != snapshot.payload_hash:
                        raise SnapshotConflictError(
                            "same readonly module/external_id/observed_at has a different payload"
                        )
                    if existing is None:
                        connection.execute(
                            "INSERT INTO readonly_module_snapshots "
                            "(run_id, module, external_id, observed_at, payload_json, payload_hash) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                run_id,
                                run["module"],
                                snapshot.external_id,
                                observed_at,
                                snapshot.payload_json,
                                snapshot.payload_hash,
                            ),
                        )
                        history_rows_inserted += 1
                    if previous.get(snapshot.external_id) != snapshot.payload_hash:
                        changed_count += 1
                    connection.execute(
                        "INSERT INTO readonly_module_current "
                        "(module, external_id, first_seen_at, last_seen_at, last_run_id, "
                        "payload_json, payload_hash) VALUES (?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(module, external_id) DO UPDATE SET "
                        "last_seen_at = excluded.last_seen_at, "
                        "last_run_id = excluded.last_run_id, "
                        "payload_json = excluded.payload_json, "
                        "payload_hash = excluded.payload_hash",
                        (
                            run["module"],
                            snapshot.external_id,
                            observed_at,
                            observed_at,
                            run_id,
                            snapshot.payload_json,
                            snapshot.payload_hash,
                        ),
                    )

                changed_count += len(set(previous) - seen)
                if seen:
                    placeholders = ",".join("?" for _ in seen)
                    connection.execute(
                        "DELETE FROM readonly_module_current WHERE module = ? "
                        f"AND external_id NOT IN ({placeholders})",
                        (run["module"], *sorted(seen)),
                    )
                else:
                    connection.execute(
                        "DELETE FROM readonly_module_current WHERE module = ?",
                        (run["module"],),
                    )
                cursor = connection.execute(
                    "UPDATE readonly_module_runs SET status = 'succeeded', observed_at = ?, "
                    "finished_at = ?, source_total_count = ?, observed_count = ?, "
                    "snapshot_count = ?, history_rows_inserted = ?, changed_count = ?, "
                    "error_type = NULL, error_message = NULL "
                    "WHERE run_id = ? AND status = 'running'",
                    (
                        observed_at,
                        finished_at,
                        source_total_count,
                        len(snapshots),
                        len(snapshots),
                        history_rows_inserted,
                        changed_count,
                        run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StorageError(f"readonly run completion lost state: {run_id}")
        finally:
            connection.close()
        return ReadonlyStorageApplyResult(
            history_rows_inserted=history_rows_inserted,
            changed_count=changed_count,
        )

    @staticmethod
    def _validate_readonly_snapshot(
        snapshot: ReadonlySnapshot,
        *,
        observed_at: str,
    ) -> None:
        if not snapshot.external_id.strip():
            raise ValueError("readonly snapshot external_id is required")
        if snapshot.observed_at != observed_at:
            raise ValueError("all readonly snapshots in a run must share observed_at")
        if not _HASH_RE.fullmatch(snapshot.payload_hash):
            raise ValueError("readonly snapshot payload_hash must be lowercase SHA-256")
        actual_hash = hashlib.sha256(snapshot.payload_json.encode("utf-8")).hexdigest()
        if actual_hash != snapshot.payload_hash:
            raise ValueError("readonly snapshot payload_hash does not match payload_json")
        try:
            payload = json.loads(snapshot.payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError("readonly snapshot payload_json is invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("readonly snapshot payload_json must be an object")

    def current_readonly_snapshots(
        self,
        module: ReadonlyModuleKind,
    ) -> tuple[ReadonlySnapshot, ...]:
        self.initialize()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM readonly_module_current WHERE module = ? "
                "ORDER BY external_id",
                (module.value,),
            ).fetchall()
            return tuple(
                ReadonlySnapshot(
                    module=module,
                    external_id=row["external_id"],
                    observed_at=row["last_seen_at"],
                    payload_json=row["payload_json"],
                    payload_hash=row["payload_hash"],
                )
                for row in rows
            )
        finally:
            connection.close()

    def upsert_readonly_scope_assertion(
        self,
        *,
        module: ReadonlyModuleKind,
        external_id: str,
        scope_reason: WorkItemScopeReason,
        confirmed_at: str,
    ) -> None:
        external_id = external_id.strip()
        if not external_id:
            raise ValueError("readonly scope assertion external_id is required")
        if not confirmed_at.strip():
            raise ValueError("readonly scope assertion confirmed_at is required")
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                current = connection.execute(
                    "SELECT 1 FROM readonly_module_current "
                    "WHERE module = ? AND external_id = ?",
                    (module.value, external_id),
                ).fetchone()
                if current is None:
                    raise ValueError(
                        "readonly scope assertion must reference a current object"
                    )
                connection.execute(
                    "INSERT INTO readonly_scope_assertions "
                    "(module, external_id, scope_reason, evidence_kind, confirmed_at) "
                    "VALUES (?, ?, ?, 'account_holder_confirmation', ?) "
                    "ON CONFLICT(module, external_id, scope_reason) DO UPDATE SET "
                    "evidence_kind = excluded.evidence_kind, "
                    "confirmed_at = excluded.confirmed_at",
                    (
                        module.value,
                        external_id,
                        scope_reason.value,
                        confirmed_at,
                    ),
                )
        finally:
            connection.close()

    def readonly_scope_assertions(
        self,
        module: ReadonlyModuleKind,
    ) -> dict[str, tuple[WorkItemScopeReason, ...]]:
        self.initialize()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT external_id, scope_reason FROM readonly_scope_assertions "
                "WHERE module = ? ORDER BY external_id, scope_reason",
                (module.value,),
            ).fetchall()
        finally:
            connection.close()
        found: dict[str, set[WorkItemScopeReason]] = {}
        for row in rows:
            reason = WorkItemScopeReason(str(row["scope_reason"]))
            found.setdefault(str(row["external_id"]), set()).add(reason)
        return {
            external_id: tuple(
                reason for reason in WorkItemScopeReason if reason in reasons
            )
            for external_id, reasons in found.items()
        }

    def delete_readonly_scope_assertion(
        self,
        *,
        module: ReadonlyModuleKind,
        external_id: str,
        scope_reason: WorkItemScopeReason,
    ) -> bool:
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM readonly_scope_assertions "
                    "WHERE module = ? AND external_id = ? AND scope_reason = ?",
                    (module.value, external_id.strip(), scope_reason.value),
                )
                return cursor.rowcount == 1
        finally:
            connection.close()

    def latest_readonly_successful_run(
        self,
        module: ReadonlyModuleKind,
    ) -> dict[str, object] | None:
        self.initialize()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM readonly_module_runs "
                "WHERE module = ? AND status = 'succeeded' "
                "ORDER BY started_at DESC, run_id DESC LIMIT 1",
                (module.value,),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def list_readonly_runs(self, *, limit: int = 20) -> tuple[dict[str, object], ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        self.initialize()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM readonly_module_runs "
                "ORDER BY started_at DESC, run_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return tuple(dict(row) for row in rows)
        finally:
            connection.close()

    def get_readonly_run(self, run_id: str) -> dict[str, object] | None:
        self.initialize()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM readonly_module_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def get_run(self, run_id: str) -> dict[str, object] | None:
        self.initialize()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM sync_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def list_runs(self, *, limit: int = 20) -> tuple[dict[str, object], ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        self.initialize()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM sync_runs ORDER BY started_at DESC, run_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return tuple(dict(row) for row in rows)
        finally:
            connection.close()

    def latest_successful_observed_at(self) -> str | None:
        self.initialize()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT observed_at FROM sync_runs "
                "WHERE status = 'succeeded' AND observed_at IS NOT NULL "
                "ORDER BY started_at DESC, run_id DESC LIMIT 1"
            ).fetchone()
            return str(row["observed_at"]) if row is not None else None
        finally:
            connection.close()

    def latest_successful_run(self) -> dict[str, object] | None:
        self.initialize()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM sync_runs WHERE status = 'succeeded' "
                "ORDER BY started_at DESC, run_id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def latest_successful_runs_by_adapter(self) -> dict[WorkflowKind, dict[str, object]]:
        """Return the newest complete checkpoint for every known adapter."""
        self.initialize()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM sync_runs WHERE status = 'succeeded' "
                "ORDER BY started_at DESC, run_id DESC"
            ).fetchall()
            latest: dict[WorkflowKind, dict[str, object]] = {}
            for row in rows:
                try:
                    adapter = WorkflowKind(row["adapter"])
                except ValueError:
                    continue
                latest.setdefault(adapter, dict(row))
            return latest
        finally:
            connection.close()

    def list_events(self, run_id: str) -> tuple[ChangeEvent, ...]:
        self.initialize()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM workflow_events WHERE run_id = ? ORDER BY event_id",
                (run_id,),
            ).fetchall()
            return tuple(
                ChangeEvent(
                    kind=ChangeKind(row["event_type"]),
                    adapter=WorkflowKind(row["adapter"]),
                    external_id=row["external_id"],
                    observed_at=row["observed_at"],
                    old_value=row["old_value"],
                    new_value=row["new_value"],
                    details=json.loads(row["details_json"]),
                )
                for row in rows
            )
        finally:
            connection.close()

    def current_snapshots(
        self,
        *,
        adapter: WorkflowKind | None = None,
        actionable_only: bool = False,
    ) -> tuple[WorkflowSnapshot, ...]:
        self.initialize()
        clauses: list[str] = []
        parameters: list[object] = []
        if adapter is not None:
            clauses.append("adapter = ?")
            parameters.append(adapter.value)
        if actionable_only:
            clauses.append("actionable = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM workflow_current"
                + where
                + " ORDER BY actionable DESC, waiting_days DESC, adapter, external_id",
                parameters,
            ).fetchall()
            return tuple(self._snapshot_from_current(row) for row in rows)
        finally:
            connection.close()

    def get_current_snapshot(
        self,
        adapter: WorkflowKind,
        external_id: str,
    ) -> WorkflowSnapshot | None:
        if not external_id.strip():
            raise ValueError("external_id is required")
        self.initialize()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM workflow_current WHERE adapter = ? AND external_id = ?",
                (adapter.value, external_id),
            ).fetchone()
            return self._snapshot_from_current(row) if row is not None else None
        finally:
            connection.close()

    @staticmethod
    def _snapshot_from_current(row: sqlite3.Row) -> WorkflowSnapshot:
        payload_json = row["payload_json"]
        relations = _payload_relations(_workflow_payload(payload_json))
        return WorkflowSnapshot(
            adapter=WorkflowKind(row["adapter"]),
            external_id=row["external_id"],
            observed_at=row["last_seen_at"],
            reference_no=row["reference_no"],
            project_no=row["project_no"],
            title=row["title"],
            applicant=row["applicant"],
            submitted_at=row["submitted_at"],
            status=row["status"],
            current_node=row["current_node"],
            current_approver=row["current_approver"],
            waiting_days=row["waiting_days"],
            source_url=row["source_url"],
            active=bool(row["active"]),
            actionable=bool(row["actionable"]),
            relations=relations,
            payload_json=payload_json,
            payload_hash=row["payload_hash"],
        )

    def table_count(self, table: str) -> int:
        if table not in _REQUIRED_TABLES:
            raise ValueError(f"unsupported table: {table}")
        self.initialize()
        connection = self._connect()
        try:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            connection.close()

    def register_material(
        self,
        *,
        material_id: str,
        sha256: str,
        size_bytes: int,
        original_path: str,
        original_name: str,
        declared_mime_type: str,
        detected_mime_type: str,
        extension: str,
        status: MaterialStatus,
        review_reason: str,
        created_at: str,
    ) -> tuple[Material, bool, bool]:
        """Register one material reference and return material/dedup/blob-created."""
        if not material_id or not original_name:
            raise ValueError("material_id and original_name are required")
        if not _HASH_RE.fullmatch(sha256):
            raise ValueError("material sha256 must be lowercase SHA-256")
        if size_bytes < 0:
            raise ValueError("material size_bytes cannot be negative")
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "INSERT INTO material_blobs "
                    "(sha256, size_bytes, original_path, detected_mime_type, created_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(sha256) DO NOTHING",
                    (
                        sha256,
                        size_bytes,
                        original_path,
                        detected_mime_type,
                        created_at,
                    ),
                )
                blob_created = cursor.rowcount == 1
                blob = connection.execute(
                    "SELECT * FROM material_blobs WHERE sha256 = ?",
                    (sha256,),
                ).fetchone()
                if blob is None:
                    raise StorageError("material blob registration disappeared")
                if blob["size_bytes"] != size_bytes or blob["original_path"] != original_path:
                    raise SnapshotConflictError(
                        "material blob metadata conflicts with an existing SHA-256"
                    )

                existing = connection.execute(
                    "SELECT m.*, b.size_bytes, b.original_path "
                    "FROM materials m JOIN material_blobs b USING (sha256) "
                    "WHERE m.sha256 = ? AND m.original_name = ?",
                    (sha256, original_name),
                ).fetchone()
                if existing is not None:
                    return self._material_from_row(existing), True, blob_created

                connection.execute(
                    "INSERT INTO materials "
                    "(material_id, sha256, original_name, declared_mime_type, "
                    "detected_mime_type, extension, ingest_status, review_reason, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        material_id,
                        sha256,
                        original_name,
                        declared_mime_type,
                        detected_mime_type,
                        extension,
                        status.value,
                        review_reason,
                        created_at,
                    ),
                )
                row = connection.execute(
                    "SELECT m.*, b.size_bytes, b.original_path "
                    "FROM materials m JOIN material_blobs b USING (sha256) "
                    "WHERE m.material_id = ?",
                    (material_id,),
                ).fetchone()
                if row is None:
                    raise StorageError("material registration disappeared")
                return self._material_from_row(row), False, blob_created
        finally:
            connection.close()

    def get_material(self, material_id: str) -> Material | None:
        self.initialize()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT m.*, b.size_bytes, b.original_path "
                "FROM materials m JOIN material_blobs b USING (sha256) "
                "WHERE m.material_id = ?",
                (material_id,),
            ).fetchone()
            return self._material_from_row(row) if row is not None else None
        finally:
            connection.close()

    def list_materials(
        self,
        *,
        status: MaterialStatus | None = None,
        limit: int = 100,
    ) -> tuple[Material, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        self.initialize()
        connection = self._connect()
        try:
            if status is None:
                rows = connection.execute(
                    "SELECT m.*, b.size_bytes, b.original_path "
                    "FROM materials m JOIN material_blobs b USING (sha256) "
                    "ORDER BY m.created_at DESC, m.material_id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT m.*, b.size_bytes, b.original_path "
                    "FROM materials m JOIN material_blobs b USING (sha256) "
                    "WHERE m.ingest_status = ? "
                    "ORDER BY m.created_at DESC, m.material_id DESC LIMIT ?",
                    (status.value, limit),
                ).fetchall()
            return tuple(self._material_from_row(row) for row in rows)
        finally:
            connection.close()

    def register_material_artifact(
        self,
        *,
        material_id: str,
        kind: str,
        path: str,
        parser_version: str,
        sha256: str,
        size_bytes: int,
        created_at: str,
    ) -> MaterialArtifact:
        if not material_id or not kind or not path:
            raise ValueError("material_id, kind, and path are required")
        if not _HASH_RE.fullmatch(sha256):
            raise ValueError("artifact sha256 must be lowercase SHA-256")
        if size_bytes < 0:
            raise ValueError("artifact size_bytes cannot be negative")
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT INTO material_artifacts "
                    "(material_id, kind, path, parser_version, sha256, size_bytes, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(material_id, kind, path) DO UPDATE SET "
                    "parser_version = excluded.parser_version, sha256 = excluded.sha256, "
                    "size_bytes = excluded.size_bytes",
                    (
                        material_id,
                        kind,
                        path,
                        parser_version,
                        sha256,
                        size_bytes,
                        created_at,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM material_artifacts "
                    "WHERE material_id = ? AND kind = ? AND path = ?",
                    (material_id, kind, path),
                ).fetchone()
                if row is None:
                    raise StorageError("material artifact registration disappeared")
                return self._artifact_from_row(row)
        finally:
            connection.close()

    def list_material_artifacts(self, material_id: str) -> tuple[MaterialArtifact, ...]:
        self.initialize()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM material_artifacts WHERE material_id = ? "
                "ORDER BY artifact_id",
                (material_id,),
            ).fetchall()
            return tuple(self._artifact_from_row(row) for row in rows)
        finally:
            connection.close()

    def start_extraction(
        self,
        *,
        extraction_id: str,
        material_id: str,
        profile: str,
        provider: str,
        model: str,
        extractor_version: str,
        confidence_threshold: float,
        started_at: str,
    ) -> None:
        if not extraction_id or not material_id or not profile or not provider:
            raise ValueError("extraction identity/profile/provider are required")
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT INTO extraction_runs "
                    "(extraction_id, material_id, profile, provider, model, "
                    "extractor_version, status, confidence_threshold, started_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)",
                    (
                        extraction_id,
                        material_id,
                        profile,
                        provider,
                        model,
                        extractor_version,
                        confidence_threshold,
                        started_at,
                    ),
                )
        finally:
            connection.close()

    def complete_extraction(
        self,
        result: ExtractionResult,
        *,
        field_specs: tuple[FieldSpec, ...],
    ) -> None:
        if result.status not in {
            ExtractionStatus.SUCCEEDED,
            ExtractionStatus.NEEDS_REVIEW,
        }:
            raise ValueError("only successful/review extraction results can complete")
        if result.can_advance != (result.status is ExtractionStatus.SUCCEEDED):
            raise ValueError("extraction status and can_advance disagree")
        specs = {spec.name: spec for spec in field_specs}
        issue_payload = [asdict(issue) for issue in result.issues]
        evidence_issue_codes = {
            "missing_evidence",
            "wrong_material",
            "unknown_source",
            "source_label_mismatch",
            "source_text_mismatch",
            "value_not_in_source",
        }
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                run = connection.execute(
                    "SELECT status FROM extraction_runs WHERE extraction_id = ?",
                    (result.id,),
                ).fetchone()
                if run is None or run["status"] != "running":
                    raise StorageError(f"extraction is missing or not running: {result.id}")
                for proposal in result.proposals:
                    spec = specs.get(proposal.field_name)
                    if spec is None:
                        raise StorageError(
                            f"validated proposal lacks field spec: {proposal.field_name}"
                        )
                    if not math.isfinite(proposal.confidence):
                        raise StorageError("proposal confidence must be finite")
                    field_issues = [
                        asdict(issue)
                        for issue in result.issues
                        if issue.field_name == proposal.field_name
                    ]
                    evidence_valid = not any(
                        issue["code"] in evidence_issue_codes for issue in field_issues
                    )
                    evidence = proposal.evidence
                    connection.execute(
                        "INSERT INTO extracted_fields "
                        "(extraction_id, field_name, proposed_value, confidence, required, "
                        "source_material_id, source_kind, source_index, source_label, "
                        "source_text, evidence_valid, validation_issues_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            result.id,
                            proposal.field_name,
                            proposal.proposed_value,
                            proposal.confidence,
                            int(spec.required),
                            evidence.material_id if evidence else None,
                            evidence.source_kind.value if evidence else None,
                            evidence.source_index if evidence else None,
                            evidence.source_label if evidence else None,
                            evidence.source_text if evidence else None,
                            int(evidence_valid),
                            json.dumps(field_issues, ensure_ascii=False, sort_keys=True),
                        ),
                    )
                cursor = connection.execute(
                    "UPDATE extraction_runs SET status = ?, can_advance = ?, "
                    "document_path = ?, result_path = ?, finished_at = ?, "
                    "field_count = ?, issue_count = ?, issues_json = ?, "
                    "error_type = NULL, error_message = NULL "
                    "WHERE extraction_id = ? AND status = 'running'",
                    (
                        result.status.value,
                        int(result.can_advance),
                        result.document_path,
                        result.result_path,
                        result.finished_at,
                        len(result.proposals),
                        len(result.issues),
                        json.dumps(issue_payload, ensure_ascii=False, sort_keys=True),
                        result.id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StorageError(f"extraction completion lost state: {result.id}")
        finally:
            connection.close()

    def fail_extraction(
        self,
        *,
        extraction_id: str,
        finished_at: str,
        error_type: str,
        error_message: str,
    ) -> None:
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "UPDATE extraction_runs SET status = 'failed', finished_at = ?, "
                    "error_type = ?, error_message = ? "
                    "WHERE extraction_id = ? AND status = 'running'",
                    (finished_at, error_type[:120], error_message[:1000], extraction_id),
                )
                if cursor.rowcount != 1:
                    raise StorageError(
                        f"extraction is missing or not running: {extraction_id}"
                    )
        finally:
            connection.close()

    def get_extraction(self, extraction_id: str) -> dict[str, object] | None:
        self.initialize()
        connection = self._connect()
        try:
            run = connection.execute(
                "SELECT * FROM extraction_runs WHERE extraction_id = ?",
                (extraction_id,),
            ).fetchone()
            if run is None:
                return None
            fields = connection.execute(
                "SELECT * FROM extracted_fields WHERE extraction_id = ? "
                "ORDER BY field_id",
                (extraction_id,),
            ).fetchall()
            result = dict(run)
            result["fields"] = [dict(field) for field in fields]
            return result
        finally:
            connection.close()

    def list_extractions(
        self,
        *,
        material_id: str | None = None,
        status: ExtractionStatus | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, object], ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        clauses: list[str] = []
        parameters: list[object] = []
        if material_id is not None:
            clauses.append("material_id = ?")
            parameters.append(material_id)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        self.initialize()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM extraction_runs"
                + where
                + " ORDER BY started_at DESC, extraction_id DESC LIMIT ?",
                parameters,
            ).fetchall()
            return tuple(dict(row) for row in rows)
        finally:
            connection.close()

    def create_workflow_draft(
        self,
        *,
        draft_id: str,
        extraction_id: str,
        workflow: str,
        field_specs: tuple[FieldSpec, ...],
        actor: str,
        created_at: str,
    ) -> DraftCreateResult:
        if not draft_id or not extraction_id or not workflow or not actor:
            raise ValueError("draft identity, workflow, and actor are required")
        if not field_specs or len({spec.name for spec in field_specs}) != len(field_specs):
            raise ValueError("draft field specs must be non-empty and unique")
        self.initialize()
        connection = self._connect()
        actual_draft_id = draft_id
        created = False
        try:
            with connection:
                run = connection.execute(
                    "SELECT profile, status FROM extraction_runs WHERE extraction_id = ?",
                    (extraction_id,),
                ).fetchone()
                if run is None:
                    raise StorageError(f"extraction not found: {extraction_id}")
                if run["status"] not in {"succeeded", "needs_review"}:
                    raise DraftStateConflict(
                        f"extraction status cannot create a draft: {run['status']}"
                    )
                initial_state = (
                    DraftState.EXTRACTED
                    if run["status"] == "succeeded"
                    else DraftState.NEEDS_REVIEW
                )
                cursor = connection.execute(
                    "INSERT INTO workflow_drafts "
                    "(draft_id, extraction_id, workflow, profile, state, version, "
                    "created_by, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?) "
                    "ON CONFLICT(extraction_id) DO NOTHING",
                    (
                        draft_id,
                        extraction_id,
                        workflow,
                        run["profile"],
                        initial_state.value,
                        actor,
                        created_at,
                        created_at,
                    ),
                )
                created = cursor.rowcount == 1
                if not created:
                    existing = connection.execute(
                        "SELECT draft_id FROM workflow_drafts WHERE extraction_id = ?",
                        (extraction_id,),
                    ).fetchone()
                    if existing is None:
                        raise StorageError("draft conflict did not return an existing row")
                    actual_draft_id = str(existing["draft_id"])
                else:
                    sources = connection.execute(
                        "SELECT field_id, field_name, review_status "
                        "FROM extracted_fields WHERE extraction_id = ?",
                        (extraction_id,),
                    ).fetchall()
                    source_by_name = {str(row["field_name"]): row for row in sources}
                    spec_names = {spec.name for spec in field_specs}
                    unknown = sorted(set(source_by_name) - spec_names)
                    if unknown:
                        raise StorageError(
                            "extraction fields are outside the draft profile: "
                            + ", ".join(unknown)
                        )
                    for source in sources:
                        if source["review_status"] != ReviewDecision.PENDING.value:
                            raise StorageError(
                                "extracted field review state lacks a draft audit trail"
                            )
                    for spec in field_specs:
                        source = source_by_name.get(spec.name)
                        decision = (
                            ReviewDecision.PENDING
                            if source is not None or spec.required
                            else ReviewDecision.NOT_PROPOSED
                        )
                        connection.execute(
                            "INSERT INTO draft_fields "
                            "(draft_id, field_name, label, required, source_field_id, "
                            "review_decision) VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                draft_id,
                                spec.name,
                                spec.label,
                                int(spec.required),
                                source["field_id"] if source is not None else None,
                                decision.value,
                            ),
                        )
                    self._insert_draft_event(
                        connection,
                        draft_id=draft_id,
                        sequence=1,
                        event_type="draft_created",
                        actor=actor,
                        from_state=None,
                        to_state=initial_state,
                        field_name=None,
                        details={
                            "extraction_id": extraction_id,
                            "field_count": len(field_specs),
                            "profile": str(run["profile"]),
                        },
                        created_at=created_at,
                    )
        finally:
            connection.close()
        draft = self.get_workflow_draft(actual_draft_id)
        if draft is None:
            raise StorageError("created workflow draft disappeared")
        return DraftCreateResult(draft=draft, created=created)

    def get_workflow_draft(self, draft_id: str) -> WorkflowDraft | None:
        self.initialize()
        connection = self._connect()
        try:
            return self._load_workflow_draft(connection, draft_id)
        finally:
            connection.close()

    def get_workflow_draft_by_extraction(
        self,
        extraction_id: str,
    ) -> WorkflowDraft | None:
        self.initialize()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT draft_id FROM workflow_drafts WHERE extraction_id = ?",
                (extraction_id,),
            ).fetchone()
            if row is None:
                return None
            return self._load_workflow_draft(connection, str(row["draft_id"]))
        finally:
            connection.close()

    def list_workflow_draft_summaries(
        self,
        *,
        state: DraftState | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, object], ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        parameters: list[object] = []
        where = ""
        if state is not None:
            where = " WHERE d.state = ?"
            parameters.append(state.value)
        parameters.append(limit)
        self.initialize()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT d.*, r.material_id, r.status AS extraction_status, "
                "SUM(CASE WHEN df.review_decision = 'pending' THEN 1 ELSE 0 END) "
                "AS pending_count, "
                "SUM(CASE WHEN df.required = 1 AND df.review_decision != 'confirmed' "
                "THEN 1 ELSE 0 END) AS unresolved_required_count, "
                "MAX(CASE WHEN df.field_name = 'PR_PrjName' "
                "THEN COALESCE(df.confirmed_value, ef.proposed_value) END) AS title "
                "FROM workflow_drafts d "
                "JOIN extraction_runs r USING (extraction_id) "
                "LEFT JOIN draft_fields df USING (draft_id) "
                "LEFT JOIN extracted_fields ef ON ef.field_id = df.source_field_id"
                + where
                + " GROUP BY d.draft_id "
                "ORDER BY d.updated_at DESC, d.draft_id DESC LIMIT ?",
                parameters,
            ).fetchall()
            summaries: list[dict[str, object]] = []
            for row in rows:
                summary = dict(row)
                summary["validation_issue_count"] = len(
                    self._issues_from_json(row["validation_issues_json"])
                )
                summaries.append(summary)
            return tuple(summaries)
        finally:
            connection.close()

    def review_workflow_draft_field(
        self,
        *,
        draft_id: str,
        field_name: str,
        decision: ReviewDecision,
        confirmed_value: str | None,
        human_evidence: FieldEvidence | None,
        actor: str,
        reviewed_at: str,
        expected_version: int,
    ) -> WorkflowDraft:
        if decision not in {ReviewDecision.CONFIRMED, ReviewDecision.REJECTED}:
            raise ValueError("human review decision must be confirmed or rejected")
        if decision is ReviewDecision.CONFIRMED:
            if confirmed_value is None or not confirmed_value.strip():
                raise ValueError("confirmed fields require a non-empty value")
            confirmed_value = confirmed_value.strip()
        elif confirmed_value is not None or human_evidence is not None:
            raise ValueError("rejected fields cannot contain a value or human evidence")
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                draft = connection.execute(
                    "SELECT d.state, d.version, r.material_id "
                    "FROM workflow_drafts d "
                    "JOIN extraction_runs r USING (extraction_id) "
                    "WHERE d.draft_id = ?",
                    (draft_id,),
                ).fetchone()
                if draft is None:
                    raise StorageError(f"workflow draft not found: {draft_id}")
                state = DraftState(str(draft["state"]))
                if state not in {DraftState.EXTRACTED, DraftState.NEEDS_REVIEW}:
                    raise DraftStateConflict(
                        f"field review is not allowed from draft state {state.value}"
                    )
                self._require_draft_version(draft, expected_version)
                if (
                    human_evidence is not None
                    and human_evidence.material_id != draft["material_id"]
                ):
                    raise ValueError("human evidence material does not match the draft")
                field = connection.execute(
                    "SELECT * FROM draft_fields WHERE draft_id = ? AND field_name = ?",
                    (draft_id, field_name),
                ).fetchone()
                if field is None:
                    raise ValueError(f"field is not in the draft profile: {field_name}")
                next_version = expected_version + 1
                previous_details = {
                    "decision": str(field["review_decision"]),
                    "confirmed_value": field["confirmed_value"],
                }
                connection.execute(
                    "UPDATE draft_fields SET review_decision = ?, confirmed_value = ?, "
                    "human_source_kind = ?, human_source_index = ?, "
                    "human_source_label = ?, human_source_text = ?, "
                    "reviewed_by = ?, reviewed_at = ? "
                    "WHERE draft_id = ? AND field_name = ?",
                    (
                        decision.value,
                        confirmed_value,
                        human_evidence.source_kind.value if human_evidence else None,
                        human_evidence.source_index if human_evidence else None,
                        human_evidence.source_label if human_evidence else None,
                        human_evidence.source_text if human_evidence else None,
                        actor,
                        reviewed_at,
                        draft_id,
                        field_name,
                    ),
                )
                if field["source_field_id"] is not None:
                    cursor = connection.execute(
                        "UPDATE extracted_fields SET review_status = ?, confirmed_value = ? "
                        "WHERE field_id = ?",
                        (decision.value, confirmed_value, field["source_field_id"]),
                    )
                    if cursor.rowcount != 1:
                        raise StorageError("source extracted field disappeared during review")
                cursor = connection.execute(
                    "UPDATE workflow_drafts SET version = ?, updated_at = ?, "
                    "validation_issues_json = '[]', validated_at = NULL "
                    "WHERE draft_id = ? AND version = ?",
                    (next_version, reviewed_at, draft_id, expected_version),
                )
                if cursor.rowcount != 1:
                    raise DraftVersionConflict("draft changed during field review")
                self._insert_draft_event(
                    connection,
                    draft_id=draft_id,
                    sequence=next_version,
                    event_type="field_reviewed",
                    actor=actor,
                    from_state=state,
                    to_state=state,
                    field_name=field_name,
                    details={
                        "previous": previous_details,
                        "decision": decision.value,
                        "confirmed_value": confirmed_value,
                        "human_evidence": asdict(human_evidence) if human_evidence else None,
                    },
                    created_at=reviewed_at,
                )
        finally:
            connection.close()
        updated = self.get_workflow_draft(draft_id)
        if updated is None:
            raise StorageError("reviewed workflow draft disappeared")
        return updated

    def apply_workflow_draft_validation(
        self,
        *,
        draft_id: str,
        issues: tuple[FieldIssue, ...],
        actor: str,
        validated_at: str,
        expected_version: int,
    ) -> WorkflowDraft:
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                draft = connection.execute(
                    "SELECT state, version FROM workflow_drafts WHERE draft_id = ?",
                    (draft_id,),
                ).fetchone()
                if draft is None:
                    raise StorageError(f"workflow draft not found: {draft_id}")
                state = DraftState(str(draft["state"]))
                if state not in {DraftState.EXTRACTED, DraftState.NEEDS_REVIEW}:
                    raise DraftStateConflict(
                        f"validation is not allowed from draft state {state.value}"
                    )
                self._require_draft_version(draft, expected_version)
                target = DraftState.NEEDS_REVIEW if issues else DraftState.VALIDATED
                next_version = expected_version + 1
                issue_payload = [asdict(issue) for issue in issues]
                cursor = connection.execute(
                    "UPDATE workflow_drafts SET state = ?, version = ?, "
                    "validation_issues_json = ?, updated_at = ?, validated_at = ? "
                    "WHERE draft_id = ? AND version = ?",
                    (
                        target.value,
                        next_version,
                        json.dumps(issue_payload, ensure_ascii=False, sort_keys=True),
                        validated_at,
                        validated_at if not issues else None,
                        draft_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise DraftVersionConflict("draft changed during validation")
                self._insert_draft_event(
                    connection,
                    draft_id=draft_id,
                    sequence=next_version,
                    event_type="validation_failed" if issues else "validation_passed",
                    actor=actor,
                    from_state=state,
                    to_state=target,
                    field_name=None,
                    details={"issues": issue_payload},
                    created_at=validated_at,
                )
        finally:
            connection.close()
        updated = self.get_workflow_draft(draft_id)
        if updated is None:
            raise StorageError("validated workflow draft disappeared")
        return updated

    def mark_workflow_draft_ready(
        self,
        *,
        draft_id: str,
        actor: str,
        ready_at: str,
        expected_version: int,
    ) -> WorkflowDraft:
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                draft = connection.execute(
                    "SELECT state, version, validation_issues_json "
                    "FROM workflow_drafts WHERE draft_id = ?",
                    (draft_id,),
                ).fetchone()
                if draft is None:
                    raise StorageError(f"workflow draft not found: {draft_id}")
                state = DraftState(str(draft["state"]))
                if state is not DraftState.VALIDATED:
                    raise DraftStateConflict(
                        f"ready is not allowed from draft state {state.value}"
                    )
                self._require_draft_version(draft, expected_version)
                if self._issues_from_json(draft["validation_issues_json"]):
                    raise DraftStateConflict("draft still has validation issues")
                invalid = connection.execute(
                    "SELECT COUNT(*) FROM draft_fields WHERE draft_id = ? AND ("
                    "review_decision = 'pending' OR "
                    "(required = 1 AND review_decision != 'confirmed') OR "
                    "(review_decision = 'confirmed' AND "
                    "length(trim(COALESCE(confirmed_value, ''))) = 0))",
                    (draft_id,),
                ).fetchone()[0]
                if invalid:
                    raise DraftStateConflict("draft fields no longer satisfy ready gates")
                next_version = expected_version + 1
                cursor = connection.execute(
                    "UPDATE workflow_drafts SET state = 'ready', version = ?, "
                    "updated_at = ?, ready_at = ? WHERE draft_id = ? AND version = ?",
                    (next_version, ready_at, ready_at, draft_id, expected_version),
                )
                if cursor.rowcount != 1:
                    raise DraftVersionConflict("draft changed while marking ready")
                self._insert_draft_event(
                    connection,
                    draft_id=draft_id,
                    sequence=next_version,
                    event_type="draft_ready",
                    actor=actor,
                    from_state=state,
                    to_state=DraftState.READY,
                    field_name=None,
                    details={},
                    created_at=ready_at,
                )
        finally:
            connection.close()
        updated = self.get_workflow_draft(draft_id)
        if updated is None:
            raise StorageError("ready workflow draft disappeared")
        return updated

    @staticmethod
    def _require_draft_version(row: sqlite3.Row, expected_version: int) -> None:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        if int(row["version"]) != expected_version:
            raise DraftVersionConflict(
                f"stale draft version {expected_version}; current version is {row['version']}"
            )

    @staticmethod
    def _insert_draft_event(
        connection: sqlite3.Connection,
        *,
        draft_id: str,
        sequence: int,
        event_type: str,
        actor: str,
        from_state: DraftState | None,
        to_state: DraftState,
        field_name: str | None,
        details: dict[str, object],
        created_at: str,
    ) -> None:
        connection.execute(
            "INSERT INTO draft_audit_events "
            "(draft_id, sequence, event_type, actor, from_state, to_state, "
            "field_name, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                draft_id,
                sequence,
                event_type,
                actor,
                from_state.value if from_state is not None else None,
                to_state.value,
                field_name,
                json.dumps(details, ensure_ascii=False, sort_keys=True),
                created_at,
            ),
        )

    @classmethod
    def _load_workflow_draft(
        cls,
        connection: sqlite3.Connection,
        draft_id: str,
    ) -> WorkflowDraft | None:
        draft = connection.execute(
            "SELECT d.*, r.material_id FROM workflow_drafts d "
            "JOIN extraction_runs r USING (extraction_id) WHERE d.draft_id = ?",
            (draft_id,),
        ).fetchone()
        if draft is None:
            return None
        field_rows = connection.execute(
            "SELECT df.*, ef.proposed_value AS original_proposed_value, "
            "ef.confidence AS original_confidence, "
            "ef.source_material_id AS original_material_id, "
            "ef.source_kind AS original_source_kind, "
            "ef.source_index AS original_source_index, "
            "ef.source_label AS original_source_label, "
            "ef.source_text AS original_source_text, "
            "ef.evidence_valid AS original_evidence_valid, "
            "ef.validation_issues_json AS original_validation_issues_json "
            "FROM draft_fields df LEFT JOIN extracted_fields ef "
            "ON ef.field_id = df.source_field_id "
            "WHERE df.draft_id = ? ORDER BY df.rowid",
            (draft_id,),
        ).fetchall()
        fields: list[DraftField] = []
        for row in field_rows:
            original_evidence = None
            if row["original_material_id"] is not None:
                original_evidence = FieldEvidence(
                    material_id=str(row["original_material_id"]),
                    source_kind=SourceKind(str(row["original_source_kind"])),
                    source_index=int(row["original_source_index"]),
                    source_label=str(row["original_source_label"]),
                    source_text=str(row["original_source_text"]),
                )
            human_evidence = None
            if row["human_source_kind"] is not None:
                human_evidence = FieldEvidence(
                    material_id=str(draft["material_id"]),
                    source_kind=SourceKind(str(row["human_source_kind"])),
                    source_index=int(row["human_source_index"]),
                    source_label=str(row["human_source_label"]),
                    source_text=str(row["human_source_text"]),
                )
            fields.append(
                DraftField(
                    field_name=str(row["field_name"]),
                    label=str(row["label"]),
                    required=bool(row["required"]),
                    source_field_id=(
                        int(row["source_field_id"])
                        if row["source_field_id"] is not None
                        else None
                    ),
                    proposed_value=(
                        str(row["original_proposed_value"])
                        if row["original_proposed_value"] is not None
                        else None
                    ),
                    confidence=(
                        float(row["original_confidence"])
                        if row["original_confidence"] is not None
                        else None
                    ),
                    original_evidence=original_evidence,
                    original_evidence_valid=bool(row["original_evidence_valid"]),
                    original_validation_issues=cls._issues_from_json(
                        row["original_validation_issues_json"] or "[]"
                    ),
                    decision=ReviewDecision(str(row["review_decision"])),
                    confirmed_value=(
                        str(row["confirmed_value"])
                        if row["confirmed_value"] is not None
                        else None
                    ),
                    human_evidence=human_evidence,
                    reviewed_by=(
                        str(row["reviewed_by"])
                        if row["reviewed_by"] is not None
                        else None
                    ),
                    reviewed_at=(
                        str(row["reviewed_at"])
                        if row["reviewed_at"] is not None
                        else None
                    ),
                )
            )
        event_rows = connection.execute(
            "SELECT * FROM draft_audit_events WHERE draft_id = ? ORDER BY sequence",
            (draft_id,),
        ).fetchall()
        events = tuple(
            DraftAuditEvent(
                id=int(row["event_id"]),
                sequence=int(row["sequence"]),
                event_type=str(row["event_type"]),
                actor=str(row["actor"]),
                from_state=(
                    DraftState(str(row["from_state"]))
                    if row["from_state"] is not None
                    else None
                ),
                to_state=DraftState(str(row["to_state"])),
                field_name=(
                    str(row["field_name"])
                    if row["field_name"] is not None
                    else None
                ),
                details=cls._details_from_json(row["details_json"]),
                created_at=str(row["created_at"]),
            )
            for row in event_rows
        )
        return WorkflowDraft(
            id=str(draft["draft_id"]),
            extraction_id=str(draft["extraction_id"]),
            material_id=str(draft["material_id"]),
            workflow=str(draft["workflow"]),
            profile=str(draft["profile"]),
            state=DraftState(str(draft["state"])),
            version=int(draft["version"]),
            validation_issues=cls._issues_from_json(draft["validation_issues_json"]),
            created_by=str(draft["created_by"]),
            created_at=str(draft["created_at"]),
            updated_at=str(draft["updated_at"]),
            validated_at=(
                str(draft["validated_at"])
                if draft["validated_at"] is not None
                else None
            ),
            ready_at=str(draft["ready_at"]) if draft["ready_at"] is not None else None,
            fields=tuple(fields),
            audit_events=events,
        )

    @staticmethod
    def _issues_from_json(value: object) -> tuple[FieldIssue, ...]:
        if not isinstance(value, str):
            raise StorageError("stored field issues are not JSON text")
        payload = json.loads(value)
        if not isinstance(payload, list):
            raise StorageError("stored field issues are not a list")
        issues: list[FieldIssue] = []
        for item in payload:
            if not isinstance(item, dict):
                raise StorageError("stored field issue is not an object")
            issues.append(
                FieldIssue(
                    code=str(item.get("code", "")),
                    field_name=str(item.get("field_name", "")),
                    message=str(item.get("message", "")),
                )
            )
        return tuple(issues)

    @staticmethod
    def _details_from_json(value: object) -> dict[str, object]:
        if not isinstance(value, str):
            raise StorageError("stored audit details are not JSON text")
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise StorageError("stored audit details are not an object")
        return payload

    @staticmethod
    def _material_from_row(row: sqlite3.Row) -> Material:
        return Material(
            id=row["material_id"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            original_name=row["original_name"],
            declared_mime_type=row["declared_mime_type"],
            detected_mime_type=row["detected_mime_type"],
            extension=row["extension"],
            status=MaterialStatus(row["ingest_status"]),
            review_reason=row["review_reason"],
            original_path=row["original_path"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> MaterialArtifact:
        return MaterialArtifact(
            id=row["artifact_id"],
            material_id=row["material_id"],
            kind=row["kind"],
            path=row["path"],
            parser_version=row["parser_version"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            created_at=row["created_at"],
        )
