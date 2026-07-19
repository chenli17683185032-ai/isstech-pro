"""The local Web service LaunchAgent is private, recoverable, and health-gated."""

from __future__ import annotations

from pathlib import Path
import plistlib
import subprocess

import pytest

from tools.install_web_launch_agent import (
    WEB_LAUNCH_AGENT_LABEL,
    WebLaunchAgentInstallError,
    install_web_agent,
    render_web_plist,
)


TEMPLATE = (
    Path(__file__).parents[1]
    / "ops"
    / "com.isstech.workflow-center.web.plist"
)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    executable = repo / ".venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    return repo


def test_web_plist_is_loopback_service_configuration_without_secrets(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    data_dir = tmp_path / "Application Support" / "data"
    rendered = render_web_plist(TEMPLATE, runtime_root=repo, data_dir=data_dir)
    payload = plistlib.loads(rendered)

    assert payload["Label"] == WEB_LAUNCH_AGENT_LABEL
    assert payload["ProgramArguments"] == [
        str(repo / ".venv" / "bin" / "python"),
        "-m",
        "isstech_replay.api",
    ]
    assert payload["WorkingDirectory"] == str(repo)
    assert payload["EnvironmentVariables"] == {"ISSTECH_DATA_DIR": str(data_dir)}
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert payload["Umask"] == 0o77
    lowered = rendered.lower()
    for forbidden in (b"password", b"api_key", b"cookie", b"ticket", b".ipsa"):
        assert forbidden not in lowered


def test_web_plist_write_without_bootstrap_is_atomic_and_private(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    content = render_web_plist(TEMPLATE, runtime_root=repo)
    destination = tmp_path / "LaunchAgents" / "web.plist"

    install_web_agent(
        content,
        destination=destination,
        runtime_root=repo,
        bootstrap=False,
        timeout_seconds=5,
    )

    assert destination.read_bytes() == content
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.parent.stat().st_mode & 0o777 == 0o700


def test_web_health_failure_restores_previous_loaded_service(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    content = render_web_plist(TEMPLATE, runtime_root=repo)
    destination = tmp_path / "LaunchAgents" / "web.plist"
    destination.parent.mkdir()
    previous = b"previous web plist"
    destination.write_bytes(previous)
    bootstrap_calls = 0

    def runner(command, **_kwargs):
        nonlocal bootstrap_calls
        action = command[1]
        if action == "print":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="state = running\npid = 42\n",
                stderr="",
            )
        if action == "bootstrap":
            bootstrap_calls += 1
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(
        WebLaunchAgentInstallError,
        match="previous state was restored",
    ):
        install_web_agent(
            content,
            destination=destination,
            runtime_root=repo,
            bootstrap=True,
            timeout_seconds=5,
            runner=runner,
            health_checker=lambda _url, _timeout: False,
            port_release_checker=lambda _timeout: True,
        )

    assert destination.read_bytes() == previous
    assert destination.with_name(destination.name + ".backup").read_bytes() == previous
    assert bootstrap_calls == 2


def test_web_install_requires_health_after_launchctl_load(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    content = render_web_plist(TEMPLATE, runtime_root=repo)
    destination = tmp_path / "LaunchAgents" / "web.plist"
    checked: list[tuple[str, float]] = []

    def runner(command, **_kwargs):
        code = 113 if command[1] == "print" and not destination.exists() else 0
        output = "state = running\npid = 42\n" if code == 0 else ""
        return subprocess.CompletedProcess(command, code, stdout=output, stderr="")

    def health(url: str, timeout: float) -> bool:
        checked.append((url, timeout))
        return True

    install_web_agent(
        content,
        destination=destination,
        runtime_root=repo,
        bootstrap=True,
        timeout_seconds=5,
        runner=runner,
        health_checker=health,
    )

    assert checked == [("http://127.0.0.1:8000/health", 5)]
    assert destination.stat().st_mode & 0o777 == 0o600


def test_web_install_rejects_other_process_health_when_target_has_no_pid(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    content = render_web_plist(TEMPLATE, runtime_root=repo)
    destination = tmp_path / "LaunchAgents" / "web.plist"
    health_calls = 0

    def runner(command, **_kwargs):
        if command[1] == "print" and not destination.exists():
            return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="state = spawn scheduled\nlast exit code = 1\n",
            stderr="",
        )

    def health(_url: str, _timeout: float) -> bool:
        nonlocal health_calls
        health_calls += 1
        return True

    with pytest.raises(
        WebLaunchAgentInstallError,
        match="previous state was restored",
    ):
        install_web_agent(
            content,
            destination=destination,
            runtime_root=repo,
            bootstrap=True,
            timeout_seconds=0.01,
            runner=runner,
            health_checker=health,
        )

    assert health_calls == 0
    assert not destination.exists()


def test_web_install_retries_transient_bootstrap_failure(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    content = render_web_plist(TEMPLATE, runtime_root=repo)
    destination = tmp_path / "LaunchAgents" / "web.plist"
    bootstrap_calls = 0

    def runner(command, **_kwargs):
        nonlocal bootstrap_calls
        action = command[1]
        if action == "bootstrap":
            bootstrap_calls += 1
            code = 5 if bootstrap_calls == 1 else 0
            return subprocess.CompletedProcess(command, code, stdout="", stderr="")
        if action == "print":
            if bootstrap_calls < 2:
                return subprocess.CompletedProcess(command, 113, stdout="", stderr="")
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="state = running\npid = 42\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    install_web_agent(
        content,
        destination=destination,
        runtime_root=repo,
        bootstrap=True,
        timeout_seconds=1,
        runner=runner,
        health_checker=lambda _url, _timeout: True,
    )

    assert bootstrap_calls == 2


def test_web_update_waits_for_old_port_release_before_bootstrap(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    content = render_web_plist(TEMPLATE, runtime_root=repo)
    destination = tmp_path / "LaunchAgents" / "web.plist"
    destination.parent.mkdir()
    destination.write_bytes(content)
    actions: list[str] = []
    port_checks: list[float] = []

    def runner(command, **_kwargs):
        action = command[1]
        actions.append(action)
        if action == "print":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="state = running\npid = 42\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def port_released(timeout: float) -> bool:
        port_checks.append(timeout)
        actions.append("port-released")
        return True

    install_web_agent(
        content,
        destination=destination,
        runtime_root=repo,
        bootstrap=True,
        timeout_seconds=5,
        runner=runner,
        health_checker=lambda _url, _timeout: True,
        port_release_checker=port_released,
    )

    assert port_checks == [5]
    assert actions.index("bootout") < actions.index("port-released")
    assert actions.index("port-released") < actions.index("bootstrap")
