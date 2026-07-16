"""BizCase WebForms schema, state, and identity guards."""

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from isstech_replay.client import IsstechClient, PaginationIncompleteError
from isstech_replay.parsers.bizcase import (
    parse_bizcase_application_page,
    parse_bizcase_page,
)
from isstech_replay.policy import (
    BIZCASE_APPLICATION_URL,
    BIZCASE_QUERY_THURL,
)


FIXTURES = Path("tests/fixtures/bizcase")


def _html(page: int = 1) -> str:
    return (FIXTURES / f"page{page}.html").read_text(encoding="utf-8")


def _application_html() -> str:
    return (FIXTURES / "application.html").read_text(encoding="utf-8")


def test_bizcase_application_parser_reads_complete_single_page() -> None:
    result = parse_bizcase_application_page(
        _application_html(),
        source_url="http://example.test/bizcase-application",
    )

    assert result.total_count == 2
    assert result.page_count == 1
    assert [item.ordinal for item in result.items] == [1, 2]
    assert [item.id for item in result.items] == [
        "BC-REDACTED-001-V001",
        "BC-REDACTED-003-V001",
    ]
    assert result.items[0].field_dict()["BizCase状态"] == "已保存"


def test_bizcase_application_parser_fails_closed_on_drift_or_pager() -> None:
    with pytest.raises(ValueError, match="application schema changed"):
        parse_bizcase_application_page(
            _application_html().replace("BizCase状态", "UNKNOWN", 1)
        )

    duplicate = _application_html().replace(
        "BC-REDACTED-003",
        "BC-REDACTED-001",
    )
    with pytest.raises(ValueError, match="duplicate stable identity"):
        parse_bizcase_application_page(duplicate)

    paged = _application_html().replace(
        "</form>",
        (
            '<select name="ctl05$GridPager1ddlPager">'
            '<option selected value="1">1</option><option value="2">2</option>'
            "</select></form>"
        ),
    )
    with pytest.raises(ValueError, match="single page"):
        parse_bizcase_application_page(paged)

    scope_drift = _application_html().replace(
        'name="__VIEWSTATE" value="',
        'name="__VIEWSTATE" value="Tk9fUEVSU09OQUxfU0NPUEU=ignored-',
        1,
    )
    with pytest.raises(ValueError, match="personal scope predicate changed"):
        parse_bizcase_application_page(scope_drift)


def test_bizcase_parser_reads_fixed_schema_and_opaque_state() -> None:
    result = parse_bizcase_page(_html(), source_url="http://example.test/bizcase")

    assert result.current_page == 1
    assert result.page_count == 2
    assert [item.ordinal for item in result.items] == [1, 2]
    assert result.items[0].id == "BC-REDACTED-001-V001"
    assert result.items[0].project_no == "PROJECT-1"
    assert "STATE_PAGE_1" not in repr(result)

    form = result.pagination_form(2)
    assert form["__EVENTTARGET"] == "ctl05$GridPager1"
    assert form["__EVENTARGUMENT"] == "2"
    assert form["__VIEWSTATE"] == "STATE_PAGE_1"


def test_bizcase_parser_accepts_response_without_event_hidden_inputs() -> None:
    result = parse_bizcase_page(_html(2))

    assert result.current_page == 2
    assert [item.ordinal for item in result.items] == [3]
    assert result.pagination_form(1)["__EVENTARGUMENT"] == "1"


def test_bizcase_parser_accepts_empty_list() -> None:
    result = parse_bizcase_page((FIXTURES / "empty.html").read_text(encoding="utf-8"))
    assert result.items == ()
    assert result.current_page == 1
    assert result.page_count == 1


def test_bizcase_parser_rejects_schema_drift_and_missing_identity() -> None:
    with pytest.raises(ValueError, match="schema changed"):
        parse_bizcase_page(_html().replace("客户名称", "未知客户列", 1))

    broken_link = _html().replace("$lbtnVersionNo", "$unknownAction", 1)
    with pytest.raises(ValueError, match="stable identity"):
        parse_bizcase_page(broken_link)


