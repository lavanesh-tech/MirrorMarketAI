// Thin fetch wrapper for the ProofStack API. Errors follow RFC 9457 problem details.

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  constructor(status, problem = {}) {
    super(problem.detail || problem.title || `Request failed (HTTP ${status})`);
    this.name = "ApiError";
    this.status = status;
    this.type = problem.type || "about:blank";
    this.title = problem.title || "Error";
    this.detail = problem.detail || "";
  }
}

let unauthorizedHandler = null;

export function onUnauthorized(handler) {
  unauthorizedHandler = handler;
}

async function parseBody(response) {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text.slice(0, 200) };
  }
}

export async function apiFetch(path, { method = "GET", body, token, signal } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
      credentials: "same-origin",
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(0, { title: "Network error", detail: "The API could not be reached." });
  }
  const data = await parseBody(response);
  if (!response.ok) {
    if (response.status === 401 && token && unauthorizedHandler) unauthorizedHandler();
    throw new ApiError(response.status, data || {});
  }
  return data;
}
