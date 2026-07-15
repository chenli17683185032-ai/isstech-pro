#!/usr/bin/env python3
"""Render, install, verify, or remove the scheduled-sync LaunchAgent."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from typing import Any, Callable, Sequence

from isstech_replay.scheduler import (
    DEFAULT_KEYCHAIN_TIMEOUT_SECONDS,
    KEYCHAIN_PASSWORD_SERVICE,
    KEYCHAIN_USERNAME_SERVICE,
    LAUNCH_AGENT_LABEL,
    local_account_name,
    read_keychain_value,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "ops" / f"{LAUNCH_AGENT_LABEL}.plist"
DEFAULT_DESTINATION = (
    Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
)
DEFAULT_COMMAND_TIMEOUT_SECONDS = 15.0
ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


class LaunchAgentInstallError(RuntimeError):
    """LaunchAgent validation, activation, or rollback failed."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the workflow-center weekday read-only sync LaunchAgent."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--uninstall", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--template", type=Path, default=TEMPLATE_PATH)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--hour", type=int, default=8)
    parser.add_argument("--minute", type=int, default=30)
    parser.add_argument("--no-bootstrap", action="store_true")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    return parser


def render_plist(
    template_path: Path,
    *,
    repo_root: Path,
    hour: int,
    minute: int,
) -> bytes:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("schedule hour/minute is outside the valid range")
    root = repo_root.expanduser().resolve()
    with template_path.expanduser().open("rb") as source:
        payload = plistlib.load(source)
    if not isinstance(payload, dict):
        raise LaunchAgentInstallError("LaunchAgent template root must be a dictionary")
    python = root / ".venv" / "bin" / "python"
    wrapper = root / "tools" / "scheduled_sync.py"
    data_dir = root / "data"
    log_file = data_dir / "logs" / "scheduled-sync.log"
    payload["Label"] = LAUNCH_AGENT_LABEL
    payload["ProgramArguments"] = [
        str(python),
        str(wrapper),
        "--repo-root",
        str(root),
        "--data-dir",
        str(data_dir),
        "--log-file",
        str(log_file),
        "--keychain-timeout-seconds",
        str(int(DEFAULT_KEYCHAIN_TIMEOUT_SECONDS)),
        "--sync-timeout-seconds",
        "900",
    ]
    payload["WorkingDirectory"] = str(root)
    payload["StartCalendarInterval"] = [
        {"Weekday": weekday, "Hour": hour, "Minute": minute}
        for weekday in range(1, 6)
    ]
    payload["RunAtLoad"] = False
    payload["Umask"] = 0o77
    payload["StandardOutPath"] = "/dev/null"
    payload["StandardErrorPath"] = "/dev/null"
    _validate_payload(payload)
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("Label") != LAUNCH_AGENT_LABEL:
        raise LaunchAgentInstallError("LaunchAgent label is incorrect")
    arguments = payload.get("ProgramArguments")
    if not isinstance(arguments, list) or len(arguments) < 2:
        raise LaunchAgentInstallError("LaunchAgent ProgramArguments are incomplete")
    if not all(isinstance(argument, str) and argument for argument in arguments):
        raise LaunchAgentInstallError("LaunchAgent arguments must be non-empty strings")
    serialized = plistlib.dumps(payload, fmt=plistlib.FMT_XML).lower()
    for forbidden in (
        b"isstech_password",
        b"isstech_username",
        b".ipsa",
        b"api_key",
        b"cookie=",
        b"ticket=",
    ):
        if forbidden in serialized:
            raise LaunchAgentInstallError("LaunchAgent contains a credential-like value")
    intervals = payload.get("StartCalendarInterval")
    if not isinstance(intervals, list) or len(intervals) != 5:
        raise LaunchAgentInstallError("LaunchAgent must contain five weekday intervals")
    if {interval.get("Weekday") for interval in intervals} != set(range(1, 6)):
        raise LaunchAgentInstallError("LaunchAgent weekday intervals are incorrect")
    if payload.get("StandardOutPath") != "/dev/null" or payload.get(
        "StandardErrorPath"
    ) != "/dev/null":
        raise LaunchAgentInstallError("LaunchAgent output must use the private wrapper log")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _run(
    command: list[str],
    *,
    timeout_seconds: float,
    runner: ProcessRunner,
    allowed_returncodes: frozenset[int] = frozenset({0}),
) -> subprocess.CompletedProcess[str]:
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LaunchAgentInstallError(f"command timed out: {command[0]}") from exc
    if completed.returncode not in allowed_returncodes:
        raise LaunchAgentInstallError(
            f"command failed ({completed.returncode}): {' '.join(command[:2])}"
        )
    return completed


