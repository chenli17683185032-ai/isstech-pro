import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../api";

const emptyState = {
  brief: null,
  preferences: [],
  stale: false,
};

export default function useAssistantData(token, onAuthExpired) {
  const [data, setData] = useState(emptyState);
  const [loading, setLoading] = useState(Boolean(token));
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState(null);

  const handleError = useCallback((requestError) => {
    if (requestError.status === 401) onAuthExpired();
    setError(requestError);
    throw requestError;
  }, [onAuthExpired]);

  const refresh = useCallback(async () => {
    if (!token) {
      setData(emptyState);
      setLoading(false);
      setError(null);
      return null;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await apiRequest("/v1/assistant/brief", { token });
      setData(result);
      return result;
    } catch (requestError) {
      return handleError(requestError);
    } finally {
      setLoading(false);
    }
  }, [token, handleError]);

  const update = useCallback(async (path, options) => {
    if (!token) return null;
    setUpdating(true);
    setError(null);
    try {
      const result = await apiRequest(path, { token, ...options });
      setData(result);
      return result;
    } catch (requestError) {
      return handleError(requestError);
    } finally {
      setUpdating(false);
    }
  }, [token, handleError]);

  const generate = useCallback(
    () => update("/v1/assistant/briefs", { method: "POST" }),
    [update],
  );
  const addPreference = useCallback(
    (text) => update("/v1/assistant/preferences", { method: "POST", body: { text } }),
    [update],
  );
  const clearPreferences = useCallback(
    () => update("/v1/assistant/preferences", { method: "DELETE" }),
    [update],
  );

  useEffect(() => {
    refresh().catch(() => {});
  }, [refresh]);

  return {
    data,
    loading,
    updating,
    error,
    refresh,
    generate,
    addPreference,
    clearPreferences,
  };
}
