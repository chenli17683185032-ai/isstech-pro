import { Clipboard, Eye, Layers3, ListTodo, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { useCallback, useDeferredValue, useEffect, useMemo, useState } from "react";
import { apiRequest } from "../api";
import Button from "../components/Button";
import EmptyState from "../components/EmptyState";
import StatusTag from "../components/StatusTag";
import WorkItemDetailDrawer from "../components/WorkItemDetailDrawer";
import { formatDateTime } from "../lib/format";

const CLIPBOARD_TIMEOUT_MS = 3000;
const RELATION_LABELS = {
  applicant: "发起人",
  submitter: "提交人",
  project_manager: "项目经理",
  procurement_manager: "采购经理",
  approver: "审批人",
};
const SCOPE_LABELS = {
  my_project: "我的项目",
  submitted_by_me: "我提交的",
};
const WORKFLOW_OPTIONS = [
  { value: "purchase_requisition", label: "采购立项" },
  { value: "procurement_contract", label: "采购合同" },
  { value: "procurement_order", label: "采购订单" },
  { value: "cost_confirmation", label: "成本确认" },
  { value: "check_acceptance", label: "采购验收" },
];

function relationLabels(item) {
  return (item.relations || []).map((relation) => RELATION_LABELS[relation] || relation);
}

function scopeLabels(item) {
  return (item.scope_reasons || []).map((reason) => SCOPE_LABELS[reason] || reason);
}

async function writeClipboardText(text) {
  if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
  let timeoutId;
  try {
    await Promise.race([
      navigator.clipboard.writeText(text),
      new Promise((_, reject) => {
        timeoutId = window.setTimeout(
          () => reject(new Error("clipboard write timed out")),
          CLIPBOARD_TIMEOUT_MS,
        );
      }),
    ]);
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export default function WorkItemsView({ token, data, notify, onSync, syncing }) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("all");
  const [scopeMode, setScopeMode] = useState("all");
  const [workflow, setWorkflow] = useState("all");
  const [selectedItem, setSelectedItem] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailRequestVersion, setDetailRequestVersion] = useState(0);
  const deferredQuery = useDeferredValue(query);
  const workflowOptions = useMemo(
    () => WORKFLOW_OPTIONS.map((option) => ({
      ...option,
      count: data.workItems.workflow_counts?.[option.value] ?? 0,
    })),
    [data.workItems.workflow_counts],
  );
  const items = useMemo(() => {
    const normalized = deferredQuery.trim().toLowerCase();
    return data.workItems.items.filter((item) => {
      if (scopeMode !== "all" && !(item.scope_reasons || []).includes(scopeMode)) return false;
      if (workflow !== "all" && item.workflow !== workflow) return false;
      if (mode === "approved" && item.category !== "approved") return false;
      if (mode === "follow_up" && item.category !== "follow_up") return false;
      if (!normalized) return true;
      return [
        item.workflow_label,
        item.reference_no,
        item.project_no,
        item.title,
        item.applicant,
        item.current_approver,
        item.status,
        ...scopeLabels(item),
        ...relationLabels(item),
      ]
        .some((value) => String(value || "").toLowerCase().includes(normalized));
    });
  }, [data.workItems.items, deferredQuery, mode, scopeMode, workflow]);

  const closeDetail = useCallback(() => setSelectedItem(null), []);

  useEffect(() => {
    if (!selectedItem) return undefined;
    let active = true;
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    apiRequest(`/v1/work-items/${encodeURIComponent(selectedItem.workflow)}/${encodeURIComponent(selectedItem.external_id)}/detail`, { token })
      .then((result) => {
        if (active) setDetail(result);
      })
      .catch((error) => {
        if (active) setDetailError(error);
      })
      .finally(() => {
        if (active) setDetailLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedItem, token, detailRequestVersion]);

  function openDetail(item) {
    setSelectedItem(item);
  }

  async function copyList() {
    const text = items.map((item) => [
      item.reference_no || item.external_id,
      item.workflow_label,
      item.title || item.project_no,
      `归属：${scopeLabels(item).join("、")}`,
      `状态：${item.status || "待确认"}`,
      `审批人：${item.current_approver || "--"}`,
      item.category === "approved"
        ? "已完成"
        : (item.waiting_days == null ? "天数未知" : `${item.waiting_days}天`),
    ].join("\t")).join("\n");
    try {
      await writeClipboardText(text);
      notify({ tone: "success", message: `已复制 ${items.length} 条单据` });
    } catch {
      notify({ tone: "error", message: "复制失败" });
    }
  }

  return (
    <div className="view-stack">
      <section className="work-item-summary">
        <div><span>个人相关</span><strong>{data.workItems.total_count}</strong></div>
        <div><span>我的项目</span><strong>{data.workItems.my_project_count ?? 0}</strong></div>
        <div><span>我提交</span><strong>{data.workItems.submitted_by_me_count ?? 0}</strong></div>
        <div><span>快照时间</span><strong>{formatDateTime(data.workItems.synced_at)}</strong></div>
      </section>
      <section className="content-section">
        <div className="work-item-scope">
          <ShieldCheck size={17} aria-hidden="true" />
          <div><strong>范围：我的项目与我提交的</strong><span>采购立项、合同、订单、成本确认、验收</span></div>
          <p>
            源数据 <strong>{data.workItems.source_total_count ?? "--"}</strong>
            <span>·</span>
            待处理 <strong>{data.workItems.follow_up_count}</strong>
            <span>·</span>
            已完成 <strong>{data.workItems.approved_count}</strong>
          </p>
        </div>
        <div className="table-toolbar">
          <div className="search-control"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索编号、项目、审批人" aria-label="搜索流程记录" /></div>
          <div className="workflow-filter">
            <Layers3 size={16} aria-hidden="true" />
            <select value={workflow} onChange={(event) => setWorkflow(event.target.value)} aria-label="筛选流程类型">
              <option value="all">全部流程</option>
              {workflowOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}（{option.count}）
                </option>
              ))}
            </select>
          </div>
          <div
            className="segmented segmented--scope"
            aria-label="单据范围"
          >
            <button className={scopeMode === "all" ? "is-active" : ""} onClick={() => setScopeMode("all")} type="button">全部相关</button>
            <button className={scopeMode === "my_project" ? "is-active" : ""} onClick={() => setScopeMode("my_project")} type="button">我的项目</button>
            <button className={scopeMode === "submitted_by_me" ? "is-active" : ""} onClick={() => setScopeMode("submitted_by_me")} type="button">我提交的</button>
          </div>
          <div
            className="segmented segmented--status"
            aria-label="单据状态"
          >
            <button className={mode === "all" ? "is-active" : ""} onClick={() => setMode("all")} type="button">全部</button>
            <button className={mode === "follow_up" ? "is-active" : ""} onClick={() => setMode("follow_up")} type="button">待处理</button>
            <button className={mode === "approved" ? "is-active" : ""} onClick={() => setMode("approved")} type="button">已完成</button>
          </div>
          <div className="table-toolbar__actions">
            <Button icon={Clipboard} onClick={copyList} disabled={!items.length}>复制清单</Button>
            <Button icon={RefreshCw} variant="primary" onClick={onSync} disabled={syncing}>{syncing ? "同步中" : "立即同步"}</Button>
          </div>
        </div>
        {items.length ? (
          <div className="table-wrap">
            <table className="data-table followup-table">
              <thead><tr><th>流程</th><th>编号</th><th>单据</th><th>归属</th><th>当前节点</th><th>审批人</th><th>状态</th><th /></tr></thead>
              <tbody>
                {items.map((item) => (
                  <tr
                    key={item.key}
                    className="clickable-row"
                    role="button"
                    tabIndex={0}
                    aria-label={`查看 ${item.reference_no || item.external_id} 详情`}
                    onClick={() => openDetail(item)}
                    onKeyDown={(event) => {
                      if (event.target === event.currentTarget && ["Enter", " "].includes(event.key)) {
                        event.preventDefault();
                        openDetail(item);
                      }
                    }}
                  >
                    <td><strong>{item.workflow_label}</strong></td>
                    <td className="mono">{item.reference_no || item.external_id}</td>
                    <td><strong>{item.title || "未命名单据"}</strong><span>{item.project_no}</span></td>
                    <td>
                      <div className="relation-list">
                        {scopeLabels(item).map((label) => <span className="relation-chip" key={label}>{label}</span>)}
                      </div>
                    </td>
                    <td>{item.status || "--"}</td>
                    <td><strong>{item.current_approver || "--"}</strong></td>
                    <td><StatusTag value={item.category} label={item.status || "状态未知"} /></td>
                    <td className="align-right">
                      <button
                        className="icon-link"
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          openDetail(item);
                        }}
                        title="查看本地详情"
                        aria-label={`查看 ${item.reference_no || item.external_id} 本地详情`}
                      >
                        <Eye size={16} aria-hidden="true" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <EmptyState icon={ListTodo} title={query ? "没有匹配的单据" : "当前筛选无单据"} />}
      </section>
      {selectedItem ? (
        <WorkItemDetailDrawer
          item={selectedItem}
          detail={detail}
          loading={detailLoading}
          error={detailError}
          onClose={closeDetail}
          onRetry={() => setDetailRequestVersion((current) => current + 1)}
        />
      ) : null}
    </div>
  );
}
