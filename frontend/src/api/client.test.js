import { describe, expect, it, vi } from "vitest";
import { jsonResponse } from "../test/utils.jsx";
import { ApiError, apiFetch, onUnauthorized } from "./client.js";

describe("apiFetch", () => {
  it("sends JSON with the bearer token and returns parsed data", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const data = await apiFetch("/projects", { method: "POST", body: { a: 1 }, token: "t" });
    expect(data).toEqual({ ok: true });
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/projects");
    expect(options.headers.Authorization).toBe("Bearer t");
    expect(options.body).toBe('{"a":1}');
  });

  it("turns problem details into ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ type: "urn:x:conflict", title: "Conflict", detail: "Taken." }, 409),
      ),
    );
    const error = await apiFetch("/x").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.message).toBe("Taken.");
    expect(error.type).toBe("urn:x:conflict");
  });

  it("calls the unauthorized handler on 401 with a token", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ title: "Unauthorized" }, 401)));
    await apiFetch("/x", { token: "expired" }).catch(() => {});
    expect(handler).toHaveBeenCalledOnce();
    onUnauthorized(null);
  });

  it("reports network failures without leaking internals", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("boom"))));
    const error = await apiFetch("/x").catch((e) => e);
    expect(error.status).toBe(0);
    expect(error.message).toBe("The API could not be reached.");
  });

  it("handles 204 No Content", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    expect(await apiFetch("/x", { method: "DELETE" })).toBeNull();
  });
});
