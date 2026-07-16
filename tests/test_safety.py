"""Safety boundary: policy classification and transport gating."""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
import pytest

from isstech_replay.client import IsstechClient
from isstech_replay.models.payment import payment_empty_query_form, payment_query_form
from isstech_replay.policy import (
    BIZCASE_QUERY_URL,
    BIZCASE_VIEW_PARAMS,
    EndpointPolicy,
    PAYMENT_LIST_PATHS,
    PAYMENT_QUERY_PATH,
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
        "/WebTP/PurchaseRequisition/ApprovalIndex",
        "/WebTP/PurchaseRequisition/AdjustIndex",
        "/WebTP/PurchaseRequisition/RevocationIndex",
        "/WebTP/PurchaseRequisition/SearchIndex",
        "/WebTP/PurchaseRequisition/SearchIndex/0/1/False/2",
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
        "/WebTP/PurchaseRequisition/SearchIndex/0/1/False/2",
    ],
)
def test_captured_view_filter_posts_are_allowed(path: str) -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        client.post(f"{BUSINESS}{path}", data={"btnSearch": "查询"})
    assert len(seen) == 1


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


@pytest.mark.parametrize(
    "path",
    [
        "/WebTP/Attachment/Download/file-id",
        "/WebTP/PurchaseRequisition/Download/file-id",
    ],
)
def test_attachment_download_allowed(path: str) -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        client.get(f"{BUSINESS}{path}")
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


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/WebTP/PurchaseRequisition/Delete/1"),
        ("POST", "/WebTP/PurchaseRequisition/Submit/1"),
        ("POST", "/WebTP/PurchaseRequisition/Approve/1"),
        ("POST", "/WebTP/PurchaseRequisition/Adjust/1"),
        ("POST", "/WebTP/PurchaseRequisition/Revocation/1"),
        ("POST", "/WebTP/Attachment/Upload/1"),
        ("GET", "/WebTP/Attachment/Delete/1"),
    ],
)
def test_all_known_write_families_are_build_only_before_transport(
    method: str,
    path: str,
) -> None:
    transport, seen = _tracking_transport()
    policy = EndpointPolicy()
    decision = policy.decide(method, f"{BUSINESS}{path}")
    assert decision.request_class is RequestClass.BUILD_ONLY
    assert decision.side_effect is SideEffect.MUTATING

    with IsstechClient(transport=transport, policy=policy) as client:
        with pytest.raises(PolicyViolation):
            client.request(method, f"{BUSINESS}{path}")
    assert seen == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/WebTP/PurchaseRequisition/Edit/1"),
        ("HEAD", "/WebTP/PurchaseRequisition/ProjectSelection"),
        ("POST", "/WebTP/PurchaseRequisition/Edit/1"),
    ],
)
def test_write_preparation_pages_are_blocked(method: str, path: str) -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation):
            client.request(method, f"{BUSINESS}{path}", data={"x": "1"})
    assert seen == []


@pytest.mark.parametrize("alias", ["Details", "View", "Display"])
def test_unobserved_detail_aliases_are_denied(alias: str) -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation):
            client.get(f"{BUSINESS}/WebTP/PurchaseRequisition/{alias}/1")
    assert seen == []


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


def _bizcase_pagination_form() -> dict[str, str]:
    return {
        "__EVENTTARGET": "ctl05$GridPager1",
        "__EVENTARGUMENT": "2",
        "__VIEWSTATE": "OPAQUE_STATE",
        "__VIEWSTATEGENERATOR": "ABCD1234",
        "ctl05$GridPager1ddlPager": "1",
    }


def _payment_query_form() -> dict[str, str]:
    return payment_empty_query_form()


def test_payment_policy_allows_only_served_list_gets() -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        for path in PAYMENT_LIST_PATHS.values():
            client.get(f"{BUSINESS}{path}")
        for method, path in (
            ("POST", "/WebPMS/?Length=7"),
            ("POST", "/WebPMS/Payment/QueryList"),
            ("GET", "/WebPMS/Payment/QueryList?status=all"),
            ("GET", "/WebPMS/Payment/Edit/1"),
            ("POST", "/WebPMS/Payment/DelMain"),
            ("GET", "/WebPMS/selector/selecttype"),
        ):
            with pytest.raises(PolicyViolation):
                client.request(method, f"{BUSINESS}{path}")
    assert seen == [f"GET {BUSINESS}{path}" for path in PAYMENT_LIST_PATHS.values()]


def test_payment_policy_body_validates_empty_query() -> None:
    url = f"{BUSINESS}{PAYMENT_QUERY_PATH}"
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        client.post(url, data=_payment_query_form())

    assert seen == [f"POST {url}"]


def test_payment_policy_body_validates_proven_pager() -> None:
    url = f"{BUSINESS}{PAYMENT_QUERY_PATH}/0/1/False/14"
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        client.post(url, data=payment_empty_query_form(pager=True))

    assert seen == [f"POST {url}"]


