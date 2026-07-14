"""Sole real network egress for upstream iPSA/passport traffic.

Every request is classified by EndpointPolicy before the underlying httpx
transport is invoked. Mutating and unknown endpoints never leave this process.
"""

from __future__ import annotations

from typing import Callable

import httpx

from .policy import EndpointPolicy, PolicyViolation


class GuardedTransport(httpx.BaseTransport):
    """Wrap an httpx transport and refuse non-ALLOW_LIVE decisions."""

    def __init__(
        self,
        *,
        policy: EndpointPolicy | None = None,
        inner: httpx.BaseTransport | None = None,
        on_blocked: Callable[[httpx.Request, PolicyViolation], None] | None = None,
    ) -> None:
        self.policy = policy or EndpointPolicy()
        self._inner = inner if inner is not None else httpx.HTTPTransport()
        self._on_blocked = on_blocked
        self._owns_inner = inner is None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            self.policy.assert_live_allowed(request.method, str(request.url))
        except PolicyViolation as exc:
            if self._on_blocked is not None:
                self._on_blocked(request, exc)
            raise
        return self._inner.handle_request(request)

    def close(self) -> None:
        if self._owns_inner:
            self._inner.close()
