import "server-only";

/** Base URL of the FastAPI backend, reachable from the Next.js server (never from the browser). */
export function backendUrl(): string {
  return (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

export const API_PREFIX = "/api/v1";
