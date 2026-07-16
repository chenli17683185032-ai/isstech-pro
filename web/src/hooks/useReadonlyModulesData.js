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
    submitted_or_managed_count: 0,
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
    submitted_or_managed_count: 0,
    items: [],
  },
  runs: [],
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
      const [payment, bizcases, runs] = await Promise.all([
        apiRequest("/v1/readonly-modules/payment", { token }),
        apiRequest("/v1/readonly-modules/bizcases", { token }),
        apiRequest("/v1/readonly-modules/runs?limit=20", { token }),
      ]);
      setData({ payment, bizcases, runs });
      loadedRef.current = true;
    } catch (requestError) {
      if (requestError.status === 401) onAuthExpired();
      setError(requestError);
    } finally {
      setLoading(false);
    }
  }, [token, onAuthExpired]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { data, loading, error, refresh };
}
