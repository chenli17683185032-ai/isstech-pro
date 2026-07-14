"""Attachment list and download routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel

from isstech_replay.errors import bad_request, upstream_error
from isstech_replay.routes.deps import get_session
from isstech_replay.session_store import SessionRecord

router = APIRouter(tags=["attachments"])


class AttachmentOut(BaseModel):
    id: str
    file_name: str = ""
    uploader_name: str = ""
    upload_date: str = ""
    doc_id: str = ""


class AttachmentContentMetaOut(BaseModel):
    id: str
    content_type: str | None = None
    content_length: int | None = None
    sha256: str


@router.get(
    "/purchase-requisitions/{requisition_id}/attachments",
    response_model=list[AttachmentOut],
)
def list_attachments(
    requisition_id: str,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> list[AttachmentOut]:
    if not requisition_id.strip():
        raise bad_request("requisition_id required")
    try:
        items = session.client.list_attachments_for(requisition_id.strip())
    except PermissionError as exc:
        raise upstream_error(str(exc)) from exc
    except Exception as exc:
        raise upstream_error(f"attachment list failed: {exc}") from exc
    return [
        AttachmentOut(
            id=i.id,
            file_name=i.file_name,
            uploader_name=i.uploader_name,
            upload_date=i.upload_date,
            doc_id=i.doc_id,
        )
        for i in items
    ]


@router.get("/attachments/{attachment_id}/content")
def download_attachment(
    attachment_id: str,
    session: Annotated[SessionRecord, Depends(get_session)],
    meta_only: bool = False,
) -> Response:
    if not attachment_id.strip():
        raise bad_request("attachment_id required")
    try:
        content = session.client.download_attachment(
            attachment_id.strip(),
            keep_bytes=not meta_only,
        )
    except PermissionError as exc:
        raise upstream_error(str(exc)) from exc
    except Exception as exc:
        raise upstream_error(f"download failed: {exc}") from exc

    if meta_only:
        import json

        payload = AttachmentContentMetaOut(
            id=content.id,
            content_type=content.content_type,
            content_length=content.content_length,
            sha256=content.sha256,
        )
        return Response(
            content=json.dumps(payload.model_dump()).encode("utf-8"),
            media_type="application/json",
        )

    return Response(
        content=content.data or b"",
        media_type=content.content_type or "application/octet-stream",
        headers={
            "X-Content-SHA256": content.sha256,
            "X-Content-Length": str(content.content_length or 0),
        },
    )
