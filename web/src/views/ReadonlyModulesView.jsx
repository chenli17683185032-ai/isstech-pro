import {
  AlertTriangle,
  BriefcaseBusiness,
  CreditCard,
  Database,
  Eye,
  PlaneTakeoff,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import Button from "../components/Button";
import EmptyState from "../components/EmptyState";
import StatusTag from "../components/StatusTag";
import { formatDateTime } from "../lib/format";

const MODULES = [
  { key: "payment", module: "payment", label: "付款申请", icon: CreditCard, placeholder: "搜索编号、项目、公司" },
  { key: "bizcases", module: "bizcase", label: "BizCase查询", icon: BriefcaseBusiness, placeholder: "搜索 BizCase、客户、项目" },
  { key: "travelApplications", module: "travel_application", label: "出差申请", icon: PlaneTakeoff, placeholder: "搜索单据、项目、申请人" },
];

const SCOPE_LABELS = {
  my_project: "我的项目",
  submitted_by_me: "我申请",
  managed_by_me: "我管理",
};

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

function rowInteraction(item, onOpen, label) {
  return {
    className: "clickable-row",
    role: "button",
    tabIndex: 0,
    "aria-label": `查看 ${label} 本地详情`,
    onClick: () => onOpen(item),
    onKeyDown: (event) => {
      if (event.target === event.currentTarget && ["Enter", " "].includes(event.key)) {
        event.preventDefault();
        onOpen(item);
      }
    },
  };
}

function DetailButton({ item, label, onOpen }) {
  return (
    <button
      className="icon-link"
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onOpen(item);
      }}
      title="查看本地详情"
      aria-label={`查看 ${label} 本地详情`}
    >
      <Eye size={16} aria-hidden="true" />
    </button>
  );
}

