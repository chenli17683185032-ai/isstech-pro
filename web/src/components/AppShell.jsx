import {
  ClipboardCheck,
  Files,
  LayoutDashboard,
  ListTodo,
  LogOut,
  RefreshCw,
  Workflow,
} from "lucide-react";
import Button from "./Button";

const navItems = [
  { id: "overview", label: "总览", icon: LayoutDashboard },
  { id: "materials", label: "材料", icon: Files },
  { id: "drafts", label: "审阅草稿", icon: ClipboardCheck },
  { id: "work-items", label: "催办清单", icon: ListTodo },
];

export default function AppShell({
  activeView,
  onViewChange,
  title,
  username,
  syncing,
  onSync,
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
          <div>
            <h1>{title}</h1>
            <span className="topbar__date">
              {new Intl.DateTimeFormat("zh-CN", { dateStyle: "long" }).format(new Date())}
            </span>
          </div>
          <div className="topbar__actions">
            <Button
              icon={RefreshCw}
              variant="primary"
              onClick={onSync}
              disabled={syncing}
              className={syncing ? "is-spinning" : ""}
            >
              {syncing ? "同步中" : "同步"}
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
          </button>
        ))}
      </nav>
    </div>
  );
}
