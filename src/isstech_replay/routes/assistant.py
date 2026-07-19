"""Account-scoped local briefing and priority-preference endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from isstech_replay.account_scope import account_database_path
from isstech_replay.ai.briefing import (
    BriefingProvider,
    ModelBriefing,
    assistant_provider_config,
    provider_from_config,
)
from isstech_replay.assistant import generate_assistant_brief
from isstech_replay.errors import local_storage_error
from isstech_replay.models.assistant import (
    AssistantBrief,
    AssistantBriefSource,
    AssistantPreference,
    WaitingBasis,
)
from isstech_replay.routes.deps import get_session
from isstech_replay.scheduler import (
    local_account_name,
    read_keychain_value,
)
from isstech_replay.session_store import SessionRecord
from isstech_replay.storage import WorkflowStorage


router = APIRouter(tags=["assistant"])
AssistantProviderFactory = Callable[[], BriefingProvider | None]


class AssistantBriefItemOut(BaseModel):
    item_key: str
    category: str
    reference: str
    title: str
    project: str = ""
    status: str
    current_approver: str = ""
    waiting_days: int | None = None
    waiting_basis: WaitingBasis
    destination: str
    target: str | None = None
    reason: str


class AssistantBriefOut(BaseModel):
    brief_id: str
    business_date: str
    preference_version: int
    generated_at: str
    source: AssistantBriefSource
    provider: str = ""
    model: str = ""
    provider_configured: bool
    fallback_code: str | None = None
    summary: str
    items: list[AssistantBriefItemOut] = Field(default_factory=list)
    candidate_count: int


class AssistantPreferenceOut(BaseModel):
    preference_id: int
    created_at: str
    text: str


class AssistantStateOut(BaseModel):
    brief: AssistantBriefOut
    preferences: list[AssistantPreferenceOut] = Field(default_factory=list)
    stale: bool


class AssistantPreferenceIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@dataclass(slots=True)
class _UnavailableProvider:
    error: Exception
    name: str = "invalid_configuration"
    model: str = ""

    def prioritize(self, *_args, **_kwargs) -> ModelBriefing:
        raise self.error


def runtime_assistant_provider() -> BriefingProvider | None:
    config = assistant_provider_config(
        account=local_account_name(),
        credential_reader=read_keychain_value,
    )
    if config is None:
        return None
    return provider_from_config(config)


def _storage(session: SessionRecord) -> WorkflowStorage:
    return WorkflowStorage(account_database_path(session.username))


def _provider(request: Request) -> BriefingProvider | None:
    factory: AssistantProviderFactory = request.app.state.assistant_provider_factory
    try:
        return factory()
    except Exception as exc:
        return _UnavailableProvider(error=exc)


def _brief_out(brief: AssistantBrief) -> AssistantBriefOut:
    return AssistantBriefOut(
        brief_id=brief.brief_id,
        business_date=brief.business_date,
        preference_version=brief.preference_version,
        generated_at=brief.generated_at,
        source=brief.source,
        provider=brief.provider,
        model=brief.model,
        provider_configured=brief.provider_configured,
        fallback_code=brief.fallback_code,
        summary=brief.summary,
        items=[
            AssistantBriefItemOut(
                item_key=item.item_key,
                category=item.category,
                reference=item.reference,
                title=item.title,
                project=item.project,
                status=item.status,
                current_approver=item.current_approver,
                waiting_days=item.waiting_days,
                waiting_basis=item.waiting_basis,
                destination=item.destination,
                target=item.target,
                reason=item.reason,
            )
            for item in brief.items
        ],
        candidate_count=brief.candidate_count,
    )


def _preference_out(preference: AssistantPreference) -> AssistantPreferenceOut:
    return AssistantPreferenceOut(
        preference_id=preference.preference_id,
        created_at=preference.created_at,
        text=preference.text,
    )


def _state(storage: WorkflowStorage, brief: AssistantBrief) -> AssistantStateOut:
    return AssistantStateOut(
        brief=_brief_out(brief),
        preferences=[
            _preference_out(preference)
            for preference in storage.list_assistant_preferences(limit=50)
        ],
        stale=brief.business_date != date.today().isoformat(),
    )


def _generate(
    storage: WorkflowStorage,
    *,
    provider: BriefingProvider | None,
) -> AssistantBrief:
    return generate_assistant_brief(
        storage,
        now=datetime.now(UTC),
        provider=provider,
    )


@router.get("/assistant/brief", response_model=AssistantStateOut)
def get_assistant_brief(
    session: Annotated[SessionRecord, Depends(get_session)],
) -> AssistantStateOut:
    storage = _storage(session)
    try:
        brief = storage.latest_assistant_brief()
        if brief is None:
            brief = _generate(storage, provider=None)
        return _state(storage, brief)
    except Exception as exc:
        raise local_storage_error(
            f"assistant brief read failed: {type(exc).__name__}"
        ) from exc


@router.post("/assistant/briefs", response_model=AssistantStateOut)
def create_assistant_brief(
    request: Request,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> AssistantStateOut:
    storage = _storage(session)
    try:
        return _state(storage, _generate(storage, provider=_provider(request)))
    except Exception as exc:
        raise local_storage_error(
            f"assistant brief generation failed: {type(exc).__name__}"
        ) from exc


@router.post("/assistant/preferences", response_model=AssistantStateOut)
def add_assistant_preference(
    body: AssistantPreferenceIn,
    request: Request,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> AssistantStateOut:
    storage = _storage(session)
    try:
        storage.add_assistant_preference(
            text=body.text,
            created_at=datetime.now(UTC).isoformat(),
        )
        return _state(storage, _generate(storage, provider=_provider(request)))
    except Exception as exc:
        raise local_storage_error(
            f"assistant preference update failed: {type(exc).__name__}"
        ) from exc


@router.delete("/assistant/preferences", response_model=AssistantStateOut)
def clear_assistant_preferences(
    request: Request,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> AssistantStateOut:
    storage = _storage(session)
    try:
        storage.clear_assistant_preferences(created_at=datetime.now(UTC).isoformat())
        return _state(storage, _generate(storage, provider=_provider(request)))
    except Exception as exc:
        raise local_storage_error(
            f"assistant preference clear failed: {type(exc).__name__}"
        ) from exc
