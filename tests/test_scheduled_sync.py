"""Scheduled read-only sync is bounded, private, reversible, and CLI-identical."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import plistlib
import subprocess

import pytest

from isstech_replay.scheduler import (
    KEYCHAIN_PASSWORD_SERVICE,
    KEYCHAIN_USERNAME_SERVICE,
    ScheduledSyncConfig,
    ScheduledSyncError,
    read_keychain_value,
    run_scheduled_sync,
)
from tools import configure_sync_keychain as keychain_cli
from tools.install_launch_agent import (
    LaunchAgentInstallError,
    install_agent,
    render_plist,
)


NOW = datetime(2026, 7, 15, 8, 30, tzinfo=UTC)
PASSWORD = "TEST_SCHEDULE_PASSWORD"
USERNAME = "TEST_SCHEDULE_USER"


def _config(tmp_path: Path) -> ScheduledSyncConfig:
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "sync_work_items.py").write_text("# test\n", encoding="utf-8")
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    return ScheduledSyncConfig(
        repo_root=repo,
        python_executable=python,
        data_dir=repo / "data",
        log_file=repo / "data" / "logs" / "scheduled-sync.log",
        keychain_timeout_seconds=2,
        sync_timeout_seconds=30,
    )


def _credentials(service: str, account: str, timeout: float) -> str:
    assert account == "local-test-account"
    assert timeout == 2
    if service == KEYCHAIN_USERNAME_SERVICE:
        return USERNAME
    if service == KEYCHAIN_PASSWORD_SERVICE:
        return PASSWORD
    raise AssertionError(service)


def _log_records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_scheduled_sync_calls_existing_cli_and_logs_only_safe_counts(tmp_path: Path) -> None:
    config = _config(tmp_path)
    captured: dict[str, object] = {}

    def runner(command, **kwargs):
        captured["command"] = list(command)
        captured["cwd"] = kwargs["cwd"]
        captured["environment"] = dict(kwargs["env"])
        payload = {
            "run_id": "run-scheduled-1",
            "status": "succeeded",
            "observed_count": 16,
            "actionable_count": 3,
            "procurement_observed_count": 12,
            "readonly_observed_count": 4,
            "readonly_changed_count": 1,
            "events": [{"kind": "new"}],
            "work_items": [{"title": "MUST_NOT_ENTER_SCHEDULER_LOG"}],
        }
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="summary /private/path\ncsv /private/path",
        )

    result = run_scheduled_sync(
        config,
        credential_reader=_credentials,
        runner=runner,
        account="local-test-account",
        now=NOW,
    )

    assert result == 0
    command = captured["command"]
    assert isinstance(command, list)
    assert command[1] == str(config.repo_root / "tools" / "sync_work_items.py")
    assert command[-2:] == ["--json", "--csv"]
    assert PASSWORD not in command
    assert USERNAME not in command
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["ISSTECH_USERNAME"] == USERNAME
    assert environment["ISSTECH_PASSWORD"] == PASSWORD
    log_text = config.log_file.read_text(encoding="utf-8")
    assert PASSWORD not in log_text
    assert USERNAME not in log_text
    assert "MUST_NOT_ENTER_SCHEDULER_LOG" not in log_text
    assert config.log_file.stat().st_mode & 0o777 == 0o600
    record = _log_records(config.log_file)[0]
    assert record == {
        "actionable_count": 3,
        "event_count": 1,
        "exit_code": 0,
        "observed_count": 16,
        "outcome": "succeeded",
        "procurement_observed_count": 12,
        "readonly_changed_count": 1,
        "readonly_observed_count": 4,
        "run_id": "run-scheduled-1",
        "status": "succeeded",
        "timestamp": "2026-07-15T08:30:00+00:00",
    }


def test_scheduled_sync_preserves_virtualenv_interpreter_symlink(tmp_path: Path) -> None:
    config = _config(tmp_path)
    interpreter_link = config.python_executable
    interpreter_link.unlink()
    base_interpreter = tmp_path / "base-python"
    base_interpreter.write_text("", encoding="utf-8")
    interpreter_link.symlink_to(base_interpreter)
    captured: dict[str, object] = {}

    def runner(command, **_kwargs):
        captured["command"] = list(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "run_id": "run-symlink",
                    "status": "succeeded",
                    "observed_count": 0,
                    "actionable_count": 0,
                    "events": [],
                }
            ),
            stderr="",
        )

    result = run_scheduled_sync(
        config,
        credential_reader=_credentials,
        runner=runner,
        account="local-test-account",
        now=NOW,
    )

    assert result == 0
    command = captured["command"]
    assert isinstance(command, list)
    assert command[0] == str(interpreter_link.absolute())
    assert command[0] != str(interpreter_link.resolve())


def test_keychain_failure_is_nonzero_logged_and_does_not_start_sync(tmp_path: Path) -> None:
    config = _config(tmp_path)
    called = False

    def credentials(_service: str, _account: str, _timeout: float) -> str:
        raise ScheduledSyncError("password=" + PASSWORD)

    def runner(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("sync must not start without Keychain values")

    result = run_scheduled_sync(
        config,
        credential_reader=credentials,
        runner=runner,
        account="local-test-account",
        now=NOW,
    )

    assert result == 1
    assert called is False
    log_text = config.log_file.read_text(encoding="utf-8")
    assert PASSWORD not in log_text
    assert "<redacted>" in log_text
    assert _log_records(config.log_file)[0]["stage"] == "keychain"


def test_child_failure_preserves_run_id_and_redacts_error(tmp_path: Path) -> None:
    config = _config(tmp_path)

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            7,
            stdout="",
            stderr=f"SYNC_FAILED run_id=run-failed password={PASSWORD}",
        )

    result = run_scheduled_sync(
        config,
        credential_reader=_credentials,
        runner=runner,
        account="local-test-account",
        now=NOW,
    )

    assert result == 7
    log_text = config.log_file.read_text(encoding="utf-8")
    assert PASSWORD not in log_text
    record = _log_records(config.log_file)[0]
    assert record["run_id"] == "run-failed"
    assert record["exit_code"] == 7
    assert record["stage"] == "sync"


def test_child_timeout_returns_124_and_records_failure(tmp_path: Path) -> None:
    config = _config(tmp_path)

    def runner(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, timeout=30)

    result = run_scheduled_sync(
        config,
        credential_reader=_credentials,
        runner=runner,
        account="local-test-account",
        now=NOW,
    )

    assert result == 124
    record = _log_records(config.log_file)[0]
    assert record["exit_code"] == 124
    assert record["error_message"] == "manual sync CLI timed out"


def test_keychain_reader_uses_bounded_security_stdout_only() -> None:
    captured: dict[str, object] = {}

    def runner(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="VALUE\n", stderr="ignored")

    value = read_keychain_value(
        KEYCHAIN_PASSWORD_SERVICE,
        "local-account",
        3,
        runner=runner,
    )

    assert value == "VALUE"
    assert captured["command"] == [
        "/usr/bin/security",
        "find-generic-password",
        "-a",
        "local-account",
        "-s",
        KEYCHAIN_PASSWORD_SERVICE,
        "-w",
    ]
    kwargs = captured["kwargs"]
    assert kwargs["timeout"] == 3
    assert kwargs["capture_output"] is True


def test_plist_render_has_weekdays_private_umask_and_no_credentials(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    rendered = render_plist(
        Path(__file__).parents[1] / "ops" / "com.isstech.workflow-center.sync.plist",
        repo_root=repo,
        hour=9,
        minute=15,
    )
    payload = plistlib.loads(rendered)

    assert payload["Label"] == "com.isstech.workflow-center.sync"
    assert payload["ProgramArguments"][0] == str(repo / ".venv" / "bin" / "python")
    assert payload["ProgramArguments"][1] == str(repo / "tools" / "scheduled_sync.py")
    assert payload["WorkingDirectory"] == str(repo)
    assert payload["Umask"] == 0o77
    assert payload["StandardOutPath"] == "/dev/null"
    assert payload["StandardErrorPath"] == "/dev/null"
    assert payload["StartCalendarInterval"] == [
        {"Weekday": weekday, "Hour": 9, "Minute": 15} for weekday in range(1, 6)
    ]
    lowered = rendered.lower()
    for forbidden in (b"password", b"username", b"cookie", b"ticket", b".ipsa"):
        assert forbidden not in lowered


def test_install_without_bootstrap_is_atomic_private_and_reversible(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    wrapper = repo / "tools" / "scheduled_sync.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("", encoding="utf-8")
    destination = tmp_path / "LaunchAgents" / "agent.plist"
    content = render_plist(
        Path(__file__).parents[1] / "ops" / "com.isstech.workflow-center.sync.plist",
        repo_root=repo,
        hour=8,
        minute=30,
    )

    install_agent(
        content,
        destination=destination,
        repo_root=repo,
        bootstrap=False,
        timeout_seconds=5,
    )

    assert destination.read_bytes() == content
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.parent.stat().st_mode & 0o777 == 0o700


def test_failed_bootstrap_restores_previous_file_and_service(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    wrapper = repo / "tools" / "scheduled_sync.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("", encoding="utf-8")
    destination = tmp_path / "LaunchAgents" / "agent.plist"
    destination.parent.mkdir()
    previous = b"previous plist bytes"
    destination.write_bytes(previous)
    content = render_plist(
        Path(__file__).parents[1] / "ops" / "com.isstech.workflow-center.sync.plist",
        repo_root=repo,
        hour=8,
        minute=30,
    )
    bootstrap_calls = 0

    def runner(command, **_kwargs):
        nonlocal bootstrap_calls
        action = command[1]
        if action == "print":
            return subprocess.CompletedProcess(command, 0, stdout="loaded", stderr="")
        if action == "bootstrap":
            bootstrap_calls += 1
            return subprocess.CompletedProcess(
                command,
                5 if bootstrap_calls == 1 else 0,
                stdout="",
                stderr="failed",
            )
        if action == "bootout":
            code = 0 if bootstrap_calls == 0 else 3
            return subprocess.CompletedProcess(command, code, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(LaunchAgentInstallError, match="previous state was restored"):
        install_agent(
            content,
            destination=destination,
            repo_root=repo,
            bootstrap=True,
            timeout_seconds=5,
            runner=runner,
            require_keychain=lambda _account, _timeout: None,
        )

    assert destination.read_bytes() == previous
    assert destination.with_name(destination.name + ".backup").read_bytes() == previous
    assert bootstrap_calls == 2


def test_failed_plist_lint_restores_file_without_stopping_old_service(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    wrapper = repo / "tools" / "scheduled_sync.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("", encoding="utf-8")
    destination = tmp_path / "LaunchAgents" / "agent.plist"
    destination.parent.mkdir()
    previous = b"previous plist bytes"
    destination.write_bytes(previous)
    content = render_plist(
        Path(__file__).parents[1] / "ops" / "com.isstech.workflow-center.sync.plist",
        repo_root=repo,
        hour=8,
        minute=30,
    )
    actions: list[str] = []

    def runner(command, **_kwargs):
        action = command[1]
        actions.append(action)
        if action == "print":
            return subprocess.CompletedProcess(command, 0, stdout="loaded", stderr="")
        if action == "-lint":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="invalid")
        raise AssertionError(f"unexpected launchctl action after lint failure: {action}")

    with pytest.raises(LaunchAgentInstallError, match="previous state was restored"):
        install_agent(
            content,
            destination=destination,
            repo_root=repo,
            bootstrap=True,
            timeout_seconds=5,
            runner=runner,
            require_keychain=lambda _account, _timeout: None,
        )

    assert destination.read_bytes() == previous
    assert actions == ["print", "-lint"]


def test_keychain_configuration_uses_security_prompt_not_value_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def runner(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(keychain_cli.subprocess, "run", runner)
    keychain_cli._store_interactively(
        KEYCHAIN_PASSWORD_SERVICE,
        "local-account",
        timeout_seconds=5,
    )

    command = captured["command"]
    assert command[-1] == "-w"
    assert PASSWORD not in command
    assert captured["kwargs"]["timeout"] == 5
