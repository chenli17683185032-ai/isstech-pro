"""Short-lived local session handles. Never store or return .iPSA values."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .client import IsstechClient
from .config import Settings


DEFAULT_SESSION_TTL_SECONDS = 3600


@dataclass
class SessionRecord:
    token: str
    client: IsstechClient
    username: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def expired(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now >= self.expires_at


class SessionStore:
    """In-memory Bearer token → upstream client mapping."""

    def __init__(self, ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionRecord] = {}

    def create(self, client: IsstechClient, username: str) -> SessionRecord:
        token = secrets.token_urlsafe(32)
        now = time.time()
        record = SessionRecord(
            token=token,
            client=client,
            username=username,
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._purge_locked(now)
            self._sessions[token] = record
        return record

    def get(self, token: str) -> SessionRecord | None:
        with self._lock:
            self._purge_locked()
            record = self._sessions.get(token)
            if record is None:
                return None
            if record.expired():
                self._drop_locked(token)
                return None
            return record

    def delete(self, token: str) -> bool:
        with self._lock:
            return self._drop_locked(token)

    def _drop_locked(self, token: str) -> bool:
        record = self._sessions.pop(token, None)
        if record is None:
            return False
        try:
            record.client.close()
        except Exception:
            pass
        return True

    def _purge_locked(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        expired = [t for t, r in self._sessions.items() if r.expires_at <= now]
        for token in expired:
            self._drop_locked(token)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._purge_locked()
            return {"active_sessions": len(self._sessions), "ttl_seconds": self.ttl_seconds}


# Process-wide default store used by the API app
default_store = SessionStore()


def session_ttl_from_settings(settings: Settings | None = None) -> float:
    import os

    raw = os.getenv("ISSTECH_SESSION_TTL_SECONDS")
    if raw:
        return float(raw)
    return DEFAULT_SESSION_TTL_SECONDS
