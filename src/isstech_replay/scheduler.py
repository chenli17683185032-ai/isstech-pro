"""Bounded scheduled-sync wrapper with Keychain credentials and private logs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable

from .sync import safe_error_message


LAUNCH_AGENT_LABEL = "com.isstech.workflow-center.sync"
KEYCHAIN_USERNAME_SERVICE = f"{LAUNCH_AGENT_LABEL}.username"
KEYCHAIN_PASSWORD_SERVICE = f"{LAUNCH_AGENT_LABEL}.password"
DEFAULT_KEYCHAIN_TIMEOUT_SECONDS = 10.0
DEFAULT_SYNC_TIMEOUT_SECONDS = 15 * 60.0
_RUN_ID_RE = re.compile(r"\brun_id=([A-Za-z0-9_-]+)\b")


class ScheduledSyncError(RuntimeError):
    """A bounded scheduler facility or child-sync operation failed."""


@dataclass(frozen=True, slots=True)
class ScheduledSyncConfig:
    repo_root: Path
    python_executable: Path
    data_dir: Path
    log_file: Path
    keychain_timeout_seconds: float = DEFAULT_KEYCHAIN_TIMEOUT_SECONDS
    sync_timeout_seconds: float = DEFAULT_SYNC_TIMEOUT_SECONDS


CredentialReader = Callable[[str, str, float], str]
ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


def local_account_name() -> str:
    account = getpass.getuser().strip()
    if not account or any(character in account for character in "\r\n\x00"):
        raise ScheduledSyncError("local Keychain account name is invalid")
    return account


def read_keychain_value(
    service: str,
    account: str,
    timeout_seconds: float,
    *,
    runner: ProcessRunner = subprocess.run,
) -> str:
    if timeout_seconds <= 0:
        raise ValueError("Keychain timeout must be positive")
    try:
        completed = runner(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                account,
                "-s",
                service,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScheduledSyncError(f"Keychain lookup timed out for service {service}") from exc
    if completed.returncode != 0:
        raise ScheduledSyncError(f"Keychain item is unavailable for service {service}")
    value = completed.stdout.rstrip("\r\n")
    if not value:
        raise ScheduledSyncError(f"Keychain item is empty for service {service}")
    return value


def append_private_log(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    payload = (
        json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run_scheduled_sync(
    config: ScheduledSyncConfig,
    *,
    credential_reader: CredentialReader = read_keychain_value,
    runner: ProcessRunner = subprocess.run,
    account: str | None = None,
    now: datetime | None = None,
) -> int:
    timestamp = _utc_iso(now or datetime.now(UTC))
    if config.keychain_timeout_seconds <= 0 or config.sync_timeout_seconds <= 0:
        raise ValueError("scheduler timeouts must be positive")
    repo_root = config.repo_root.expanduser().resolve()
    # Keep the virtualenv symlink intact; resolving it launches the base interpreter
    # without the virtualenv's site-packages.
    python_executable = config.python_executable.expanduser().absolute()
    data_dir = config.data_dir.expanduser().resolve()
    log_file = config.log_file.expanduser().resolve()
    sync_script = repo_root / "tools" / "sync_work_items.py"
    if not python_executable.is_file():
        raise ScheduledSyncError(f"scheduled Python executable is missing: {python_executable}")
    if not sync_script.is_file():
        raise ScheduledSyncError(f"manual sync CLI is missing: {sync_script}")
    actual_account = account or local_account_name()

    try:
        username = credential_reader(
            KEYCHAIN_USERNAME_SERVICE,
            actual_account,
            config.keychain_timeout_seconds,
        ).strip()
        password = credential_reader(
            KEYCHAIN_PASSWORD_SERVICE,
            actual_account,
            config.keychain_timeout_seconds,
        )
        if not username or not password:
            raise ScheduledSyncError("scheduled credentials are empty")
    except Exception as error:
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="keychain",
            error=error,
            exit_code=1,
        )
        return 1

    command = [
        str(python_executable),
        str(sync_script),
        "--data-dir",
        str(data_dir),
        "--json",
        "--csv",
    ]
    environment = os.environ.copy()
    environment["ISSTECH_USERNAME"] = username
    environment["ISSTECH_PASSWORD"] = password
    try:
        completed = runner(
            command,
            cwd=repo_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=config.sync_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="sync",
            error=ScheduledSyncError("manual sync CLI timed out"),
            exit_code=124,
        )
        return 124
    except Exception as error:
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="sync",
            error=error,
            exit_code=1,
        )
        return 1
    finally:
        environment.pop("ISSTECH_PASSWORD", None)
        environment.pop("ISSTECH_USERNAME", None)
        password = ""
        username = ""

    if completed.returncode != 0:
        message = completed.stderr.strip() or f"manual sync CLI exited {completed.returncode}"
        run_match = _RUN_ID_RE.search(message)
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="sync",
            error=ScheduledSyncError(message),
            exit_code=completed.returncode or 1,
            run_id=run_match.group(1) if run_match else None,
        )
        return completed.returncode or 1
    try:
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("sync JSON summary is not an object")
        if payload.get("status") != "succeeded":
            raise ValueError("sync JSON summary status is not succeeded")
        record = {
            "timestamp": timestamp,
            "outcome": "succeeded",
            "exit_code": 0,
            "run_id": str(payload["run_id"]),
            "status": str(payload["status"]),
            "observed_count": int(payload["observed_count"]),
            "actionable_count": int(payload["actionable_count"]),
            "event_count": len(payload.get("events", [])),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="summary",
            error=ScheduledSyncError(f"manual sync CLI returned invalid JSON: {type(error).__name__}"),
            exit_code=1,
        )
        return 1
    append_private_log(log_file, record)
    return 0


def _record_failure(
    log_file: Path,
    *,
    timestamp: str,
    stage: str,
    error: BaseException,
    exit_code: int,
    run_id: str | None = None,
) -> None:
    append_private_log(
        log_file,
        {
            "timestamp": timestamp,
            "outcome": "failed",
            "stage": stage,
            "exit_code": exit_code,
            "run_id": run_id,
            "error_type": type(error).__name__,
            "error_message": safe_error_message(error),
        },
    )


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("scheduler timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat()
