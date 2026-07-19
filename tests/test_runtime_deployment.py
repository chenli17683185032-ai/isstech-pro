"""The LaunchAgent runtime is immutable, private, and independent of Documents."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess

from isstech_replay.runtime_deployment import (
    deploy_runtime_release,
    runtime_release_id,
    seed_runtime_data,
)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    package = source / "src" / "isstech_replay"
    tools = source / "tools"
    package.mkdir(parents=True)
    tools.mkdir()
    (source / "README.md").write_text("runtime\n", encoding="utf-8")
    (source / "pyproject.toml").write_text("[project]\nname='runtime'\n", encoding="utf-8")
    (source / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name in ("generate_daily_brief.py", "scheduled_sync.py", "sync_work_items.py"):
        (tools / name).write_text("", encoding="utf-8")
    return source


def test_runtime_release_is_content_addressed_and_excludes_unlisted_files(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    (source / ".env").write_text("SECRET=value\n", encoding="utf-8")
    releases = tmp_path / "Application Support" / "releases"
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        calls.append(command)
        if command[1] == "sync":
            runtime = Path(command[-1])
            python = runtime / ".venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    release_id = runtime_release_id(source)
    deployed = deploy_runtime_release(
        source,
        releases_dir=releases,
        uv_executable=Path("/usr/bin/true"),
        runner=runner,
    )

    assert deployed == releases / release_id
    assert not (deployed / ".env").exists()
    assert (deployed / "src" / "isstech_replay" / "__init__.py").is_file()
    assert deployed.stat().st_mode & 0o777 == 0o700
    assert len(calls) == 3

    assert deploy_runtime_release(source, releases_dir=releases, runner=runner) == deployed
    assert len(calls) == 4


def test_runtime_data_seed_uses_sqlite_backup_and_private_modes(tmp_path: Path) -> None:
    source = tmp_path / "source-data"
    source.mkdir()
    database = source / "accounts" / "scope" / "workflow-center.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('current')")
    (database.parent / "workflow-center.sqlite3-wal").write_bytes(b"transient")
    (database.parent / "workflow-center.sqlite3-shm").write_bytes(b"transient")
    (database.parent / "workflow-center.sqlite3-journal").write_bytes(b"")
    (source / "logs").mkdir()
    (source / "logs" / "scheduled-sync.log").write_text("safe\n", encoding="utf-8")

    destination = tmp_path / "Application Support" / "data"
    seeded = seed_runtime_data(source, destination)

    assert seeded == destination
    with sqlite3.connect(destination / database.relative_to(source)) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("current",)
    assert not (destination / database.relative_to(source).with_suffix(".sqlite3-wal")).exists()
    assert not (destination / database.relative_to(source).with_suffix(".sqlite3-shm")).exists()
    assert not (
        destination / database.relative_to(source).with_suffix(".sqlite3-journal")
    ).exists()
    for path in (destination, *destination.rglob("*")):
        assert path.stat().st_mode & 0o077 == 0

    assert seed_runtime_data(source, destination) == destination


def test_runtime_release_id_changes_with_package_content(tmp_path: Path) -> None:
    source = _source(tmp_path)
    before = runtime_release_id(source)
    package = source / "src" / "isstech_replay" / "__init__.py"
    package.write_text("VERSION = 'next'\n", encoding="utf-8")

    assert runtime_release_id(source) != before
