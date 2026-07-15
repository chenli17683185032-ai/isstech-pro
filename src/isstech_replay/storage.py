"""Versioned SQLite storage for workflow measurements and change events."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3

from .models.extraction import ExtractionResult, ExtractionStatus, FieldSpec
from .models.work_items import ChangeEvent, ChangeKind, WorkflowKind, WorkflowSnapshot
from .models.materials import Material, MaterialArtifact, MaterialStatus


SCHEMA_VERSION = 3
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
_REQUIRED_TABLES = _WORKFLOW_TABLES | _MATERIAL_TABLES | _EXTRACTION_TABLES
_REQUIRED_TABLES_BY_VERSION = {
    1: _WORKFLOW_TABLES,
    2: _WORKFLOW_TABLES | _MATERIAL_TABLES,
    3: _REQUIRED_TABLES,
}
_MIGRATIONS = {
    1: "migration_002_materials.sql",
    2: "migration_003_extraction.sql",
}


class StorageError(RuntimeError):
    """Base class for workflow storage failures."""


class UnsupportedSchemaVersion(StorageError):
    """The database requires a migration this binary does not know."""


class SnapshotConflictError(StorageError):
    """The same observation key was reused with different state."""


class SnapshotOrderError(StorageError):
    """An older measurement attempted to replace a newer current state."""


@dataclass(frozen=True, slots=True)
class StorageApplyResult:
    history_rows_inserted: int
    events: tuple[ChangeEvent, ...]


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
                if source_total_count is not None and source_total_count != len(snapshots):
                    raise StorageError(
                        f"source_total_count={source_total_count} does not match "
                        f"snapshot_count={len(snapshots)}"
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

    @staticmethod
    def _snapshot_from_current(row: sqlite3.Row) -> WorkflowSnapshot:
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
            payload_json=row["payload_json"],
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
