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

