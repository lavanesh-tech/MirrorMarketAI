// @vitest-environment node
import { NextResponse } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { POST as login } from "@/app/api/session/login/route";
import { POST as logout } from "@/app/api/session/logout/route";
import { POST as register } from "@/app/api/session/register/route";
import {
  clearSessionCookies,
  isSameOrigin,
  isTokens,
  refreshTokens,
  setSessionCookies,
} from "@/lib/server/session";

import { TOKENS, json, mockFetch, request } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

describe("session cookies", () => {
  it("are HttpOnly, SameSite=Lax and expire with their tokens", () => {
    const response = NextResponse.json({});
    setSessionCookies(response, TOKENS);
    const access = response.cookies.get("mm_access");
    const refresh = response.cookies.get("mm_refresh");
    expect(access).toMatchObject({ value: "access-1", httpOnly: true, sameSite: "lax", path: "/" });
    expect(access?.expires).toEqual(new Date(TOKENS.expires_at));
    expect(refresh).toMatchObject({ value: "refresh-1", httpOnly: true, sameSite: "lax" });
    expect(refresh?.expires).toEqual(new Date(TOKENS.refresh_expires_at));
  });

  it("are removed by expiring them", () => {
    const response = NextResponse.json({});
    clearSessionCookies(response);
    expect(response.cookies.get("mm_access")?.value).toBe("");
    expect(response.cookies.get("mm_refresh")?.expires).toEqual(new Date(0));
  });

  it("only accepts complete token payloads", () => {
    expect(isTokens(TOKENS)).toBe(true);
    expect(isTokens({ ...TOKENS, refresh_token: "" })).toBe(false);
    expect(isTokens({ access_token: "a" })).toBe(false);
    expect(isTokens(null)).toBe(false);
  });
});

describe("same-origin check", () => {
  it("allows reads, same-origin writes and non-browser clients", () => {
    expect(isSameOrigin(request("/x", { headers: { origin: "https://evil.test" } }))).toBe(true);
    expect(
      isSameOrigin(request("/x", { method: "POST", headers: { origin: "http://app.test" } })),
    ).toBe(true);
    expect(isSameOrigin(request("/x", { method: "POST" }))).toBe(true);
  });

  it("blocks writes from another site", () => {
    for (const origin of ["https://evil.test", "http://app.test.evil.test", "null"]) {
      expect(isSameOrigin(request("/x", { method: "POST", headers: { origin } }))).toBe(false);
    }
  });
});

describe("refreshTokens", () => {
  it("shares one refresh between parallel callers", async () => {
    const calls = mockFetch(json({ ...TOKENS, access_token: "access-2" }));
    const [a, b] = await Promise.all([refreshTokens("r1"), refreshTokens("r1")]);
    expect(calls).toHaveLength(1);
    expect(a).toBe(b);
    expect(a?.access_token).toBe("access-2");
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({ refresh_token: "r1" });
  });

  it("returns null when the session is over or the API is down", async () => {
    mockFetch(json({ error: {} }, 401));
    expect(await refreshTokens("r2")).toBeNull();
    mockFetch(new Error("connection refused"));
    expect(await refreshTokens("r3")).toBeNull();
    mockFetch(json({ unexpected: true }));
    expect(await refreshTokens("r4")).toBeNull();
  });
});

describe("POST /api/session/login", () => {
  const credentials = { email: "a@example.com", password: "correct-horse-battery" };

  it("stores tokens in cookies and never returns them", async () => {
    const calls = mockFetch(json(TOKENS));
    const response = await login(
      request("/api/session/login", { method: "POST", body: credentials }),
    );
    expect(calls[0]?.url).toBe("http://127.0.0.1:8000/api/v1/auth/login");
    expect(response.status).toBe(200);
    const text = await response.text();
    expect(JSON.parse(text)).toEqual({ ok: true });
    expect(text).not.toContain("access-1");
    expect(text).not.toContain("refresh-1");
    expect(response.cookies.get("mm_refresh")?.value).toBe("refresh-1");
  });

  it("passes API errors through without setting cookies", async () => {
    const error = {
      error: {
        code: "invalid_credentials",
        message: "Incorrect email or password.",
        request_id: "r",
      },
    };
    mockFetch(json(error, 401));
    const response = await login(
      request("/api/session/login", { method: "POST", body: credentials }),
    );
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual(error);
    expect(response.cookies.get("mm_access")).toBeUndefined();

    mockFetch(
      json({ error: { code: "rate_limited", message: "Slow down.", request_id: "r" } }, 429, {
        "retry-after": "7",
      }),
    );
    const limited = await login(
      request("/api/session/login", { method: "POST", body: credentials }),
    );
    expect([limited.status, limited.headers.get("retry-after")]).toEqual([429, "7"]);
  });

  it("answers 502 when the API is unreachable and 403 to other sites", async () => {
    mockFetch(new Error("connection refused"));
    const down = await login(request("/api/session/login", { method: "POST", body: credentials }));
    expect(down.status).toBe(502);
    expect((await down.json()).error.code).toBe("api_unavailable");

    const calls = mockFetch();
    const foreign = await login(
      request("/api/session/login", {
        method: "POST",
        body: credentials,
        headers: { origin: "https://evil.test" },
      }),
    );
    expect(foreign.status).toBe(403);
    expect(calls).toHaveLength(0);
  });
});

describe("POST /api/session/register and /logout", () => {
  it("register forwards the API's answer", async () => {
    mockFetch(json({ id: "u1", email: "a@example.com" }, 201));
    const created = await register(request("/api/session/register", { method: "POST", body: {} }));
    expect(created.status).toBe(201);
    mockFetch(
      json(
        { error: { code: "email_already_registered", message: "Taken.", request_id: "r" } },
        409,
      ),
    );
    const taken = await register(request("/api/session/register", { method: "POST", body: {} }));
    expect(taken.status).toBe(409);
  });

  it("logout revokes the session and clears cookies even if the API fails", async () => {
    const calls = mockFetch(new Response(null, { status: 204 }));
    const response = await logout(
      request("/api/session/logout", { method: "POST", cookies: { mm_refresh: "refresh-1" } }),
    );
    expect(calls[0]?.url).toBe("http://127.0.0.1:8000/api/v1/auth/logout");
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({ refresh_token: "refresh-1" });
    expect(response.cookies.get("mm_refresh")?.value).toBe("");

    mockFetch(new Error("down"));
    const offline = await logout(
      request("/api/session/logout", { method: "POST", cookies: { mm_refresh: "refresh-1" } }),
    );
    expect(offline.status).toBe(200);
    expect(offline.cookies.get("mm_access")?.value).toBe("");
  });
});