def _keychain_ready(account: str, timeout_seconds: float) -> None:
    username = read_keychain_value(
        KEYCHAIN_USERNAME_SERVICE,
        account,
        min(timeout_seconds, DEFAULT_KEYCHAIN_TIMEOUT_SECONDS),
    )
    password = read_keychain_value(
        KEYCHAIN_PASSWORD_SERVICE,
        account,
        min(timeout_seconds, DEFAULT_KEYCHAIN_TIMEOUT_SECONDS),
    )
    if not username.strip() or not password:
        raise LaunchAgentInstallError("scheduled Keychain values are empty")
    username = ""
    password = ""


def install_agent(
    content: bytes,
    *,
    destination: Path,
    repo_root: Path,
    bootstrap: bool,
    timeout_seconds: float,
    runner: ProcessRunner = subprocess.run,
    require_keychain: Callable[[str, float], None] = _keychain_ready,
) -> None:
    if timeout_seconds <= 0:
        raise ValueError("command timeout must be positive")
    root = repo_root.expanduser().resolve()
    for required in (root / ".venv" / "bin" / "python", root / "tools" / "scheduled_sync.py"):
        if not required.is_file():
            raise LaunchAgentInstallError(f"required scheduled-sync file is missing: {required}")
    destination = destination.expanduser()
    if not bootstrap:
        _atomic_write(destination, content)
        return

    account = local_account_name()
    require_keychain(account, timeout_seconds)
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LAUNCH_AGENT_LABEL}"
    was_loaded = (
        _run(
            ["/bin/launchctl", "print", target],
            timeout_seconds=timeout_seconds,
            runner=runner,
            allowed_returncodes=frozenset({0, 113}),
        ).returncode
        == 0
    )
    previous = destination.read_bytes() if destination.is_file() else None
    backup = destination.with_name(destination.name + ".backup")
    if previous is not None:
        _atomic_write(backup, previous)
    old_service_stopped = False
    bootstrap_attempted = False
    try:
        _atomic_write(destination, content)
        _run(
            ["/usr/bin/plutil", "-lint", str(destination)],
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        if was_loaded:
            _run(
                ["/bin/launchctl", "bootout", target],
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
            old_service_stopped = True
        bootstrap_attempted = True
        _run(
            ["/bin/launchctl", "bootstrap", domain, str(destination)],
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        _run(
            ["/bin/launchctl", "enable", target],
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        _run(
            ["/bin/launchctl", "print", target],
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
    except Exception as install_error:
        if bootstrap_attempted:
            try:
                _run(
                    ["/bin/launchctl", "bootout", target],
                    timeout_seconds=timeout_seconds,
                    runner=runner,
                    allowed_returncodes=frozenset({0, 3, 113}),
                )
            except Exception:
                pass
        if previous is None:
            destination.unlink(missing_ok=True)
        else:
            _atomic_write(destination, previous)
            if old_service_stopped:
                try:
                    _run(
                        ["/bin/launchctl", "bootstrap", domain, str(destination)],
                        timeout_seconds=timeout_seconds,
                        runner=runner,
                    )
                except Exception as rollback_error:
                    raise LaunchAgentInstallError(
                        "new LaunchAgent failed and previous service rollback also failed"
                    ) from rollback_error
        raise LaunchAgentInstallError("new LaunchAgent failed; previous state was restored") from install_error


def uninstall_agent(
    *,
    destination: Path,
    timeout_seconds: float,
    runner: ProcessRunner = subprocess.run,
) -> None:
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LAUNCH_AGENT_LABEL}"
    loaded = (
        _run(
            ["/bin/launchctl", "print", target],
            timeout_seconds=timeout_seconds,
            runner=runner,
            allowed_returncodes=frozenset({0, 113}),
        ).returncode
        == 0
    )
    if loaded:
        _run(
            ["/bin/launchctl", "bootout", target],
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
    destination.expanduser().unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.timeout_seconds <= 0:
            raise ValueError("--timeout-seconds must be positive")
        if args.uninstall:
            uninstall_agent(
                destination=args.destination,
                timeout_seconds=args.timeout_seconds,
            )
            print(f"LaunchAgent removed: {args.destination.expanduser()}")
            return 0
        content = render_plist(
            args.template,
            repo_root=args.repo_root,
            hour=args.hour,
            minute=args.minute,
        )
        if args.dry_run:
            sys.stdout.buffer.write(content)
            return 0
        install_agent(
            content,
            destination=args.destination,
            repo_root=args.repo_root,
            bootstrap=not args.no_bootstrap,
            timeout_seconds=args.timeout_seconds,
        )
        action = "written" if args.no_bootstrap else "installed and loaded"
        print(f"LaunchAgent {action}: {args.destination.expanduser()}")
        return 0
    except Exception as error:
        print(f"LAUNCH_AGENT_FAILED {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
