import {
  AlertTriangle,
  ArrowRight,
  CircleCheckBig,
  ClipboardCheck,
  FileText,
  Inbox,
  ListTodo,
} from "lucide-react";
import Button from "../components/Button";
import EmptyState from "../components/EmptyState";
import StatusTag from "../components/StatusTag";
import { formatDateTime } from "../lib/format";

const APPROVED_STATUSES = new Set(["审批通过", "已通过", "已完成"]);
const READONLY_CATEGORIES = [
  { key: "payment", label: "付款申请" },
  { key: "bizcases", label: "BizCase查询" },
  { key: "travelApplications", label: "出差申请" },
  { key: "dailyExpenses", label: "日常报销申请" },
  { key: "travelReimbursements", label: "差旅报销申请" },
  { key: "travelSubsidies", label: "差旅补助申请" },
];
const CATEGORY_ORDER = [
  "采购验收",
  "付款申请",
  "出差申请",
  "日常报销申请",
  "差旅报销申请",
  "差旅补助申请",
  "采购立项",
  "采购合同",
  "采购订单",
  "成本确认",
  "BizCase查询",
];

function isUnapproved(status) {
  const normalized = (status || "").trim();
  return Boolean(normalized) && !APPROVED_STATUSES.has(normalized);
}

function statusTone(status) {
  if (/(拒绝|退回|失败)/.test(status || "")) return "failed";
  return "pending";
}

function readonlyRecord(item, label, target) {
  return {
    key: `${label}:${item.id}`,
    category: label,
    reference: item.payment_no || item.application_no || item.bizcase_no || item.id,
    title: item.project_name || item.payee_company || item.client_name || "未命名单据",
    project: item.project_no || "",
    approver: item.current_approver || "",
    status: item.status || "",
    destination: "readonly-modules",
    target,
  };
}

function unapprovedGroups(workItems, readonlyData) {
  const records = workItems.items
    .filter((item) => isUnapproved(item.status))
    .map((item) => ({
      key: item.key,
      category: item.workflow_label,
      reference: item.reference_no || item.external_id,
      title: item.title || "未命名单据",
      project: item.project_no || "",
      approver: item.current_approver || "",
      status: item.status,
      destination: "work-items",
    }));
  READONLY_CATEGORIES.forEach(({ key, label }) => {
    readonlyData[key].items
      .filter((item) => isUnapproved(item.status))
      .forEach((item) => records.push(readonlyRecord(item, label, key)));
  });

  const grouped = new Map();
  records.forEach((record) => {
    if (!grouped.has(record.category)) grouped.set(record.category, []);
    grouped.get(record.category).push(record);
  });
  return [...grouped.entries()]
    .map(([label, items]) => ({
      label,
      items,
      destination: items[0].destination,
      target: items[0].target,
    }))
    .sort((left, right) => {
      const leftIndex = CATEGORY_ORDER.indexOf(left.label);
      const rightIndex = CATEGORY_ORDER.indexOf(right.label);
      return (leftIndex < 0 ? CATEGORY_ORDER.length : leftIndex)
        - (rightIndex < 0 ? CATEGORY_ORDER.length : rightIndex);
    });
}

