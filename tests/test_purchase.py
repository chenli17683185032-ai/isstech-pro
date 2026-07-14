"""Purchase list/detail parsers and read-only client methods."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from isstech_replay.client import EvidenceGapError, IsstechClient
from isstech_replay.config import Settings
from isstech_replay.models.purchase import PurchaseListQuery, PurchaseView
from isstech_replay.parsers.purchase import parse_purchase_detail, parse_purchase_list
from isstech_replay.policy import PolicyViolation

FIXTURES = Path(__file__).parent / "fixtures" / "purchase"
BUSINESS = "http://ipsapro.isstech.com"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_application_list() -> None:
    result = parse_purchase_list(_html("list_application.html"), view=PurchaseView.APPLICATION)
    assert result.total_count == 2
    assert len(result.items) == 2
    first = result.items[0]
    assert first.id == "10001"
    assert first.requisition_no == "XQ-REDACTED-001"
    assert first.project_no == "PRJ-REDACTED-A"
    assert first.project_name == "REDACTED PROJECT ALPHA"
    assert first.creator_name == "USER_A"
    assert first.create_date == "2026-07-01"
    assert first.status == "已保存"
    assert result.items[1].id == "10002"
    assert result.items[1].status == "审批中"


def test_parse_detail_fields() -> None:
    detail = parse_purchase_detail(_html("edit_detail.html"), requisition_id="10001")
    assert detail.id == "10001"
    assert detail.fields["PR_RequisitionNo"] == "XQ-REDACTED-001"
    assert detail.fields["PR_PrjNo"] == "PRJ-REDACTED-A"
    assert detail.fields["PR_Description"] == "A & B"
    assert detail.fields["PR_Category"] == "service"
    assert detail.fields["PR_Urgent"] == "true"
    assert "PR_Unchecked" not in detail.fields
    assert "OUTSIDE_FORM" not in detail.fields
    assert "btnSave" not in detail.fields


def test_list_query_paths() -> None:
    q = PurchaseListQuery(view=PurchaseView.APPROVAL)
    assert q.path() == "/WebTP/PurchaseRequisition/ApprovalIndex"
    q2 = PurchaseListQuery(page=2, page_size=15)
    assert q2.path().endswith("/0/1/False/2/15")
    q3 = PurchaseListQuery(sort_field="PR_PrjNo", page=1, page_size=10)
    assert "lastOrderField/PR_PrjNo" in q3.path()


def test_client_list_application_view() -> None:
    list_html = _html("list_application.html")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "ipsapro.isstech.com"
        assert request.method == "GET"
        assert "/WebTP/PurchaseRequisition/" in request.url.path
        return httpx.Response(200, text=list_html, request=request)

    settings = Settings(base_url=BUSINESS)
    with IsstechClient(settings=settings, transport=httpx.MockTransport(handler)) as client:
        result = client.list_view(PurchaseView.APPLICATION)
        assert result.view is PurchaseView.APPLICATION
        assert len(result.items) == 2


@pytest.mark.parametrize(
    "view",
    [
        PurchaseView.APPROVAL,
        PurchaseView.ADJUSTMENT,
        PurchaseView.REVOCATION,
        PurchaseView.SEARCH,
    ],
)
def test_client_rejects_uncaptured_views_before_transport(view: PurchaseView) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="", request=request)

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(EvidenceGapError):
            client.list_view(view)
    assert seen == []


def test_client_filter_posts_form() -> None:
    list_html = _html("list_application.html")
    posts: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request.content)
            return httpx.Response(200, text=list_html, request=request)
        return httpx.Response(200, text=list_html, request=request)

    settings = Settings(base_url=BUSINESS)
    with IsstechClient(settings=settings, transport=httpx.MockTransport(handler)) as client:
        result = client.list_purchase_requisitions(
            PurchaseListQuery(project_no="PRJ-REDACTED-A", requisition_no="")
        )
    assert len(result.items) == 2
    assert len(posts) == 1
    body = posts[0].decode()
    assert "PR_PrjNo=PRJ-REDACTED-A" in body


def test_client_get_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/Edit/10001")
        return httpx.Response(200, text=_html("edit_detail.html"), request=request)

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        detail = client.get_purchase_requisition("10001")
    assert detail.fields["PR_ID"] == "10001"


@pytest.mark.parametrize("unsafe_id", ["../Delete/1", "%2e%2e/Delete/1", "a/b", "a?b"])
def test_client_rejects_unsafe_requisition_ids(unsafe_id: str) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="<html></html>", request=request)

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ValueError):
            client.get_purchase_requisition(unsafe_id)
    assert seen == []


def test_list_blocks_if_login_page() -> None:
    loginish = (
        '<html><body><form><input name="emp_DomainName"/>'
        '<input name="emp_Password" type="password"/>'
        '<div id="g_loginform"></div></form></body></html>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=loginish, request=request)

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(PermissionError):
            client.list_view(PurchaseView.APPLICATION)


def test_delete_still_blocked_from_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(PolicyViolation):
            client.get(f"{BUSINESS}/WebTP/PurchaseRequisition/Delete/10001")
