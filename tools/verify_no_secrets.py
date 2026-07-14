#!/usr/bin/env python3
"""Fail when commit-eligible files contain likely live credentials or tickets."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}
SKIP_PREFIXES = (
    ROOT / "captures" / "raw",
    ROOT / "captures" / "playwright",
)

PATTERNS = (
    ("api-key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("ipsa-cookie", re.compile(r"\.iPSA=([^;\s\"'`]+)")),
    ("password-form", re.compile(r"emp_Password=([^&\s\"'`]+)")),
)


def _commit_eligible(path: Path) -> bool:
    if path.resolve() == Path(__file__).resolve():
        return False
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    if any(path.is_relative_to(prefix) for prefix in SKIP_PREFIXES):
        return False
    if path.name.startswith("login_fail_") or path.name == ".env":
        return False
    return path.is_file()


def _is_placeholder(value: str) -> bool:
    upper = value.upper()
    return upper.startswith("TEST_") or value.startswith("<") or set(value) == {"."}


def scan(root: Path = ROOT) -> list[str]:
    findings: list[str] = []
    for path in root.rglob("*"):
        if not _commit_eligible(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(1) if match.lastindex else match.group(0)
                if _is_placeholder(value):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{path.relative_to(root)}:{line}: {label}")
    return findings


def main() -> int:
    findings = scan()
    if findings:
        print("Potential secrets found:")
        print("\n".join(findings))
        return 1
    print("No likely live secrets found in commit-eligible files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
