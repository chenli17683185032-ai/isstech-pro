"""The daily assistant remains account-scoped, bounded, and locally recoverable."""

from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import httpx
import pytest

from isstech_replay.account_scope import account_database_path
from isstech_replay.ai.briefing import (
    BriefingProviderError,
    HttpChatBriefingProvider,
    ModelBriefing,
    ModelPriority,
)
from isstech_replay.assistant import (
    collect_assistant_candidates,
    generate_assistant_brief,
)
from isstech_replay.models.assistant import AssistantBriefSource
from isstech_replay.models.readonly_modules import ReadonlyModuleKind, ReadonlySnapshot
from isstech_replay.models.work_items import WorkflowKind, WorkflowSnapshot
from isstech_replay.storage import WorkflowStorage
from tools import configure_assistant_keychain as assistant_keychain_cli
from tools import generate_daily_brief as daily_brief_cli


OBSERVED_AT = "2026-07-19T00:30:00+00:00"
NOW = datetime(2026, 7, 19, 1, 0, tzinfo=UTC)


def _payload_json(payload: dict[str, object]) -> tuple[str, str]:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return serialized, hashlib.sha256(serialized.encode()).hexdigest()


def _workflow_snapshot(
    external_id: str,
    *,
    status: str,
    submitted_at: str,
    title: str,
) -> WorkflowSnapshot:
    payload_json, payload_hash = _payload_json(
        {
            "payload_version": 2,
            "relations": ["applicant"],
            "status": status,
        }
    )
    return WorkflowSnapshot(
        adapter=WorkflowKind.PURCHASE_REQUISITION,
        external_id=external_id,
        observed_at=OBSERVED_AT,
        reference_no=f"PR-{external_id}",
        project_no="PROJECT-ONE",
        title=title,
        applicant="ACCOUNT_HOLDER",
        submitted_at=submitted_at,
        status=status,
        current_node="审批",
        current_approver="APPROVER_A",
        waiting_days=None,
        source_url="http://ipsapro.isstech.com/read-only",
        active=status != "审批通过",
        actionable=status == "审批中",
        payload_json=payload_json,
        payload_hash=payload_hash,
    )


def _readonly_snapshot(
    external_id: str,
    *,
    status: str,
    application_date: str,
    scoped: bool = True,
) -> ReadonlySnapshot:
    payload_json, payload_hash = _payload_json(
        {
            "amount": "100.00",
            "applicant": "ACCOUNT_HOLDER",
            "application_date": application_date,
            "application_no": f"FEE-{external_id}",
            "current_approver": "APPROVER_B",
            "fields": {},
            "id": external_id,
            "module": "daily_expense",
            "ordinal": 1,
            "project_name": "DAILY EXPENSE",
            "schema_version": 1,
            "scope_reasons": ["submitted_by_me"] if scoped else [],
            "source_url": "http://ipsapro.isstech.com/read-only",
            "status": status,
        }
    )
    return ReadonlySnapshot(
        module=ReadonlyModuleKind.DAILY_EXPENSE,
        external_id=external_id,
        observed_at=OBSERVED_AT,
        payload_json=payload_json,
        payload_hash=payload_hash,
    )


def _seed_storage(path: Path) -> WorkflowStorage:
    storage = WorkflowStorage(path)
    procurement = (
        _workflow_snapshot(
            "pending",
            status="审批中",
            submitted_at="2026-06-01",
            title="PROCUREMENT",
        ),
        _workflow_snapshot(
            "approved",
            status="审批通过",
            submitted_at="2026-05-01",
            title="APPROVED",
        ),
    )
    storage.start_run(
        run_id="procurement-run",
        adapter=WorkflowKind.PURCHASE_REQUISITION,
        started_at=OBSERVED_AT,
        max_pages=20,
    )
    storage.complete_run(
        run_id="procurement-run",
        observed_at=OBSERVED_AT,
        finished_at=OBSERVED_AT,
        source_total_count=len(procurement),
        snapshots=procurement,
        actionable_count=1,
    )
    readonly = (
        _readonly_snapshot(
            "fee-pending",
            status="已提交",
            application_date="2026-07-15",
        ),
        _readonly_snapshot(
            "fee-approved",
            status="已完成",
            application_date="2026-06-15",
        ),
        _readonly_snapshot(
            "fee-unscoped",
            status="已提交",
            application_date="2026-07-01",
            scoped=False,
        ),
    )
    storage.start_readonly_run(
        run_id="daily-run",
        module=ReadonlyModuleKind.DAILY_EXPENSE,
        started_at=OBSERVED_AT,
        max_pages=20,
    )
    storage.complete_readonly_run(
        run_id="daily-run",
        observed_at=OBSERVED_AT,
        finished_at=OBSERVED_AT,
        source_total_count=len(readonly),
        snapshots=readonly,
    )
    return storage


