#!/usr/bin/env python3
"""LaunchAgent entrypoint for the existing manual read-only sync CLI."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Sequence

from isstech_replay.scheduler import (
    DEFAULT_KEYCHAIN_TIMEOUT_SECONDS,
    DEFAULT_SYNC_TIMEOUT_SECONDS,
    ScheduledSyncConfig,
    run_scheduled_day,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one bounded scheduled read-only workflow synchronization."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.getenv("ISSTECH_DATA_DIR", REPO_ROOT / "data")),
    )
    parser.add_argument("--log-file", type=Path, default=None)
    parser.add_argument(
        "--keychain-timeout-seconds",
        type=float,
        default=DEFAULT_KEYCHAIN_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--sync-timeout-seconds",
        type=float,
        default=DEFAULT_SYNC_TIMEOUT_SECONDS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_dir = args.data_dir.expanduser()
    log_file = (
        args.log_file.expanduser()
        if args.log_file is not None
        else data_dir / "logs" / "scheduled-sync.log"
    )
    try:
        result = run_scheduled_day(
            ScheduledSyncConfig(
                repo_root=args.repo_root,
                python_executable=Path(sys.executable),
                data_dir=data_dir,
                log_file=log_file,
                keychain_timeout_seconds=args.keychain_timeout_seconds,
                sync_timeout_seconds=args.sync_timeout_seconds,
            )
        )
    except Exception as error:
        print(
            f"SCHEDULED_SYNC_SETUP_FAILED {type(error).__name__}; log={log_file}",
            file=sys.stderr,
        )
        return 1
    if result.exit_code == 0:
        print(f"SCHEDULED_DAY_OK log={log_file}")
    else:
        print(
            "SCHEDULED_DAY_FAILED "
            f"sync={result.sync_exit_code} brief={result.brief_exit_code} "
            f"open={result.open_exit_code}; log={log_file}",
            file=sys.stderr,
        )
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
