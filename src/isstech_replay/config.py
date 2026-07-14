from __future__ import annotations

from dataclasses import dataclass
import os


DEFAULT_BASE_URL = "http://ipsapro.isstech.com"
DEFAULT_PASSPORT_URL = "https://passport.isstech.com"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Settings:
    base_url: str = DEFAULT_BASE_URL
    passport_url: str = DEFAULT_PASSPORT_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            base_url=os.getenv("ISSTECH_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            passport_url=os.getenv("ISSTECH_PASSPORT_URL", DEFAULT_PASSPORT_URL).rstrip("/"),
            timeout_seconds=float(
                os.getenv("ISSTECH_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
            ),
            max_attachment_bytes=int(
                os.getenv(
                    "ISSTECH_MAX_ATTACHMENT_BYTES",
                    str(DEFAULT_MAX_ATTACHMENT_BYTES),
                )
            ),
        )
