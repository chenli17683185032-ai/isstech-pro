"""Write-request preview models. Never send these to the live transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RequestPreview:
    """Redacted snapshot of a would-be mutating request."""

    method: str
    url: str
    action: str
    headers: dict[str, str] = field(default_factory=dict)
    form_fields: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    # Structural body description only — never raw password/cookie values
    body_kind: str = "none"  # none | form | multipart | json | raw
    body_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "action": self.action,
            "headers": dict(self.headers),
            "form_fields": dict(self.form_fields),
            "body_kind": self.body_kind,
            "body_summary": dict(self.body_summary),
            "notes": list(self.notes),
            "sendable": False,
        }
