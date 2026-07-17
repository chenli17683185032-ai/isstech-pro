"""Daily expense fixed-schema, identity, and GET-only policy guards."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from isstech_replay.client import IsstechClient, PaginationIncompleteError
from isstech_replay.parsers.daily_expense import parse_daily_expense_page
from isstech_replay.policy import DAILY_EXPENSE_URL, PolicyViolation


FIXTURE = Path("tests/fixtures/daily_expense/page1.html")
BUSINESS = "http://ipsapro.isstech.com"


def _page() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_daily_expense_parser_reads_proven_single_page() -> None:
    page = parse_daily_expense_page(
        _page(),
        source_url=f"{BUSINESS}{DAILY_EXPENSE_URL}",
    )

    assert page.current_page == 1
    assert page.page_count == 1
    assert len(page.items) == 1
    assert page.items[0].id == "DEA-REDACTED-001"
    assert page.items[0].project_name == "PROJECT REDACTED"
    assert page.items[0].applicant == "USER-A"
    assert page.items[0].current_approver == "USER-APPROVER"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("单据状态", "UNKNOWN", "schema changed"),
        ("List.aspx?helpmenucode=90", "Add.aspx", "form action changed"),
        ('name="ctl00$ContentPlaceHolder1$txtApplyNo" value=""', 'name="ctl00$ContentPlaceHolder1$txtApplyNo" value="FILTER"', "unapproved filter"),
        ('name="ctl00$ContentPlaceHolder1$ddlOrderBy">', 'name="ctl00$ContentPlaceHolder1$ddlOrderBy"><option value="UNKNOWN" selected>UNKNOWN</option>', "ordering changed"),
        ('name="ctl00$ContentPlaceHolder1$GridPager1" value="GO" disabled', 'name="ctl00$ContentPlaceHolder1$GridPager1" value="GO"', "pagination is not proven"),
        ('name="ctl00$ContentPlaceHolder1$GridPager1_input" value="1"', 'name="ctl00$ContentPlaceHolder1$GridPager1_input" value="2"', "pagination is not proven"),
        ("&amp;oper=edit", "&amp;oper=delete", "detail reference changed"),
    ],
)
def test_daily_expense_parser_fails_closed_on_drift(
    old: str,
    new: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_daily_expense_page(_page().replace(old, new, 1))


def test_daily_expense_parser_rejects_duplicate_identity_and_live_pager() -> None:
    html = _page()
    start = html.index('      <tr class="data-row">')
    end = html.index("</tr>", start) + len("</tr>")
    duplicate = html[start:end].replace("<td>1</td>", "<td>2</td>", 1)
    duplicate = duplicate.replace("id=1001", "id=1002")
    with pytest.raises(ValueError, match="duplicate identity"):
        parse_daily_expense_page(html[:end] + "\n" + duplicate + html[end:])

    live_pager = html.replace(
        '<div id="ctl00_ContentPlaceHolder1_GridPager1">',
        '<div id="ctl00_ContentPlaceHolder1_GridPager1"><a href="javascript:__doPostBack(\'ctl00$ContentPlaceHolder1$GridPager1\',\'2\')">2</a>',
    )
    with pytest.raises(ValueError, match="pagination is not proven"):
        parse_daily_expense_page(live_pager)


def test_daily_expense_client_reads_once_and_requires_current_identity() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        return httpx.Response(200, text=_page(), request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        result = client.list_personal_daily_expenses(
            display_name="USER-A",
            max_pages=1,
        )

    assert result.total_count == 1
    assert result.page_count == 1
    assert seen == [f"GET {BUSINESS}{DAILY_EXPENSE_URL}"]

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PaginationIncompleteError, match="current identity"):
            client.list_personal_daily_expenses(
                display_name="USER-OTHER",
                max_pages=1,
            )


def test_daily_expense_client_rejects_missing_identity_and_page_limit() -> None:
    with IsstechClient(transport=httpx.MockTransport(lambda request: None)) as client:
        with pytest.raises(ValueError, match="display_name"):
            client.list_personal_daily_expenses(display_name="", max_pages=1)
        with pytest.raises(ValueError, match="max_pages"):
            client.list_personal_daily_expenses(display_name="USER-A", max_pages=0)


def test_daily_expense_policy_allows_only_exact_get() -> None:
    url = f"{BUSINESS}{DAILY_EXPENSE_URL}"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        return httpx.Response(200, text=_page(), request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        client.get(url)
        with pytest.raises(PolicyViolation):
            client.post(url, data={"ctl00$ContentPlaceHolder1$btnQuery": "QUERY"})
        with pytest.raises(PolicyViolation):
            client.post(
                url,
                data={
                    "ctl00$ContentPlaceHolder1$MyGridView$ctl02$imageBtn.x": "1",
                    "ctl00$ContentPlaceHolder1$MyGridView$ctl02$imageBtn.y": "1",
                },
            )
        with pytest.raises(PolicyViolation):
            client.get(
                f"{BUSINESS}/WebPSAOA/Fee/FeeApply/DailyExpense/Add.aspx"
                "?id=1001&oper=edit"
            )
        with pytest.raises(PolicyViolation):
            client.get(url + "&unexpected=1")

    assert seen == [f"GET {url}"]
