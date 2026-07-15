import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../api";

const emptyData = {
  materials: [],
  extractions: [],
  drafts: [],
  workItems: {
    items: [],
    total_count: 0,
    follow_up_count: 0,
    approved_count: 0,
    synced_at: null,
    ownership_scope: "participant",
    source_total_count: null,
    matched_count: 0,
  },
  syncRuns: [],
};

export default function useWorkspaceData(token, onAuthExpired) {
  const [data, setData] = useState(emptyData);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [materials, extractions, drafts, workItems, syncRuns] = await Promise.all([
        apiRequest("/v1/materials?limit=250", { token }),
        apiRequest("/v1/extractions?limit=250", { token }),
        apiRequest("/v1/drafts?limit=250", { token }),
        apiRequest("/v1/work-items/current", { token }),
        apiRequest("/v1/sync/runs?limit=10", { token }),
      ]);
      setData({ materials, extractions, drafts, workItems, syncRuns });
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

  return { data, loading, error, refresh, setData };
}
