"""Fixed-schema BizCase query records and opaque WebForms page state."""

from __future__ import annotations

from dataclasses import dataclass, field


BIZCASE_HEADERS = (
    "序号",
    "BizCase版本号",
    "BizCase编号",
    "客户名称",
    "利润中心组",
    "利润中心",
    "项目编号",
    "项目名称",
    "收入确认类型",
    "当前审批人",
)

BIZCASE_APPLICATION_HEADERS = (
    "序号",
    "BizCase版本号",
    "BizCase编号",
    "客户名称",
    "利润中心组",
    "利润中心",
    "项目编号",
    "项目名称",
    "收入确认类型",
    "BizCase状态",
    "操作",
)

BIZCASE_PAGER_TARGET = "ctl05$GridPager1"


@dataclass(frozen=True, slots=True)
class BizCaseRecord:
    id: str
    ordinal: int
    version_no: str
    bizcase_no: str = ""
    client_name: str = ""
    profit_center_group: str = ""
    profit_center: str = ""
    project_no: str = ""
    project_name: str = ""
    revenue_recognition_type: str = ""
    current_approver: str = ""
    fields: tuple[tuple[str, str], ...] = ()

    def field_dict(self) -> dict[str, str]:
        return dict(self.fields)


@dataclass(frozen=True, slots=True)
class BizCasePage:
    items: tuple[BizCaseRecord, ...]
    current_page: int
    page_count: int
    source_url: str = ""
    postback_fields: tuple[tuple[str, str], ...] = field(
        default=(),
        repr=False,
        compare=False,
    )

    def pagination_form(self, page: int) -> dict[str, str]:
        if page < 1 or page > self.page_count:
            raise ValueError(f"page must be between 1 and {self.page_count}")
        fields = dict(self.postback_fields)
        fields["__EVENTTARGET"] = BIZCASE_PAGER_TARGET
        fields["__EVENTARGUMENT"] = str(page)
        return fields


@dataclass(frozen=True, slots=True)
class BizCaseListResult:
    items: tuple[BizCaseRecord, ...]
    total_count: int
    page_count: int
    source_url: str = ""
    submitted_or_managed_ids: tuple[str, ...] = ()
