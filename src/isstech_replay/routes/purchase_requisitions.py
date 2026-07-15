"""Read-only purchase requisition routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from isstech_replay.client import EvidenceGapError
from isstech_replay.errors import bad_request, not_captured, parse_error, upstream_error
from isstech_replay.models.purchase import PurchaseListQuery, PurchaseView
from isstech_replay.routes.deps import get_session
from isstech_replay.session_store import SessionRecord

router = APIRouter(tags=["purchase-requisitions"])

_VIEW_MAP = {
    "application": PurchaseView.APPLICATION,
    "approval": PurchaseView.APPROVAL,
    "adjustment": PurchaseView.ADJUSTMENT,
    "revocation": PurchaseView.REVOCATION,
    "search": PurchaseView.SEARCH,
}


class PurchaseItemOut(BaseModel):
    id: str
    requisition_no: str = ""
    project_no: str = ""
    project_name: str = ""
    creator_name: str = ""
    create_date: str = ""
    status: str = ""
    next_approver: str = ""


class PurchaseListOut(BaseModel):
    view: str
    items: list[PurchaseItemOut]
    total_count: int | None = None
    total_text: str | None = None
    page: int = 1
    page_size: int = 10
    source_url: str = ""


class PurchaseApprovalStepOut(BaseModel):
    sequence: str = ""
    timestamp: str = ""
    approver_name: str = ""
    role: str = ""
    action: str = ""
    comment: str = ""


class PurchaseDetailOut(BaseModel):
    id: str
    fields: dict[str, str] = Field(default_factory=dict)
    html_title: str = ""
    approval_steps: list[PurchaseApprovalStepOut] = Field(default_factory=list)


@router.get("/purchase-requisitions", response_model=PurchaseListOut)
def list_purchase_requisitions(
    session: Annotated[SessionRecord, Depends(get_session)],
    view: str = Query(default="application"),
    project_no: str = Query(default=""),
    requisition_no: str = Query(default=""),
    status: str = Query(default=""),
    next_approver: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    sort_field: str | None = Query(default=None),
) -> PurchaseListOut:
    try:
        pview = _VIEW_MAP[view]
    except KeyError as exc:
        raise bad_request(
            f"unknown view: {view}",
            details={"allowed": sorted(_VIEW_MAP)},
        ) from exc

    query = PurchaseListQuery(
        view=pview,
        project_no=project_no,
        requisition_no=requisition_no,
        status=status,
        next_approver=next_approver,
        page=page,
        page_size=page_size,
        sort_field=sort_field,
    )
    try:
        result = session.client.list_purchase_requisitions(query)
    except EvidenceGapError as exc:
        raise not_captured(str(exc), details={"view": view}) from exc
    except PermissionError as exc:
        raise upstream_error(str(exc), details={"code_hint": "AUTH_EXPIRED"}) from exc
    except Exception as exc:
        raise upstream_error(f"list failed: {exc}") from exc

    try:
        items = [
            PurchaseItemOut(
                id=i.id,
                requisition_no=i.requisition_no,
                project_no=i.project_no,
                project_name=i.project_name,
                creator_name=i.creator_name,
                create_date=i.create_date,
                status=i.status,
                next_approver=i.next_approver,
            )
            for i in result.items
        ]
    except Exception as exc:
        raise parse_error(str(exc)) from exc

    return PurchaseListOut(
        view=result.view.value,
        items=items,
        total_count=result.total_count,
        total_text=result.total_text,
        page=result.page,
        page_size=result.page_size,
        source_url=result.source_url,
    )


@router.get("/purchase-requisitions/{requisition_id}", response_model=PurchaseDetailOut)
def get_purchase_requisition(
    requisition_id: str,
    session: Annotated[SessionRecord, Depends(get_session)],
) -> PurchaseDetailOut:
    if not requisition_id.strip():
        raise bad_request("requisition_id required")
    try:
        detail = session.client.get_purchase_requisition(requisition_id.strip())
    except PermissionError as exc:
        raise upstream_error(str(exc)) from exc
    except Exception as exc:
        raise upstream_error(f"detail failed: {exc}") from exc
    return PurchaseDetailOut(
        id=detail.id,
        fields=detail.fields,
        html_title=detail.html_title,
        approval_steps=[
            PurchaseApprovalStepOut(
                sequence=step.sequence,
                timestamp=step.timestamp,
                approver_name=step.approver_name,
                role=step.role,
                action=step.action,
                comment=step.comment,
            )
            for step in detail.approval_steps
        ],
    )
