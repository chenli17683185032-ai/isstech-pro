#!/usr/bin/env python3
"""Ingest local files/directories into immutable content-addressed storage."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from isstech_replay.materials import MaterialService
from isstech_replay.storage import DEFAULT_DATABASE_NAME, WorkflowStorage


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest project material files locally.")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recurse into supplied directories.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.getenv("ISSTECH_DATA_DIR", "data")),
    )
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--max-bytes", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def _safe_message(error: BaseException) -> str:
    return str(error).replace("\r", " ").replace("\n", " ")[:1000]


def _expand(paths: Sequence[Path], *, recursive: bool) -> tuple[Path, ...]:
    output: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        candidate = path.expanduser()
        if candidate.is_dir() and not candidate.is_symlink():
            children = candidate.rglob("*") if recursive else candidate.iterdir()
            for child in sorted(children, key=lambda item: str(item)):
                if child.is_file() or child.is_symlink():
                    absolute = child.absolute()
                    if absolute not in seen:
                        seen.add(absolute)
                        output.append(child)
        else:
            absolute = candidate.absolute()
            if absolute not in seen:
                seen.add(absolute)
                output.append(candidate)
    return tuple(output)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_bytes is not None and args.max_bytes < 1:
        print("--max-bytes must be positive", file=sys.stderr)
        return 2

    data_dir: Path = args.data_dir.expanduser()
    database = (
        args.database.expanduser()
        if args.database is not None
        else data_dir / DEFAULT_DATABASE_NAME
    )
    service = MaterialService(
        data_dir=data_dir,
        storage=WorkflowStorage(database),
        max_bytes=args.max_bytes,
    )
    candidates = _expand(args.paths, recursive=args.recursive)
    if not candidates:
        print("No material files found.", file=sys.stderr)
        return 2

    data_root = data_dir.resolve()
    successes = []
    failures = []
    for path in candidates:
        try:
            resolved = path.resolve()
            if resolved.is_relative_to(data_root):
                raise ValueError("refusing to ingest a file from the runtime data directory")
            result = service.ingest_path(path)
            successes.append(
                {
                    "path": str(path),
                    "material": asdict(result.material),
                    "deduplicated": result.deduplicated,
                    "blob_created": result.blob_created,
                }
            )
        except Exception as error:
            failures.append(
                {
                    "path": str(path),
                    "error_type": type(error).__name__,
                    "message": _safe_message(error),
                }
            )

    summary = {
        "ingested_count": len(successes),
        "failed_count": len(failures),
        "materials": successes,
        "failures": failures,
        "database": str(database),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for item in successes:
            material = item["material"]
            print(
                "ingested",
                material["id"],
                material["status"],
                material["original_name"],
                material["sha256"],
                "deduplicated=" + str(item["deduplicated"]).lower(),
                sep="\t",
            )
        for failure in failures:
            print(
                "failed",
                failure["path"],
                failure["error_type"],
                failure["message"],
                sep="\t",
                file=sys.stderr,
            )
        print(f"ingested_count {len(successes)}")
        print(f"failed_count {len(failures)}")
        print(f"database {database}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