@pytest.mark.parametrize(
    "form",
    [
        payment_query_form(applicant="USER-A"),
        payment_query_form(project_no="PROJECT-1"),
        payment_query_form(applicant="USER-A", pager=True),
        payment_query_form(project_no="PROJECT-1", pager=True),
    ],
)
def test_payment_policy_allows_bounded_personal_filters(form: dict[str, str]) -> None:
    is_pager = "PI_PaymentCompany" not in form
    path = (
        f"{PAYMENT_QUERY_PATH}/0/1/False/2"
        if is_pager
        else PAYMENT_QUERY_PATH
    )
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        client.post(f"{BUSINESS}{path}", data=form)
    assert seen == [f"POST {BUSINESS}{path}"]


@pytest.mark.parametrize(
    "change",
    [
        {"ajax": "0"},
        {"PM_EmpName": "USER-A"},
        {"unexpected": ""},
    ],
)
def test_payment_policy_blocks_query_drift(change: dict[str, str]) -> None:
    url = f"{BUSINESS}{PAYMENT_QUERY_PATH}"
    form = _payment_query_form()
    form.update(change)
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation):
            client.post(url, data=form)
        with pytest.raises(PolicyViolation):
            client.post(url, json=form)
        with pytest.raises(PolicyViolation):
            client.get(url)

    assert seen == []


def test_payment_policy_blocks_duplicate_query_fields() -> None:
    url = f"{BUSINESS}{PAYMENT_QUERY_PATH}"
    form = list(_payment_query_form().items()) + [("ajax", "1")]
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation):
            client.post(
                url,
                content=urlencode(form),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    assert seen == []


def test_payment_policy_blocks_unbounded_or_wrong_pager_body() -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation):
            client.post(
                f"{BUSINESS}{PAYMENT_QUERY_PATH}/0/1/False/101",
                data=payment_empty_query_form(pager=True),
            )
        with pytest.raises(PolicyViolation):
            client.post(
                f"{BUSINESS}{PAYMENT_QUERY_PATH}/0/1/False/2",
                data=payment_empty_query_form(),
            )
    assert seen == []


def test_bizcase_policy_requires_exact_query_and_body_validated_pager() -> None:
    url = f"{BUSINESS}{BIZCASE_QUERY_URL}"
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        client.get(url)
        client.post(url, data=_bizcase_pagination_form())

    assert seen == [f"GET {url}", f"POST {url}"]
    assert EndpointPolicy().decide("POST", url).request_class is RequestClass.BUILD_ONLY


def test_bizcase_policy_allows_only_served_list_view_gets() -> None:
    transport, seen = _tracking_transport()
    urls = [
        (
            f"{BUSINESS}{BIZCASE_QUERY_URL}"
            f"&url={control}&urltype=mcontrol&helpmenucode={help_menu_code}"
        )
        for control, help_menu_code in BIZCASE_VIEW_PARAMS.values()
    ]
    with IsstechClient(transport=transport) as client:
        for url in urls:
            client.get(url)
        with pytest.raises(PolicyViolation):
            client.get(urls[0] + "&unexpected=1")
        with pytest.raises(PolicyViolation):
            client.post(urls[0], data=_bizcase_pagination_form())

    assert seen == [f"GET {url}" for url in urls]


@pytest.mark.parametrize(
    "change",
    [
        {"__EVENTTARGET": "ctl05$dgr$ctl03$lbtnVersionNo"},
        {"__EVENTARGUMENT": "0"},
        {"__EVENTARGUMENT": "1000"},
        {"ctl05$btnQuery": "QUERY"},
        {"__VIEWSTATEGENERATOR": "not-hex"},
    ],
)
def test_bizcase_policy_blocks_unapproved_postbacks_before_transport(
    change: dict[str, str],
) -> None:
    url = f"{BUSINESS}{BIZCASE_QUERY_URL}"
    form = _bizcase_pagination_form()
    form.update(change)
    transport, seen = _tracking_transport()

    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation):
            client.post(url, data=form)
    assert seen == []


def test_bizcase_policy_blocks_json_duplicate_fields_and_query_drift() -> None:
    url = f"{BUSINESS}{BIZCASE_QUERY_URL}"
    form = _bizcase_pagination_form()
    duplicate_form = list(form.items()) + [("__EVENTARGUMENT", "3")]
    transport, seen = _tracking_transport()

    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation):
            client.post(url, json=form)
        with pytest.raises(PolicyViolation):
            client.post(
                url,
                content=urlencode(duplicate_form),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        with pytest.raises(PolicyViolation):
            client.get(url + "&oper=editfp")
    assert seen == []


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_new_module_unobserved_methods_are_blocked(method: str) -> None:
    transport, seen = _tracking_transport()
    with IsstechClient(transport=transport) as client:
        with pytest.raises(PolicyViolation):
            client.request(method, f"{BUSINESS}/WebPMS/Payment/index")
        with pytest.raises(PolicyViolation):
            client.request(method, f"{BUSINESS}{BIZCASE_QUERY_URL}")
    assert seen == []
