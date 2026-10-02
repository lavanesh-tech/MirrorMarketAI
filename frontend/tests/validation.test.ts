import { describe, expect, it } from "vitest";

import { ApiError, toApiError } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import {
  fieldErrors,
  loginSchema,
  registerSchema,
  safeNextPath,
  workspaceSchema,
} from "@/lib/validation";

describe("form schemas", () => {
  it("accept valid input and trim names", () => {
    expect(loginSchema.safeParse({ email: "a@example.com", password: "x" }).success).toBe(true);
    const parsed = registerSchema.parse({
      display_name: "  Ada  ",
      email: "ada@example.com",
      password: "correct-horse-battery",
    });
    expect(parsed.display_name).toBe("Ada");
    expect(workspaceSchema.parse({ name: " Trip " }).name).toBe("Trip");
  });

  it("report one message per field", () => {
    const result = registerSchema.safeParse({
      display_name: " ",
      email: "nope",
      password: "short",
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(fieldErrors(result.error)).toEqual({
        display_name: "Enter your name.",
        email: "Enter a valid email address.",
        password: "Use at least 12 characters.",
      });
    }
    expect(workspaceSchema.safeParse({ name: "x".repeat(121) }).success).toBe(false);
  });
});

describe("safeNextPath", () => {
  it("keeps internal paths", () => {
    expect(safeNextPath("/workspaces/1")).toBe("/workspaces/1");
  });
  it("refuses anything that could leave the site", () => {
    for (const next of [
      null,
      "",
      "https://evil.test",
      "//evil.test",
      "/\\evil.test",
      "workspaces",
    ]) {
      expect(safeNextPath(next)).toBe("/workspaces");
    }
  });
});

describe("toApiError and formatDate", () => {
  it("reads the API error envelope", () => {
    const error = toApiError(
      { error: { code: "workspace_not_found", message: "Workspace not found.", request_id: "r1" } },
      404,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect([error.code, error.status, error.message, error.requestId]).toEqual([
      "workspace_not_found",
      404,
      "Workspace not found.",
      "r1",
    ]);
  });
  it("falls back for anything else", () => {
    expect(toApiError("<html>502</html>", 502).code).toBe("unexpected_error");
    expect(toApiError(undefined, 500).message).toBe("Something went wrong. Try again.");
  });
  it("formats dates the same on server and client", () => {
    expect(formatDate("2026-10-01T23:59:00Z")).toBe("Oct 1, 2026");
    expect(formatDate("not a date")).toBe("");
  });
});
