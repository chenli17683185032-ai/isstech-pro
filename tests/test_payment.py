"""Payment list schema and identity guards."""

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from isstech_replay.client import IsstechClient, PaginationIncompleteError
from isstech_replay.models.payment import (
    PAYMENT_QUERY_FORM_FIELDS,
    PAYMENT_QUERY_PAGER_FORM_FIELDS,
    payment_query_form,
)
from isstech_replay.parsers.payment import parse_payment_list, parse_payment_query_list


FIXTURE = Path("tests/fixtures/payment/list.html")
QUERY_PAGE_1 = FIXTURE.parent / "query_page1.html"
QUERY_PAGE_2 = FIXTURE.parent / "query_page2.html"


def _html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_payment_parser_reads_fixed_schema_and_pager() -> None:
    result = parse_payment_list(_html(), source_url="http://example.test/payment")

    assert result.total_count == 2
    assert result.page_count == 1
    assert result.current_page == 1
    assert [item.id for item in result.items] == ["PAY-1", "PAY-2"]
    assert result.items[0].payment_no == "PAYMENT-REDACTED-1"
    assert result.items[0].field_dict()["状态"] == "已保存"


def test_payment_parser_accepts_empty_list() -> None:
    result = parse_payment_list(
        (FIXTURE.parent / "empty.html").read_text(encoding="utf-8")
    )
    assert result.items == ()
    assert result.total_count == 0


def test_payment_parser_rejects_schema_drift_and_missing_identity() -> None:
    with pytest.raises(ValueError, match="schema changed"):
        parse_payment_list(_html().replace("付款类别", "未知类别", 1))

    missing_identity = _html().replace(' ajax-data="PAY-1"', "")
    with pytest.raises(ValueError, match="stable identity"):
        parse_payment_list(missing_identity)


def test_payment_parser_rejects_missing_or_impossible_pager() -> None:
    with pytest.raises(ValueError, match="pager metadata not found"):
        parse_payment_list(_html().replace("总共2条记录，共1页，当前第1页", ""))

    with pytest.raises(ValueError, match="exceeds declared total"):
        parse_payment_list(_html().replace("总共2条记录", "总共1条记录"))


def test_payment_parser_rejects_login_page() -> None:
    login_html = Path("tests/fixtures/auth/passport_login.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="grid not found"):
        parse_payment_list(login_html)


def test_payment_query_parser_reads_extended_schema() -> None:
    result = parse_payment_query_list(QUERY_PAGE_1.read_text(encoding="utf-8"))

    assert result.total_count == 3
    assert result.page_count == 2
    assert [item.id for item in result.items] == ["PAY-1", "PAY-2"]
    assert result.items[0].applicant == "USER-A"
    assert result.items[0].field_dict()["下级审批人"] == "APPROVER-A"
    assert result.items[0].field_dict()["资金支付状态"] == "待付款"


def test_payment_query_parser_rejects_schema_and_duplicate_identity() -> None:
    html = QUERY_PAGE_1.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="schema changed"):
        parse_payment_query_list(html.replace("付款事由", "未知字段", 1))
    with pytest.raises(ValueError, match="duplicate stable identity"):
        parse_payment_query_list(html.replace('ajax-data="PAY-2"', 'ajax-data="PAY-1"'))


def test_payment_client_replays_complete_query_pages() -> None:
    seen: list[tuple[str, str, str | None, set[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode(), keep_blank_values=True)
        seen.append(
            (
                request.method,
                request.url.path,
                request.headers.get("accept-language"),
                set(body),
            )
        )
        fixture = QUERY_PAGE_1 if request.url.path.endswith("QueryListBySearch") else QUERY_PAGE_2
        return httpx.Response(200, text=fixture.read_text(encoding="utf-8"), request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        result = client.list_payment_records(max_pages=2)

    assert result.total_count == 3
    assert [item.id for item in result.items] == ["PAY-1", "PAY-2", "PAY-3"]
    assert seen == [
        (
            "POST",
            "/WebPMS/payment/QueryListBySearch",
            "zh-CN",
            {"ajax", *PAYMENT_QUERY_FORM_FIELDS},
        ),
        (
            "POST",
            "/WebPMS/payment/QueryListBySearch/0/1/False/2",
            "zh-CN",
            {"ajax", *PAYMENT_QUERY_PAGER_FORM_FIELDS},
        ),
    ]


def test_payment_client_rejects_page_limit_and_total_drift() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        html = QUERY_PAGE_1.read_text(encoding="utf-8")
        if not request.url.path.endswith("QueryListBySearch"):
            html = QUERY_PAGE_2.read_text(encoding="utf-8").replace(
                "总共3条记录",
                "总共4条记录",
            )
        return httpx.Response(
            200,
            text=html,
            request=request,
        )

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PaginationIncompleteError, match="exceeds max_pages"):
            client.list_payment_records(max_pages=1)
        with pytest.raises(PaginationIncompleteError, match="totals changed"):
            client.list_payment_records(max_pages=2)


def test_payment_client_unions_applicant_and_exact_project_scope() -> None:
    html = QUERY_PAGE_1.read_text(encoding="utf-8").replace(
        "总共3条记录，共2页，当前第1页",
        "总共2条记录，共1页，当前第1页",
    )
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(request.content.decode(), keep_blank_values=True))
        return httpx.Response(200, text=html, request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        result = client.list_personal_payment_records(
            display_name="USER-A",
            project_numbers=(" PROJECT-2 ", "PROJECT-2"),
            max_pages=1,
        )

    assert [item.id for item in result.items] == ["PAY-1", "PAY-2"]
    assert result.total_count == 2
    assert result.page_count == 2
    assert len(seen) == 2
    assert seen[0]["PM_EmpName"] == ["USER-A"]
    assert seen[1]["PM_ProjectNo"] == ["PROJECT-2"]


def test_payment_personal_query_rejects_missing_identity_or_mixed_scope() -> None:
    with IsstechClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="", request=request)
        )
    ) as client:
        with pytest.raises(ValueError, match="display name"):
            client.list_personal_payment_records(
                display_name=" ",
                project_numbers=(),
            )
    with pytest.raises(ValueError, match="one personal scope"):
        payment_query_form(applicant="USER-A", project_no="PROJECT-1")
