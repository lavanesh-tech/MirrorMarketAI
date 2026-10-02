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

export type ApiCall = { method: string; path: string; body: unknown };
type Reply = Response | ((call: ApiCall) => Response);

/**
 * Replace global fetch with a table of "METHOD /path" -> response. Unlike `mockFetch`
 * the order of requests does not matter, which suits screens that load several
 * things at once. A request with no entry fails the test loudly.
 */
export function mockApi(routes: Record<string, Reply>): ApiCall[] {
  const calls: ApiCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string | URL, init: RequestInit = {}) => {
      const request =
        input instanceof Request
          ? input
          : new Request(new URL(String(input), "http://localhost"), init);
      const url = new URL(request.url);
      const text = await request.clone().text();
      const call: ApiCall = {
        method: request.method,
        path: url.pathname + url.search,
        body: parseBody(text),
      };
      calls.push(call);
      const reply =
        routes[`${call.method} ${call.path}`] ?? routes[`${call.method} ${url.pathname}`];
      if (reply === undefined) throw new Error(`unexpected request: ${call.method} ${call.path}`);
      return typeof reply === "function" ? reply(call) : reply.clone();
    }),
  );
  return calls;
}

export function apiError(code: string, message: string, status: number): Response {
  return json({ error: { code, message, request_id: "req-1" } }, status);
}

/** JSON bodies are parsed; anything else (multipart uploads) is kept as text. */
function parseBody(text: string): unknown {
  if (text === "") return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** A stand-in for the browser's WebSocket that tests drive by hand. */
export class FakeSocket {
  static instances: FakeSocket[] = [];
  sent: unknown[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }

  close(): void {
    this.closed = true;
  }

  /** The server sends a message. */
  receive(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  /** The server (or the network) ends the connection. */
  drop(code: number): void {
    this.onclose?.({ code });
  }

  static install(): void {
    FakeSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeSocket);
  }
}
