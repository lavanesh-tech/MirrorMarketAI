// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { DELETE, GET, POST } from "@/app/api/v1/[...path]/route";

import { TOKENS, authHeader, json, mockFetch, request } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });
const SESSION = { mm_access: "access-1", mm_refresh: "refresh-1" };

describe("/api/v1 proxy", () => {
  it("forwards with the access token from the cookie and returns the API response", async () => {
    const calls = mockFetch(
      json({ items: [] }, 200, { "x-request-id": "req-1", "set-cookie": "evil=1" }),
    );
    const response = await GET(
      request("/api/v1/workspaces?limit=5", {
        cookies: SESSION,
        headers: { "x-request-id": "abc12345" },
      }),
      ctx("workspaces"),
    );
    expect(calls[0]?.url).toBe("http://127.0.0.1:8000/api/v1/workspaces?limit=5");
    expect(authHeader(calls[0]!)).toBe("Bearer access-1");
    expect(new Headers(calls[0]?.init.headers).get("x-request-id")).toBe("abc12345");
    expect(new Headers(calls[0]?.init.headers).get("cookie")).toBeNull(); // cookies stay here
    expect(await response.json()).toEqual({ items: [] });
    expect(response.headers.get("x-request-id")).toBe("req-1");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("set-cookie")).toBeNull(); // upstream cannot set cookies
  });

  it("forwards bodies and the idempotency key", async () => {
    const calls = mockFetch(json({ id: "w1" }, 201, { "idempotent-replayed": "true" }));
    const response = await POST(
      request("/api/v1/workspaces", {
        method: "POST",
        body: { name: "Trip" },
        cookies: SESSION,
        headers: { "idempotency-key": "k-1", origin: "http://app.test" },
      }),
      ctx("workspaces"),
    );
    expect(response.status).toBe(201);
    expect(response.headers.get("idempotent-replayed")).toBe("true");
    const sent = new Headers(calls[0]?.init.headers);
    expect([sent.get("idempotency-key"), sent.get("content-type")]).toEqual([
      "k-1",
      "application/json",
    ]);
    expect(new TextDecoder().decode(calls[0]?.init.body as ArrayBuffer)).toBe('{"name":"Trip"}');
  });

  it("refreshes once on 401, retries, and replaces the cookies", async () => {
    const calls = mockFetch(
      json(
        { error: { code: "not_authenticated", message: "Token has expired.", request_id: "r" } },
        401,
      ),
      json({ ...TOKENS, access_token: "access-2", refresh_token: "refresh-2" }),
      json({ id: "u1" }),
    );
    const response = await GET(request("/api/v1/auth/me", { cookies: SESSION }), ctx("auth", "me"));
    expect(calls.map((c) => c.url.split("/api/v1/")[1])).toEqual([
      "auth/me",
      "auth/refresh",
      "auth/me",
    ]);
    expect(authHeader(calls[2]!)).toBe("Bearer access-2");
    expect(response.status).toBe(200);
    expect(response.cookies.get("mm_access")?.value).toBe("access-2");
    expect(response.cookies.get("mm_refresh")?.value).toBe("refresh-2");
  });

  it("refreshes first when only the refresh cookie is left", async () => {
    const calls = mockFetch(json({ ...TOKENS, access_token: "access-9" }), json({ id: "u1" }));
    const response = await GET(
      request("/api/v1/auth/me", { cookies: { mm_refresh: "refresh-only" } }),
      ctx("auth", "me"),
    );
    expect(calls).toHaveLength(2);
    expect(authHeader(calls[1]!)).toBe("Bearer access-9");
    expect(response.cookies.get("mm_access")?.value).toBe("access-9");
  });

  it("ends the session when the refresh token is rejected", async () => {
    const calls = mockFetch(
      json({ error: { code: "not_authenticated", message: "x", request_id: "r" } }, 401),
      json({ error: { code: "invalid_refresh_token", message: "x", request_id: "r" } }, 401),
    );
    const response = await GET(
      request("/api/v1/auth/me", { cookies: { mm_access: "old", mm_refresh: "refresh-dead" } }),
      ctx("auth", "me"),
    );
    expect(calls).toHaveLength(2); // no endless retry
    expect(response.status).toBe(401);
    expect(response.cookies.get("mm_access")?.value).toBe("");
    expect(response.cookies.get("mm_refresh")?.value).toBe("");
  });

  it("keeps token endpoints unreachable from the browser", async () => {
    const calls = mockFetch();
    for (const path of ["login", "refresh", "logout", "register", "change-password"]) {
      const response = await POST(
        request(`/api/v1/auth/${path}`, { method: "POST", body: {}, cookies: SESSION }),
        ctx("auth", path),
      );
      expect(response.status).toBe(404);
    }
    expect(calls).toHaveLength(0);
  });

  it("blocks cross-site writes, encodes path segments and handles 204", async () => {
    const none = mockFetch();
    const foreign = await DELETE(
      request("/api/v1/workspaces/1", {
        method: "DELETE",
        cookies: SESSION,
        headers: { origin: "https://evil.test" },
      }),
      ctx("workspaces", "1"),
    );
    expect(foreign.status).toBe(403);
    expect(none).toHaveLength(0);

    const calls = mockFetch(new Response(null, { status: 204 }));
    const gone = await DELETE(
      request("/api/v1/x", { method: "DELETE", cookies: SESSION }),
      ctx("workspaces", "../admin", "a b"),
    );
    expect(calls[0]?.url).toBe("http://127.0.0.1:8000/api/v1/workspaces/..%2Fadmin/a%20b");
    expect([gone.status, await gone.text()]).toEqual([204, ""]);
  });

  it("answers 502 when the API is unreachable and 413 for oversized bodies", async () => {
    mockFetch(new Error("connection refused"));
    const down = await GET(request("/api/v1/workspaces", { cookies: SESSION }), ctx("workspaces"));
    expect(down.status).toBe(502);
    expect((await down.json()).error.code).toBe("api_unavailable");

    const calls = mockFetch();
    const huge = await POST(
      request("/api/v1/workspaces", {
        method: "POST",
        body: { name: "x".repeat(7 * 1024 * 1024) },
        cookies: SESSION,
      }),
      ctx("workspaces"),
    );
    expect(huge.status).toBe(413);
    expect(calls).toHaveLength(0);
  });
});
