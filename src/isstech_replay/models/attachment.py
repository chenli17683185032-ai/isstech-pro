"""Attachment models. Content bytes are never logged by default."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AttachmentMeta:
    id: str
    file_name: str = ""
    uploader_name: str = ""
    upload_date: str = ""
    doc_id: str = ""


@dataclass(frozen=True, slots=True)
class AttachmentContent:
    id: str
    content_type: str | None
    content_length: int | None
    sha256: str
    # Optional in-memory bytes for callers that need them; tests compare digests.
    data: bytes | None = None
