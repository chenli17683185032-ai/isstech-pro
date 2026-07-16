"""BizCase WebForms schema, state, and identity guards."""

from pathlib import Path

import pytest

from isstech_replay.parsers.bizcase import parse_bizcase_page


FIXTURES = Path("tests/fixtures/bizcase")


def _html(page: int = 1) -> str:
    return (FIXTURES / f"page{page}.html").read_text(encoding="utf-8")


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
