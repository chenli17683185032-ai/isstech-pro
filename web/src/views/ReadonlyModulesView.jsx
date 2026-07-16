import {
  AlertTriangle,
  BriefcaseBusiness,
  CreditCard,
  Database,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { useDeferredValue, useMemo, useState } from "react";
import Button from "../components/Button";
import EmptyState from "../components/EmptyState";
import StatusTag from "../components/StatusTag";
import { formatDateTime } from "../lib/format";

const MODULES = [
  { key: "payment", module: "payment", label: "付款申请", icon: CreditCard },
  { key: "bizcases", module: "bizcase", label: "BizCase查询", icon: BriefcaseBusiness },
];

function recordStatusTone(status) {
  if (/(通过|完成|已付)/.test(status || "")) return "succeeded";
  if (/(拒绝|退回|失败)/.test(status || "")) return "failed";
  if (/(审批|待|保存)/.test(status || "")) return "pending";
  return "";
}

function matchesQuery(item, query) {
  if (!query) return true;
  const fields = Object.values(item.fields || {});
  return [...Object.values(item), ...fields]
    .filter((value) => typeof value === "string" || typeof value === "number")
    .some((value) => String(value).toLowerCase().includes(query));
}

function LoadingRows() {
  return (
    <div className="readonly-loading" aria-label="正在读取业务查询快照" aria-busy="true">
      {Array.from({ length: 5 }, (_, index) => (
        <div key={index}><span /><span /><span /><span /></div>
      ))}
    </div>
  );
}

function PaymentTable({ items }) {
  return (
    <div className="table-wrap">
      <table className="data-table readonly-table payment-table" aria-label="付款申请记录">
        <thead>
          <tr><th>付款单编号</th><th>类别</th><th>项目</th><th>收付款公司</th><th>金额</th><th>申请人</th><th>状态</th></tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td className="mono"><strong>{item.payment_no || item.id}</strong></td>
              <td>{item.payment_type || "--"}</td>
              <td><strong>{item.project_name || "未命名项目"}</strong><span>{item.project_no || "--"}</span></td>
              <td><strong>{item.payee_company || "--"}</strong><span>{item.payer_company || "--"}</span></td>
              <td><strong>{item.amount || "--"}</strong><span>{item.currency || ""}</span></td>
              <td>{item.applicant || "--"}</td>
              <td><StatusTag value={recordStatusTone(item.status)} label={item.status || "状态未知"} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BizCaseTable({ items }) {
  return (
    <div className="table-wrap">
      <table className="data-table readonly-table bizcase-table" aria-label="BizCase查询记录">
        <thead>
          <tr><th>序号</th><th>BizCase</th><th>客户</th><th>项目</th><th>利润中心</th><th>收入确认</th><th>当前审批人</th></tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td className="mono">{item.ordinal}</td>
              <td><strong>{item.bizcase_no || "--"}</strong><span>{item.version_no || item.id}</span></td>
              <td>{item.client_name || "--"}</td>
              <td><strong>{item.project_name || "未命名项目"}</strong><span>{item.project_no || "--"}</span></td>
              <td><strong>{item.profit_center || "--"}</strong><span>{item.profit_center_group || "--"}</span></td>
              <td>{item.revenue_recognition_type || "--"}</td>
              <td><strong>{item.current_approver || "--"}</strong></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ReadonlyModulesView({
  data,
  loading,
  error,
  onReload,
  onSync,
  syncing,
}) {
  const [activeModule, setActiveModule] = useState("payment");
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const definition = MODULES.find((module) => module.key === activeModule) || MODULES[0];
  const current = data[definition.key];
  const latestRun = data.runs.find((run) => run.module === definition.module);
  const items = useMemo(
    () => current.items.filter((item) => matchesQuery(item, deferredQuery)),
    [current.items, deferredQuery],
  );

  const emptyTitle = deferredQuery
    ? `没有匹配的${definition.label}记录`
    : `暂无个人相关的${definition.label}记录`;

  return (
    <div className="view-stack">
      {error ? (
        <div className="inline-alert readonly-alert" role="alert">
          <AlertTriangle size={17} aria-hidden="true" />
          <span>{error.message}</span>
          <Button icon={RefreshCw} onClick={onReload}>重试</Button>
        </div>
      ) : null}

      <section className="readonly-summary" aria-label="业务查询指标">
        <div><span>付款申请</span><strong>{loading ? "--" : data.payment.total_count}</strong></div>
        <div><span>BizCase查询</span><strong>{loading ? "--" : data.bizcases.total_count}</strong></div>
        <div><span>当前快照</span><strong>{formatDateTime(current.synced_at)}</strong></div>
        <div className="readonly-summary__state">
          <span>最近运行</span>
          {latestRun ? <StatusTag value={latestRun.status} /> : <strong>--</strong>}
        </div>
      </section>

      <section className="content-section">
        <div className="work-item-scope readonly-scope">
          <ShieldCheck size={17} aria-hidden="true" />
          <div>
            <strong>个人相关范围</strong>
            <span>仅显示我申请、我的项目或我管理的单据；无法证明归属时不显示</span>
          </div>
          <p>
            <span>源候选</span><strong>{current.source_total_count}</strong>
            <span>我申请</span><strong>{current.submitted_by_me_count}</strong>
            <span>我的项目</span><strong>{current.my_project_count}</strong>
            <span>我管理</span><strong>{current.managed_by_me_count}</strong>
          </p>
        </div>

        <div className="table-toolbar readonly-toolbar">
          <div className="segmented segmented--modules" aria-label="业务查询模块">
            {MODULES.map(({ key, label, icon: Icon }) => (
              <button
                className={activeModule === key ? "is-active" : ""}
                key={key}
                onClick={() => setActiveModule(key)}
                type="button"
                aria-pressed={activeModule === key}
              >
                <Icon size={14} aria-hidden="true" />
                <span>{label}</span>
                <strong>{data[key].total_count}</strong>
              </button>
            ))}
          </div>
          <div className="search-control readonly-search">
            <Search size={16} aria-hidden="true" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={activeModule === "payment" ? "搜索编号、项目、公司" : "搜索 BizCase、客户、项目"}
              aria-label={`搜索${definition.label}记录`}
            />
          </div>
        </div>

        {loading ? <LoadingRows /> : (
          items.length ? (
            activeModule === "payment"
              ? <PaymentTable items={items} />
              : <BizCaseTable items={items} />
          ) : (
            <EmptyState
              icon={activeModule === "payment" ? CreditCard : Database}
              title={emptyTitle}
              action={!deferredQuery ? (
                <Button icon={RefreshCw} variant="primary" onClick={onSync} disabled={syncing}>
                  {syncing ? "同步中" : "同步模块"}
                </Button>
              ) : null}
            />
          )
        )}
      </section>
    </div>
  );
}