def test_v8_database_migrates_to_v9_without_touching_existing_tables(
    tmp_path: Path,
) -> None:
    database = tmp_path / "workflow.sqlite3"
    storage = WorkflowStorage(database)
    assert storage.schema_version() == 9
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE assistant_briefs")
        connection.execute("DROP TABLE assistant_preferences")
        connection.execute("PRAGMA user_version = 8")

    migrated = WorkflowStorage(database)

    assert migrated.schema_version() == 9
    assert migrated.table_count("assistant_preferences") == 0
    assert migrated.table_count("assistant_briefs") == 0
    assert migrated.table_count("workflow_current") == 0


def test_preferences_are_versioned_bounded_and_clear_with_a_tombstone(
    tmp_path: Path,
) -> None:
    storage = WorkflowStorage(tmp_path / "workflow.sqlite3")
    for index in range(55):
        storage.add_assistant_preference(
            text=f"PREFERENCE {index}",
            created_at=f"2026-07-19T00:{index:02d}:00+00:00",
        )

    preferences = storage.list_assistant_preferences()
    assert len(preferences) == 50
    assert preferences[0].text == "PREFERENCE 5"
    assert preferences[-1].text == "PREFERENCE 54"
    assert storage.assistant_preference_version() == 55

    clear_version = storage.clear_assistant_preferences(created_at=OBSERVED_AT)
    assert clear_version == 56
    assert storage.list_assistant_preferences() == ()
    assert storage.assistant_preference_version() == 56

    latest = storage.add_assistant_preference(text="付款申请优先", created_at=OBSERVED_AT)
    assert latest.preference_id == 57
    assert [item.text for item in storage.list_assistant_preferences()] == ["付款申请优先"]


def test_candidate_collection_reuses_personal_scope_and_approved_filter(
    tmp_path: Path,
) -> None:
    storage = _seed_storage(tmp_path / "workflow.sqlite3")

    candidates = collect_assistant_candidates(storage, today=date(2026, 7, 19))

    assert [candidate.item_key for candidate in candidates] == [
        "daily_expense:fee-pending",
        "purchase_requisition:pending",
    ]
    daily = candidates[0]
    procurement = candidates[1]
    assert daily.waiting_days == 4
    assert procurement.waiting_days == 48
    assert daily.destination == "readonly-modules"
    assert daily.target == "dailyExpenses"
    assert procurement.destination == "work-items"
    assert procurement.target is None


def test_fallback_brief_applies_preference_and_is_idempotent(tmp_path: Path) -> None:
    storage = _seed_storage(tmp_path / "workflow.sqlite3")
    storage.add_assistant_preference(text="日常报销申请更重要", created_at=OBSERVED_AT)

    first = generate_assistant_brief(storage, now=NOW)
    second = generate_assistant_brief(storage, now=NOW)

    assert first.source is AssistantBriefSource.FALLBACK
    assert first.fallback_code == "provider_not_configured"
    assert first.provider_configured is False
    assert first.items[0].item_key == "daily_expense:fee-pending"
    assert first.items[0].reason == "符合你设置的优先级偏好"
    assert first.candidate_count == 2
    assert second.brief_id == first.brief_id
    assert storage.table_count("assistant_briefs") == 1

    storage.clear_assistant_preferences(created_at="2026-07-19T01:05:00+00:00")
    without_preference = generate_assistant_brief(
        storage,
        now=datetime(2026, 7, 19, 1, 6, tzinfo=UTC),
    )
    assert without_preference.items[0].item_key == "purchase_requisition:pending"
    assert without_preference.preference_version > first.preference_version
    assert storage.table_count("assistant_briefs") == 2


def test_chat_provider_sends_minimal_fields_and_drops_unknown_or_duplicate_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _seed_storage(tmp_path / "workflow.sqlite3")
    candidates = collect_assistant_candidates(storage, today=date(2026, 7, 19))
    real_client = httpx.Client
    client_options: dict[str, object] = {}

    def client(*args, **kwargs):
        client_options.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://llm.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        user_payload = json.loads(body["messages"][1]["content"])
        serialized = json.dumps(user_payload)
        for forbidden in ("applicant", "payload_json", "cookie", "approval_steps"):
            assert forbidden not in serialized.casefold()
        content = json.dumps(
            {
                "summary": "先处理费用单，再跟进等待较久的采购单。",
                "priorities": [
                    {
                        "item_key": "daily_expense:fee-pending",
                        "reason": "费用单符合当前优先级",
                    },
                    {
                        "item_key": "daily_expense:fee-pending",
                        "reason": "重复项应被丢弃",
                    },
                    {"item_key": "unknown:1", "reason": "未知项应被丢弃"},
                    {
                        "item_key": "purchase_requisition:pending",
                        "reason": "采购单等待时间较长",
                    },
                ],
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
        )

    provider = HttpChatBriefingProvider(
        endpoint="https://llm.example/v1/chat/completions",
        model="chat-model",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )

    result = provider.prioritize(candidates, ("费用优先",))

    assert [item.item_key for item in result.priorities] == [
        "daily_expense:fee-pending",
        "purchase_requisition:pending",
    ]
    assert client_options["trust_env"] is False


def test_invalid_model_response_falls_back_without_losing_items(tmp_path: Path) -> None:
    storage = _seed_storage(tmp_path / "workflow.sqlite3")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not-json"}}]},
        )

    provider = HttpChatBriefingProvider(
        endpoint="https://llm.example/v1/chat/completions",
        model="chat-model",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )

    brief = generate_assistant_brief(storage, now=NOW, provider=provider)

    assert brief.source is AssistantBriefSource.FALLBACK
    assert brief.provider_configured is True
    assert brief.fallback_code == "provider_invalid_response"
    assert len(brief.items) == 2
    assert storage.latest_assistant_brief() == brief


