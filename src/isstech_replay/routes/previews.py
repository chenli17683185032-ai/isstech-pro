"""Write-request preview endpoints. Never send upstream."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from isstech_replay import request_builders as rb
from isstech_replay.config import Settings
from isstech_replay.errors import bad_request, write_blocked
from isstech_replay.policy import EndpointPolicy
from isstech_replay.routes.deps import get_session
from isstech_replay.session_store import SessionRecord

router = APIRouter(prefix="/previews", tags=["previews"])


class FieldsBody(BaseModel):
    fields: dict[str, str] = Field(default_factory=dict)


class WorkflowBody(BaseModel):
    fields: dict[str, str] = Field(default_factory=dict)


class UploadBody(BaseModel):
    doc_id: str
    filename: str
    content_type: str = "application/octet-stream"
    doc_no: str = ""
    cid: str = ""
    ctitle: str = ""
    file_type: str = ""
    description: str = ""


def _preview_response(preview: rb.RequestPreview) -> dict[str, Any]:
    return preview.to_dict()


def _settings() -> Settings:
    return Settings.from_env()


def _assert_would_block(method: str, url: str) -> None:
    decision = EndpointPolicy().decide(method, url)
    if decision.allows_transport:
        raise write_blocked(
            "preview maps to an allow-live endpoint; refusing to advertise as write preview",
            details={"rule_id": decision.rule_id},
        )


@router.post("/purchase-requisitions/create")
def preview_create(
    body: FieldsBody,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> dict[str, Any]:
    _ = session
    try:
        request, preview = rb.build_create_purchase(body.fields, settings=_settings())
    except rb.BuildError as exc:
        raise bad_request(str(exc)) from exc
    _assert_would_block(request.method, str(request.url))
    return _preview_response(preview)


@router.post("/purchase-requisitions/{requisition_id}/edit")
def preview_edit(
    requisition_id: str,
    body: FieldsBody,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> dict[str, Any]:
    _ = session
    try:
        request, preview = rb.build_edit_purchase(
            requisition_id, body.fields, settings=_settings()
        )
    except rb.BuildError as exc:
        raise bad_request(str(exc)) from exc
    _assert_would_block(request.method, str(request.url))
    return _preview_response(preview)


@router.post("/purchase-requisitions/{requisition_id}/delete")
def preview_delete(
    requisition_id: str,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> dict[str, Any]:
    _ = session
    try:
        request, preview = rb.build_delete_purchase(requisition_id, settings=_settings())
    except rb.BuildError as exc:
        raise bad_request(str(exc)) from exc
    decision = session.client.classify(request.method, str(request.url))
    if decision.allows_transport:
        raise write_blocked("delete unexpectedly allow-live")
    return _preview_response(preview)


def _workflow(
    action: str,
    requisition_id: str,
    body: WorkflowBody,
    session: SessionRecord,
) -> dict[str, Any]:
    _ = session
    try:
        request, preview = rb.build_workflow_action(
            action, requisition_id, fields=body.fields, settings=_settings()
        )
    except rb.BuildError as exc:
        raise bad_request(str(exc)) from exc
    _assert_would_block(request.method, str(request.url))
    return _preview_response(preview)


@router.post("/purchase-requisitions/{requisition_id}/submit")
def preview_submit(
    requisition_id: str,
    body: WorkflowBody,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> dict[str, Any]:
    return _workflow("submit", requisition_id, body, session)


@router.post("/purchase-requisitions/{requisition_id}/approve")
def preview_approve(
    requisition_id: str,
    body: WorkflowBody,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> dict[str, Any]:
    return _workflow("approve", requisition_id, body, session)


@router.post("/purchase-requisitions/{requisition_id}/adjust")
def preview_adjust(
    requisition_id: str,
    body: WorkflowBody,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> dict[str, Any]:
    return _workflow("adjust", requisition_id, body, session)


@router.post("/purchase-requisitions/{requisition_id}/revoke")
def preview_revoke(
    requisition_id: str,
    body: WorkflowBody,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> dict[str, Any]:
    return _workflow("revoke", requisition_id, body, session)


@router.post("/attachments/upload")
def preview_upload(
    body: UploadBody,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> dict[str, Any]:
    _ = session
    try:
        request, preview = rb.build_attachment_upload(
            body.doc_id,
            filename=body.filename,
            content_type=body.content_type,
            doc_no=body.doc_no,
            cid=body.cid,
            ctitle=body.ctitle,
            file_type=body.file_type,
            description=body.description,
            settings=_settings(),
        )
    except rb.BuildError as exc:
        raise bad_request(str(exc)) from exc
    _assert_would_block(request.method, str(request.url))
    return _preview_response(preview)
