#!/usr/bin/env python3
"""Verify local evidence hashes and restrictive permissions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "evidence-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest() -> list[str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    findings: list[str] = []
    for artifact in manifest.get("artifacts", []):
        relative = Path(artifact["path"])
        path = ROOT / relative
        if not path.is_file():
            findings.append(f"missing: {relative}")
            continue
        actual = sha256(path)
        if actual != artifact["sha256"]:
            findings.append(f"hash mismatch: {relative}")
        if artifact.get("sensitivity") in {"high", "medium"}:
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                findings.append(f"permissions too broad ({mode:o}): {relative}")
    return findings


def main() -> int:
    findings = verify_manifest()
    if findings:
        print("Evidence verification failed:")
        print("\n".join(findings))
        return 1
    print("Evidence hashes and sensitive-file permissions verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
