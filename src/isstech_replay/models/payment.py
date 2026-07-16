"""Fixed-schema records from the Payment application list."""

from __future__ import annotations

from dataclasses import dataclass


PAYMENT_HEADERS = (
    "操作",
    "付款单编号",
    "付款类别",
    "申请人",
    "项目编号",
    "项目名称",
    "核算成本中心名称",
    "收款公司",
    "付款公司",
    "金额",
    "币种",
    "状态",
)

PAYMENT_QUERY_HEADERS = (
    "操作",
    "付款单编号",
    "付款类别",
    "付款事由",
    "申请人",
    "项目编号",
    "项目名称",
    "核算成本中心名称",
    "收款公司",
    "付款公司",
    "金额",
    "币种",
    "状态",
    "下级审批人",
    "资金支付状态",
    "支付日期",
    "合同付款日期",
    "后补发票状态",
    "是否无合同付款",
    "无合同付款原因",
)

PAYMENT_QUERY_FORM_FIELDS = (
    "PM_ApplyNo",
    "PM_SDName",
    "PM_SDNo",
    "PM_ProjectNo",
    "PM_ProjectName",
    "PM_Status",
    "PaymentType",
    "PI_PaymentCompanyNo",
    "PI_PaymentCompany",
    "BGName",
    "BGNo",
    "BUName",
    "BUNo",
    "PM_FundsPay",
    "PM_EmpNo",
    "PM_EmpName",
    "PM_SumDetail",
    "PI_ReceivingCompany",
    "PI_ReceivingCompanyNo",
    "PM_PayDate",
    "IsTongLiPaid",
    "IsReplenishInvoice",
    "PM_FundsPayDateStart",
    "PM_FundsPayDateEnd",
    "IsDepartmentValid",
)

PAYMENT_QUERY_PAGER_FORM_FIELDS = tuple(
    name
    for name in PAYMENT_QUERY_FORM_FIELDS
    if name not in {"PI_PaymentCompany", "IsTongLiPaid", "IsReplenishInvoice"}
)


def payment_query_form(
    *,
    applicant: str = "",
    project_no: str = "",
    pager: bool = False,
) -> dict[str, str]:
    applicant = applicant.strip()
    project_no = project_no.strip()
    if applicant and project_no:
        raise ValueError("Payment query accepts one personal scope at a time")
    fields = PAYMENT_QUERY_PAGER_FORM_FIELDS if pager else PAYMENT_QUERY_FORM_FIELDS
    form = {"ajax": "1", **{name: "" for name in fields}}
    if applicant:
        form["PM_EmpNo"] = applicant
        form["PM_EmpName"] = applicant
    elif project_no:
        form["PM_ProjectNo"] = project_no
        form["PM_ProjectName"] = project_no
    return form


def payment_empty_query_form(*, pager: bool = False) -> dict[str, str]:
    return payment_query_form(pager=pager)


@dataclass(frozen=True, slots=True)
class PaymentRecord:
    id: str
    payment_no: str
    payment_type: str = ""
    applicant: str = ""
    project_no: str = ""
    project_name: str = ""
    cost_center: str = ""
    payee_company: str = ""
    payer_company: str = ""
    amount: str = ""
    currency: str = ""
    status: str = ""
    fields: tuple[tuple[str, str], ...] = ()

    def field_dict(self) -> dict[str, str]:
        return dict(self.fields)


@dataclass(frozen=True, slots=True)
class PaymentListResult:
    items: tuple[PaymentRecord, ...]
    total_count: int
    page_count: int
    current_page: int
    source_url: str = ""
