#!/usr/bin/env python3
"""Parse one local material and persist evidence-backed field proposals."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from isstech_replay.ai.provider import provider_from_env
from isstech_replay.extraction import FieldExtractionService
from isstech_replay.field_mapping import DEFAULT_CONFIDENCE_THRESHOLD
from isstech_replay.materials import MaterialService
from isstech_replay.storage import DEFAULT_DATABASE_NAME, WorkflowStorage
from isstech_replay.sync import safe_error_message


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract workflow fields from one ingested local material."
    )
    parser.add_argument("material_id")
    parser.add_argument(
        "--provider",
        choices=("local_rules", "http_json"),
        default="local_rules",
    )
    parser.add_argument(
        "--profile",
        choices=("purchase_requisition",),
        default="purchase_requisition",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.getenv("ISSTECH_DATA_DIR", "data")),
    )
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 0 <= args.confidence_threshold <= 1:
        print("--confidence-threshold must be between 0 and 1", file=sys.stderr)
        return 2

    data_dir: Path = args.data_dir.expanduser()
    database = (
        args.database.expanduser()
        if args.database is not None
        else data_dir / DEFAULT_DATABASE_NAME
    )
    material_service = MaterialService(
        data_dir=data_dir,
        storage=WorkflowStorage(database),
    )
    try:
        provider = provider_from_env(args.provider)
        result = FieldExtractionService(material_service, provider).extract(
            args.material_id,
            profile=args.profile,
            confidence_threshold=args.confidence_threshold,
        )
    except Exception as error:
        print(
            f"EXTRACTION_FAILED {type(error).__name__}: {safe_error_message(error)}",
            file=sys.stderr,
        )
        return 1

    payload = {
        "extraction": asdict(result),
        "database": str(database),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"extraction_id {result.id}")
        print(f"status {result.status.value}")
        print(f"can_advance {str(result.can_advance).lower()}")
        print(f"field_count {len(result.proposals)}")
        print(f"issue_count {len(result.issues)}")
        print(f"result {data_dir / result.result_path}")
        print(f"database {database}")
        for proposal in result.proposals:
            evidence = proposal.evidence
            source = (
                f"{evidence.source_kind.value}:{evidence.source_index}"
                if evidence is not None
                else "missing"
            )
            print(
                "field",
                proposal.field_name,
                f"confidence={proposal.confidence:.3f}",
                f"source={source}",
                sep="\t",
            )
        for issue in result.issues:
            print(
                "issue",
                issue.code,
                issue.field_name or "document",
                issue.message,
                sep="\t",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
