import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../api";

const emptyReadonlyData = {
  payment: {
    module: "payment",
    module_label: "付款申请",
    ownership_scope: "account_visible",
    synced_at: null,
    source_total_count: 0,
    total_count: 0,
    items: [],
  },
  bizcases: {
    module: "bizcase",
    module_label: "BizCase查询",
    ownership_scope: "account_visible",
    synced_at: null,
    source_total_count: 0,
    total_count: 0,
    items: [],
  },
  runs: [],
};

export default function useReadonlyModulesData(token, onAuthExpired) {
  const [data, setData] = useState(emptyReadonlyData);
  const [loading, setLoading] = useState(Boolean(token));
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setData(emptyReadonlyData);
    setError(null);
    if (!token) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [payment, bizcases, runs] = await Promise.all([
        apiRequest("/v1/readonly-modules/payment", { token }),
        apiRequest("/v1/readonly-modules/bizcases", { token }),
        apiRequest("/v1/readonly-modules/runs?limit=20", { token }),
      ]);
      setData({ payment, bizcases, runs });
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
