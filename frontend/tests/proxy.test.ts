// @vitest-environment node
import { describe, expect, it } from "vitest";

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
