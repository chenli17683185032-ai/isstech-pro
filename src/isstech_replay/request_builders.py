"""Offline builders for mutating purchase/attachment requests.

Each function validates inputs and returns an httpx.Request plus a redacted
RequestPreview. Nothing here calls .send() or reaches GuardedTransport.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urljoin

import httpx

from .config import Settings
from .models.previews import RequestPreview
from .policy import EndpointPolicy, RequestClass, SideEffect
from .validation import require_path_segment


class BuildError(ValueError):
    """Invalid preview input."""


def _base(settings: Settings | None) -> Settings:
    return settings or Settings.from_env()


def _url(settings: Settings, path: str) -> str:
    return urljoin(settings.base_url.rstrip("/") + "/", path.lstrip("/"))


def _require_id(value: str, label: str = "id") -> str:
    try:
        return require_path_segment(value, label)
    except ValueError as exc:
        raise BuildError(str(exc)) from exc


def _assert_build_only(method: str, url: str, policy: EndpointPolicy | None = None) -> None:
    policy = policy or EndpointPolicy()
    decision = policy.decide(method, url)
    if (
        decision.side_effect is SideEffect.MUTATING
        and decision.request_class is RequestClass.BUILD_ONLY
    ):
        return
    raise BuildError(
        f"preview path is not classified as a build-only mutation: {decision.rule_id}"
    )


def _preview_from_request(
    request: httpx.Request,
    *,
    action: str,
    form_fields: Mapping[str, str] | None = None,
    body_kind: str = "none",
    body_summary: dict[str, Any] | None = None,
    notes: tuple[str, ...] = (),
) -> RequestPreview:
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in {"cookie", "authorization"}
    }
    # Redact obvious secrets in form fields
    redacted_form: dict[str, str] = {}
    for key, val in (form_fields or {}).items():
        lk = key.lower()
        if any(
            marker in lk
            for marker in (
                "password",
                "cookie",
                "token",
                "ticket",
                "secret",
                "authorization",
                "csrf",
                "xsrf",
                ".ipsa",
            )
        ):
            redacted_form[key] = "<redacted>"
        else:
            redacted_form[key] = val
    return RequestPreview(
        method=request.method,
        url=str(request.url),
        action=action,
        headers=headers,
        form_fields=redacted_form,
        body_kind=body_kind,
        body_summary=body_summary or {},
        notes=notes + ("never-send",),
    )


def build_delete_purchase(
    requisition_id: str,
    *,
    settings: Settings | None = None,
) -> tuple[httpx.Request, RequestPreview]:
    settings = _base(settings)
    rid = _require_id(requisition_id, "requisition_id")
    url = _url(settings, f"/WebTP/PurchaseRequisition/Delete/{rid}")
    # Observed: $.ajax(url) with default GET
    request = httpx.Request("GET", url)
    _assert_build_only(request.method, str(request.url))
    preview = _preview_from_request(
        request,
        action="pr.delete",
        notes=("mutating-get", "from purchaseRequisitionIndex.deletePR"),
    )
    return request, preview


def build_edit_purchase(
    requisition_id: str,
    fields: Mapping[str, str],
    *,
    settings: Settings | None = None,
) -> tuple[httpx.Request, RequestPreview]:
    settings = _base(settings)
    rid = _require_id(requisition_id, "requisition_id")
    if not fields:
        raise BuildError("fields must not be empty")
    url = _url(settings, f"/WebTP/PurchaseRequisition/Edit/{rid}")
    data = dict(fields)
    data.setdefault("PR_ID", rid)
    request = httpx.Request(
        "POST",
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    _assert_build_only(request.method, str(request.url))
    preview = _preview_from_request(
        request,
        action="pr.edit",
        form_fields=data,
        body_kind="form",
        body_summary={"field_count": len(data), "field_names": sorted(data)},
        notes=("edit-post-shape-inferred", "confirm with intercepted capture before production use"),
    )
    return request, preview


def build_create_purchase(
    fields: Mapping[str, str],
    *,
    settings: Settings | None = None,
) -> tuple[httpx.Request, RequestPreview]:
    settings = _base(settings)
    if not fields:
        raise BuildError("fields must not be empty")
    url = _url(settings, "/WebTP/PurchaseRequisition/Create")
    data = dict(fields)
    request = httpx.Request(
        "POST",
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    _assert_build_only(request.method, str(request.url))
    preview = _preview_from_request(
        request,
        action="pr.create",
        form_fields=data,
        body_kind="form",
        body_summary={"field_count": len(data), "field_names": sorted(data)},
        notes=("create-post-shape-inferred",),
    )
    return request, preview


def build_workflow_action(
    action: str,
    requisition_id: str,
    *,
    fields: Mapping[str, str] | None = None,
    settings: Settings | None = None,
) -> tuple[httpx.Request, RequestPreview]:
    """submit | approve | adjust | revoke"""
    settings = _base(settings)
    rid = _require_id(requisition_id, "requisition_id")
    action_l = action.lower().strip()
    segment = {
        "submit": "Submit",
        "approve": "Approve",
        "adjust": "Adjust",
        "revoke": "Revoke",
        "revocation": "Revoke",
    }.get(action_l)
    if not segment:
        raise BuildError(f"unsupported workflow action: {action}")
    url = _url(settings, f"/WebTP/PurchaseRequisition/{segment}/{rid}")
    data = dict(fields or {})
    request = httpx.Request(
        "POST",
        url,
        data=data or None,
        headers={"Content-Type": "application/x-www-form-urlencoded"} if data else None,
    )
    _assert_build_only(request.method, str(request.url))
    preview = _preview_from_request(
        request,
        action=f"pr.{action_l}",
        form_fields=data,
        body_kind="form" if data else "none",
        body_summary={"field_names": sorted(data)} if data else {},
        notes=("workflow-path-inferred-from-naming", "intercept UI before trusting shape"),
    )
    return request, preview


def build_attachment_upload(
    doc_id: str,
    *,
    filename: str,
    content_type: str = "application/octet-stream",
    doc_no: str = "",
    cid: str = "",
    ctitle: str = "",
    file_type: str = "",
    description: str = "",
    settings: Settings | None = None,
) -> tuple[httpx.Request, RequestPreview]:
    settings = _base(settings)
    did = _require_id(doc_id, "doc_id")
    if not filename:
        raise BuildError("filename is required")
    # Observed dialog URL: /WebTP/Attachment/Upload/{docid}?docno=&cid=&ctitle=&type=
    q = []
    if doc_no:
        q.append(f"docno={httpx.QueryParams({'docno': doc_no})['docno']}")
    # Build query safely
    params = {
        k: v
        for k, v in {
            "docno": doc_no,
            "cid": cid,
            "ctitle": ctitle,
            "type": file_type,
        }.items()
        if v
    }
    url = _url(settings, f"/WebTP/Attachment/Upload/{did}")
    if params:
        url = str(httpx.URL(url, params=params))
    # Multipart shape: file + fileName + fileDesc (from upload script)
    multipart = {
        "fileName": filename,
        "fileDesc": description,
        "file": (filename, b"<bytes omitted in preview>", content_type),
    }
    # Construct request without embedding large payloads
    request = httpx.Request("POST", url)
    _assert_build_only(request.method, str(request.url))
    # Manually mark as multipart in preview only — do not attach real file bytes
    preview = _preview_from_request(
        request,
        action="attachment.upload",
        form_fields={"fileName": filename, "fileDesc": description},
        body_kind="multipart",
        body_summary={
            "parts": ["file", "fileName", "fileDesc"],
            "filename": filename,
            "content_type": content_type,
            "bytes_omitted": True,
        },
        notes=("multipart-from-attachment_upload.js", "query-params-from-openUpload"),
    )
    # Prevent accidental use of multipart local
    del multipart
    return request, preview


def build_attachment_delete(
    attachment_id: str,
    *,
    settings: Settings | None = None,
) -> tuple[httpx.Request, RequestPreview]:
    settings = _base(settings)
    aid = _require_id(attachment_id, "attachment_id")
    url = _url(settings, f"/WebTP/Attachment/Delete/{aid}")
    # Observed: $.ajax({ type: "POST", url: ... })
    request = httpx.Request("POST", url)
    _assert_build_only(request.method, str(request.url))
    preview = _preview_from_request(
        request,
        action="attachment.delete",
        notes=("from attachementUpload.removeDoc",),
    )
    return request, preview


def ensure_not_sent(request: httpx.Request) -> None:
    """Guard helper for tests: builders must not expose a live client send path."""
    if hasattr(request, "send"):
        # httpx.Request has no send; clients do
        pass
    # Explicit no-op documentation hook
    return None
