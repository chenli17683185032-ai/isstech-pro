#!/usr/bin/env python3
"""Generate one bounded daily briefing from the account's cached snapshots."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from isstech_replay.account_scope import account_database_path
from isstech_replay.ai.briefing import (
    BriefingProvider,
    ModelBriefing,
    assistant_provider_config,
    provider_from_config,
)
from isstech_replay.assistant import generate_assistant_brief
from isstech_replay.scheduler import local_account_name, read_keychain_value
from isstech_replay.storage import WorkflowStorage


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(slots=True)
class _UnavailableProvider:
    error: Exception
    model: str
    name: str = "invalid_configuration"

    def prioritize(self, *_args, **_kwargs) -> ModelBriefing:
        raise self.error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate one local follow-up briefing from cached account snapshots."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.getenv("ISSTECH_DATA_DIR", REPO_ROOT / "data")),
    )
    return parser


def _provider() -> BriefingProvider | None:
    config = assistant_provider_config(
        account=local_account_name(),
        credential_reader=read_keychain_value,
    )
    if config is None:
        return None
    try:
        return provider_from_config(config)
    except Exception as exc:
        return _UnavailableProvider(error=exc, model=config.model)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    username = os.getenv("ISSTECH_USERNAME", "").strip()
    if not username:
        print("DAILY_BRIEF_FAILED MissingUsername", file=sys.stderr)
        return 2
    try:
        database = account_database_path(
            username,
            base_database_path=args.data_dir.expanduser() / "workflow-center.sqlite3",
        )
        brief = generate_assistant_brief(
            WorkflowStorage(database),
            provider=_provider(),
        )
        print(
            json.dumps(
                {
                    "status": "succeeded",
                    "source": brief.source.value,
                    "candidate_count": brief.candidate_count,
                    "item_count": len(brief.items),
                    "provider_configured": brief.provider_configured,
                    "fallback_code": brief.fallback_code,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    except Exception as error:
        print(f"DAILY_BRIEF_FAILED {type(error).__name__}", file=sys.stderr)
        return 1
    finally:
        username = ""


if __name__ == "__main__":
    raise SystemExit(main())
