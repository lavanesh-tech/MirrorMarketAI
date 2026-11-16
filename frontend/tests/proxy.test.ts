// @vitest-environment node
import { describe, expect, it } from "vitest";

import { contentSecurityPolicy, wsOrigin } from "@/lib/csp";
import { config, proxy } from "@/proxy";

import { request } from "./helpers";

const location = (path: string, cookies?: Record<string, string>) =>
  proxy(request(path, { cookies })).headers.get("location");

describe("page access (proxy.ts)", () => {
  it("sends signed-out visitors to the login page and remembers where they were going", () => {
    expect(location("/workspaces/abc")).toBe("http://app.test/login?next=%2Fworkspaces%2Fabc");
    expect(location("/")).toBe("http://app.test/login");
  });

  it("lets signed-out visitors see the login and register pages", () => {
    expect(location("/login")).toBeNull();
    expect(location("/register")).toBeNull();
  });

  it("keeps signed-in visitors out of the login pages and lets them in elsewhere", () => {
    const session = { mm_refresh: "r" };
    expect(location("/login", session)).toBe("http://app.test/workspaces");
    expect(location("/workspaces", session)).toBeNull();
  });

  it("does not run for API routes, Next assets or files", () => {
    const pattern = new RegExp(`^${config.matcher[0]}$`);
    for (const path of [
      "/api/v1/workspaces",
      "/api/session/login",
      "/_next/static/a.js",
      "/favicon.ico",
    ]) {
      expect(pattern.test(path)).toBe(false);
    }
    for (const path of ["/", "/login", "/workspaces/123"]) {
      expect(pattern.test(path)).toBe(true);
    }
  });
});

describe("Content-Security-Policy (proxy.ts, lib/csp.ts)", () => {
  const policyFor = (path: string, cookies?: Record<string, string>) =>
    proxy(request(path, { cookies })).headers.get("content-security-policy");

  it("sends a policy with a fresh nonce on every page", () => {
    const first = policyFor("/login");
    const second = policyFor("/login");
    expect(first).toMatch(/script-src 'self' 'nonce-[A-Za-z0-9+/=]{24}' 'strict-dynamic'/);
    expect(first).not.toBe(second);
    expect(policyFor("/workspaces", { mm_refresh: "r" })).toContain("frame-ancestors 'none'");
  });

  it("gives the page renderer the same nonce the browser enforces", () => {
    const response = proxy(request("/login"));
    const policy = response.headers.get("content-security-policy") ?? "";
    const forwarded = response.headers.get("x-middleware-request-x-nonce");
    expect(forwarded).toBeTruthy();
    expect(policy).toContain(`'nonce-${forwarded}'`);
  });

  it("allows nothing the app does not use", () => {
    const policy = contentSecurityPolicy({
      nonce: "abc",
      development: false,
      wsUrl: "ws://api.test:8000/x",
    });
    expect(policy).toContain("object-src 'none'");
    expect(policy).toContain("connect-src 'self' ws://api.test:8000");
    expect(policy).not.toContain("unsafe-eval");
    expect(policy).not.toContain("unsafe-inline");
    expect(policy).not.toContain("*");
  });

  it("allows eval and dev sockets only in development", () => {
    const policy = contentSecurityPolicy({
      nonce: "abc",
      development: true,
      wsUrl: "wss://api.test",
    });
    expect(policy).toContain("'unsafe-eval'");
    expect(policy).toContain("connect-src 'self' wss://api.test ws:");
  });

  it("ignores a WebSocket URL that is not one", () => {
    expect(wsOrigin("http://api.test")).toBe("");
    expect(wsOrigin("not a url")).toBe("");
    expect(wsOrigin("wss://api.test:443/ws")).toBe("wss://api.test");
  });

  it("does not put a policy on redirects", () => {
    expect(policyFor("/workspaces")).toBeNull();
  });
});
