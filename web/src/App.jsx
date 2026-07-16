import { useCallback, useEffect, useMemo, useState } from "react";
import { apiRequest } from "./api";
import AppShell from "./components/AppShell";
import LoginScreen from "./components/LoginScreen";
import Toast from "./components/Toast";
import useWorkspaceData from "./hooks/useWorkspaceData";
import DraftsView from "./views/DraftsView";
import MaterialsView from "./views/MaterialsView";
import OverviewView from "./views/OverviewView";
import WorkItemsView from "./views/WorkItemsView";

const TOKEN_KEY = "isstech.workflow-center.session.v1";
const viewTitles = {
  overview: "工作总览",
  materials: "项目材料",
  drafts: "审阅草稿",
  "work-items": "催办清单",
};

function restoreToken() {
  const current = localStorage.getItem(TOKEN_KEY);
  if (current) {
    sessionStorage.removeItem(TOKEN_KEY);
    return current;
  }
  const legacy = sessionStorage.getItem(TOKEN_KEY);
  if (legacy) {
    localStorage.setItem(TOKEN_KEY, legacy);
    sessionStorage.removeItem(TOKEN_KEY);
  }
  return legacy;
}

export default function App() {
  const [token, setToken] = useState(restoreToken);
  const [session, setSession] = useState(null);
  const [checkingSession, setCheckingSession] = useState(Boolean(token));
  const [activeView, setActiveView] = useState("overview");
  const [syncing, setSyncing] = useState(false);
  const [toast, setToast] = useState(null);
  const clearSession = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setSession(null);
  }, []);
  const workspace = useWorkspaceData(token, clearSession);

  useEffect(() => {
    if (!token) {
      setCheckingSession(false);
      return;
    }
    let active = true;
    setCheckingSession(true);
    apiRequest("/v1/session", { token })
      .then((record) => {
        if (active) setSession(record);
      })
      .catch(() => {
        if (active) clearSession();
      })
      .finally(() => {
        if (active) setCheckingSession(false);
      });
    return () => {
      active = false;
    };
  }, [token, clearSession]);

  async function handleLogin(username, password) {
    const record = await apiRequest("/v1/sessions", {
      method: "POST",
      body: { username, password },
    });
    localStorage.setItem(TOKEN_KEY, record.token);
    sessionStorage.removeItem(TOKEN_KEY);
    setToken(record.token);
    setSession(record);
  }

  async function handleLogout() {
    if (token) {
      try {
        await apiRequest("/v1/session", { token, method: "DELETE" });
      } catch {
        // Local state is cleared even if the in-memory session already expired.
      }
    }
    clearSession();
  }

  async function handleSync() {
    setSyncing(true);
    try {
      const result = await apiRequest("/v1/sync/work-items", { token, method: "POST" });
      await workspace.refresh();
      const failedStreams = (result.streams || []).filter((stream) => stream.status === "failed");
      setToast(result.status === "succeeded" ? {
        tone: "success",
        message: `同步完成：${result.observed_count} 条单据，${result.actionable_count} 条待处理`,
      } : {
        tone: "error",
        message: `部分同步完成：${result.observed_count} 条，${failedStreams.length} 个流程失败`,
      });
    } catch (error) {
      setToast({ tone: "error", message: error.message || "同步失败" });
    } finally {
      setSyncing(false);
    }
  }

  const shared = useMemo(
    () => ({
      token,
      data: workspace.data,
      loading: workspace.loading,
      error: workspace.error,
      refresh: workspace.refresh,
      notify: setToast,
      navigate: setActiveView,
    }),
    [token, workspace.data, workspace.loading, workspace.error, workspace.refresh],
  );

  if (!token || checkingSession || !session) {
    return <LoginScreen onLogin={handleLogin} checking={checkingSession} />;
  }

  let content;
  if (activeView === "materials") content = <MaterialsView {...shared} />;
  else if (activeView === "drafts") content = <DraftsView {...shared} />;
  else if (activeView === "work-items") content = <WorkItemsView {...shared} onSync={handleSync} syncing={syncing} />;
  else content = <OverviewView {...shared} onSync={handleSync} syncing={syncing} />;

  return (
    <>
      <AppShell
        activeView={activeView}
        onViewChange={setActiveView}
        title={viewTitles[activeView]}
        username={session.username}
        syncing={syncing}
        onSync={handleSync}
        onLogout={handleLogout}
      >
        {content}
      </AppShell>
      <Toast toast={toast} onClose={() => setToast(null)} />
    </>
  );
}
