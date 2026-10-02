import { NextRequest } from "next/server";
import { vi } from "vitest";

export const TOKENS = {
  access_token: "access-1",
  token_type: "bearer",
  expires_at: "2030-01-01T00:15:00Z",
  refresh_token: "refresh-1",
  refresh_expires_at: "2030-01-15T00:00:00Z",
};

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

type Call = { url: string; init: RequestInit };

/** Replace global fetch with a queue of responses; returns the recorded calls. */
export function mockFetch(...responses: Array<Response | Error>): Call[] {
  const calls: Call[] = [];
  const queue = [...responses];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string | URL, init: RequestInit = {}) => {
      calls.push({ url: String(url), init });
      const next = queue.shift();
      if (next === undefined) throw new Error(`unexpected fetch: ${String(url)}`);
      if (next instanceof Error) throw next;
      return next;
    }),
  );
  return calls;
}

export function request(
  path: string,
  init: {
    method?: string;
    body?: unknown;
    cookies?: Record<string, string>;
    headers?: Record<string, string>;
  } = {},
): NextRequest {
  const headers = new Headers({ host: "app.test", ...init.headers });
  if (init.cookies) {
    headers.set(
      "cookie",
      Object.entries(init.cookies)
        .map(([k, v]) => `${k}=${v}`)
        .join("; "),
    );
  }
  if (init.body !== undefined) headers.set("content-type", "application/json");
  return new NextRequest(`http://app.test${path}`, {
    method: init.method ?? "GET",
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
}

export function authHeader(call: Call): string | null {
  return new Headers(call.init.headers).get("authorization");
}
