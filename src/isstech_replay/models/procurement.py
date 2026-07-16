"""Normalized list records shared by the five procurement workflows."""

from __future__ import annotations

from dataclasses import dataclass

from .work_items import WorkflowKind


@dataclass(frozen=True, slots=True)
class ProcurementStreamSpec:
    workflow: WorkflowKind
    module: str
    headers: tuple[str, ...]
    reference_field: str
    title_field: str
    project_no_field: str = "项目编号"
    applicant_field: str = ""
    submitted_at_field: str = ""
    status_field: str = "单据状态"
    next_approver_field: str = "下一级审批人"

    @property
    def search_path(self) -> str:
        return f"/WebTP/{self.module}/SearchIndex"

    def page_path(self, page: int, page_size: int) -> str:
        if page < 1:
            raise ValueError("page must be at least 1")
        if page_size not in {10, 15, 20, 30, 50, 100}:
            raise ValueError("unsupported page size")
        return f"{self.search_path}/0/1/False/{page}/{page_size}"


@dataclass(frozen=True, slots=True)
class ProcurementDocumentSummary:
    workflow: WorkflowKind
    id: str
    reference_no: str = ""
    project_no: str = ""
    title: str = ""
    applicant: str = ""
    submitted_at: str = ""
    status: str = ""
    next_approver: str = ""
    fields: tuple[tuple[str, str], ...] = ()

    def field_dict(self) -> dict[str, str]:
        return dict(self.fields)


@dataclass(frozen=True, slots=True)
class ProcurementListResult:
    workflow: WorkflowKind
    items: tuple[ProcurementDocumentSummary, ...]
    total_count: int | None = None
    page: int = 1
    page_size: int = 50
    source_url: str = ""


PROCUREMENT_STREAMS = (
    ProcurementStreamSpec(
        workflow=WorkflowKind.PURCHASE_REQUISITION,
        module="PurchaseRequisition",
        headers=(
            "操作",
            "申请单编号",
            "项目编号",
            "项目名称",
            "申请人",
            "申请时间",
            "单据状态",
            "下一级审批人",
        ),
        reference_field="申请单编号",
        title_field="项目名称",
        applicant_field="申请人",
        submitted_at_field="申请时间",
    ),
    ProcurementStreamSpec(
        workflow=WorkflowKind.PROCUREMENT_CONTRACT,
        module="ProcurementContract",
        headers=(
            "操作",
            "合同编号",
            "合同名称",
            "填报日期",
            "填报人",
            "供应商名称",
            "采购方式",
            "合同主体",
            "项目编号",
            "项目名称",
            "所属BU",
            "币种",
            "合同金额",
            "采购类型",
            "单据状态",
            "下一级审批人",
        ),
        reference_field="合同编号",
        title_field="合同名称",
        applicant_field="填报人",
        submitted_at_field="填报日期",
    ),
    ProcurementStreamSpec(
        workflow=WorkflowKind.PROCUREMENT_ORDER,
        module="ProcurementOrder",
        headers=(
            "操作",
            "订单编号",
            "订单名称",
            "填报日期",
            "填报人",
            "供应商名称",
            "采购方式",
            "项目编号",
            "项目名称",
            "所属BU",
            "币种",
            "订单金额",
            "采购类型",
            "单据状态",
            "下一级审批人",
        ),
        reference_field="订单编号",
        title_field="订单名称",
        applicant_field="填报人",
        submitted_at_field="填报日期",
    ),
    ProcurementStreamSpec(
        workflow=WorkflowKind.COST_CONFIRMATION,
        module="CostConfirmation",
        headers=(
            "操作",
            "确认单编号",
            "被冲红原始单号",
            "公司代码",
            "项目编号",
            "项目名称",
            "合同/订单名称",
            "供应商编号",
            "供应商名称",
            "合同/订单主体",
            "商品一级分类",
            "币种",
            "验收金额",
            "收入确认类型",
            "单据状态",
            "下一级审批人",
            "记账期间",
            "服务月份",
        ),
        reference_field="确认单编号",
        title_field="合同/订单名称",
    ),
    ProcurementStreamSpec(
        workflow=WorkflowKind.CHECK_ACCEPTANCE,
        module="CheckAcceptance",
        headers=(
            "操作",
            "验收编号",
            "合同/订单编号",
            "合同/订单名称",
            "填报日期",
            "填报人",
            "供应商名称",
            "合同/订单主体",
            "项目编号",
            "项目名称",
            "所属BU",
            "币种",
            "合同/订单金额",
            "商品类型",
            "验收方式",
            "单据状态",
            "下一级审批人",
        ),
        reference_field="验收编号",
        title_field="合同/订单名称",
        applicant_field="填报人",
        submitted_at_field="填报日期",
    ),
)

PROCUREMENT_STREAM_BY_WORKFLOW = {
    stream.workflow: stream for stream in PROCUREMENT_STREAMS
}
