import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiRequest } from "./api";
import AppShell from "./components/AppShell";
import LoginScreen from "./components/LoginScreen";
import Toast from "./components/Toast";
import useAssistantData from "./hooks/useAssistantData";
import useReadonlyModulesData from "./hooks/useReadonlyModulesData";
import useWorkspaceData from "./hooks/useWorkspaceData";
import { normalizeNavigationTarget, parseHash, serializeLocation } from "./navigation";
import DraftsView from "./views/DraftsView";
import MaterialsView from "./views/MaterialsView";
import OverviewView from "./views/OverviewView";
import RecordsView from "./views/RecordsView";
import { resolveProcurementSyncScope, resolveReadonlySyncScope } from "./syncScope";

const TOKEN_KEY = "isstech.workflow-center.session.v1";
const NAVIGATION_DEPTH_KEY = "isstechNavigationDepth";
const viewTitles = {
  overview: "工作台",
  records: "单据中心",
  materials: "材料处理",
  drafts: "草稿审阅",
};

function useHashNavigation() {
  const [location, setLocation] = useState(() => parseHash(window.location.hash));

  useEffect(() => {
    const syncLocation = () => setLocation(parseHash(window.location.hash));
    const canonical = serializeLocation(parseHash(window.location.hash));
    const currentDepth = Number(window.history.state?.[NAVIGATION_DEPTH_KEY]) || 0;
    window.history.replaceState(
      { ...window.history.state, [NAVIGATION_DEPTH_KEY]: currentDepth },
      "",
      canonical,
    );
    syncLocation();
    window.addEventListener("popstate", syncLocation);
    window.addEventListener("hashchange", syncLocation);
    return () => {
      window.removeEventListener("popstate", syncLocation);
      window.removeEventListener("hashchange", syncLocation);
    };
  }, []);

  const navigate = useCallback((view, params = {}, options = {}) => {
    const next = normalizeNavigationTarget({ view, params });
    const nextHash = serializeLocation(next);
    if (nextHash === window.location.hash) return;
    const previous = parseHash(window.location.hash);
    const currentDepth = Number(window.history.state?.[NAVIGATION_DEPTH_KEY]) || 0;
    const nextDepth = options.replace ? currentDepth : currentDepth + 1;
    const method = options.replace ? "replaceState" : "pushState";
    window.history[method](
      { ...window.history.state, [NAVIGATION_DEPTH_KEY]: nextDepth },
      "",
      nextHash,
    );
    setLocation(next);
    if (previous.view !== next.view || previous.params.area !== next.params.area) {
      window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    }
  }, []);

  const goBack = useCallback((fallback = { view: "overview", params: {} }) => {
    const currentDepth = Number(window.history.state?.[NAVIGATION_DEPTH_KEY]) || 0;
    if (currentDepth > 0) {
      window.history.back();
      return;
    }
    const next = normalizeNavigationTarget(fallback);
    window.history.replaceState(
      { ...window.history.state, [NAVIGATION_DEPTH_KEY]: 0 },
      "",
      serializeLocation(next),
    );
    setLocation(next);
  }, []);

  return { location, navigate, goBack };
}

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
  const { location, navigate, goBack } = useHashNavigation();
  const [token, setToken] = useState(restoreToken);
  const [session, setSession] = useState(null);
  const [checkingSession, setCheckingSession] = useState(Boolean(token));
  const [syncing, setSyncing] = useState(false);
  const [readonlySyncing, setReadonlySyncing] = useState(false);
  const [toast, setToast] = useState(null);
  const workItemsSyncRef = useRef(false);
  const readonlySyncRef = useRef(false);
  const clearSession = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setSession(null);
  }, []);
  const workspace = useWorkspaceData(token, clearSession);
  const readonlyModules = useReadonlyModulesData(token, clearSession);
  const assistant = useAssistantData(token, clearSession);

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

  async function handleSync(workflow = null) {
    if (workItemsSyncRef.current) return;
    workItemsSyncRef.current = true;
    setSyncing(true);
    setToast({ tone: "pending", message: "正在更新采购单据，当前快照仍可使用" });
    try {
      const query = workflow ? `?workflow=${encodeURIComponent(workflow)}` : "";
      const result = await apiRequest(`/v1/sync/work-items${query}`, { token, method: "POST" });
      await workspace.refreshWorkItems();
      const failedStreams = (result.streams || []).filter((stream) => stream.status === "failed");
      const scopeLabel = result.streams?.length === 1
        ? result.streams[0].workflow_label
        : "采购单据";
      setToast(result.status === "succeeded" ? {
        tone: "success",
        message: `${scopeLabel}更新完成：本次 ${result.observed_count} 条，当前 ${result.actionable_count} 条待处理`,
      } : {
        tone: "error",
        message: `${scopeLabel}部分更新：本次 ${result.observed_count} 条，${failedStreams.length} 个流程失败`,
      });
    } catch (error) {
      if (error.status === 401) clearSession();
      setToast({ tone: "error", message: error.message || "同步失败" });
    } finally {
      workItemsSyncRef.current = false;
      setSyncing(false);
    }
  }

  async function handleReadonlySync(module = null) {
    if (readonlySyncRef.current) return;
    readonlySyncRef.current = true;
    setReadonlySyncing(true);
    setToast({ tone: "pending", message: "正在更新当前业务单据，当前快照仍可使用" });
    try {
      const query = module ? `?module=${encodeURIComponent(module)}` : "";
      const result = await apiRequest(`/v1/readonly-modules/sync${query}`, {
        token,
        method: "POST",
      });
      if (module) await readonlyModules.refreshModule(module);
      else await readonlyModules.refresh();
      const failedStreams = (result.streams || []).filter((stream) => stream.status === "failed");
      const scopeLabel = result.streams?.length === 1
        ? result.streams[0].module_label
        : "业务单据";
      setToast(result.status === "succeeded" ? {
        tone: "success",
        message: `${scopeLabel}更新完成：${result.observed_count} 条记录，${result.changed_count} 条变化`,
      } : {
        tone: "error",
        message: `${scopeLabel}部分更新：${result.observed_count} 条记录，${failedStreams.length} 个模块失败`,
      });
    } catch (error) {
      if (error.status === 401) clearSession();
      setToast({ tone: "error", message: error.message || "业务查询同步失败" });
    } finally {
      readonlySyncRef.current = false;
      setReadonlySyncing(false);
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
      navigate,
    }),
    [token, workspace.data, workspace.loading, workspace.error, workspace.refresh, navigate],
  );

  if (!token || checkingSession || !session) {
    return <LoginScreen onLogin={handleLogin} checking={checkingSession} />;
  }

  const activeView = location.view;
  let content;
  if (activeView === "materials") content = (
    <MaterialsView {...shared} navigationParams={location.params} />
  );
  else if (activeView === "drafts") content = (
    <DraftsView {...shared} navigationParams={location.params} navigate={navigate} />
  );
  else if (activeView === "records") content = (
    <RecordsView
      params={location.params}
      navigate={navigate}
      goBack={goBack}
      token={token}
      workspace={workspace}
      readonlyModules={readonlyModules}
      notify={setToast}
      onWorkItemsSync={handleSync}
      workItemsSyncing={syncing}
      onReadonlySync={handleReadonlySync}
      readonlySyncing={readonlySyncing}
    />
  );
  else content = (
    <OverviewView
      {...shared}
      readonlyData={readonlyModules.data}
      readonlyLoading={readonlyModules.loading}
      readonlyError={readonlyModules.error}
      assistant={assistant}
    />
  );

  const recordsUseReadonlySync = activeView === "records"
    && location.params.area
    && location.params.area !== "procurement";
  const procurementSyncScope = resolveProcurementSyncScope(location.params);
  const readonlySyncScope = resolveReadonlySyncScope(location.params, readonlyModules.data);
  const activeSyncing = recordsUseReadonlySync ? readonlySyncing : syncing;
  const activeSync = recordsUseReadonlySync
    ? () => handleReadonlySync(readonlySyncScope)
    : () => handleSync(activeView === "records" ? procurementSyncScope : null);
  const activeSyncLabel = activeView === "records"
    ? (recordsUseReadonlySync ? "更新业务单据" : "更新采购单据")
    : "更新数据";
  const backFallback = activeView === "records" && location.params.item
    ? {
      view: "records",
      params: {
        area: location.params.area,
        module: location.params.module,
        workflow: location.params.workflow,
      },
    }
    : { view: "overview", params: {} };
  const canGoBack = activeView !== "overview";
  const totalRecords = workspace.data.workItems.total_count
    + readonlyModules.data.payment.total_count
    + readonlyModules.data.bizcases.total_count
    + readonlyModules.data.travelApplications.total_count
    + readonlyModules.data.dailyExpenses.total_count
    + readonlyModules.data.travelReimbursements.total_count
    + readonlyModules.data.travelSubsidies.total_count;
  const draftCount = workspace.data.drafts.filter((draft) => (
    ["extracted", "needs_review"].includes(draft.state)
  )).length;
  const areaLabels = {
    procurement: "采购单据",
    payment: "付款申请",
    bizcases: "BizCase",
    feeManagement: "费用单据",
  };

  return (
    <>
      <AppShell
        activeView={activeView}
        onViewChange={navigate}
        onBack={canGoBack ? () => goBack(backFallback) : null}
        backLabel={location.params.item ? "返回单据列表" : "返回工作台"}
        title={viewTitles[activeView]}
        subtitle={activeView === "records" ? areaLabels[location.params.area || "procurement"] : undefined}
        username={session.username}
        navBadges={{
          records: totalRecords,
          materials: workspace.data.materials.length,
          drafts: draftCount,
        }}
        syncing={activeSyncing}
        onSync={activeSync}
        syncLabel={activeSyncLabel}
        onLogout={handleLogout}
      >
        {content}
      </AppShell>
      <Toast toast={toast} onClose={() => setToast(null)} />
    </>
  );
}
