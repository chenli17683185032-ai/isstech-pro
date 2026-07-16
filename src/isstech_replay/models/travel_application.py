"""Fixed-schema records from the account's travel application list."""

from __future__ import annotations

from dataclasses import dataclass


TRAVEL_APPLICATION_HEADERS = (
    "序号",
    "单据编号",
    "项目名称",
    "申请人",
    "申请日期",
    "单据状态",
    "总金额",
    "下一级审批人",
    "操作",
)
TRAVEL_APPLICATION_GRID_ID = "ctl00_ContentPlaceHolder1_MyGridView"
TRAVEL_APPLICATION_PAGER_TARGET = "ctl00$ContentPlaceHolder1$gp"
TRAVEL_APPLICATION_PAGE_SIZE = 10


@dataclass(frozen=True, slots=True)
class TravelApplicationRecord:
    id: str
    ordinal: int
    application_no: str
    project_name: str = ""
    applicant: str = ""
    application_date: str = ""
    status: str = ""
    amount: str = ""
    current_approver: str = ""
    fields: tuple[tuple[str, str], ...] = ()

    def field_dict(self) -> dict[str, str]:
        return dict(self.fields)


@dataclass(frozen=True, slots=True)
class TravelApplicationPage:
    items: tuple[TravelApplicationRecord, ...]
    current_page: int
    page_count: int
    source_url: str = ""
    postback_fields: tuple[tuple[str, str], ...] = ()

    def pagination_form(self, page: int) -> dict[str, str]:
        form = dict(self.postback_fields)
        form["__EVENTTARGET"] = TRAVEL_APPLICATION_PAGER_TARGET
        form["__EVENTARGUMENT"] = str(page)
        return form


@dataclass(frozen=True, slots=True)
class TravelApplicationListResult:
    items: tuple[TravelApplicationRecord, ...]
    total_count: int
    page_count: int
    source_url: str = ""
