"""Account-derived runtime paths are stable, isolated, and non-identifying."""

from __future__ import annotations

from pathlib import Path

import pytest

from isstech_replay.account_scope import (
    account_database_path,
    account_runtime_dir,
    account_scope_key,
)


def test_scope_normalizes_case_whitespace_and_unicode_compatibility() -> None:
    expected = account_scope_key("alice")

    assert account_scope_key(" Alice ") == expected
    assert account_scope_key("ＡＬＩＣＥ") == expected
    assert account_scope_key("bob") != expected
    assert len(expected) == 64


@pytest.mark.parametrize("username", ["", "   ", "alice\nadmin", "alice\x00admin"])
def test_scope_rejects_invalid_account_names(username: str) -> None:
    with pytest.raises(ValueError, match="username is invalid"):
        account_scope_key(username)


def test_scoped_paths_do_not_contain_the_raw_username(tmp_path: Path) -> None:
    username = "Sensitive.User@Example"
    base = tmp_path / "custom.sqlite3"
    runtime_dir = account_runtime_dir(tmp_path, username)
    database = account_database_path(username, base_database_path=base)

    assert runtime_dir.parent == tmp_path / "accounts"
    assert database == runtime_dir / base.name
    assert username.casefold() not in str(runtime_dir).casefold()
    assert account_database_path(username, base_database_path=database) == database
