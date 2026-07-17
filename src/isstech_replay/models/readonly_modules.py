"""Independent checkpoints for account-visible read-only business modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReadonlyModuleKind(StrEnum):
    PAYMENT = "payment"
    BIZCASE = "bizcase"
    TRAVEL_APPLICATION = "travel_application"
    DAILY_EXPENSE = "daily_expense"

    @property
    def label(self) -> str:
        return {
            ReadonlyModuleKind.PAYMENT: "付款申请",
            ReadonlyModuleKind.BIZCASE: "BizCase查询",
            ReadonlyModuleKind.TRAVEL_APPLICATION: "出差申请",
            ReadonlyModuleKind.DAILY_EXPENSE: "日常报销申请",
        }[self]


@dataclass(frozen=True, slots=True)
class ReadonlySnapshot:
    module: ReadonlyModuleKind
    external_id: str
    observed_at: str
    payload_json: str
    payload_hash: str


@dataclass(frozen=True, slots=True)
class ReadonlySyncResult:
    run_id: str
    module: ReadonlyModuleKind
    status: str
    dry_run: bool
    started_at: str
    observed_at: str
    finished_at: str
    source_total_count: int
    observed_count: int
    snapshot_count: int
    history_rows_inserted: int
    changed_count: int
    database_path: str | None = None


@dataclass(frozen=True, slots=True)
class ReadonlyStreamSummary:
    module: ReadonlyModuleKind
    run_id: str
    status: str
    source_total_count: int = 0
    observed_count: int = 0
    snapshot_count: int = 0
    history_rows_inserted: int = 0
    changed_count: int = 0
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class ReadonlySyncBatchResult:
    run_id: str
    status: str
    dry_run: bool
    started_at: str
    observed_at: str
    finished_at: str
    source_total_count: int
    observed_count: int
    snapshot_count: int
    history_rows_inserted: int
    changed_count: int
    streams: tuple[ReadonlyStreamSummary, ...]
    database_path: str | None = None
