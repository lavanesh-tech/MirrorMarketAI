import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

/**
 * Typed client for the API. Paths, parameters and response types come from the
 * OpenAPI contract (`npm run api:types`), so a backend change that breaks the UI
 * fails the type check instead of failing in the browser.
 *
 * Requests go to this app's own origin (`/api/v1/...`); the Next.js server adds
 * the credentials and forwards them (see `src/app/api/v1/[...path]/route.ts`).
 */
export const api = createClient<paths>({
  // Absolute, because `new Request()` rejects relative URLs outside a browser page.
  baseUrl: typeof window === "undefined" ? "http://localhost" : window.location.origin,
  // Looked up per call (not captured once), so tests and instrumentation can replace it.
  fetch: (request) => globalThis.fetch(request),
});

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
    readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Turn the API's error envelope (or anything unexpected) into one error type. */
export function toApiError(error: unknown, status: number): ApiError {
  const body = (error as Partial<Schemas["ErrorResponse"]> | undefined)?.error;
  if (body && typeof body.code === "string" && typeof body.message === "string") {
    return new ApiError(body.message, body.code, status, body.request_id ?? null);
  }
  return new ApiError("Something went wrong. Try again.", "unexpected_error", status);
}

/** Send a JSON body to one of this app's own session routes. */
export async function postSession(path: string, body?: unknown): Promise<void> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("You appear to be offline. Check your connection.", "network_error", 0);
  }
  if (!response.ok) {
    throw toApiError(await response.json().catch(() => null), response.status);
  }
}
