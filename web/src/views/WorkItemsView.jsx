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

function relationLabels(item) {
  return (item.relations || []).map((relation) => RELATION_LABELS[relation] || relation);
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
  const [workflow, setWorkflow] = useState("all");
  const [selectedItem, setSelectedItem] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailRequestVersion, setDetailRequestVersion] = useState(0);
  const deferredQuery = useDeferredValue(query);
  const workflowOptions = useMemo(() => {
    const labels = new Map();
    data.workItems.items.forEach((item) => labels.set(item.workflow, item.workflow_label));
    return Array.from(labels, ([value, label]) => ({ value, label }));
  }, [data.workItems.items]);
  const items = useMemo(() => {
    const normalized = deferredQuery.trim().toLowerCase();
    return data.workItems.items.filter((item) => {
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
        ...relationLabels(item),
      ]
        .some((value) => String(value || "").toLowerCase().includes(normalized));
    });
  }, [data.workItems.items, deferredQuery, mode, workflow]);

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
      `关系：${relationLabels(item).join("、") || "未标注"}`,
      `状态：${item.status || "待确认"}`,
      item.category === "approved" ? "已过审" : (item.current_approver || "待确认"),
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
        <div><span>账号可见</span><strong>{data.workItems.total_count}</strong></div>
        <div><span>待处理</span><strong>{data.workItems.follow_up_count}</strong></div>
        <div><span>已完成</span><strong>{data.workItems.approved_count}</strong></div>
        <div><span>快照时间</span><strong>{formatDateTime(data.workItems.synced_at)}</strong></div>
      </section>
      <section className="content-section">
        <div className="work-item-scope">
          <ShieldCheck size={17} aria-hidden="true" />
          <div><strong>范围：账号可见全集</strong><span>采购立项、合同、订单、成本确认、验收</span></div>
          <p>
            上游对账 <strong>{data.workItems.source_total_count ?? "--"}</strong>
            <span>·</span>
            关系标注 <strong>{data.workItems.matched_count ?? 0}</strong>
            <span>·</span>
            其他状态 <strong>{data.workItems.other_count ?? 0}</strong>
          </p>
        </div>
        <div className="table-toolbar">
          <div className="search-control"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索编号、项目、责任人" aria-label="搜索流程记录" /></div>
          <div className="workflow-filter">
            <Layers3 size={16} aria-hidden="true" />
            <select value={workflow} onChange={(event) => setWorkflow(event.target.value)} aria-label="筛选流程类型">
              <option value="all">全部流程</option>
              {workflowOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>
          <div
            className="segmented"
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
              <thead><tr><th>流程</th><th>编号</th><th>单据</th><th>关系</th><th>当前节点</th><th>责任人</th><th>状态</th><th /></tr></thead>
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
                        {relationLabels(item).length
                          ? relationLabels(item).map((label) => <span className="relation-chip" key={label}>{label}</span>)
                          : <span className="relation-chip relation-chip--muted">未标注</span>}
                      </div>
                    </td>
                    <td>{item.status || "--"}</td>
                    <td><strong>{item.category === "approved" ? "流程已完成" : (item.current_approver || "待确认")}</strong></td>
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
