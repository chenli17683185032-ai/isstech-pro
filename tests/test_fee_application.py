"""Additional fee-list schema, identity, pagination, and policy guards."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from isstech_replay.client import IsstechClient, PaginationIncompleteError
from isstech_replay.models.fee_application import (
    TRAVEL_REIMBURSEMENT_SPEC,
    TRAVEL_SUBSIDY_SPEC,
)
from isstech_replay.parsers.fee_application import parse_fee_application_page
from isstech_replay.policy import (
    PolicyViolation,
    TRAVEL_REIMBURSEMENT_URL,
    TRAVEL_SUBSIDY_URL,
)


FIXTURES = Path("tests/fixtures")
BUSINESS = "http://ipsapro.isstech.com"


def _reimbursement_page() -> str:
    return (FIXTURES / "travel_reimbursement/page1.html").read_text(
        encoding="utf-8"
    )


def _subsidy_page(page: int) -> str:
    return (FIXTURES / f"travel_subsidy/page{page}.html").read_text(
        encoding="utf-8"
    )


def test_fee_application_parser_reads_single_and_paginated_lists() -> None:
    reimbursement = parse_fee_application_page(
        _reimbursement_page(),
        spec=TRAVEL_REIMBURSEMENT_SPEC,
        source_url=f"{BUSINESS}{TRAVEL_REIMBURSEMENT_URL}",
    )
    assert reimbursement.current_page == 1
    assert reimbursement.page_count == 1
    assert len(reimbursement.items) == 2
    assert reimbursement.items[0].id == "EEA-REDACTED-R001"
    assert reimbursement.items[0].applicant == "USER-A"

    pages = [
        parse_fee_application_page(
            _subsidy_page(page),
            spec=TRAVEL_SUBSIDY_SPEC,
        )
        for page in range(1, 4)
    ]
    assert [page.current_page for page in pages] == [1, 2, 3]
    assert [page.page_count for page in pages] == [3, 3, 3]
    assert [len(page.items) for page in pages] == [10, 10, 8]
    assert [page.items[0].ordinal for page in pages] == [1, 11, 21]
    form = pages[0].pagination_form(2)
    assert form["__VIEWSTATE"] == "OPAQUE_SUBSIDY_1"
    assert form["__EVENTTARGET"] == TRAVEL_SUBSIDY_SPEC.pager_target
    assert form[TRAVEL_SUBSIDY_SPEC.pager_input] == "2"
    assert "ctl00$ContentPlaceHolder1$btnAdd" not in form
    assert not any(name.endswith("imageBtn") for name in form)


@pytest.mark.parametrize(
    ("spec", "html", "old", "new", "message"),
    [
        (
            TRAVEL_REIMBURSEMENT_SPEC,
            _reimbursement_page(),
            "申请编号",
            "UNKNOWN",
            "schema changed",
        ),
        (
            TRAVEL_REIMBURSEMENT_SPEC,
            _reimbursement_page(),
            "helpmenucode=93",
            "helpmenucode=112",
            "form action changed",
        ),
        (
            TRAVEL_SUBSIDY_SPEC,
            _subsidy_page(1),
            "OPAQUE_SUBSIDY_1",
            "",
            "opaque state is missing",
        ),
        (
            TRAVEL_SUBSIDY_SPEC,
            _subsidy_page(1),
            "ESA-REDACTED-S002",
            "ESA-REDACTED-S001",
            "duplicate identity",
        ),
        (
            TRAVEL_SUBSIDY_SPEC,
            _subsidy_page(1),
            "oper=edit",
            "oper=delete",
            "detail reference changed",
        ),
    ],
)
def test_fee_application_parser_fails_closed_on_drift(
    spec,
    html: str,
    old: str,
    new: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_fee_application_page(html.replace(old, new, 1), spec=spec)


def test_fee_application_clients_return_complete_identity_bound_results() -> None:
    seen: list[tuple[str, int, str]] = []

    def subsidy_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            seen.append(("GET", 1, ""))
            page = 1
        else:
            form = dict(httpx.QueryParams(request.content.decode()))
            page = int(form[TRAVEL_SUBSIDY_SPEC.pager_input])
            seen.append(("POST", page, form["__VIEWSTATE"]))
            assert form["__VIEWSTATE"] == f"OPAQUE_SUBSIDY_{page - 1}"
        return httpx.Response(200, text=_subsidy_page(page), request=request)

    with IsstechClient(transport=httpx.MockTransport(subsidy_handler)) as client:
        result = client.list_personal_travel_subsidies(
            display_name="USER-A",
            max_pages=3,
        )

    assert result.total_count == 28
    assert result.page_count == 3
    assert result.items[-1].id == "ESA-REDACTED-S028"
    assert seen == [
        ("GET", 1, ""),
        ("POST", 2, "OPAQUE_SUBSIDY_1"),
        ("POST", 3, "OPAQUE_SUBSIDY_2"),
    ]

    reimbursement_seen: list[str] = []

    def reimbursement_handler(request: httpx.Request) -> httpx.Response:
        reimbursement_seen.append(request.method)
        return httpx.Response(200, text=_reimbursement_page(), request=request)

    with IsstechClient(transport=httpx.MockTransport(reimbursement_handler)) as client:
        reimbursement = client.list_personal_travel_reimbursements(
            display_name="USER-A",
            max_pages=1,
        )
    assert reimbursement.total_count == 2
    assert reimbursement_seen == ["GET"]


def test_fee_application_client_rejects_page_limit_and_identity_drift() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = 1
        if request.method == "POST":
            form = dict(httpx.QueryParams(request.content.decode()))
            page = int(form[TRAVEL_SUBSIDY_SPEC.pager_input])
        html = _subsidy_page(page)
        if page == 2:
            html = html.replace("USER-A", "USER-OTHER", 1)
        return httpx.Response(200, text=html, request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PaginationIncompleteError, match="current identity"):
            client.list_personal_travel_subsidies(
                display_name="USER-A",
                max_pages=3,
            )
    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PaginationIncompleteError, match="exceeds max_pages"):
            client.list_personal_travel_subsidies(
                display_name="USER-A",
                max_pages=2,
            )


def test_fee_application_policy_allows_only_exact_list_and_numeric_pager() -> None:
    reimbursement_url = f"{BUSINESS}{TRAVEL_REIMBURSEMENT_URL}"
    subsidy_url = f"{BUSINESS}{TRAVEL_SUBSIDY_URL}"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        return httpx.Response(200, text=_subsidy_page(1), request=request)

    form = parse_fee_application_page(
        _subsidy_page(1), spec=TRAVEL_SUBSIDY_SPEC
    ).pagination_form(2)
    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        client.get(reimbursement_url)
        client.get(subsidy_url)
        client.post(subsidy_url, data=form)
        with pytest.raises(PolicyViolation):
            client.post(reimbursement_url, data=form)
        with pytest.raises(PolicyViolation):
            client.get(reimbursement_url + "&unexpected=1")
        with pytest.raises(PolicyViolation):
            client.get(
                f"{BUSINESS}/WebPSAOA/Fee/FeeApply/EvectionSubsidy/Add.aspx"
                "?id=2001&oper=edit"
            )
    assert seen == [
        f"GET {reimbursement_url}",
        f"GET {subsidy_url}",
        f"POST {subsidy_url}",
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"__EVENTTARGET": "ctl00$ContentPlaceHolder1$btnAdd"},
        {"ctl00$ContentPlaceHolder1$txtApplyNo": "ES-OTHER"},
        {"ctl00$ContentPlaceHolder1$MyGridView$ctl02$imageBtn.x": "1"},
        {"ctl00$ContentPlaceHolder1$GridPager1_input": "0"},
        {"ctl00$ContentPlaceHolder1$ddlOrderBy": "UNKNOWN"},
        {"ctl00$ContentPlaceHolder1$MyGridView$ctl02$workflowownerid": "1,WRITE"},
    ],
)
def test_travel_subsidy_policy_blocks_write_or_filter_postbacks(
    change: dict[str, str],
) -> None:
    url = f"{BUSINESS}{TRAVEL_SUBSIDY_URL}"
    form = parse_fee_application_page(
        _subsidy_page(1), spec=TRAVEL_SUBSIDY_SPEC
    ).pagination_form(2)
    form.update(change)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PolicyViolation):
            client.post(url, data=form)
    assert seen == []
