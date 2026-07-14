"""Write previews: construct only, never reach transport."""

from __future__ import annotations

import httpx
import pytest

from isstech_replay.client import IsstechClient
from isstech_replay.config import Settings
from isstech_replay.policy import PolicyViolation, RequestClass, SideEffect, EndpointPolicy
from isstech_replay import request_builders as rb

BUSINESS = "http://ipsapro.isstech.com"
SETTINGS = Settings(base_url=BUSINESS)


def test_delete_preview_is_mutating_get() -> None:
    request, preview = rb.build_delete_purchase("10001", settings=SETTINGS)
    assert request.method == "GET"
    assert request.url.path.endswith("/Delete/10001")
    assert preview.action == "pr.delete"
    assert preview.to_dict()["sendable"] is False
    decision = EndpointPolicy().decide(request.method, str(request.url))
    assert decision.side_effect is SideEffect.MUTATING
    assert decision.request_class is RequestClass.BUILD_ONLY


def test_delete_preview_not_sent_through_client() -> None:
    request, _ = rb.build_delete_purchase("10001", settings=SETTINGS)
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, request=req)

    with IsstechClient(settings=SETTINGS, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PolicyViolation):
            client.request(request.method, str(request.url))
    assert seen == []


def test_edit_and_create_require_fields() -> None:
    with pytest.raises(rb.BuildError):
        rb.build_edit_purchase("1", {}, settings=SETTINGS)
    with pytest.raises(rb.BuildError):
        rb.build_create_purchase({}, settings=SETTINGS)
    req, prev = rb.build_edit_purchase(
        "10001",
        {"PR_PrjName": "REDACTED"},
        settings=SETTINGS,
    )
    assert req.method == "POST"
    assert prev.form_fields["PR_ID"] == "10001"
    assert prev.body_kind == "form"


def test_preview_redacts_token_like_form_fields() -> None:
    _, preview = rb.build_edit_purchase(
        "10001",
        {
            "PR_PrjName": "REDACTED",
            "csrfToken": "TEST_TOKEN_VALUE",
            "apiSecret": "TEST_SECRET_VALUE",
        },
        settings=SETTINGS,
    )
    assert preview.form_fields["csrfToken"] == "<redacted>"
    assert preview.form_fields["apiSecret"] == "<redacted>"


def test_workflow_actions() -> None:
    for action in ("submit", "approve", "adjust", "revoke"):
        req, prev = rb.build_workflow_action(action, "10001", settings=SETTINGS)
        assert req.method == "POST"
        assert prev.action.startswith("pr.")
        decision = EndpointPolicy().decide(req.method, str(req.url))
        assert decision.request_class is RequestClass.BUILD_ONLY


def test_attachment_upload_preview_omits_bytes() -> None:
    req, prev = rb.build_attachment_upload(
        "10001",
        filename="spec.pdf",
        description="desc",
        doc_no="XQ-1",
        settings=SETTINGS,
    )
    assert req.method == "POST"
    assert "/Attachment/Upload/10001" in str(req.url)
    assert prev.body_summary.get("bytes_omitted") is True
    assert "spec.pdf" in prev.form_fields.get("fileName", "")
    # Must not be sendable via policy
    with IsstechClient(settings=SETTINGS, transport=httpx.MockTransport(lambda r: httpx.Response(200, request=r))) as client:
        with pytest.raises(PolicyViolation):
            client.request(req.method, str(req.url))


def test_attachment_delete_preview() -> None:
    req, prev = rb.build_attachment_delete("9001", settings=SETTINGS)
    assert req.method == "POST"
    assert prev.action == "attachment.delete"


def test_invalid_ids() -> None:
    with pytest.raises(rb.BuildError):
        rb.build_delete_purchase("", settings=SETTINGS)
    with pytest.raises(rb.BuildError):
        rb.build_delete_purchase("1 2", settings=SETTINGS)
    with pytest.raises(rb.BuildError):
        rb.build_workflow_action("explode", "1", settings=SETTINGS)
