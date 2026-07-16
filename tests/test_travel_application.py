"""Travel application WebForms schema, pager, identity, and policy guards."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from isstech_replay.client import IsstechClient, PaginationIncompleteError
from isstech_replay.parsers.travel_application import parse_travel_application_page
from isstech_replay.policy import PolicyViolation, TRAVEL_APPLICATION_URL


FIXTURES = Path("tests/fixtures/travel_application")
BUSINESS = "http://ipsapro.isstech.com"


def _page(page: int) -> str:
    if page == 6:
        return (FIXTURES / "last_page.html").read_text(encoding="utf-8")
    html = (FIXTURES / "page1.html").read_text(encoding="utf-8")
    if page == 1:
        return html
    html = html.replace("OPAQUE_PAGE_1", f"OPAQUE_PAGE_{page}")
    html = html.replace("VALIDATION_PAGE_1", f"VALIDATION_PAGE_{page}")
    html = html.replace("&amp;page=1", f"&amp;page={page}")
    html = html.replace(
        'name="ctl00$ContentPlaceHolder1$gp_input" value="1"',
        f'name="ctl00$ContentPlaceHolder1$gp_input" value="{page}"',
    )
    for index in range(1, 11):
        global_index = (page - 1) * 10 + index
        html = html.replace(
            f"ELA-REDACTED-{index:03d}",
            f"ELA-REDACTED-{global_index:03d}",
        )
        html = html.replace(f"id={1000 + index}&amp;", f"id={1000 + global_index}&amp;")
    return html


def test_travel_application_parser_reads_fixed_schema_and_latest_state() -> None:
    page = parse_travel_application_page(
        _page(1),
        source_url=f"{BUSINESS}{TRAVEL_APPLICATION_URL}",
    )

    assert page.current_page == 1
    assert page.page_count == 6
    assert len(page.items) == 10
    assert page.items[0].id == "ELA-REDACTED-001"
    assert page.items[0].project_name == "PROJECT REDACTED 1"
    assert page.items[0].applicant == "USER-A"
    assert page.items[1].current_approver == "USER-APPROVER"
    form = page.pagination_form(2)
    assert form["__EVENTTARGET"] == "ctl00$ContentPlaceHolder1$gp"
    assert form["__EVENTARGUMENT"] == "2"
    assert form["__VIEWSTATE"] == "OPAQUE_PAGE_1"
    assert "ctl00$ContentPlaceHolder1$btnAdd" not in form
    assert not any(name.endswith("imageBtn") for name in form)


def test_travel_application_parser_synthesizes_optional_event_fields() -> None:
    html = _page(1)
    html = html.replace(
        '  <input type="hidden" name="__EVENTTARGET" value="">\n',
        "",
    ).replace(
        '  <input type="hidden" name="__EVENTARGUMENT" value="">\n',
        "",
    ).replace(
        'name="ctl00$ContentPlaceHolder1$chkOrderBy" value="on" checked',
        'name="ctl00$ContentPlaceHolder1$chkOrderBy" checked',
    )

    form = parse_travel_application_page(html).pagination_form(2)

    assert form["__EVENTTARGET"] == "ctl00$ContentPlaceHolder1$gp"
    assert form["__EVENTARGUMENT"] == "2"
    assert form["ctl00$ContentPlaceHolder1$chkOrderBy"] == "on"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("单据状态", "UNKNOWN", "schema changed"),
        ("List.aspx?helpmenucode=92", "Add.aspx", "form action changed"),
        ("OPAQUE_PAGE_1", "", "opaque state is missing"),
        ("ELA-REDACTED-002", "ELA-REDACTED-001", "duplicate identity"),
        ("&amp;oper=edit", "&amp;oper=delete", "detail reference changed"),
    ],
)
def test_travel_application_parser_fails_closed_on_drift(
    old: str,
    new: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_travel_application_page(_page(1).replace(old, new))


def test_travel_application_client_replays_latest_state_to_complete_54_rows() -> None:
    seen: list[tuple[str, int, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            seen.append(("GET", 1, ""))
            return httpx.Response(200, text=_page(1), request=request)
        form = dict(httpx.QueryParams(request.content.decode()))
        page = int(form["__EVENTARGUMENT"])
        seen.append(("POST", page, form["__VIEWSTATE"]))
        assert form["__EVENTTARGET"] == "ctl00$ContentPlaceHolder1$gp"
        assert form["__VIEWSTATE"] == f"OPAQUE_PAGE_{page - 1}"
        return httpx.Response(200, text=_page(page), request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        result = client.list_personal_travel_applications(
            display_name="USER-A",
            max_pages=6,
        )

    assert result.total_count == 54
    assert result.page_count == 6
    assert result.items[0].id == "ELA-REDACTED-001"
    assert result.items[-1].id == "ELA-REDACTED-054"
    assert seen == [
        ("GET", 1, ""),
        ("POST", 2, "OPAQUE_PAGE_1"),
        ("POST", 3, "OPAQUE_PAGE_2"),
        ("POST", 4, "OPAQUE_PAGE_3"),
        ("POST", 5, "OPAQUE_PAGE_4"),
        ("POST", 6, "OPAQUE_PAGE_5"),
    ]


def test_travel_application_client_rejects_identity_drift_and_page_limit() -> None:
    def identity_drift(request: httpx.Request) -> httpx.Response:
        page = 1
        if request.method == "POST":
            page = int(httpx.QueryParams(request.content.decode())["__EVENTARGUMENT"])
        html = _page(page)
        if page == 3:
            html = html.replace("USER-A", "USER-OTHER", 1)
        return httpx.Response(200, text=html, request=request)

    with IsstechClient(transport=httpx.MockTransport(identity_drift)) as client:
        with pytest.raises(PaginationIncompleteError, match="current identity"):
            client.list_personal_travel_applications(
                display_name="USER-A",
                max_pages=6,
            )

    with IsstechClient(transport=httpx.MockTransport(identity_drift)) as client:
        with pytest.raises(PaginationIncompleteError, match="exceeds max_pages"):
            client.list_personal_travel_applications(
                display_name="USER-A",
                max_pages=5,
            )


def test_travel_application_policy_allows_only_exact_list_and_pager() -> None:
    url = f"{BUSINESS}{TRAVEL_APPLICATION_URL}"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        return httpx.Response(200, text=_page(1), request=request)

    form = parse_travel_application_page(_page(1)).pagination_form(2)
    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        client.get(url)
        client.post(url, data=form)

    assert seen == [f"GET {url}", f"POST {url}"]


@pytest.mark.parametrize(
    "change",
    [
        {"__EVENTTARGET": "ctl00$ContentPlaceHolder1$btnAdd"},
        {"ctl00$ContentPlaceHolder1$txtApplyNo": "ELA-OTHER"},
        {"ctl00$ContentPlaceHolder1$MyGridView$ctl02$imageBtn.x": "1"},
        {"__EVENTARGUMENT": "0"},
        {"ctl00$ContentPlaceHolder1$ddlOrderBy": "UNKNOWN"},
    ],
)
def test_travel_application_policy_blocks_write_or_filter_postbacks(
    change: dict[str, str],
) -> None:
    url = f"{BUSINESS}{TRAVEL_APPLICATION_URL}"
    form = parse_travel_application_page(_page(1)).pagination_form(2)
    form.update(change)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PolicyViolation):
            client.post(url, data=form)
        with pytest.raises(PolicyViolation):
            client.get(
                f"{BUSINESS}/WebPSAOA/Fee/FeeApply/EvectionLoan/Add.aspx"
                "?id=1001&oper=edit"
            )
        with pytest.raises(PolicyViolation):
            client.get(url + "&unexpected=1")
    assert seen == []
