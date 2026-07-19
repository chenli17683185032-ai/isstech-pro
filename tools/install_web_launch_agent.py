#!/usr/bin/env python3
"""Render, install, verify, or remove the persistent local Web LaunchAgent."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import re
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Sequence
from urllib.request import urlopen

from isstech_replay.runtime_deployment import (
    DEFAULT_DEPLOY_TIMEOUT_SECONDS,
    DEFAULT_RELEASES_DIR,
    DEFAULT_RUNTIME_DATA_DIR,
    deploy_runtime_release,
    runtime_release_id,
    seed_runtime_data,
)
from isstech_replay.scheduler import LOCAL_WORKSPACE_URL


WEB_LAUNCH_AGENT_LABEL = "com.isstech.workflow-center.web"
REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "ops" / f"{WEB_LAUNCH_AGENT_LABEL}.plist"
DEFAULT_DESTINATION = (
    Path.home() / "Library" / "LaunchAgents" / f"{WEB_LAUNCH_AGENT_LABEL}.plist"
)
DEFAULT_COMMAND_TIMEOUT_SECONDS = 15.0
ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]
HealthChecker = Callable[[str, float], bool]
PortReleaseChecker = Callable[[float], bool]


class WebLaunchAgentInstallError(RuntimeError):
    """Web LaunchAgent validation, activation, health, or rollback failed."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the persistent loopback workflow-center Web service."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--uninstall", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--releases-dir", type=Path, default=DEFAULT_RELEASES_DIR)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_RUNTIME_DATA_DIR)
    parser.add_argument("--template", type=Path, default=TEMPLATE_PATH)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--no-bootstrap", action="store_true")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--deploy-timeout-seconds",
        type=float,
        default=DEFAULT_DEPLOY_TIMEOUT_SECONDS,
    )
    return parser


def render_web_plist(
    template_path: Path,
    *,
    runtime_root: Path,
    data_dir: Path | None = None,
) -> bytes:
    root = runtime_root.expanduser().resolve()
    runtime_data = (data_dir or root / "data").expanduser().resolve()
    with template_path.expanduser().open("rb") as source:
        payload = plistlib.load(source)
    if not isinstance(payload, dict):
        raise WebLaunchAgentInstallError("Web LaunchAgent template root is invalid")
    payload["Label"] = WEB_LAUNCH_AGENT_LABEL
    payload["ProgramArguments"] = [
        str(root / ".venv" / "bin" / "python"),
        "-m",
        "isstech_replay.api",
    ]
    payload["WorkingDirectory"] = str(root)
    payload["EnvironmentVariables"] = {"ISSTECH_DATA_DIR": str(runtime_data)}
    payload["RunAtLoad"] = True
    payload["KeepAlive"] = True
    payload["Umask"] = 0o77
    payload["StandardOutPath"] = "/dev/null"
    payload["StandardErrorPath"] = "/dev/null"
    _validate_payload(payload)
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("Label") != WEB_LAUNCH_AGENT_LABEL:
        raise WebLaunchAgentInstallError("Web LaunchAgent label is incorrect")
    arguments = payload.get("ProgramArguments")
    if not isinstance(arguments, list) or len(arguments) != 3:
        raise WebLaunchAgentInstallError("Web LaunchAgent program is invalid")
    if (
        not isinstance(arguments[0], str)
        or not arguments[0].endswith("/.venv/bin/python")
        or arguments[1:] != ["-m", "isstech_replay.api"]
    ):
        raise WebLaunchAgentInstallError("Web LaunchAgent executable is invalid")
    if payload.get("RunAtLoad") is not True or payload.get("KeepAlive") is not True:
        raise WebLaunchAgentInstallError("Web LaunchAgent recovery settings are invalid")
    if payload.get("Umask") != 0o77:
        raise WebLaunchAgentInstallError("Web LaunchAgent umask is not private")
    if payload.get("StandardOutPath") != "/dev/null" or payload.get(
        "StandardErrorPath"
    ) != "/dev/null":
        raise WebLaunchAgentInstallError("Web LaunchAgent output must be disabled")
    serialized = plistlib.dumps(payload, fmt=plistlib.FMT_XML).lower()
    for forbidden in (b"password", b"api_key", b"cookie=", b"ticket=", b".ipsa"):
        if forbidden in serialized:
            raise WebLaunchAgentInstallError("Web LaunchAgent contains a secret-like value")


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
        raise WebLaunchAgentInstallError(f"command timed out: {command[0]}") from exc
    if completed.returncode not in allowed_returncodes:
        raise WebLaunchAgentInstallError(
            f"command failed ({completed.returncode}): {' '.join(command[:2])}"
        )
    return completed


