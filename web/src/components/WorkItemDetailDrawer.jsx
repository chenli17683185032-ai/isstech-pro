import { AlertCircle, LoaderCircle, RotateCw, X } from "lucide-react";
import { useEffect, useRef } from "react";
import Button from "./Button";
import StatusTag from "./StatusTag";

const DETAIL_FIELDS = [
  ["PR_RequisitionNo", "申请单编号"],
  ["PR_PrjNo", "项目编号"],
  ["PR_PrjName", "项目名称"],
  ["PR_ProjectManagerName", "项目经理"],
  ["PR_SalesContractNo", "销售合同"],
  ["PR_SigningEntity", "签署主体"],
  ["PR_RemainingHardwareCost", "第三方软硬件剩余成本"],
  ["PR_RemainingServiceCost", "第三方服务剩余成本"],
  ["PR_ProcurementMethod", "采购方式"],
  ["PR_ProcurementManagerName", "采购经理"],
  ["PR_Remark", "备注"],
];

function fieldValue(detail, item, key) {
  const value = detail?.fields?.[key];
  if (value) return value;
  if (key === "PR_RequisitionNo") return item.reference_no;
  if (key === "PR_PrjNo") return item.project_no;
  if (key === "PR_PrjName") return item.title;
  return "";
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
            <span>采购立项申请</span>
            <h2 id="work-detail-title">{summary.reference_no || summary.external_id}</h2>
            <small>{summary.title || "未命名项目"}</small>
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
                  <span>{DETAIL_FIELDS.length + 3} 项</span>
                </div>
                <dl className="work-detail-fields">
                  {DETAIL_FIELDS.map(([key, label]) => (
                    <div key={key} className={key === "PR_Remark" ? "work-detail-field--wide" : ""}>
                      <dt>{label}</dt>
                      <dd>{fieldValue(detail, summary, key) || "--"}</dd>
                    </div>
                  ))}
                  <div><dt>申请人</dt><dd>{summary.applicant || "--"}</dd></div>
                  <div><dt>申请时间</dt><dd>{summary.submitted_at || "--"}</dd></div>
                  <div><dt>当前状态</dt><dd>{summary.status || "--"}</dd></div>
                </dl>
              </section>

              <section className="work-detail-section work-detail-section--approval">
                <div className="work-detail-section__heading">
                  <h3>审批轨迹</h3>
                  <span>{steps.length} 个节点</span>
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
                  <div className="work-detail-empty">暂无审批轨迹</div>
                )}
              </section>
            </>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
