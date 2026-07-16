#!/usr/bin/env python3
"""Run the read-only PurchaseRequisition measurement and local snapshot loop."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, date, datetime
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Sequence
from uuid import uuid4

from isstech_replay.account_scope import account_database_path, account_runtime_dir
from isstech_replay.auth import login_with_settings
from isstech_replay.models.work_items import WorkItem, WorkflowKind
from isstech_replay.storage import DEFAULT_DATABASE_NAME, WorkflowStorage
from isstech_replay.sync import (
    safe_error_message,
    sync_procurement_workflows,
    sync_result_dict,
    utc_iso,
)


_AUTO_CSV = "__AUTO_CSV__"
_CSV_FIELDS = (
    "key",
    "workflow",
    "external_id",
    "reference_no",
    "project_no",
    "title",
    "applicant",
    "submitted_at",
    "status",
    "current_approver",
    "waiting_days",
    "source_url",
)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def render_work_items_csv(items: tuple[WorkItem, ...]) -> str:
    stream = io.StringIO(newline="")
    stream.write("\ufeff")
    writer = csv.DictWriter(stream, fieldnames=_CSV_FIELDS)
    writer.writeheader()
    for item in items:
        writer.writerow(
            {
                "key": _csv_cell(item.key),
                "workflow": item.workflow.value,
                "external_id": _csv_cell(item.external_id),
                "reference_no": _csv_cell(item.reference_no),
                "project_no": _csv_cell(item.project_no),
                "title": _csv_cell(item.title),
                "applicant": _csv_cell(item.applicant),
                "submitted_at": _csv_cell(item.submitted_at),
                "status": _csv_cell(item.status),
                "current_approver": _csv_cell(item.current_approver),
                "waiting_days": _csv_cell(item.waiting_days),
                "source_url": _csv_cell(item.source_url),
            }
        )
    return stream.getvalue()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read all procurement workflow pages and update local snapshots."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.getenv("ISSTECH_DATA_DIR", "data")),
        help="Runtime output root (default: data or ISSTECH_DATA_DIR).",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help=(
            "SQLite base path; the actual file is isolated under "
            "accounts/<account-scope>/ (default base: "
            "<data-dir>/workflow-center.sqlite3)."
        ),
    )
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and normalize without creating or changing SQLite/run files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete run summary as JSON to stdout.",
    )
    parser.add_argument(
        "--csv",
        nargs="?",
        const=_AUTO_CSV,
        default=None,
        metavar="PATH",
        help=(
            "Write work items to PATH or the dated default under the "
            "account-scoped exports directory."
        ),
    )
    return parser


def _print_human(result: object, summary_path: Path | None, csv_path: Path | None) -> None:
    data = sync_result_dict(result)  # type: ignore[arg-type]
    print(f"run_id {data['run_id']}")
    print(f"status {data['status']}")
    print(f"observed_count {data['observed_count']}")
    print(f"actionable_count {data['actionable_count']}")
    print(f"event_count {len(data['events'])}")
    if data.get("database_path"):
        print(f"database {data['database_path']}")
    if summary_path is not None:
        print(f"summary {summary_path}")
    if csv_path is not None:
        print(f"csv {csv_path}")
    for item in data["work_items"]:
        waiting = item["waiting_days"] if item["waiting_days"] is not None else "unknown"
        print(
            "todo",
            item["reference_no"] or item["external_id"],
            item["current_approver"],
            item["status"],
            f"days={waiting}",
            item["source_url"],
            sep="\t",
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_pages < 1:
        print("--max-pages must be at least 1", file=sys.stderr)
        return 2

    username = os.getenv("ISSTECH_USERNAME", "").strip()
    password = os.getenv("ISSTECH_PASSWORD", "")
    if not username or not password:
        print(
            "Set ISSTECH_USERNAME and ISSTECH_PASSWORD in the current environment.",
            file=sys.stderr,
        )
        return 2

    data_dir: Path = args.data_dir.expanduser()
    database_base_path = (
        args.database.expanduser()
        if args.database is not None
        else data_dir / DEFAULT_DATABASE_NAME
    )
    scoped_data_dir = account_runtime_dir(data_dir, username)
    database_path = account_database_path(
        username,
        base_database_path=database_base_path,
    )
    storage = None if args.dry_run else WorkflowStorage(database_path)
    client = None
    run_id = uuid4().hex
    run_started_at = datetime.now(UTC)
    try:
        client, _ = login_with_settings(username, password)
        result = sync_procurement_workflows(
            client,
            storage=storage,
            max_pages=args.max_pages,
            dry_run=args.dry_run,
            run_id=run_id,
            started_at=run_started_at,
        )

        summary_path: Path | None = None
        if not args.dry_run:
            summary_path = scoped_data_dir / "runs" / result.run_id / "summary.json"
            _atomic_write_text(
                summary_path,
                json.dumps(
                    sync_result_dict(result),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )

        csv_path: Path | None = None
        if args.csv is not None:
            csv_path = (
                scoped_data_dir
                / "exports"
                / f"{date.today().isoformat()}-work-items.csv"
                if args.csv == _AUTO_CSV
                else Path(args.csv).expanduser()
            )
            _atomic_write_text(csv_path, render_work_items_csv(result.work_items))

        if args.json:
            print(
                json.dumps(
                    sync_result_dict(result),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            if summary_path is not None:
                print(f"summary {summary_path}", file=sys.stderr)
            if csv_path is not None:
                print(f"csv {csv_path}", file=sys.stderr)
        else:
            _print_human(result, summary_path, csv_path)
        if result.status != "succeeded":
            print(
                f"SYNC_FAILED run_id={result.run_id} status={result.status}",
                file=sys.stderr,
            )
            return 1
        return 0
    except Exception as error:
        if client is None and storage is not None:
            try:
                storage.start_run(
                    run_id=run_id,
                    adapter=WorkflowKind.PURCHASE_REQUISITION,
                    started_at=utc_iso(run_started_at),
                    max_pages=args.max_pages,
                )
                storage.fail_run(
                    run_id=run_id,
                    finished_at=utc_iso(datetime.now(UTC)),
                    error_type=type(error).__name__,
                    error_message=safe_error_message(error),
                )
            except Exception as record_error:
                print(
                    "RUN_RECORD_FAILED "
                    f"{type(record_error).__name__}: {safe_error_message(record_error)}",
                    file=sys.stderr,
                )
        print(
            f"SYNC_FAILED run_id={run_id} "
            f"{type(error).__name__}: {safe_error_message(error)}",
            file=sys.stderr,
        )
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
