import { Clipboard, ExternalLink, ListTodo, RefreshCw, Search } from "lucide-react";
import { useMemo, useState } from "react";
import Button from "../components/Button";
import EmptyState from "../components/EmptyState";
import StatusTag from "../components/StatusTag";
import { formatDateTime } from "../lib/format";

const CLIPBOARD_TIMEOUT_MS = 3000;

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

export default function WorkItemsView({ data, notify, onSync, syncing }) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("follow_up");
  const items = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return data.workItems.items.filter((item) => {
      if (mode === "approved" && item.category !== "approved") return false;
      if (mode === "follow_up" && item.category !== "follow_up") return false;
      if (mode === "overdue" && (
        item.category !== "follow_up"
        || item.waiting_days == null
        || item.waiting_days < 7
      )) return false;
      if (!normalized) return true;
      return [item.reference_no, item.project_no, item.title, item.current_approver, item.status]
        .some((value) => String(value || "").toLowerCase().includes(normalized));
    });
  }, [data.workItems.items, query, mode]);

  async function copyList() {
    const text = items.map((item) => [
      item.reference_no || item.external_id,
      item.title || item.project_no,
      item.category === "approved" ? "已过审" : (item.current_approver || "待确认"),
      item.category === "approved"
        ? "已完成"
        : (item.waiting_days == null ? "天数未知" : `${item.waiting_days}天`),
      item.source_url,
    ].join("\t")).join("\n");
    try {
      await writeClipboardText(text);
      notify({ tone: "success", message: `已复制 ${items.length} 条流程记录` });
    } catch {
      notify({ tone: "error", message: "复制失败" });
    }
  }

  return (
    <div className="view-stack">
      <section className="work-item-summary">
        <div><span>当前待催办</span><strong>{data.workItems.follow_up_count}</strong></div>
        <div><span>超过 7 天</span><strong>{data.workItems.items.filter((item) => item.category === "follow_up" && (item.waiting_days || 0) >= 7).length}</strong></div>
        <div><span>已过审</span><strong>{data.workItems.approved_count}</strong></div>
        <div><span>快照时间</span><strong>{formatDateTime(data.workItems.synced_at)}</strong></div>
      </section>
      <section className="content-section">
        <div className="table-toolbar">
          <div className="search-control"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索编号、项目、责任人" aria-label="搜索流程记录" /></div>
          <div className="segmented" aria-label="流程范围">
            <button className={mode === "follow_up" ? "is-active" : ""} onClick={() => setMode("follow_up")} type="button">待催办</button>
            <button className={mode === "overdue" ? "is-active" : ""} onClick={() => setMode("overdue")} type="button">超过 7 天</button>
            <button className={mode === "approved" ? "is-active" : ""} onClick={() => setMode("approved")} type="button">已过审</button>
          </div>
          <div className="table-toolbar__actions">
            <Button icon={Clipboard} onClick={copyList} disabled={!items.length}>复制清单</Button>
            <Button icon={RefreshCw} variant="primary" onClick={onSync} disabled={syncing}>{syncing ? "同步中" : "立即同步"}</Button>
          </div>
        </div>
        {items.length ? (
          <div className="table-wrap">
            <table className="data-table followup-table">
              <thead><tr><th>编号</th><th>项目</th><th>当前节点</th><th>责任人</th><th>停留</th><th>状态</th><th /></tr></thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.key}>
                    <td className="mono">{item.reference_no || item.external_id}</td>
                    <td><strong>{item.title || "未命名项目"}</strong><span>{item.project_no}</span></td>
                    <td>{item.status || "--"}</td>
                    <td><strong>{item.current_approver || "--"}</strong></td>
                    <td className={(item.waiting_days || 0) >= 7 ? "waiting waiting--high" : "waiting"}>{item.waiting_days == null ? "--" : `${item.waiting_days} 天`}</td>
                    <td><StatusTag value={item.category} label={item.category === "approved" ? "已过审" : item.status} /></td>
                    <td className="align-right"><a className="icon-link" href={item.source_url} target="_blank" rel="noreferrer" title="打开只读详情" aria-label="打开只读详情"><ExternalLink size={16} /></a></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <EmptyState icon={ListTodo} title={query ? "没有匹配的流程记录" : (mode === "approved" ? "暂无已过审单据" : "暂无本地待催办项")} />}
      </section>
    </div>
  );
}
