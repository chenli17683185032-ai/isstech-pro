#!/usr/bin/env python3
"""Export or verify the committed OpenAPI schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from isstech_replay.api import app


DEFAULT_OUTPUT = Path("docs/openapi.json")


def rendered_schema() -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rendered = rendered_schema()
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            print(f"OpenAPI drift: run {Path(__file__).name} without --check")
            return 1
        print(f"OpenAPI matches runtime: {args.output}")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
