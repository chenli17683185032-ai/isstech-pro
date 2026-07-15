import {
  AlertTriangle,
  ArrowRight,
  ClipboardCheck,
  FileText,
  Inbox,
  ListTodo,
  RefreshCw,
} from "lucide-react";
import Button from "../components/Button";
import EmptyState from "../components/EmptyState";
import StatusTag from "../components/StatusTag";
import { formatDateTime } from "../lib/format";

export default function OverviewView({ data, loading, error, navigate, onSync, syncing }) {
  const reviewDrafts = data.drafts.filter((draft) =>
    ["extracted", "needs_review"].includes(draft.state),
  );
  const latestRun = data.syncRuns[0];
  const metrics = [
    { label: "待审草稿", value: reviewDrafts.length, icon: ClipboardCheck, tone: "warning" },
    { label: "待催办", value: data.workItems.total_count, icon: ListTodo, tone: "danger" },
    { label: "今日变化", value: latestRun?.event_count || 0, icon: RefreshCw, tone: "success" },
    { label: "材料", value: data.materials.length, icon: FileText, tone: "neutral" },
  ];

  return (
    <div className="view-stack">
      {error ? (
        <div className="inline-alert" role="alert">
          <AlertTriangle size={17} />
          <span>{error.message}</span>
        </div>
      ) : null}
      <section className="metric-strip" aria-label="工作指标">
        {metrics.map(({ label, value, icon: Icon, tone }) => (
          <div className="metric" key={label}>
            <span className={`metric__icon metric__icon--${tone}`}><Icon size={17} /></span>
            <div><strong>{loading ? "--" : value}</strong><span>{label}</span></div>
          </div>
        ))}
        <div className="metric-strip__freshness">
          <span>最近同步</span>
          <strong>{formatDateTime(latestRun?.finished_at)}</strong>
        </div>
      </section>

      <div className="overview-grid">
        <section className="content-section overview-grid__wide">
          <div className="section-heading">
            <div><h2>催办清单</h2><span>{data.workItems.synced_at ? `快照 ${formatDateTime(data.workItems.synced_at)}` : "尚无快照"}</span></div>
            <Button variant="ghost" icon={ArrowRight} onClick={() => navigate("work-items")}>查看全部</Button>
          </div>
          {data.workItems.items.length ? (
            <div className="table-wrap">
              <table className="data-table">
                <thead><tr><th>编号</th><th>项目</th><th>责任人</th><th>停留</th><th>状态</th></tr></thead>
                <tbody>
                  {data.workItems.items.slice(0, 6).map((item) => (
                    <tr key={item.key}>
                      <td className="mono">{item.reference_no || item.external_id}</td>
                      <td><strong>{item.title || "未命名项目"}</strong><span>{item.project_no}</span></td>
                      <td>{item.current_approver || "--"}</td>
                      <td>{item.waiting_days == null ? "--" : `${item.waiting_days} 天`}</td>
                      <td><StatusTag value="needs_review" label={item.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              icon={ListTodo}
              title="暂无本地待催办项"
              action={<Button icon={RefreshCw} onClick={onSync} disabled={syncing}>{syncing ? "同步中" : "立即同步"}</Button>}
            />
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
