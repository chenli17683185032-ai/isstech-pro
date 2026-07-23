import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "../api";

const emptyReadonlyData = {
  payment: {
    module: "payment",
    module_label: "付款申请",
    ownership_scope: "personal_submissions_projects_and_management",
    synced_at: null,
    source_total_count: 0,
    total_count: 0,
    my_project_count: 0,
    submitted_by_me_count: 0,
    managed_by_me_count: 0,
    items: [],
  },
  bizcases: {
    module: "bizcase",
    module_label: "BizCase查询",
    ownership_scope: "personal_submissions_projects_and_management",
    synced_at: null,
    source_total_count: 0,
    total_count: 0,
    my_project_count: 0,
    submitted_by_me_count: 0,
    managed_by_me_count: 0,
    items: [],
  },
  travelApplications: {
    module: "travel_application",
    module_label: "出差申请",
    ownership_scope: "personal_submissions_projects_and_management",
    synced_at: null,
    source_total_count: 0,
    total_count: 0,
    my_project_count: 0,
    submitted_by_me_count: 0,
    managed_by_me_count: 0,
    items: [],
  },
  dailyExpenses: {
    module: "daily_expense",
    module_label: "日常报销申请",
    ownership_scope: "personal_submissions_projects_and_management",
    synced_at: null,
    source_total_count: 0,
    total_count: 0,
    my_project_count: 0,
    submitted_by_me_count: 0,
    managed_by_me_count: 0,
    items: [],
  },
  travelReimbursements: {
    module: "travel_reimbursement",
    module_label: "差旅报销申请",
    ownership_scope: "personal_submissions_projects_and_management",
    synced_at: null,
    source_total_count: 0,
    total_count: 0,
    my_project_count: 0,
    submitted_by_me_count: 0,
    managed_by_me_count: 0,
    items: [],
  },
  travelSubsidies: {
    module: "travel_subsidy",
    module_label: "差旅补助申请",
    ownership_scope: "personal_submissions_projects_and_management",
    synced_at: null,
    source_total_count: 0,
    total_count: 0,
    my_project_count: 0,
    submitted_by_me_count: 0,
    managed_by_me_count: 0,
    items: [],
  },
  runs: [],
};

const moduleEndpoints = {
  payment: ["payment", "/v1/readonly-modules/payment"],
  bizcase: ["bizcases", "/v1/readonly-modules/bizcases"],
  travel_application: ["travelApplications", "/v1/readonly-modules/travel-applications"],
  daily_expense: ["dailyExpenses", "/v1/readonly-modules/daily-expenses"],
  travel_reimbursement: ["travelReimbursements", "/v1/readonly-modules/travel-reimbursements"],
  travel_subsidy: ["travelSubsidies", "/v1/readonly-modules/travel-subsidies"],
};

export default function useReadonlyModulesData(token, onAuthExpired) {
  const [data, setData] = useState(emptyReadonlyData);
  const [loading, setLoading] = useState(Boolean(token));
  const [error, setError] = useState(null);
  const loadedRef = useRef(false);

  const refresh = useCallback(async () => {
    setError(null);
    if (!token) {
      loadedRef.current = false;
      setData(emptyReadonlyData);
      setLoading(false);
      return;
    }
    if (!loadedRef.current) setLoading(true);
    try {
      const [
        payment,
        bizcases,
        travelApplications,
        dailyExpenses,
        travelReimbursements,
        travelSubsidies,
        runs,
      ] = await Promise.all([
        apiRequest("/v1/readonly-modules/payment", { token }),
        apiRequest("/v1/readonly-modules/bizcases", { token }),
        apiRequest("/v1/readonly-modules/travel-applications", { token }),
        apiRequest("/v1/readonly-modules/daily-expenses", { token }),
        apiRequest("/v1/readonly-modules/travel-reimbursements", { token }),
        apiRequest("/v1/readonly-modules/travel-subsidies", { token }),
        apiRequest("/v1/readonly-modules/runs?limit=20", { token }),
      ]);
      setData({
        payment,
        bizcases,
        travelApplications,
        dailyExpenses,
        travelReimbursements,
        travelSubsidies,
        runs,
      });
      loadedRef.current = true;
    } catch (requestError) {
      if (requestError.status === 401) onAuthExpired();
      setError(requestError);
    } finally {
      setLoading(false);
    }
  }, [token, onAuthExpired]);

  const refreshModule = useCallback(async (module) => {
    const target = moduleEndpoints[module];
    if (!token || !target) return;
    setError(null);
    const [key, endpoint] = target;
    try {
      const [records, runs] = await Promise.all([
        apiRequest(endpoint, { token }),
        apiRequest("/v1/readonly-modules/runs?limit=20", { token }),
      ]);
      setData((current) => ({ ...current, [key]: records, runs }));
      loadedRef.current = true;
    } catch (requestError) {
      if (requestError.status === 401) onAuthExpired();
      setError(requestError);
    }
  }, [token, onAuthExpired]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { data, loading, error, refresh, refreshModule };
}