function UnapprovedWorkflowGroups({ groups, navigate }) {
  return (
    <div className="unapproved-groups">
      {groups.map((group) => (
        <section className="unapproved-group" key={group.label} aria-labelledby={`group-${group.label}`}>
          <div className="unapproved-group__heading">
            <div>
              <h3 id={`group-${group.label}`}>{group.label}</h3>
              <span>{group.items.length} 条</span>
            </div>
            <Button
              variant="ghost"
              icon={ArrowRight}
              onClick={() => navigate(group.destination, group.target)}
            >
              查看类目
            </Button>
          </div>
          <div className="table-wrap">
            <table className="data-table unapproved-table" aria-label={`${group.label}未审批流程`}>
              <thead><tr><th>编号</th><th>单据</th><th>审批人</th><th>状态</th><th aria-label="进入类目" /></tr></thead>
              <tbody>
                {group.items.map((item) => (
                  <tr
                    className="clickable-row"
                    key={item.key}
                    role="button"
                    tabIndex={0}
                    onClick={() => navigate(item.destination, item.target)}
                    onKeyDown={(event) => {
                      if (["Enter", " "].includes(event.key)) {
                        event.preventDefault();
                        navigate(item.destination, item.target);
                      }
                    }}
                  >
                    <td className="mono"><strong>{item.reference}</strong></td>
                    <td><strong>{item.title}</strong><span>{item.project}</span></td>
                    <td>{item.approver || "--"}</td>
                    <td><StatusTag value={statusTone(item.status)} label={item.status} /></td>
                    <td className="align-right"><ArrowRight size={15} aria-hidden="true" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}

export default function OverviewView({
  data,
  loading,
  error,
  navigate,
  readonlyData,
  readonlyLoading,
  readonlyError,
}) {
  const reviewDrafts = data.drafts.filter((draft) =>
    ["extracted", "needs_review"].includes(draft.state),
  );
  const groups = unapprovedGroups(data.workItems, readonlyData);
  const unapprovedCount = groups.reduce((total, group) => total + group.items.length, 0);
  const readonlyCount = READONLY_CATEGORIES.reduce(
    (total, { key }) => total + readonlyData[key].total_count,
    0,
  );
  const personalCount = data.workItems.total_count + readonlyCount;
  const latestRun = data.syncRuns[0];
  const latestReadonlyRun = readonlyData.runs[0];
  const latestFinishedAt = [latestRun?.finished_at, latestReadonlyRun?.finished_at]
    .filter(Boolean)
    .sort()
    .at(-1);
  const metrics = [
    { label: "待审草稿", value: reviewDrafts.length, icon: ClipboardCheck, tone: "warning" },
    { label: "未审批", value: unapprovedCount, icon: ListTodo, tone: "danger", needsReadonly: true },
    { label: "个人单据", value: personalCount, icon: CircleCheckBig, tone: "success", needsReadonly: true },
    { label: "材料", value: data.materials.length, icon: FileText, tone: "neutral" },
  ];
  const isLoading = loading || readonlyLoading;

  return (
    <div className="view-stack">
      {error ? (
        <div className="inline-alert" role="alert">
          <AlertTriangle size={17} aria-hidden="true" />
          <span>{error.message}</span>
        </div>
      ) : null}
      {readonlyError ? (
        <div className="inline-alert" role="alert">
          <AlertTriangle size={17} aria-hidden="true" />
          <span>部分业务快照读取失败：{readonlyError.message}</span>
        </div>
      ) : null}
      <section className="metric-strip" aria-label="工作指标">
        {metrics.map(({ label, value, icon: Icon, tone, needsReadonly }) => (
          <div className="metric" key={label}>
            <span className={`metric__icon metric__icon--${tone}`}><Icon size={17} /></span>
            <div>
              <strong>{isLoading || (needsReadonly && readonlyError) ? "--" : value}</strong>
              <span>{label}</span>
            </div>
          </div>
        ))}
        <div className="metric-strip__freshness">
          <span>最近同步</span>
          <strong>{formatDateTime(latestFinishedAt)}</strong>
        </div>
      </section>

      <div className="overview-grid">
        <section className="content-section overview-grid__wide">
          <div className="section-heading">
            <div>
              <h2>未审批流程</h2>
              <span>{readonlyError ? "部分本地快照不可用" : `${groups.length} 个类目 · ${unapprovedCount} 条`}</span>
            </div>
          </div>
          {groups.length ? (
            <UnapprovedWorkflowGroups groups={groups} navigate={navigate} />
          ) : (
            <EmptyState icon={ListTodo} title={isLoading ? "正在读取本地快照" : "暂无未审批流程"} />
          )}
        </section>

        <section className="content-section">
          <div className="section-heading">
            <div><h2>待审草稿</h2><span>{reviewDrafts.length} 项</span></div>
            <Button variant="ghost" icon={ArrowRight} onClick={() => navigate("drafts")}>进入审阅</Button>
          </div>
          {reviewDrafts.length ? (
            <div className="compact-list">
              {reviewDrafts.slice(0, 5).map((draft) => (
                <button key={draft.draft_id} onClick={() => navigate("drafts")} type="button">
                  <span className="compact-list__main"><strong>{draft.title}</strong><small>{formatDateTime(draft.updated_at)}</small></span>
                  <span className="compact-list__aside"><StatusTag value={draft.state} /><small>{draft.pending_count} 待处理</small></span>
                </button>
              ))}
            </div>
          ) : <EmptyState icon={ClipboardCheck} title="暂无待审草稿" />}
        </section>

        <section className="content-section overview-grid__wide">
          <div className="section-heading">
            <div><h2>最近材料</h2><span>{data.materials.length} 份本地材料</span></div>
            <Button variant="ghost" icon={ArrowRight} onClick={() => navigate("materials")}>管理材料</Button>
          </div>
          {data.materials.length ? (
            <div className="material-rail">
              {data.materials.slice(0, 4).map((material) => (
                <button key={material.id} type="button" onClick={() => navigate("materials")}>
                  <FileText size={18} />
                  <span><strong>{material.original_name}</strong><small>{formatDateTime(material.created_at)}</small></span>
                  <StatusTag value={material.status} />
                </button>
              ))}
            </div>
          ) : <EmptyState icon={Inbox} title="暂无本地材料" />}
        </section>

        <section className="content-section sync-history">
          <div className="section-heading"><div><h2>同步记录</h2><span>最近 {data.syncRuns.length} 次</span></div></div>
          <div className="compact-list compact-list--plain">
            {data.syncRuns.slice(0, 5).map((run) => (
              <div key={run.run_id}>
                <span className="compact-list__main"><strong>{formatDateTime(run.started_at)}</strong><small>{run.observed_count} 条 / {run.actionable_count} 待办</small></span>
                <StatusTag value={run.status} />
              </div>
            ))}
            {!data.syncRuns.length ? <span className="muted-row">暂无同步记录</span> : null}
          </div>
        </section>
      </div>
    </div>
  );
}