def test_bizcase_parser_rejects_invalid_pager_and_ordinals() -> None:
    pager_gap = _html().replace(
        '<option selected value="1">1</option><option value="2">2</option>',
        '<option selected value="1">1</option><option value="3">3</option>',
    )
    with pytest.raises(ValueError, match="pager metadata is invalid"):
        parse_bizcase_page(pager_gap)

    non_contiguous = _html().replace("<td>2</td>", "<td>4</td>", 1)
    with pytest.raises(ValueError, match="ordinals are not contiguous"):
        parse_bizcase_page(non_contiguous)


def test_bizcase_pagination_form_rejects_out_of_range_page() -> None:
    result = parse_bizcase_page(_html())
    with pytest.raises(ValueError, match="between 1 and 2"):
        result.pagination_form(3)


def test_bizcase_parser_rejects_login_page() -> None:
    login_html = Path("tests/fixtures/auth/passport_login.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="grid not found"):
        parse_bizcase_page(login_html)


def test_bizcase_client_replays_sequential_pager_and_collects_all_rows() -> None:
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        assert query == {"thUrl": [BIZCASE_QUERY_THURL]}
        body = parse_qs(request.content.decode(), keep_blank_values=True)
        seen.append(
            (
                request.method,
                body.get("__EVENTTARGET", [""])[0],
                body.get("__EVENTARGUMENT", [""])[0],
            )
        )
        fixture = _html(1 if request.method == "GET" else 2)
        return httpx.Response(200, text=fixture, request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        result = client.list_all_bizcases(max_pages=2)

    assert result.total_count == 3
    assert result.page_count == 2
    assert [item.ordinal for item in result.items] == [1, 2, 3]
    assert seen == [("GET", "", ""), ("POST", "ctl05$GridPager1", "2")]


def test_bizcase_client_intersects_application_evidence_with_query_source() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        if str(request.url).endswith(BIZCASE_APPLICATION_URL):
            assert request.method == "GET"
            seen.append(("application", request.method))
            return httpx.Response(200, text=_application_html(), request=request)
        assert query == {"thUrl": [BIZCASE_QUERY_THURL]}
        seen.append(("query", request.method))
        return httpx.Response(
            200,
            text=_html(1 if request.method == "GET" else 2),
            request=request,
        )

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        result = client.list_personal_bizcases(max_pages=2)

    assert result.total_count == 3
    assert result.submitted_or_managed_ids == (
        "BC-REDACTED-001-V001",
        "BC-REDACTED-003-V001",
    )
    assert seen == [
        ("application", "GET"),
        ("query", "GET"),
        ("query", "POST"),
    ]


def test_bizcase_client_rejects_application_record_missing_from_query_source() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith(BIZCASE_APPLICATION_URL):
            html = _application_html().replace(
                "BC-REDACTED-003-V001",
                "BC-REDACTED-999-V001",
                1,
            ).replace("BC-REDACTED-003", "BC-REDACTED-999", 1)
            return httpx.Response(200, text=html, request=request)
        return httpx.Response(
            200,
            text=_html(1 if request.method == "GET" else 2),
            request=request,
        )

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PaginationIncompleteError, match="application identity"):
            client.list_personal_bizcases(max_pages=2)


def test_bizcase_client_rejects_page_count_drift_and_max_page_limit() -> None:
    def drifting_handler(request: httpx.Request) -> httpx.Response:
        fixture = _html(1 if request.method == "GET" else 2).replace(
            '<option value="1">1</option><option selected value="2">2</option>',
            (
                '<option value="1">1</option><option selected value="2">2</option>'
                '<option value="3">3</option>'
            ),
        )
        return httpx.Response(200, text=fixture, request=request)

    with IsstechClient(transport=httpx.MockTransport(drifting_handler)) as client:
        with pytest.raises(PaginationIncompleteError, match="page count changed"):
            client.list_all_bizcases(max_pages=3)

    with IsstechClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=_html(), request=request)
        )
    ) as client:
        with pytest.raises(PaginationIncompleteError, match="exceeds max_pages"):
            client.list_all_bizcases(max_pages=1)
