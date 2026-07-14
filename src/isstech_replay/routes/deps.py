"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, Request

from isstech_replay.errors import auth_expired
from isstech_replay.client import IsstechClient
from isstech_replay.session_store import SessionRecord, SessionStore


def get_store(request: Request) -> SessionStore:
    return request.app.state.session_store


def get_client_factory(request: Request) -> Callable[[], IsstechClient]:
    return request.app.state.client_factory


def get_bearer_token(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization:
        raise auth_expired("Authorization header required")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise auth_expired("Expected Authorization: Bearer <token>")
    return token.strip()


def get_session(
    token: Annotated[str, Depends(get_bearer_token)],
    store: Annotated[SessionStore, Depends(get_store)],
) -> SessionRecord:
    record = store.get(token)
    if record is None:
        raise auth_expired()
    return record
