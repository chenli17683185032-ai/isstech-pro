import { AlertCircle, LoaderCircle, RotateCw, X } from "lucide-react";
import { useEffect, useRef } from "react";
import Button from "./Button";
import StatusTag from "./StatusTag";

const FIELD_LABELS = {
  PR_RequisitionNo: "申请单编号",
  PR_PrjNo: "项目编号",
  PR_PrjName: "项目名称",
  PR_ProjectManagerName: "项目经理",
  PR_SalesContractNo: "销售合同",
  PR_SigningEntity: "签署主体",
  PR_RemainingHardwareCost: "第三方软硬件剩余成本",
  PR_RemainingServiceCost: "第三方服务剩余成本",
  PR_ProcurementMethod: "采购方式",
  PR_ProcurementManagerName: "采购经理",
  PR_Remark: "备注",
};
const FIELD_ORDER = Object.keys(FIELD_LABELS);
const SCOPE_LABELS = {
  my_project: "我的项目",
  submitted_by_me: "我提交的",
};

function detailFields(detail, item) {
  const source = detail?.fields || {};
  const preferred = FIELD_ORDER.filter((key) => Object.hasOwn(source, key));
  const keys = [...preferred, ...Object.keys(source).filter((key) => !FIELD_ORDER.includes(key))];
  const labels = new Set();
  const fields = [];
  keys.forEach((key) => {
    const label = FIELD_LABELS[key] || key;
    if (labels.has(label)) return;
    labels.add(label);
    fields.push({ key, label, value: source[key] || "" });
  });
  [
    ["summary-applicant", "填报人", item.applicant],
    ["summary-date", "填报日期", item.submitted_at],
    ["summary-status", "当前状态", item.status],
    ["summary-approver", "审批人", item.current_approver],
  ].forEach(([key, label, value]) => {
    if (labels.has(label) || (label === "填报人" && labels.has("申请人"))) return;
    labels.add(label);
    fields.push({ key, label, value: value || "" });
  });
  return fields;
}

export default function WorkItemDetailDrawer({
  item,
  detail,
  loading,
  error,
  onClose,
  onRetry,
}) {
  const returnFocusRef = useRef(document.activeElement);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      returnFocusRef.current?.focus?.();
    };
  }, [onClose]);

  const summary = detail?.item || item;
  const steps = detail?.approval_steps || [];
  const scopes = (summary.scope_reasons || []).map(
    (reason) => SCOPE_LABELS[reason] || reason,
  );
  const fields = detailFields(detail, summary);
  const approvalStatus = detail?.approval_status || "not_fetched";
  const approvalEmptyLabel = {
    upstream_empty: "上游未返回审批轨迹",
    fetch_failed: "审批轨迹同步失败",
    not_fetched: "审批轨迹尚未同步",
  }[approvalStatus] || "审批轨迹不可用";

  return (
    <div
      className="work-detail-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside
        className="work-detail-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="work-detail-title"
      >
        <header className="work-detail-header">
          <div>
            <span>{summary.workflow_label || "采购单据"}</span>
            <h2 id="work-detail-title">{summary.reference_no || summary.external_id}</h2>
            <small>{summary.title || "未命名单据"}</small>
            <div className="work-detail-relations" aria-label="单据归属">
              <span>归属</span>
              {scopes.map((scope) => <strong key={scope}>{scope}</strong>)}
            </div>
          </div>
          <div className="work-detail-header__actions">
            <StatusTag
              value={summary.category}
              label={summary.category === "approved" ? "已过审" : summary.status}
            />
            <Button
              autoFocus
              icon={X}
              variant="ghost"
              size="icon"
              onClick={onClose}
              title="关闭详情"
              aria-label="关闭详情"
            />
          </div>
        </header>

        <div className="work-detail-body">
          {loading ? (
            <div className="work-detail-state">
              <LoaderCircle className="spin" size={20} aria-hidden="true" />
              <span>正在读取详情</span>
            </div>
          ) : error ? (
            <div className="work-detail-state work-detail-state--error" role="alert">
              <AlertCircle size={20} aria-hidden="true" />
              <strong>详情读取失败</strong>
              <span>{error.message || "请稍后重试"}</span>
              <Button icon={RotateCw} onClick={onRetry}>重新读取</Button>
            </div>
          ) : detail ? (
            <>
              <section className="work-detail-section">
                <div className="work-detail-section__heading">
                  <h3>单据详情</h3>
                  <span>{fields.length} 项</span>
                </div>
                <dl className="work-detail-fields">
                  {fields.map((field) => (
                    <div key={field.key} className={field.label === "备注" ? "work-detail-field--wide" : ""}>
                      <dt>{field.label}</dt>
                      <dd>{field.value || "--"}</dd>
                    </div>
                  ))}
                </dl>
              </section>

              <section className="work-detail-section work-detail-section--approval">
                <div className="work-detail-section__heading">
                  <h3>审批轨迹</h3>
                  <span>{steps.length ? `${steps.length} 个节点` : approvalEmptyLabel}</span>
                </div>
                {steps.length ? (
                  <ol className="approval-timeline">
                    {steps.map((step, index) => (
                      <li key={`${step.sequence}-${step.timestamp}-${index}`}>
                        <span className="approval-timeline__sequence">{step.sequence || index + 1}</span>
                        <div className="approval-timeline__main">
                          <strong>{step.action || "状态未知"}</strong>
                          <span>{step.approver_name || "--"}{step.role ? ` · ${step.role}` : ""}</span>
                          <p>{step.comment || "无批注"}</p>
                        </div>
                        <time>{step.timestamp || "--"}</time>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <div className="work-detail-empty">{approvalEmptyLabel}</div>
                )}
              </section>
            </>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
