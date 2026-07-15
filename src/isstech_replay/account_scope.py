"""Deterministic, non-identifying storage scope for one upstream account."""

from __future__ import annotations

import hashlib
from pathlib import Path
import unicodedata

from .storage import default_database_path


ACCOUNTS_DIRECTORY = "accounts"


def account_scope_key(username: str) -> str:
    normalized = unicodedata.normalize("NFKC", username).strip().casefold()
    if not normalized or any(character in normalized for character in "\r\n\x00"):
        raise ValueError("account username is invalid")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def account_runtime_dir(data_dir: str | Path, username: str) -> Path:
    return (
        Path(data_dir).expanduser()
        / ACCOUNTS_DIRECTORY
        / account_scope_key(username)
    )


def account_database_path(
    username: str,
    *,
    base_database_path: str | Path | None = None,
) -> Path:
    base = (
        default_database_path()
        if base_database_path is None
        else Path(base_database_path).expanduser()
    )
    scope = account_scope_key(username)
    if base.parent.name == scope and base.parent.parent.name == ACCOUNTS_DIRECTORY:
        return base
    return base.parent / ACCOUNTS_DIRECTORY / scope / base.name
