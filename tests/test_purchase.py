"""Purchase list/detail parsers and read-only client methods."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from isstech_replay.client import IsstechClient, PaginationIncompleteError
from isstech_replay.config import Settings
from isstech_replay.models.purchase import (
    PurchaseListQuery,
    PurchaseListResult,
    PurchaseRequisitionSummary,
    PurchaseView,
)
from isstech_replay.parsers.purchase import parse_purchase_detail, parse_purchase_list
from isstech_replay.policy import PolicyViolation

FIXTURES = Path(__file__).parent / "fixtures" / "purchase"
BUSINESS = "http://ipsapro.isstech.com"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _page(
    *ids: str,
    total_count: int | None,
    page: int,
    page_size: int = 2,
) -> PurchaseListResult:
    return PurchaseListResult(
        view=PurchaseView.SEARCH,
        items=tuple(
            PurchaseRequisitionSummary(
                id=item_id,
                requisition_no=f"REF-{item_id}",
                project_no="PROJECT-REDACTED",
            )
            for item_id in ids
        ),
        total_count=total_count,
        page=page,
        page_size=page_size,
    )


def _collect_pages(
    results: list[PurchaseListResult],
    *,
    max_pages: int = 100,
) -> tuple[PurchaseListResult, list[int]]:
    calls: list[int] = []

    def no_transport(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected transport call: {request.url}")

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(no_transport),
    ) as client:
        def fake_list(query: PurchaseListQuery | None = None) -> PurchaseListResult:
            assert query is not None
            calls.append(query.page)
            return results[query.page - 1]

        client.list_purchase_requisitions = fake_list  # type: ignore[method-assign]
        result = client.list_all_purchase_requisitions(
            PurchaseListQuery(view=PurchaseView.SEARCH, page_size=2),
            max_pages=max_pages,
        )
    return result, calls


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


def test_parse_search_list_includes_current_approver() -> None:
    result = parse_purchase_list(_html("list_search.html"), view=PurchaseView.SEARCH)
    assert result.total_count == 2
    assert len(result.items) == 2
    assert result.items[0].id == "20001"
    assert result.items[0].requisition_no == "XQ-REDACTED-101"
    assert result.items[0].status == "审批中"
    assert result.items[0].next_approver == "USER_APPROVER"
    assert result.items[1].next_approver == ""


def test_list_parser_rejects_missing_grid() -> None:
    with pytest.raises(ValueError, match="grid not found"):
        parse_purchase_list("<html><body>upstream error</body></html>")


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


def test_parse_readonly_detail_fields_and_approval_steps() -> None:
    detail = parse_purchase_detail(_html("detail_readonly.html"), requisition_id="20001")
    assert detail.fields["PR_RequisitionNo"] == "XQ-REDACTED-101"
    assert detail.fields["PR_PrjNo"] == "PRJ-REDACTED-X"
    assert detail.fields["PR_ProcurementMethod"] == "REDACTED METHOD"
    assert len(detail.approval_steps) == 2
    assert detail.approval_steps[0].action == "提交"
    assert detail.approval_steps[1].approver_name == "USER_APPROVER_A"


def test_detail_parser_rejects_missing_fields() -> None:
    with pytest.raises(ValueError, match="fields not found"):
        parse_purchase_detail("<html><body>upstream error</body></html>", requisition_id="1")


def test_list_query_paths() -> None:
    q = PurchaseListQuery(view=PurchaseView.APPROVAL)
    assert q.path() == "/WebTP/PurchaseRequisition/ApprovalIndex"
    q2 = PurchaseListQuery(page=2, page_size=15)
    assert q2.path().endswith("/0/1/False/2/15")
    q3 = PurchaseListQuery(sort_field="PR_PrjNo", page=1, page_size=10)
    assert "lastOrderField/PR_PrjNo" in q3.path()
    q4 = PurchaseListQuery(view=PurchaseView.SEARCH, page=2)
    assert q4.path().endswith("/SearchIndex/0/1/False/2")


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


@pytest.mark.parametrize("view", list(PurchaseView))
def test_client_all_captured_views_are_live_enabled(view: PurchaseView) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        fixture = "list_approval_empty.html" if view is PurchaseView.APPROVAL else "list_search.html"
        return httpx.Response(200, text=_html(fixture), request=request)

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.list_view(view)
    assert result.view is view
    assert len(seen) == 1


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


def test_client_search_filter_and_pagination_use_observed_post_shape() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=_html("list_search.html"), request=request)

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        client.list_purchase_requisitions(
            PurchaseListQuery(
                view=PurchaseView.SEARCH,
                status="审批中",
                next_approver="USER_APPROVER",
            )
        )
        client.list_purchase_requisitions(
            PurchaseListQuery(view=PurchaseView.SEARCH, page=2)
        )

    assert [request.method for request in requests] == ["POST", "POST"]
    assert requests[0].url.path.endswith("/SearchIndex")
    assert requests[1].url.path.endswith("/SearchIndex/0/1/False/2")
    body = requests[0].content.decode()
    page_body = requests[1].content.decode()
    assert "PR_Status=" in body
    assert "NextApproverName=USER_APPROVER" in body
    assert "X-Requested-With=XMLHttpRequest" in body
    assert "X-Requested-With=" not in page_body
    assert "btnSearch=" not in page_body
    assert requests[1].headers["X-Requested-With"] == "XMLHttpRequest"


def test_client_raises_on_upstream_error_before_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="<html></html>", request=request)

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.list_view(PurchaseView.SEARCH)


def test_list_all_stops_only_when_reported_total_is_satisfied() -> None:
    result, calls = _collect_pages(
        [
            _page("1", "2", total_count=3, page=1),
            _page("3", total_count=3, page=2),
        ]
    )
    assert calls == [1, 2]
    assert [item.id for item in result.items] == ["1", "2", "3"]
    assert result.total_count == 3


def test_list_all_accepts_short_terminal_page_without_reported_total() -> None:
    result, calls = _collect_pages(
        [
            _page("1", "2", total_count=None, page=1),
            _page("3", total_count=None, page=2),
        ]
    )
    assert calls == [1, 2]
    assert len(result.items) == 3
    assert result.total_count is None


def test_list_all_rejects_short_page_before_reported_total() -> None:
    with pytest.raises(PaginationIncompleteError, match="short page 2"):
        _collect_pages(
            [
                _page("1", "2", total_count=4, page=1),
                _page("3", total_count=4, page=2),
            ]
        )


def test_list_all_rejects_repeated_page_without_progress() -> None:
    with pytest.raises(PaginationIncompleteError, match="repeated without progress"):
        _collect_pages(
            [
                _page("1", "2", total_count=4, page=1),
                _page("1", "2", total_count=4, page=2),
            ]
        )


def test_list_all_rejects_total_change_during_run() -> None:
    with pytest.raises(PaginationIncompleteError, match="total changed"):
        _collect_pages(
            [
                _page("1", "2", total_count=4, page=1),
                _page("3", "4", total_count=5, page=2),
            ]
        )


def test_list_all_rejects_max_page_truncation() -> None:
    with pytest.raises(PaginationIncompleteError, match="max_pages=1"):
        _collect_pages([_page("1", "2", total_count=5, page=1)], max_pages=1)


def test_client_get_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/Detail/10001")
        return httpx.Response(200, text=_html("detail_readonly.html"), request=request)

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        detail = client.get_purchase_requisition("10001")
    assert detail.fields["PR_RequisitionNo"] == "XQ-REDACTED-101"
    assert len(detail.approval_steps) == 2


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
