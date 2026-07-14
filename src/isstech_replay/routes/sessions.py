"""Session login / inspect / logout routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, SecretStr

from isstech_replay.auth import AuthenticationError, login
from isstech_replay.client import IsstechClient
from isstech_replay.errors import auth_expired, bad_request, upstream_error
from isstech_replay.routes.deps import (
    get_bearer_token,
    get_client_factory,
    get_session,
    get_store,
)
from isstech_replay.session_store import SessionRecord, SessionStore

router = APIRouter(tags=["sessions"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: SecretStr


class SessionResponse(BaseModel):
    token: str | None = None
    token_type: str = "bearer"
    username: str | None = None
    authenticated: bool
    expires_at: float | None = None
    cookie_names: list[str] = Field(default_factory=list)


@router.post("/sessions", response_model=SessionResponse)
def create_session(
    body: LoginRequest,
    store: Annotated[SessionStore, Depends(get_store)],
    client_factory: Annotated[Callable[[], IsstechClient], Depends(get_client_factory)],
) -> SessionResponse:
    password = body.password.get_secret_value()
    if not body.username.strip() or not password:
        raise bad_request("username and password are required")

    client = client_factory()
    try:
        result = login(
            client,
            body.username.strip(),
            password,
        )
    except AuthenticationError as exc:
        client.close()
        raise upstream_error(str(exc)) from exc
    except Exception as exc:  # network/policy
        client.close()
        raise upstream_error(f"login failed: {exc}") from exc

    if not result.success:
        client.close()
        raise upstream_error(result.error_message or "login failed")

    record = store.create(client, username=body.username.strip())
    return SessionResponse(
        token=record.token,
        username=record.username,
        authenticated=True,
        expires_at=record.expires_at,
        # names only — never values
        cookie_names=list(result.session.cookie_names_present),
    )


@router.get("/session", response_model=SessionResponse)
def read_session(session: Annotated[SessionRecord, Depends(get_session)]) -> SessionResponse:
    return SessionResponse(
        token=None,  # do not echo bearer token on inspect
        username=session.username,
        authenticated=True,
        expires_at=session.expires_at,
        cookie_names=[],  # avoid leaking jar details repeatedly
    )


@router.delete("/session")
def delete_session(
    token: Annotated[str, Depends(get_bearer_token)],
    store: Annotated[SessionStore, Depends(get_store)],
) -> dict[str, Any]:
    deleted = store.delete(token)
    if not deleted:
        raise auth_expired()
    return {"deleted": True}
