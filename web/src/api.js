export class ApiError extends Error {
  constructor(message, { status = 0, code = "REQUEST_FAILED", details = {} } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function apiRequest(path, { token, body, headers, ...options } = {}) {
  const requestHeaders = new Headers(headers || {});
  if (token) requestHeaders.set("Authorization", `Bearer ${token}`);
  let requestBody = body;
  if (body != null && !(body instanceof FormData) && typeof body !== "string") {
    requestHeaders.set("Content-Type", "application/json");
    requestBody = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, { ...options, headers: requestHeaders, body: requestBody });
  } catch (error) {
    throw new ApiError("无法连接本地服务", { details: { cause: error.message } });
  }
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail = payload?.detail || {};
    throw new ApiError(detail.message || `请求失败 (${response.status})`, {
      status: response.status,
      code: detail.code || "REQUEST_FAILED",
      details: detail.details || {},
    });
  }
  return payload;
}

export function authHeaders(token) {
  return { Authorization: `Bearer ${token}` };
}
