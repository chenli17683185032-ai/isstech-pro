"""Build a private, immutable runtime outside macOS-protected project folders."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Callable


APPLICATION_SUPPORT_ROOT = (
    Path.home() / "Library" / "Application Support" / "com.isstech.workflow-center"
)
DEFAULT_RELEASES_DIR = APPLICATION_SUPPORT_ROOT / "releases"
DEFAULT_RUNTIME_DATA_DIR = APPLICATION_SUPPORT_ROOT / "data"
RUNTIME_TOOL_PATHS = (
    Path("tools/generate_daily_brief.py"),
    Path("tools/scheduled_sync.py"),
    Path("tools/sync_work_items.py"),
)
RUNTIME_METADATA_PATHS = (
    Path("README.md"),
    Path("pyproject.toml"),
    Path("uv.lock"),
)
DEFAULT_DEPLOY_TIMEOUT_SECONDS = 5 * 60.0
ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


class RuntimeDeploymentError(RuntimeError):
    """A private runtime release or data seed could not be validated."""


def runtime_source_files(source_root: Path) -> tuple[Path, ...]:
    root = source_root.expanduser().resolve()
    required = [root / relative for relative in RUNTIME_METADATA_PATHS]
    required.extend(root / relative for relative in RUNTIME_TOOL_PATHS)
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise RuntimeDeploymentError(f"runtime source file is missing: {missing[0]}")
    package_root = root / "src" / "isstech_replay"
    if not package_root.is_dir():
        raise RuntimeDeploymentError(f"runtime package is missing: {package_root}")
    package_files = [
        path
        for path in package_root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    ]
    return tuple(sorted((*required, *package_files), key=lambda path: str(path.relative_to(root))))


def runtime_release_id(source_root: Path) -> str:
    root = source_root.expanduser().resolve()
    digest = hashlib.sha256()
    for path in runtime_source_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()[:16]


def deploy_runtime_release(
    source_root: Path,
    *,
    releases_dir: Path = DEFAULT_RELEASES_DIR,
    uv_executable: Path | None = None,
    timeout_seconds: float = DEFAULT_DEPLOY_TIMEOUT_SECONDS,
    runner: ProcessRunner = subprocess.run,
) -> Path:
    if timeout_seconds <= 0:
        raise ValueError("runtime deployment timeout must be positive")
    source = source_root.expanduser().resolve()
    releases = releases_dir.expanduser().resolve()
    if releases == source or source in releases.parents:
        raise RuntimeDeploymentError("runtime releases directory cannot be inside source tree")
    release_id = runtime_release_id(source)
    destination = releases / release_id
    if destination.is_dir():
        _smoke_runtime(destination, timeout_seconds=timeout_seconds, runner=runner)
        return destination

    uv = uv_executable or _find_uv()
    releases.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(releases, 0o700)
    temporary = Path(tempfile.mkdtemp(prefix=f".{release_id}.", dir=releases))
    os.chmod(temporary, 0o700)
    try:
        _copy_runtime_source(source, temporary)
        _run_checked(
            [
                str(uv),
                "sync",
                "--frozen",
                "--no-dev",
                "--no-editable",
                "--no-progress",
                "--directory",
                str(temporary),
            ],
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        _smoke_runtime(temporary, timeout_seconds=timeout_seconds, runner=runner)
        os.replace(temporary, destination)
        _smoke_runtime(destination, timeout_seconds=timeout_seconds, runner=runner)
        return destination
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def seed_runtime_data(source_data_dir: Path, destination_data_dir: Path) -> Path:
    source = source_data_dir.expanduser().resolve()
    destination = destination_data_dir.expanduser().resolve()
    if not source.is_dir():
        raise RuntimeDeploymentError(f"runtime source data is missing: {source}")
    if destination == source or source in destination.parents:
        raise RuntimeDeploymentError("runtime data destination cannot be inside source data")
    if destination.exists():
        if not destination.is_dir() or destination.is_symlink():
            raise RuntimeDeploymentError("runtime data destination is not a private directory")
        _validate_runtime_data(destination)
        return destination

    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(destination.parent, 0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".data.", dir=destination.parent))
    os.chmod(temporary, 0o700)
    try:
        _copy_runtime_data(source, temporary)
        _validate_runtime_data(temporary)
        os.replace(temporary, destination)
        return destination
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _copy_runtime_source(source: Path, destination: Path) -> None:
    for path in runtime_source_files(source):
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
        shutil.copyfile(path, target)
        os.chmod(target, 0o600)


def _copy_runtime_data(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise RuntimeDeploymentError(f"runtime data contains a symlink: {path}")
        if path.is_file() and path.name.endswith(
            (".sqlite3-wal", ".sqlite3-shm", ".sqlite3-journal")
        ):
            continue
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(target, 0o700)
            continue
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.suffix == ".sqlite3":
            _backup_sqlite(path, target)
        else:
            shutil.copyfile(path, target)
        os.chmod(target, 0o600)


def _backup_sqlite(source: Path, destination: Path) -> None:
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)


def _validate_runtime_data(data_dir: Path) -> None:
    if data_dir.stat().st_mode & 0o077:
        raise RuntimeDeploymentError("runtime data directory is not private")
    for path in data_dir.rglob("*"):
        if path.is_symlink():
            raise RuntimeDeploymentError(f"runtime data contains a symlink: {path}")
        if path.stat().st_mode & 0o077:
            raise RuntimeDeploymentError(f"runtime data path is not private: {path}")
        if path.is_file() and path.suffix == ".sqlite3":
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise RuntimeDeploymentError(f"runtime SQLite integrity failed: {path}")


def _find_uv() -> Path:
    executable = shutil.which("uv")
    if executable is None:
        raise RuntimeDeploymentError("uv executable is unavailable")
    return Path(executable).resolve()


def _smoke_runtime(
    runtime_root: Path,
    *,
    timeout_seconds: float,
    runner: ProcessRunner,
) -> None:
    python = runtime_root / ".venv" / "bin" / "python"
    if not python.is_file():
        raise RuntimeDeploymentError(f"deployed Python is missing: {python}")
    _run_checked(
        [
            str(python),
            "-c",
            "from isstech_replay.api import app; assert app is not None",
        ],
        timeout_seconds=timeout_seconds,
        runner=runner,
        cwd=runtime_root,
    )


def _run_checked(
    command: list[str],
    *,
    timeout_seconds: float,
    runner: ProcessRunner,
    cwd: Path | None = None,
) -> None:
    try:
        completed = runner(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeDeploymentError(f"runtime command timed out: {command[0]}") from exc
    if completed.returncode != 0:
        raise RuntimeDeploymentError(
            f"runtime command failed ({completed.returncode}): {Path(command[0]).name}"
        )
