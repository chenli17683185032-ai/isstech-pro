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
DEFAULT_BRIEF_TIMEOUT_SECONDS = 75.0
DEFAULT_OPEN_TIMEOUT_SECONDS = 10.0
LOCAL_WORKSPACE_URL = "http://127.0.0.1:8000/"
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
    brief_timeout_seconds: float = DEFAULT_BRIEF_TIMEOUT_SECONDS
    open_timeout_seconds: float = DEFAULT_OPEN_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class ScheduledDayResult:
    sync_exit_code: int
    brief_exit_code: int
    open_exit_code: int

    @property
    def exit_code(self) -> int:
        for code in (
            self.sync_exit_code,
            self.brief_exit_code,
            self.open_exit_code,
        ):
            if code != 0:
                return code
        return 0


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
            "procurement_observed_count": int(
                payload.get("procurement_observed_count", payload["observed_count"])
            ),
            "readonly_observed_count": int(
                payload.get("readonly_observed_count", 0)
            ),
            "readonly_changed_count": int(
                payload.get("readonly_changed_count", 0)
            ),
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


def run_scheduled_day(
    config: ScheduledSyncConfig,
    *,
    credential_reader: CredentialReader = read_keychain_value,
    runner: ProcessRunner = subprocess.run,
    account: str | None = None,
    now: datetime | None = None,
) -> ScheduledDayResult:
    """Run sync, briefing, and page open without making later stages depend on earlier ones."""
    timestamp = _utc_iso(now or datetime.now(UTC))
    actual_account = account or local_account_name()
    try:
        sync_exit_code = run_scheduled_sync(
            config,
            credential_reader=credential_reader,
            runner=runner,
            account=actual_account,
            now=now,
        )
    except Exception as error:
        _record_failure(
            config.log_file.expanduser().resolve(),
            timestamp=timestamp,
            stage="sync_setup",
            error=error,
            exit_code=1,
        )
        sync_exit_code = 1
    try:
        username = credential_reader(
            KEYCHAIN_USERNAME_SERVICE,
            actual_account,
            config.keychain_timeout_seconds,
        ).strip()
        if not username:
            raise ScheduledSyncError("scheduled username is empty")
    except Exception as error:
        _record_failure(
            config.log_file.expanduser().resolve(),
            timestamp=timestamp,
            stage="brief_identity",
            error=error,
            exit_code=1,
        )
        brief_exit_code = 1
    else:
        try:
            brief_exit_code = _run_scheduled_brief(
                config,
                username=username,
                runner=runner,
                timestamp=timestamp,
            )
        except Exception as error:
            _record_failure(
                config.log_file.expanduser().resolve(),
                timestamp=timestamp,
                stage="brief",
                error=error,
                exit_code=1,
            )
            brief_exit_code = 1
        finally:
            username = ""
    try:
        open_exit_code = _open_local_workspace(
            config,
            runner=runner,
            timestamp=timestamp,
        )
    except Exception as error:
        _record_failure(
            config.log_file.expanduser().resolve(),
            timestamp=timestamp,
            stage="open",
            error=error,
            exit_code=1,
        )
        open_exit_code = 1
    return ScheduledDayResult(
        sync_exit_code=sync_exit_code,
        brief_exit_code=brief_exit_code,
        open_exit_code=open_exit_code,
    )


def _run_scheduled_brief(
    config: ScheduledSyncConfig,
    *,
    username: str,
    runner: ProcessRunner,
    timestamp: str,
) -> int:
    if config.brief_timeout_seconds <= 0:
        raise ValueError("brief timeout must be positive")
    repo_root = config.repo_root.expanduser().resolve()
    python_executable = config.python_executable.expanduser().absolute()
    data_dir = config.data_dir.expanduser().resolve()
    log_file = config.log_file.expanduser().resolve()
    brief_script = repo_root / "tools" / "generate_daily_brief.py"
    if not brief_script.is_file():
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="brief",
            error=ScheduledSyncError("daily briefing CLI is missing"),
            exit_code=1,
        )
        return 1
    environment = os.environ.copy()
    environment["ISSTECH_USERNAME"] = username
    command = [
        str(python_executable),
        str(brief_script),
        "--data-dir",
        str(data_dir),
    ]
    try:
        completed = runner(
            command,
            cwd=repo_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=config.brief_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="brief",
            error=ScheduledSyncError("daily briefing CLI timed out"),
            exit_code=124,
        )
        return 124
    except Exception as error:
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="brief",
            error=error,
            exit_code=1,
        )
        return 1
    finally:
        environment.pop("ISSTECH_USERNAME", None)
    if completed.returncode != 0:
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="brief",
            error=ScheduledSyncError(
                f"daily briefing CLI exited {completed.returncode or 1}"
            ),
            exit_code=completed.returncode or 1,
        )
        return completed.returncode or 1
    try:
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict) or payload.get("status") != "succeeded":
            raise ValueError("daily briefing summary is invalid")
        record = {
            "timestamp": timestamp,
            "stage": "brief",
            "outcome": "succeeded",
            "exit_code": 0,
            "status": "succeeded",
            "source": str(payload["source"]),
            "candidate_count": int(payload["candidate_count"]),
            "item_count": int(payload["item_count"]),
            "provider_configured": bool(payload["provider_configured"]),
            "fallback_code": payload.get("fallback_code"),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="brief",
            error=ScheduledSyncError(
                f"daily briefing CLI returned invalid JSON: {type(error).__name__}"
            ),
            exit_code=1,
        )
        return 1
    append_private_log(log_file, record)
    return 0


def _open_local_workspace(
    config: ScheduledSyncConfig,
    *,
    runner: ProcessRunner,
    timestamp: str,
) -> int:
    if config.open_timeout_seconds <= 0:
        raise ValueError("open timeout must be positive")
    log_file = config.log_file.expanduser().resolve()
    command = ["/usr/bin/open", LOCAL_WORKSPACE_URL]
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=config.open_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="open",
            error=ScheduledSyncError("local workspace open timed out"),
            exit_code=124,
        )
        return 124
    except Exception as error:
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="open",
            error=error,
            exit_code=1,
        )
        return 1
    if completed.returncode != 0:
        _record_failure(
            log_file,
            timestamp=timestamp,
            stage="open",
            error=ScheduledSyncError(
                f"local workspace open exited {completed.returncode or 1}"
            ),
            exit_code=completed.returncode or 1,
        )
        return completed.returncode or 1
    append_private_log(
        log_file,
        {
            "timestamp": timestamp,
            "stage": "open",
            "outcome": "succeeded",
            "exit_code": 0,
        },
    )
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
