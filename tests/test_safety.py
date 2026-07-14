"""Safety boundary: policy classification and transport gating."""

from __future__ import annotations

import httpx
import pytest

from isstech_replay.client import IsstechClient
from isstech_replay.policy import (
    EndpointPolicy,
    PolicyViolation,
    RequestClass,
    SideEffect,
)


BUSINESS = "http://ipsapro.isstech.com"
PASSPORT = "https://passport.isstech.com"


def _tracking_transport() -> tuple[httpx.MockTransport, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        return httpx.Response(200, json={"ok": True}, request=request)

    return httpx.MockTransport(handler), seen


def test_unknown_host_is_blocked_before_transport() -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation) as exc:
            client.request("GET", "http://example.test/read")
    assert seen == []
    assert exc.value.decision.request_class is RequestClass.DENY


def test_caller_cannot_pass_safety_flag() -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        with pytest.raises(TypeError, match="safety="):
            client.request(  # type: ignore[call-arg]
                "GET",
                f"{BUSINESS}/WebTP/PurchaseRequisition",
                safety="read-only",
            )
    assert seen == []


def test_purchase_entry_get_is_allowed() -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        response = client.get(f"{BUSINESS}/WebTP/PurchaseRequisition")
    assert response.json() == {"ok": True}
    assert len(seen) == 1


@pytest.mark.parametrize(
    "path",
    [
        "/WebTP/PurchaseRequisition/Index",
        "/WebTP/PurchaseRequisition/Index/0/1/False/1/15",
        "/WebTP/PurchaseRequisition/Index/0/1/True/1/10/lastOrderField/PR_PrjNo",
    ],
)
def test_list_views_are_allowed(path: str) -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        client.get(f"{BUSINESS}{path}")
    assert len(seen) == 1


@pytest.mark.parametrize(
    "path",
    [
        "/WebTP/PurchaseRequisition/ApprovalIndex",
        "/WebTP/PurchaseRequisition/AdjustIndex",
        "/WebTP/PurchaseRequisition/RevocationIndex",
        "/WebTP/PurchaseRequisition/SearchIndex",
    ],
)
def test_uncaptured_views_are_blocked(path: str) -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation):
            client.get(f"{BUSINESS}{path}")
    assert seen == []


def test_filter_post_on_entry_is_allowed() -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        client.post(
            f"{BUSINESS}/WebTP/PurchaseRequisition",
            data={"PR_PrjNo": "x", "PR_RequisitionNo": ""},
        )
    assert len(seen) == 1


def test_delete_get_is_mutating_and_blocked() -> None:
    transport, seen = _tracking_transport()
    url = f"{BUSINESS}/WebTP/PurchaseRequisition/Delete/abc123"
    policy = EndpointPolicy()
    decision = policy.decide("GET", url)
    assert decision.side_effect is SideEffect.MUTATING
    assert decision.request_class is RequestClass.BUILD_ONLY

    with IsstechClient(transport=transport, policy=policy) as client:
        with pytest.raises(PolicyViolation) as exc:
            client.get(url)
    assert seen == []
    assert exc.value.decision.rule_id == "pr.delete"


def test_attachment_upload_blocked() -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation):
            client.post(f"{BUSINESS}/WebTP/Attachment/Upload/")
    assert seen == []


def test_attachment_download_allowed() -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        client.get(f"{BUSINESS}/WebTP/Attachment/Download/file-id")
    assert len(seen) == 1


def test_passport_login_post_allowed() -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        client.post(
            f"{PASSPORT}/?DomainUrl=http://ipsapro.isstech.com&ReturnUrl=%2fWebTP%2fPurchaseRequisition",
            data={"emp_DomainName": "u", "emp_Password": "p"},
        )
    assert len(seen) == 1


def test_write_submit_post_blocked() -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation):
            client.post(f"{BUSINESS}/WebTP/PurchaseRequisition/Submit/1")
    assert seen == []


def test_edit_page_get_allowed_but_edit_post_blocked() -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        client.get(f"{BUSINESS}/WebTP/PurchaseRequisition/Edit/1")
        with pytest.raises(PolicyViolation):
            client.post(f"{BUSINESS}/WebTP/PurchaseRequisition/Edit/1", data={"x": "1"})
    assert len(seen) == 1


def test_guarded_transport_is_sole_egress() -> None:
    """Even a raw httpx call path through the client is policy-checked."""
    from isstech_replay.transport import GuardedTransport
    from isstech_replay.policy import EndpointPolicy

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(204, request=request)

    guarded = GuardedTransport(
        policy=EndpointPolicy(),
        inner=httpx.MockTransport(handler),
    )
    req = httpx.Request("GET", f"{BUSINESS}/WebTP/PurchaseRequisition/Delete/9")
    with pytest.raises(PolicyViolation):
        guarded.handle_request(req)
    assert seen == []


@pytest.mark.parametrize(
    ("method", "url", "rule_id"),
    [
        (
            "GET",
            f"{BUSINESS}/WebTP/PurchaseRequisition/Edit/%2e%2e/Delete/9",
            "deny.unsafe_path",
        ),
        (
            "GET",
            f"{BUSINESS}/WebTP/PurchaseRequisition/Edit/%252e%252e/Delete/9",
            "deny.unsafe_path",
        ),
        (
            "GET",
            "http://evil.ipsapro.isstech.com/WebTP/PurchaseRequisition",
            "deny.other_isstech",
        ),
        (
            "POST",
            "https://evil.passport.isstech.com/",
            "deny.other_isstech",
        ),
        (
            "GET",
            f"{BUSINESS}/WebTP/PurchaseRequisition/Index/Delete/9",
            "deny.other_isstech",
        ),
    ],
)
def test_adversarial_urls_are_blocked_before_transport(
    method: str,
    url: str,
    rule_id: str,
) -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation) as exc:
            client.request(method, url)
    assert seen == []
    assert exc.value.decision.rule_id == rule_id


def test_client_constructor_has_no_unguarded_escape_hatch() -> None:
    transport, seen = _tracking_transport()
    with pytest.raises(TypeError):
        IsstechClient(transport=transport, guard=False)  # type: ignore[call-arg]
    assert seen == []
