"""Payment list schema and identity guards."""

from pathlib import Path

import httpx
import pytest

from isstech_replay.client import IsstechClient, PaginationIncompleteError
from isstech_replay.parsers.payment import parse_payment_list


FIXTURE = Path("tests/fixtures/payment/list.html")


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


def test_payment_client_uses_only_localized_initial_get_and_requires_complete_page() -> None:
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                request.headers.get("accept-language"),
            )
        )
        return httpx.Response(200, text=_html(), request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        result = client.list_payment_records()

    assert result.total_count == 2
    assert seen == [("GET", "/WebPMS/Payment/index", "zh-CN")]


def test_payment_client_rejects_declared_multi_page_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_html().replace("共1页，当前第1页", "共2页，当前第1页"),
            request=request,
        )

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PaginationIncompleteError, match="multiple pages"):
            client.list_payment_records()
