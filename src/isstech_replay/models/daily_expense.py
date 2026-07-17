"""Fixed-schema records from the account's daily expense list."""

from __future__ import annotations

from dataclasses import dataclass


DAILY_EXPENSE_HEADERS = (
    "序号",
    "申请编号",
    "项目名称",
    "申请人",
    "申请日期",
    "单据状态",
    "总金额",
    "下一级审批人",
    "操作",
)
DAILY_EXPENSE_GRID_ID = "ctl00_ContentPlaceHolder1_MyGridView"
DAILY_EXPENSE_PAGE_SIZE = 10


@dataclass(frozen=True, slots=True)
class DailyExpenseRecord:
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
class DailyExpensePage:
    items: tuple[DailyExpenseRecord, ...]
    current_page: int
    page_count: int
    source_url: str = ""


@dataclass(frozen=True, slots=True)
class DailyExpenseListResult:
    items: tuple[DailyExpenseRecord, ...]
    total_count: int
    page_count: int
    source_url: str = ""