function PaymentTable({ items, onOpen }) {
  return (
    <div className="table-wrap">
      <table className="data-table readonly-table payment-table" aria-label="付款申请记录">
        <thead>
          <tr><th>付款单编号</th><th>类别</th><th>项目</th><th>收付款公司</th><th>金额</th><th>申请人</th><th>状态</th><th aria-label="操作" /></tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} {...rowInteraction(item, onOpen, item.payment_no || item.id)}>
              <td className="mono"><strong>{item.payment_no || item.id}</strong></td>
              <td>{item.payment_type || "--"}</td>
              <td><strong>{item.project_name || "未命名项目"}</strong><span>{item.project_no || "--"}</span></td>
              <td><strong>{item.payee_company || "--"}</strong><span>{item.payer_company || "--"}</span></td>
              <td><strong>{item.amount || "--"}</strong><span>{item.currency || ""}</span></td>
              <td>{item.applicant || "--"}</td>
              <td><StatusTag value={recordStatusTone(item.status)} label={item.status || "状态未知"} /></td>
              <td className="align-right"><DetailButton item={item} label={item.payment_no || item.id} onOpen={onOpen} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BizCaseTable({ items, onOpen }) {
  return (
    <div className="table-wrap">
      <table className="data-table readonly-table bizcase-table" aria-label="BizCase查询记录">
        <thead>
          <tr><th>序号</th><th>BizCase</th><th>客户</th><th>项目</th><th>利润中心</th><th>收入确认</th><th>当前审批人</th><th aria-label="操作" /></tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} {...rowInteraction(item, onOpen, item.bizcase_no || item.id)}>
              <td className="mono">{item.ordinal}</td>
              <td><strong>{item.bizcase_no || "--"}</strong><span>{item.version_no || item.id}</span></td>
              <td>{item.client_name || "--"}</td>
              <td><strong>{item.project_name || "未命名项目"}</strong><span>{item.project_no || "--"}</span></td>
              <td><strong>{item.profit_center || "--"}</strong><span>{item.profit_center_group || "--"}</span></td>
              <td>{item.revenue_recognition_type || "--"}</td>
              <td><strong>{item.current_approver || "--"}</strong></td>
              <td className="align-right"><DetailButton item={item} label={item.bizcase_no || item.id} onOpen={onOpen} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TravelApplicationTable({ items, onOpen }) {
  return (
    <div className="table-wrap">
      <table className="data-table readonly-table travel-application-table" aria-label="出差申请记录">
        <thead>
          <tr><th>单据编号</th><th>项目</th><th>申请人</th><th>申请日期</th><th>金额</th><th>下一级审批人</th><th>状态</th><th aria-label="操作" /></tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} {...rowInteraction(item, onOpen, item.application_no || item.id)}>
              <td className="mono"><strong>{item.application_no || item.id}</strong></td>
              <td><strong>{item.project_name || "未命名项目"}</strong></td>
              <td>{item.applicant || "--"}</td>
              <td>{item.application_date || "--"}</td>
              <td><strong>{item.amount || "--"}</strong></td>
              <td><strong>{item.current_approver || "--"}</strong></td>
              <td><StatusTag value={recordStatusTone(item.status)} label={item.status || "状态未知"} /></td>
              <td className="align-right"><DetailButton item={item} label={item.application_no || item.id} onOpen={onOpen} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function detailIdentity(item, module) {
  if (module === "payment") return item.payment_no || item.id;
  if (module === "bizcase") return item.bizcase_no || item.id;
  return item.application_no || item.id;
}

function detailSubtitle(item, module) {
  if (module === "bizcase") return item.client_name || item.project_name || "--";
  return item.project_name || item.payee_company || "--";
}

function ReadonlyDetailDrawer({ item, definition, syncedAt, onClose }) {
  const previousFocus = useRef(document.activeElement);
  useEffect(() => {
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.body.style.overflow = originalOverflow;
      previousFocus.current?.focus?.();
    };
  }, [onClose]);

  const fields = Object.entries(item.fields || {}).filter(([label]) => label !== "操作");
  const reasons = (item.scope_reasons || []).map((reason) => SCOPE_LABELS[reason] || reason);
  return (
    <div className="work-detail-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="work-detail-drawer" role="dialog" aria-modal="true" aria-labelledby="readonly-detail-title">
        <header className="work-detail-header">
          <div>
            <span>{definition.label} · 本地快照</span>
            <h2 id="readonly-detail-title">{detailIdentity(item, definition.module)}</h2>
            <small>{detailSubtitle(item, definition.module)}</small>
            <div className="work-detail-relations" aria-label="单据归属">
              <span>归属</span>
              {reasons.map((reason) => <strong key={reason}>{reason}</strong>)}
            </div>
          </div>
          <div className="work-detail-header__actions">
            <StatusTag value={recordStatusTone(item.status)} label={item.status || "状态未知"} />
            <Button autoFocus icon={X} variant="ghost" size="icon" onClick={onClose} title="关闭详情" aria-label="关闭详情" />
          </div>
        </header>
        <div className="work-detail-body">
          <section className="work-detail-section">
            <div className="work-detail-section__heading">
              <h3>单据字段</h3>
              <span>{fields.length} 项</span>
            </div>
            <dl className="work-detail-fields">
              {fields.map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value || "--"}</dd>
                </div>
              ))}
            </dl>
          </section>
          <section className="work-detail-section readonly-detail-meta">
            <div className="work-detail-section__heading"><h3>快照</h3></div>
            <dl className="work-detail-fields">
              <div><dt>同步时间</dt><dd>{formatDateTime(syncedAt)}</dd></div>
              <div><dt>数据来源</dt><dd>SQLite current</dd></div>
            </dl>
          </section>
        </div>
      </aside>
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
  const [selectedItem, setSelectedItem] = useState(null);
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
        <div><span>出差申请</span><strong>{loading ? "--" : data.travelApplications.total_count}</strong></div>
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
                onClick={() => {
                  setActiveModule(key);
                  setSelectedItem(null);
                }}
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
              placeholder={definition.placeholder}
              aria-label={`搜索${definition.label}记录`}
            />
          </div>
        </div>

        {loading ? <LoadingRows /> : (
          items.length ? (
            activeModule === "payment"
              ? <PaymentTable items={items} onOpen={setSelectedItem} />
              : activeModule === "bizcases"
                ? <BizCaseTable items={items} onOpen={setSelectedItem} />
                : <TravelApplicationTable items={items} onOpen={setSelectedItem} />
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
      {selectedItem ? (
        <ReadonlyDetailDrawer
          item={selectedItem}
          definition={definition}
          syncedAt={current.synced_at}
          onClose={() => setSelectedItem(null)}
        />
      ) : null}
    </div>
  );
}
