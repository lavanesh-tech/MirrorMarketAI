import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthForm } from "@/components/auth-form";
import { CreateWorkspaceForm } from "@/components/create-workspace-form";
import { EvidenceSpecimen } from "@/components/evidence-specimen";
import { WorkspaceList } from "@/components/workspace-list";

import { json, mockFetch } from "./helpers";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(search),
}));

function withQuery(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

beforeEach(() => {
  search = "";
  router.replace.mockClear();
  router.refresh.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

describe("AuthForm", () => {
  it("shows field errors without calling the server", async () => {
    const calls = mockFetch();
    render(<AuthForm mode="register" />);
    await userEvent.type(screen.getByLabelText("Email"), "not-an-email");
    await userEvent.type(screen.getByLabelText("Password"), "short");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByText("Enter your name.")).toBeInTheDocument();
    expect(screen.getByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByText("Use at least 12 characters.")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    expect(calls).toHaveLength(0);
  });

  it("logs in and goes to the page the visitor wanted", async () => {
    search = "next=/workspaces/42";
    const calls = mockFetch(json({ ok: true }));
    render(<AuthForm mode="login" />);
    await userEvent.type(screen.getByLabelText("Email"), "ada@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/workspaces/42"));
    expect(calls[0]?.url).toBe("/api/session/login");
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({
      email: "ada@example.com",
      password: "correct-horse-battery",
    });
  });

  it("ignores a next parameter that points to another site", async () => {
    search = "next=https://evil.test";
    mockFetch(json({ ok: true }));
    render(<AuthForm mode="login" />);
    await userEvent.type(screen.getByLabelText("Email"), "ada@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/workspaces"));
  });

  it("registers, then logs in", async () => {
    const calls = mockFetch(json({ id: "u1" }, 201), json({ ok: true }));
    render(<AuthForm mode="register" />);
    await userEvent.type(screen.getByLabelText("Your name"), "Ada");
    await userEvent.type(screen.getByLabelText("Email"), "ada@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(router.replace).toHaveBeenCalled());
    expect(calls.map((c) => c.url)).toEqual(["/api/session/register", "/api/session/login"]);
  });

  it("shows the server's message and lets the visitor try again", async () => {
    mockFetch(
      json(
        {
          error: {
            code: "invalid_credentials",
            message: "Incorrect email or password.",
            request_id: "r",
          },
        },
        401,
      ),
      json(
        { error: { code: "rate_limited", message: "Too many requests.", request_id: "r" } },
        429,
      ),
      new Error("offline"),
    );
    render(<AuthForm mode="login" />);
    await userEvent.type(screen.getByLabelText("Email"), "ada@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    const submit = screen.getByRole("button", { name: "Log in" });
    await userEvent.click(submit);
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password.");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    expect(
      await screen.findByText("Too many attempts. Wait a minute and try again."),
    ).toBeInTheDocument();
    await userEvent.click(submit);
    expect(
      await screen.findByText("You appear to be offline. Check your connection."),
    ).toBeInTheDocument();
    expect(router.replace).not.toHaveBeenCalled();
  });
});

describe("WorkspaceList", () => {
  const page = (items: unknown[]) => ({
    items,
    page: { total: items.length, limit: 100, offset: 0 },
  });

  it("invites the visitor to act when there is nothing yet", async () => {
    mockFetch(json(page([])));
    render(withQuery(<WorkspaceList />));
    expect(screen.getByText("Loading your workspaces…")).toBeInTheDocument();
    expect(await screen.findByText(/No workspaces yet/)).toBeInTheDocument();
  });

  it("lists workspaces with role and date, linking to each", async () => {
    mockFetch(
      json(
        page([
          {
            id: "w1",
            organization_id: "o1",
            name: "Laptop for college",
            description: null,
            created_by_id: "u1",
            created_at: "2026-10-01T10:00:00Z",
            updated_at: "2026-10-01T10:00:00Z",
            my_role: "OWNER",
          },
        ]),
      ),
    );
    render(withQuery(<WorkspaceList />));
    const link = await screen.findByRole("link", { name: "Laptop for college" });
    expect(link).toHaveAttribute("href", "/workspaces/w1");
    expect(screen.getByText("Owner")).toBeInTheDocument();
    expect(screen.getByText("Oct 1, 2026")).toBeInTheDocument();
  });

  it("explains a failure and offers a retry", async () => {
    mockFetch(
      json({ error: { code: "internal_error", message: "x", request_id: "r" } }, 500),
      json(page([])),
    );
    render(withQuery(<WorkspaceList />));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be loaded");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/No workspaces yet/)).toBeInTheDocument();
  });
});

describe("CreateWorkspaceForm", () => {
  it("requires a name", async () => {
    const calls = mockFetch();
    render(withQuery(<CreateWorkspaceForm />));
    await userEvent.click(screen.getByRole("button", { name: "Create workspace" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Give the workspace a name.");
    expect(calls).toHaveLength(0);
  });

  it("creates with an idempotency key and clears the field", async () => {
    const fetchMock = vi.fn(async (input: Request) => {
      expect(input.method).toBe("POST");
      expect(new URL(input.url).pathname).toBe("/api/v1/workspaces");
      expect(input.headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
      expect(await input.json()).toEqual({ name: "Trip" });
      return json({ id: "w9", name: "Trip", my_role: "OWNER" }, 201);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQuery(<CreateWorkspaceForm />));
    const field = screen.getByLabelText("New workspace");
    await userEvent.type(field, "  Trip ");
    await userEvent.click(screen.getByRole("button", { name: "Create workspace" }));
    await waitFor(() => expect(field).toHaveValue(""));
    expect(fetchMock).toHaveBeenCalled();
  });
});

describe("EvidenceSpecimen", () => {
  it("cites every marked claim and lists each source once", () => {
    const { container } = render(<EvidenceSpecimen />);
    expect(container.querySelectorAll(".mark")).toHaveLength(4);
    expect(container.querySelectorAll("sup")).toHaveLength(4);
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByText(/sample data/)).toBeInTheDocument();
  });
});
