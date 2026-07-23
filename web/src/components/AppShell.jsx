import {
  ArrowLeft,
  ClipboardCheck,
  Files,
  LayoutDashboard,
  LibraryBig,
  LogOut,
  RefreshCw,
  Workflow,
} from "lucide-react";
import Button from "./Button";
import WorkflowLauncher from "./WorkflowLauncher";

const navItems = [
  { id: "overview", label: "工作台", icon: LayoutDashboard },
  { id: "records", label: "单据中心", icon: LibraryBig },
  { id: "materials", label: "材料处理", icon: Files },
  { id: "drafts", label: "草稿审阅", icon: ClipboardCheck },
];

export default function AppShell({
  activeView,
  onViewChange,
  onBack,
  backLabel = "返回",
  title,
  subtitle,
  username,
  navBadges = {},
  syncing,
  onSync,
  syncLabel = "更新数据",
  onLogout,
  children,
}) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-mark"><Workflow size={19} aria-hidden="true" /></span>
          <div>
            <strong>统一流程中心</strong>
            <span>Workflow Center</span>
          </div>
        </div>
        <nav className="primary-nav" aria-label="主导航">
          {navItems.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              className={activeView === id ? "primary-nav__item is-active" : "primary-nav__item"}
              onClick={() => onViewChange(id)}
              type="button"
            >
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
              {navBadges[id] ? <strong className="primary-nav__badge">{navBadges[id]}</strong> : null}
            </button>
          ))}
        </nav>
        <div className="sidebar__footer">
          <span className="connection-state"><i />只读连接</span>
          <span className="sidebar__user" title={username}>{username}</span>
        </div>
      </aside>
      <div className="app-stage">
        <header className="topbar">
          <div className="topbar__heading">
            {onBack ? (
              <Button
                className="topbar__back"
                icon={ArrowLeft}
                variant="ghost"
                onClick={onBack}
                aria-label={backLabel}
                title={backLabel}
              >
                返回
              </Button>
            ) : null}
            <div>
              <h1>{title}</h1>
              <span className="topbar__date">
                {subtitle || new Intl.DateTimeFormat("zh-CN", { dateStyle: "long" }).format(new Date())}
              </span>
            </div>
          </div>
          <div className="topbar__actions">
            <WorkflowLauncher />
            <Button
              icon={RefreshCw}
              variant="secondary"
              onClick={onSync}
              disabled={syncing}
              className={syncing ? "is-spinning" : ""}
            >
              {syncing ? "正在更新" : syncLabel}
            </Button>
            <Button
              icon={LogOut}
              variant="ghost"
              size="icon"
              onClick={onLogout}
              aria-label="退出"
              title="退出"
            />
          </div>
        </header>
        <main className="workspace">{children}</main>
      </div>
      <nav className="mobile-nav" aria-label="移动导航">
        {navItems.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={activeView === id ? "is-active" : ""}
            onClick={() => onViewChange(id)}
            type="button"
          >
            <Icon size={19} aria-hidden="true" />
            <span>{label}</span>
            {navBadges[id] ? <strong className="mobile-nav__badge">{navBadges[id]}</strong> : null}
          </button>
        ))}
      </nav>
    </div>
  );
}
