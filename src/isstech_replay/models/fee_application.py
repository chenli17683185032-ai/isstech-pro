"""Fixed-schema records shared by the two additional fee application lists."""

from __future__ import annotations

from dataclasses import dataclass


FEE_APPLICATION_HEADERS = (
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
FEE_APPLICATION_GRID_ID = "ctl00_ContentPlaceHolder1_MyGridView"
FEE_APPLICATION_PAGE_SIZE = 10


@dataclass(frozen=True, slots=True)
class FeeApplicationSpec:
    key: str
    label: str
    list_url: str
    form_action: str
    application_no_prefix: str
    pager_target: str
    pager_input: str
    pagination_enabled: bool
    required_empty_fields: tuple[str, ...] = ()


TRAVEL_REIMBURSEMENT_SPEC = FeeApplicationSpec(
    key="travel_reimbursement",
    label="差旅报销申请",
    list_url=(
        "/WebPSAOA/Fee/FeeApply/EvectionSubsidy/List.aspx?helpmenucode=93"
    ),
    form_action="List.aspx?helpmenucode=93",
    application_no_prefix="EEA",
    pager_target="ctl00$ContentPlaceHolder1$gp",
    pager_input="ctl00$ContentPlaceHolder1$gp_input",
    pagination_enabled=False,
)

TRAVEL_SUBSIDY_SPEC = FeeApplicationSpec(
    key="travel_subsidy",
    label="差旅补助申请",
    list_url=(
        "/WebPSAOA/Fee/FeeApply/EvectionSubsidy2/List.aspx?helpmenucode=112"
    ),
    form_action="List.aspx?helpmenucode=112",
    application_no_prefix="ESA",
    pager_target="ctl00$ContentPlaceHolder1$GridPager1",
    pager_input="ctl00$ContentPlaceHolder1$GridPager1_input",
    pagination_enabled=True,
    required_empty_fields=("ctl00$ContentPlaceHolder1$hiddutyLevel",),
)


@dataclass(frozen=True, slots=True)
class FeeApplicationRecord:
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
class FeeApplicationPage:
    items: tuple[FeeApplicationRecord, ...]
    current_page: int
    page_count: int
    source_url: str = ""
    pager_target: str = ""
    pager_input: str = ""
    postback_fields: tuple[tuple[str, str], ...] = ()

    def pagination_form(self, page: int) -> dict[str, str]:
        if not self.pager_target or not self.pager_input:
            raise ValueError("fee application pagination is not enabled")
        if page < 1 or page > self.page_count:
            raise ValueError("fee application page is outside the observed range")
        form = dict(self.postback_fields)
        form["__EVENTTARGET"] = self.pager_target
        form["__EVENTARGUMENT"] = ""
        form[self.pager_input] = str(page)
        return form


@dataclass(frozen=True, slots=True)
class FeeApplicationListResult:
    items: tuple[FeeApplicationRecord, ...]
    total_count: int
    page_count: int
    source_url: str = ""