def test_full_model_priority_list_stays_capped_at_five(tmp_path: Path) -> None:
    storage = _seed_storage(tmp_path / "workflow.sqlite3")
    procurement = tuple(
        _workflow_snapshot(
            f"pending-{index}",
            status="审批中",
            submitted_at=f"2026-06-{index + 1:02d}",
            title=f"PROCUREMENT-{index}",
        )
        for index in range(5)
    )
    storage.start_run(
        run_id="five-pending-run",
        adapter=WorkflowKind.PURCHASE_REQUISITION,
        started_at=OBSERVED_AT,
        max_pages=20,
    )
    storage.complete_run(
        run_id="five-pending-run",
        observed_at=OBSERVED_AT,
        finished_at=OBSERVED_AT,
        source_total_count=len(procurement),
        snapshots=procurement,
        actionable_count=len(procurement),
    )

    class FullProvider:
        name = "test"
        model = "chat-model"

        def prioritize(self, candidates, _preferences) -> ModelBriefing:
            return ModelBriefing(
                summary="模型返回完整五项排序。",
                priorities=tuple(
                    ModelPriority(item_key=candidate.item_key, reason="模型优先")
                    for candidate in candidates[:5]
                ),
            )

    brief = generate_assistant_brief(storage, now=NOW, provider=FullProvider())

    assert brief.source == AssistantBriefSource.MODEL
    assert brief.fallback_code is None
    assert len(brief.items) == 5


@pytest.mark.parametrize(
    ("endpoint", "model"),
    [
        ("https://yunbay.xyz/v1/images/generations", "chat-model"),
        ("https://yunbay.xyz/v1/images/edits", "chat-model"),
        ("https://llm.example/v1/chat/completions", "gpt-image-2"),
        ("http://llm.example/v1/chat/completions", "chat-model"),
        ("https://ipsapro.isstech.com/v1/chat/completions", "chat-model"),
        ("https://passport.isstech.com/v1/chat/completions", "chat-model"),
    ],
)
def test_text_provider_rejects_image_insecure_or_workflow_targets(
    endpoint: str,
    model: str,
) -> None:
    with pytest.raises(ValueError):
        HttpChatBriefingProvider(
            endpoint=endpoint,
            model=model,
            api_key="test-key",
        )


def test_provider_enforces_streamed_response_limit(tmp_path: Path) -> None:
    storage = _seed_storage(tmp_path / "workflow.sqlite3")
    candidates = collect_assistant_candidates(storage, today=date(2026, 7, 19))

    provider = HttpChatBriefingProvider(
        endpoint="https://llm.example/v1/chat/completions",
        model="chat-model",
        api_key="test-key",
        max_response_bytes=10,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"x" * 11)
        ),
    )

    with pytest.raises(BriefingProviderError, match="configured limit"):
        provider.prioritize(candidates, ())


def test_assistant_keychain_configuration_never_places_value_in_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def runner(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(assistant_keychain_cli.subprocess, "run", runner)
    assistant_keychain_cli._store_interactively(
        "assistant-test-service",
        "local-account",
        timeout_seconds=5,
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[-1] == "-w"
    assert "test-key" not in command
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["timeout"] == 5


def test_daily_brief_cli_emits_only_safe_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "data"
    database = account_database_path(
        "alice",
        base_database_path=data_dir / "workflow-center.sqlite3",
    )
    _seed_storage(database)
    monkeypatch.setenv("ISSTECH_USERNAME", "alice")
    monkeypatch.setattr(daily_brief_cli, "_provider", lambda: None)

    result = daily_brief_cli.main(["--data-dir", str(data_dir)])

    assert result == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload == {
        "candidate_count": 2,
        "fallback_code": "provider_not_configured",
        "item_count": 2,
        "provider_configured": False,
        "source": "fallback",
        "status": "succeeded",
    }
    assert "PROCUREMENT" not in output
    assert "FEE-fee-pending" not in output