def _health_check(url: str, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=min(1.0, timeout_seconds)) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def _port_released(timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(min(0.2, remaining))
            if connection.connect_ex(("127.0.0.1", 8000)) != 0:
                return True
        time.sleep(min(0.1, remaining))


def _launch_agent_is_running(output: str) -> bool:
    return bool(
        re.search(r"^\s*state = running\s*$", output, flags=re.MULTILINE)
        and re.search(r"^\s*pid = [1-9][0-9]*\s*$", output, flags=re.MULTILINE)
    )


def _wait_for_running_agent(
    target: str,
    timeout_seconds: float,
    runner: ProcessRunner,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        completed = _run(
            ["/bin/launchctl", "print", target],
            timeout_seconds=max(0.1, min(1.0, timeout_seconds)),
            runner=runner,
            allowed_returncodes=frozenset({0, 113}),
        )
        if completed.returncode == 0 and _launch_agent_is_running(completed.stdout):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.2, remaining))


def _bootstrap_agent(
    domain: str,
    destination: Path,
    timeout_seconds: float,
    runner: ProcessRunner,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: WebLaunchAgentInstallError | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if last_error is None:
                raise WebLaunchAgentInstallError("Web LaunchAgent bootstrap timed out")
            raise last_error
        try:
            _run(
                ["/bin/launchctl", "bootstrap", domain, str(destination)],
                timeout_seconds=min(2.0, remaining),
                runner=runner,
            )
            return
        except WebLaunchAgentInstallError as error:
            last_error = error
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))


def install_web_agent(
    content: bytes,
    *,
    destination: Path,
    runtime_root: Path,
    bootstrap: bool,
    timeout_seconds: float,
    runner: ProcessRunner = subprocess.run,
    health_checker: HealthChecker = _health_check,
    port_release_checker: PortReleaseChecker = _port_released,
) -> None:
    if timeout_seconds <= 0:
        raise ValueError("command timeout must be positive")
    root = runtime_root.expanduser().resolve()
    executable = root / ".venv" / "bin" / "python"
    if not executable.is_file():
        raise WebLaunchAgentInstallError(f"Web executable is missing: {executable}")
    destination = destination.expanduser()
    if not bootstrap:
        _atomic_write(destination, content)
        return

    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{WEB_LAUNCH_AGENT_LABEL}"
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
            if not port_release_checker(timeout_seconds):
                raise WebLaunchAgentInstallError(
                    "previous Web LaunchAgent did not release loopback port"
                )
        bootstrap_attempted = True
        _bootstrap_agent(domain, destination, timeout_seconds, runner)
        _run(
            ["/bin/launchctl", "enable", target],
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        if not _wait_for_running_agent(target, timeout_seconds, runner):
            raise WebLaunchAgentInstallError(
                "Web LaunchAgent did not reach running state with a PID"
            )
        if not health_checker(LOCAL_WORKSPACE_URL + "health", timeout_seconds):
            raise WebLaunchAgentInstallError("Web LaunchAgent health check failed")
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
                    _bootstrap_agent(
                        domain,
                        destination,
                        timeout_seconds,
                        runner,
                    )
                except Exception as rollback_error:
                    raise WebLaunchAgentInstallError(
                        "new Web LaunchAgent failed and rollback also failed"
                    ) from rollback_error
        raise WebLaunchAgentInstallError(
            "new Web LaunchAgent failed; previous state was restored"
        ) from install_error


def uninstall_web_agent(
    *,
    destination: Path,
    timeout_seconds: float,
    runner: ProcessRunner = subprocess.run,
) -> None:
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{WEB_LAUNCH_AGENT_LABEL}"
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
        if args.timeout_seconds <= 0 or args.deploy_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        if args.uninstall:
            uninstall_web_agent(
                destination=args.destination,
                timeout_seconds=args.timeout_seconds,
            )
            print(f"Web LaunchAgent removed: {args.destination.expanduser()}")
            return 0
        release_id = runtime_release_id(args.repo_root)
        runtime_root = args.releases_dir.expanduser().resolve() / release_id
        content = render_web_plist(
            args.template,
            runtime_root=runtime_root,
            data_dir=args.data_dir,
        )
        if args.dry_run:
            sys.stdout.buffer.write(content)
            return 0
        runtime_root = deploy_runtime_release(
            args.repo_root,
            releases_dir=args.releases_dir,
            timeout_seconds=args.deploy_timeout_seconds,
        )
        seed_runtime_data(args.repo_root / "data", args.data_dir)
        install_web_agent(
            content,
            destination=args.destination,
            runtime_root=runtime_root,
            bootstrap=not args.no_bootstrap,
            timeout_seconds=args.timeout_seconds,
        )
        action = "written" if args.no_bootstrap else "installed and healthy"
        print(f"Web LaunchAgent {action}: {args.destination.expanduser()}")
        return 0
    except Exception as error:
        print(f"WEB_LAUNCH_AGENT_FAILED {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
