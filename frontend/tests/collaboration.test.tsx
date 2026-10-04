import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GET as getConfig } from "@/app/api/config/route";
import { AgentStatus } from "@/components/agent-status";
import { AskPanel } from "@/components/ask-panel";
import { Discussion } from "@/components/discussion";
import { EvidenceSearch } from "@/components/evidence-search";
import { PriceHistoryView, chartGeometry } from "@/components/price-history";
import { ProductSources } from "@/components/product-sources";
import { VoteButtons } from "@/components/vote-buttons";
import {
  agentLabel,
  engineLabel,
  parseAnswer,
  parseOrchestration,
  splitCitations,
} from "@/lib/agents";
import { collaborationKeys } from "@/lib/api/queries";
import { RealtimeProvider, refreshFor, useRealtime } from "@/lib/realtime";
import { priceSchema, textSourceSchema, urlSourceSchema } from "@/lib/validation";

import { FakeSocket, apiError, json, mockApi } from "./helpers";

function page(ui: ReactNode, client = newClient()) {
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
const newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

const W = "/api/v1/workspaces/w1";
const workspace = (my_role = "OWNER") =>
  json({
    id: "w1",
    organization_id: "o1",
    name: "Laptop for college",
    description: null,
    created_by_id: "u1",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    my_role,
  });
const me = json({
  id: "u1",
  email: "ada@example.com",
  display_name: "Ada",
  created_at: "2026-10-01T00:00:00Z",
});
const products = json([
  {
    product: {
      id: "p1",
      brand: "Aster",
      name: "Swift 14",
      category: "laptop",
      created_at: "2026-10-01T00:00:00Z",
    },
    variant_id: null,
    notes: null,
    added_by_id: "u1",
    added_at: "2026-10-01T00:00:00Z",
  },
]);
const comment = (id: string, author_id: string, body: string | null, extra = {}) => ({
  id,
  workspace_id: "w1",
  product_id: null,
  parent_id: null,
  author_id,
  author_name: author_id === "u1" ? "Ada" : "Grace",
  body,
  deleted: body === null,
  edited_at: null,
  created_at: "2026-10-02T14:05:00Z",
  ...extra,
});
const comments = (...items: unknown[]) =>
  json({ items, page: { total: items.length, limit: 100, offset: 0 } });
const run = (agent: string, output: unknown, extra = {}) => ({
  id: "r1",
  agent,
  product_id: null,
  evidence_pack_id: "pack1",
  requirement_version: 1,
  status: "SUCCEEDED",
  engine: "rules-v1",
  degraded: false,
  output,
  validation: null,
  error: null,
  duration_ms: 12,
  tokens_used: 0,
  created_by_id: "u1",
  created_at: "2026-10-02T14:05:00Z",
  ...extra,
});
const runs = (...items: unknown[]) =>
  json({ items, page: { total: items.length, limit: 1, offset: 0 } });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("agent output parsing", () => {
  it("splits an answer into text and citation markers", () => {
    expect(splitCitations("RAM is 16 GB [E1][E2]. Light.")).toEqual([
      { text: "RAM is 16 GB " },
      { marker: "E1" },
      { marker: "E2" },
      { text: ". Light." },
    ]);
    expect(splitCitations("No markers")).toEqual([{ text: "No markers" }]);
    expect(splitCitations("")).toEqual([]);
  });

  it("rejects malformed output and names agents and engines plainly", () => {
    expect(parseAnswer({ answer: "x" })).toBeNull();
    expect(parseOrchestration({ products: "nope" })).toBeNull();
    expect(agentLabel("product_research")).toBe("Specifications");
    expect(agentLabel("new_agent")).toBe("new agent");
    expect(engineLabel("rules-v1")).toBe("built-in rules");
    expect(engineLabel("gpt-4.1-mini")).toBe("gpt-4.1-mini");
    expect(engineLabel(null)).toBe("");
  });
});

describe("form validation", () => {
  it("checks sources and prices before anything is sent", () => {
    const base = { title: "Spec sheet", sourceType: "SPECIFICATION_SHEET" };
    expect(textSourceSchema.safeParse({ ...base, text: "too short" }).success).toBe(false);
    expect(
      textSourceSchema.safeParse({ ...base, sourceType: "BLOG", text: "x".repeat(30) }).success,
    ).toBe(false);
    expect(urlSourceSchema.safeParse({ ...base, url: "javascript:alert(1)" }).success).toBe(false);
    expect(urlSourceSchema.safeParse({ ...base, url: "ftp://example.com/a" }).success).toBe(false);
    expect(urlSourceSchema.safeParse({ ...base, url: "https://example.com/a" }).success).toBe(true);
    expect(priceSchema.parse({ retailer: " Shop ", amount: "1299.99", currency: "usd" })).toEqual({
      retailer: "Shop",
      amount: "1299.99",
      currency: "USD",
    });
    expect(priceSchema.safeParse({ retailer: "Shop", amount: "0", currency: "USD" }).success).toBe(
      false,
    );
    expect(
      priceSchema.safeParse({ retailer: "Shop", amount: "12.999", currency: "USD" }).success,
    ).toBe(false);
  });
});

describe("/api/config", () => {
  it("returns the WebSocket address from the server's environment", async () => {
    vi.stubEnv("PUBLIC_WS_URL", "wss://api.example.com/");
    expect(await getConfig().json()).toEqual({ wsUrl: "wss://api.example.com" });
    vi.unstubAllEnvs();
    expect(await getConfig().json()).toEqual({ wsUrl: "ws://127.0.0.1:8000" });
  });
});

describe("realtime", () => {
  function Probe() {
    const { status, online } = useRealtime();
    return <p>{`${status}:${online.join(",")}`}</p>;
  }
  const routes = {
    "GET /api/config": json({ wsUrl: "ws://api.test" }),
    [`POST ${W}/realtime-ticket`]: json({ ticket: "ticket-1", expires_at: "2030-01-01T00:00:00Z" }),
  };

  it("maps each server event to the data it makes stale", () => {
    const client = newClient();
    const invalidated: unknown[] = [];
    vi.spyOn(client, "invalidateQueries").mockImplementation(async (filters) => {
      invalidated.push(filters?.queryKey);
    });
    refreshFor(client, "w1", "comment.created");
    expect(invalidated).toEqual([
      collaborationKeys.comments("w1"),
      collaborationKeys.activity("w1"),
    ]);
    invalidated.length = 0;
    refreshFor(client, "w1", "vote.changed");
    expect(invalidated[0]).toEqual(collaborationKeys.votes("w1"));
    invalidated.length = 0;
    refreshFor(client, "w1", "agent_run.completed");
    expect(invalidated).toContainEqual(["workspaces", "w1", "comparison"]);
    expect(invalidated).toContainEqual(["workspaces", "w1", "runs"]);
    invalidated.length = 0;
    refreshFor(client, "w1", "pong");
    expect(invalidated).toEqual([]);
  });

  it("authenticates with a ticket, tracks presence and refetches on events", async () => {
    FakeSocket.install();
    const calls = mockApi(routes);
    const client = newClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const view = page(
      <RealtimeProvider workspaceId="w1">
        <Probe />
      </RealtimeProvider>,
      client,
    );
    expect(screen.getByText("connecting:")).toBeInTheDocument();
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(1));
    const socket = FakeSocket.instances[0]!;
    expect(socket.url).toBe("ws://api.test/api/v1/ws/workspaces/w1");
    // The ticket goes in the first message, never in the address.
    expect(socket.url).not.toContain("ticket");
    act(() => socket.onopen?.());
    expect(socket.sent).toEqual([{ type: "auth", ticket: "ticket-1" }]);

    act(() => socket.receive({ type: "ready", presence: ["u1"], heartbeat_seconds: 20 }));
    expect(screen.getByText("live:u1")).toBeInTheDocument();
    act(() => socket.receive({ type: "presence.joined", data: { user_id: "u2" } }));
    act(() => socket.receive({ type: "presence.joined", data: { user_id: "u2" } }));
    expect(screen.getByText("live:u1,u2")).toBeInTheDocument();
    act(() => socket.receive({ type: "presence.left", data: { user_id: "u2" } }));
    expect(screen.getByText("live:u1")).toBeInTheDocument();

    act(() => socket.receive({ type: "comment.created", data: {} }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: collaborationKeys.comments("w1") });
    act(() => socket.onmessage?.({ data: "not json" })); // ignored, not thrown

    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      "GET /api/config",
      `POST ${W}/realtime-ticket`,
    ]);
    view.unmount();
    expect(socket.closed).toBe(true);
  });

  it("reconnects with a fresh ticket after a drop, but not when it was told to stay out", async () => {
    FakeSocket.install();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = mockApi(routes);
    page(
      <RealtimeProvider workspaceId="w1">
        <Probe />
      </RealtimeProvider>,
    );
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(1));
    act(() => FakeSocket.instances[0]!.receive({ type: "ready", presence: ["u1"] }));
    act(() => FakeSocket.instances[0]!.drop(1006));
    expect(screen.getByText("off:u1")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(1100));
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(2));
    expect(calls.filter((c) => c.path.endsWith("/realtime-ticket"))).toHaveLength(2);

    act(() => FakeSocket.instances[1]!.drop(4404)); // no longer a member
    expect(screen.getByText("off:")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it("stays usable, with live updates off, when no ticket can be had", async () => {
    FakeSocket.install();
    mockApi({
      "GET /api/config": json({ wsUrl: "ws://api.test" }),
      [`POST ${W}/realtime-ticket`]: apiError("workspace_not_found", "Not found.", 404),
    });
    page(
      <RealtimeProvider workspaceId="w1">
        <Probe />
      </RealtimeProvider>,
    );
    expect(await screen.findByText("off:")).toBeInTheDocument();
    expect(FakeSocket.instances).toHaveLength(0);
  });
});

describe("Discussion", () => {
  it("posts a comment about a product and lets authors delete their own", async () => {
    const calls = mockApi({
      [`GET ${W}`]: workspace("MEMBER"),
      "GET /api/v1/auth/me": me,
      [`GET ${W}/products`]: products,
      [`GET ${W}/comments`]: comments(
        comment("c1", "u1", "Mine"),
        comment("c2", "u2", "Theirs", { product_id: "p1", edited_at: "2026-10-02T15:00:00Z" }),
        comment("c3", "u2", null),
      ),
      [`POST ${W}/comments`]: json(comment("c4", "u1", "Looks right"), 201),
      [`DELETE ${W}/comments/c1`]: new Response(null, { status: 204 }),
    });
    page(<Discussion workspaceId="w1" />);
    expect(await screen.findByText("Theirs")).toBeInTheDocument();
    expect(
      screen.getByText(/on Aster Swift 14 · Oct 2, 2026, 2:05 PM UTC · edited/),
    ).toBeInTheDocument();
    expect(screen.getByText("This comment was deleted.")).toBeInTheDocument();
    // A member may delete only their own comment.
    expect(screen.getAllByRole("button", { name: /^Delete comment/ })).toHaveLength(1);

    await userEvent.click(screen.getByRole("button", { name: "Post comment" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Write something first.");

    await userEvent.type(screen.getByLabelText("Add a comment"), "  Looks right ");
    await userEvent.selectOptions(screen.getByLabelText("About"), "p1");
    await userEvent.click(screen.getByRole("button", { name: "Post comment" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({
        body: "Looks right",
        product_id: "p1",
      }),
    );

    await userEvent.click(screen.getByRole("button", { name: "Delete comment by Ada" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("lets an owner delete anyone's comment and gives viewers no form", async () => {
    mockApi({
      [`GET ${W}`]: workspace("OWNER"),
      "GET /api/v1/auth/me": me,
      [`GET ${W}/products`]: products,
      [`GET ${W}/comments`]: comments(comment("c2", "u2", "Theirs")),
    });
    const owner = page(<Discussion workspaceId="w1" />);
    expect(
      await screen.findByRole("button", { name: "Delete comment by Grace" }),
    ).toBeInTheDocument();
    owner.unmount();

    mockApi({
      [`GET ${W}`]: workspace("VIEWER"),
      "GET /api/v1/auth/me": me,
      [`GET ${W}/products`]: products,
      [`GET ${W}/comments`]: comments(),
    });
    page(<Discussion workspaceId="w1" />);
    expect(await screen.findByText("No comments yet.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Add a comment")).not.toBeInTheDocument();
  });
});

describe("VoteButtons", () => {
  it("casts a vote, and takes it back when pressed again", async () => {
    let mine = 0;
    const calls = mockApi({
      [`GET ${W}/votes`]: () =>
        json({
          items: [{ product_id: "p1", up: 2 + mine, down: 1, score: 1 + mine, my_vote: mine }],
        }),
      [`PUT ${W}/products/p1/vote`]: (call) => {
        mine = (call.body as { value: number }).value;
        return json({ product_id: "p1", up: 2 + mine, down: 1, score: 1 + mine, my_vote: mine });
      },
    });
    page(<VoteButtons workspaceId="w1" productId="p1" productName="Aster Swift 14" canVote />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Vote for Aster Swift 14, 2 so far" }),
    );
    const pressed = await screen.findByRole("button", {
      name: "Vote for Aster Swift 14, 3 so far",
    });
    expect(pressed).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(pressed);
    await screen.findByRole("button", { name: "Vote for Aster Swift 14, 2 so far" });
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([
      { value: 1 },
      { value: 0 },
    ]);
  });

  it("shows totals to viewers without letting them vote", async () => {
    mockApi({
      [`GET ${W}/votes`]: json({
        items: [{ product_id: "p1", up: 4, down: 0, score: 4, my_vote: 0 }],
      }),
    });
    page(<VoteButtons workspaceId="w1" productId="p1" productName="Aster" canVote={false} />);
    expect(await screen.findByRole("button", { name: "Vote for Aster, 4 so far" })).toBeDisabled();
  });
});

function readText(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("could not read the file"));
    reader.readAsText(file);
  });
}

describe("ProductSources", () => {
  const source = (extra = {}) => ({
    id: "s1",
    product_id: "p1",
    workspace_id: "w1",
    source_type: "SPECIFICATION_SHEET",
    authority: "USER",
    title: "Spec sheet",
    url: null,
    status: "INGESTED",
    last_error: null,
    last_ingested_at: "2026-10-02T00:00:00Z",
    created_at: "2026-10-02T00:00:00Z",
    ...extra,
  });
  const job = json({ id: "j1", status: "SUCCEEDED" });

  it("uploads pasted text privately to the workspace, then makes it searchable", async () => {
    const calls = mockApi({
      "GET /api/v1/products/p1/sources": json([
        source(),
        source({
          id: "s2",
          title: "Review",
          url: "https://example.com/r",
          workspace_id: null,
          status: "FAILED",
          last_error: "Timed out",
        }),
      ]),
      "POST /api/v1/products/p1/sources/upload": json({ source: source({ id: "s9" }) }, 201),
      "POST /api/v1/sources/s9/embed": job,
    });
    const appended = vi.spyOn(FormData.prototype, "append");
    page(<ProductSources workspaceId="w1" productId="p1" productName="Aster" editable />);
    const list = await screen.findByRole("list", { name: "Sources for Aster" });
    expect(
      within(list).getByText(/Specification sheet · Read · only this workspace/),
    ).toBeInTheDocument();
    expect(within(list).getByText(/Could not be read · shared/)).toBeInTheDocument();
    expect(within(list).getByText("Timed out")).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "Review" })).toHaveAttribute(
      "rel",
      "noreferrer noopener",
    );

    await userEvent.click(screen.getByRole("button", { name: "Add source" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Give the source a title.");

    await userEvent.type(screen.getByLabelText("Title"), "Battery test");
    await userEvent.type(screen.getByLabelText("Text"), "The battery lasts up to 12 hours.");
    await userEvent.click(screen.getByRole("button", { name: "Add source" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/embed"))).toBe(true));
    const upload = String(calls.find((c) => c.path.endsWith("/upload"))!.body);
    expect(upload).toContain('name="workspace_id"\r\n\r\nw1');
    expect(upload).toContain('name="title"\r\n\r\nBattery test');
    // The file's content is read from the form itself: how a test runtime serialises a
    // jsdom File into a multipart body differs between Node versions.
    const file = appended.mock.calls.find(([name]) => name === "file")?.[1] as File;
    expect(file.type).toBe("text/plain");
    expect(await readText(file)).toBe("The battery lasts up to 12 hours.");
  });

  it("registers a web address, has the API fetch it, then embeds it", async () => {
    const calls = mockApi({
      "GET /api/v1/products/p1/sources": json([]),
      "POST /api/v1/products/p1/sources": json(source({ id: "s3" }), 201),
      "POST /api/v1/sources/s3/ingest": apiError(
        "url_blocked",
        "That address is not allowed.",
        422,
      ),
    });
    page(<ProductSources workspaceId="w1" productId="p1" productName="Aster" editable />);
    expect(await screen.findByText(/No sources yet/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Web address", { selector: "input[type=radio]" }));
    await userEvent.type(screen.getByLabelText("Title"), "Maker page");
    await userEvent.type(
      screen.getByLabelText("Web address", { selector: "input[type=url]" }),
      "notaurl",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add source" }));
    expect(screen.getByRole("alert")).toHaveTextContent("starting with https://");
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await userEvent.clear(screen.getByLabelText("Web address", { selector: "input[type=url]" }));
    await userEvent.type(
      screen.getByLabelText("Web address", { selector: "input[type=url]" }),
      "https://example.com/swift",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add source" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That address is not allowed.");
    expect(
      calls.find((c) => c.path === "/api/v1/products/p1/sources" && c.method === "POST")!.body,
    ).toEqual({
      source_type: "SPECIFICATION_SHEET",
      title: "Maker page",
      url: "https://example.com/swift",
      authority: "THIRD_PARTY",
      workspace_id: "w1",
    });
    expect(calls.some((c) => c.path.endsWith("/embed"))).toBe(false);
  });

  it("is read-only without edit rights", async () => {
    mockApi({ "GET /api/v1/products/p1/sources": json([source()]) });
    page(<ProductSources workspaceId="w1" productId="p1" productName="Aster" editable={false} />);
    expect(await screen.findByText("Spec sheet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add source" })).not.toBeInTheDocument();
  });
});

describe("PriceHistoryView", () => {
  const point = (bucket: string, low: string) => ({ bucket, low, high: low, observations: 1 });
  const history = {
    product_id: "p1",
    currency: "USD",
    bucket: "day",
    series: [
      {
        retailer: "Example Store",
        points: [
          point("2026-09-01T00:00:00Z", "1400.00"),
          point("2026-10-01T00:00:00Z", "1299.00"),
        ],
      },
      { retailer: "Other Shop", points: [point("2026-09-15T00:00:00Z", "1350.00")] },
    ],
    stats: {
      currency: "USD",
      observations: 3,
      current: [
        {
          retailer: "Example Store",
          amount: "1299.00",
          observed_at: "2026-10-01T00:00:00Z",
          in_stock: null,
        },
        {
          retailer: "Other Shop",
          amount: "1350.00",
          observed_at: "2026-09-15T00:00:00Z",
          in_stock: null,
        },
      ],
      lowest_current: {
        retailer: "Example Store",
        amount: "1299.00",
        observed_at: "2026-10-01T00:00:00Z",
        in_stock: null,
      },
      all_time_low: "1299.00",
      all_time_high: "1400.00",
      window_days: 30,
      window_average: "1349.67",
      window_low: "1299.00",
      lowest_in_window: true,
      change_pct: -7.2,
      volatility: 0.03,
    },
  };

  it("places earlier dates to the left and lower prices lower down", () => {
    const geometry = chartGeometry(history as never)!;
    const [first, last] = geometry.lines[0]!.points;
    expect(first!.x).toBeLessThan(last!.x);
    expect(first!.y).toBeLessThan(last!.y); // 1400 is drawn above 1299
    expect(geometry.lines[1]!.points[0]!.x).toBeGreaterThan(first!.x);
    expect(chartGeometry({ ...history, series: [] } as never)).toBeNull();
    // A single observation still has a position (no division by zero).
    const single = chartGeometry({ ...history, series: [history.series[1]] } as never)!;
    expect(Number.isFinite(single.lines[0]!.points[0]!.x)).toBe(true);
    expect(Number.isFinite(single.lines[0]!.points[0]!.y)).toBe(true);
  });

  it("shows the statistics and chart, and records a new price", async () => {
    const calls = mockApi({
      "GET /api/v1/products/p1/prices": json(history),
      "POST /api/v1/products/p1/prices": json({ received: 1, inserted: 1, duplicates: 0 }, 201),
    });
    page(<PriceHistoryView productId="p1" productName="Aster" canRecord />);
    expect(
      await screen.findByRole("img", {
        name: /Price of Aster over time.*Sep 1, 2026 to Oct 1, 2026/,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Lowest now").nextElementSibling).toHaveTextContent("$1,299");
    expect(screen.getByText("30-day average").nextElementSibling).toHaveTextContent("$1,349.67");
    expect(screen.getByText(/Other Shop/)).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Shop"), "New Shop");
    await userEvent.type(screen.getByLabelText("Price today"), "12,50");
    await userEvent.click(screen.getByRole("button", { name: "Record price" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a price like 1299");
    await userEvent.clear(screen.getByLabelText("Price today"));
    await userEvent.type(screen.getByLabelText("Price today"), "1250");
    await userEvent.click(screen.getByRole("button", { name: "Record price" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const body = calls.find((c) => c.method === "POST")!.body as {
      source: string;
      observations: Array<Record<string, string>>;
    };
    expect(body.source).toBe("MANUAL");
    expect(body.observations[0]).toMatchObject({
      retailer: "New Shop",
      amount: "1250",
      currency: "USD",
    });
    expect(Date.parse(body.observations[0]!.observed_at!)).not.toBeNaN();
  });

  it("explains an empty history and hides the form from viewers", async () => {
    mockApi({
      "GET /api/v1/products/p1/prices": json({
        ...history,
        series: [],
        stats: { ...history.stats, observations: 0, current: [], lowest_current: null },
      }),
    });
    page(<PriceHistoryView productId="p1" productName="Aster" canRecord={false} />);
    expect(await screen.findByText(/No prices recorded yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record price" })).not.toBeInTheDocument();
  });
});

describe("EvidenceSearch", () => {
  const hit = {
    chunk_id: "c1",
    document_id: "d1",
    source_id: "s1",
    product_id: "p1",
    workspace_id: "w1",
    chunk_index: 0,
    char_start: 0,
    char_end: 40,
    text: "Battery life: up to 12 hours.",
    source_title: "Spec sheet",
    source_url: null,
    authority: "USER",
    source_type: "SPECIFICATION_SHEET",
    score: 0.03,
    lexical_rank: 1,
    vector_rank: 1,
    similarity: 0.8,
  };

  it("searches one product and shows each passage with its source", async () => {
    const calls = mockApi({
      [`GET ${W}/products`]: products,
      [`POST ${W}/search`]: json({
        query: "battery",
        mode: "hybrid",
        embedding_model: "hashing",
        degraded: true,
        items: [hit],
      }),
    });
    page(<EvidenceSearch workspaceId="w1" />);
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter something to search for.");

    await userEvent.type(screen.getByLabelText("Search for"), "battery");
    await userEvent.selectOptions(await screen.findByLabelText("In"), "p1");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(
      await screen.findByRole("heading", { name: "1 passage for “battery”" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Battery life: up to 12 hours.")).toBeInTheDocument();
    expect(screen.getByText(/Added by a member/)).toBeInTheDocument();
    expect(screen.getByText(/only exact words were matched/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({
      query: "battery",
      product_ids: ["p1"],
      mode: "hybrid",
    });
  });

  it("says so when nothing matches", async () => {
    mockApi({
      [`GET ${W}/products`]: products,
      [`POST ${W}/search`]: json({
        query: "zebra",
        mode: "hybrid",
        embedding_model: null,
        degraded: false,
        items: [],
      }),
    });
    page(<EvidenceSearch workspaceId="w1" />);
    await userEvent.type(screen.getByLabelText("Search for"), "zebra");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(
      await screen.findByRole("heading", { name: "Nothing found for “zebra”" }),
    ).toBeInTheDocument();
  });
});

describe("AskPanel", () => {
  const answered = {
    question: "What is the battery life?",
    answer: "Battery life is up to 12 hours [E2].",
    abstained: false,
    message: null,
    cited: ["E2"],
    dropped_sentences: ["It is the best laptop ever."],
    evidence_items: 2,
  };
  const item = (position: number, text: string) => ({
    position,
    marker: `E${position}`,
    chunk_id: `c${position}`,
    source_id: "s1",
    product_id: "p1",
    text,
    char_start: 0,
    char_end: 10,
    content_hash: "h",
    source_title: `Source ${position}`,
    source_url: position === 2 ? "https://example.com/spec" : null,
    authority: "USER",
    source_type: "SPECIFICATION_SHEET",
    score: 0.5,
  });
  const pack = json({
    id: "pack1",
    query: "q",
    mode: "hybrid",
    embedding_model: null,
    requirement_version: 1,
    degraded: false,
    created_by_id: "u1",
    created_at: "2026-10-02T00:00:00Z",
    items: [item(1, "Unrelated passage."), item(2, "Battery life: up to 12 hours.")],
  });

  it("asks, then shows the answer with only the passages it cites", async () => {
    const calls = mockApi({
      [`GET ${W}`]: workspace("MEMBER"),
      [`GET ${W}/agent-runs`]: runs(),
      [`POST ${W}/ask`]: json(run("ask", answered), 201),
      [`GET ${W}/evidence-packs/pack1`]: pack,
    });
    page(<AskPanel workspaceId="w1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Ask" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Ask a question first.");

    await userEvent.type(
      screen.getByLabelText(/Answers come only from/),
      "What is the battery life?",
    );
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(
      await screen.findByRole("heading", { name: "What is the battery life?" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Battery life is up to 12 hours")).toHaveClass("mark");
    expect(screen.getByRole("link", { name: "Source E2" })).toHaveAttribute("href", "#evidence-E2");
    expect(screen.getByText(/One sentence was removed/)).toBeInTheDocument();
    expect(await screen.findByText("Battery life: up to 12 hours.")).toBeInTheDocument();
    expect(screen.queryByText("Unrelated passage.")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Source 2" })).toHaveAttribute(
      "href",
      "https://example.com/spec",
    );
    expect(screen.getByText(/Answered by the built-in reader/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      question: "What is the battery life?",
      product_ids: [],
      limit: 8,
    });
  });

  it("says plainly when the sources do not answer, and loads no evidence", async () => {
    const calls = mockApi({
      [`GET ${W}`]: workspace("VIEWER"),
      [`GET ${W}/agent-runs`]: runs(
        run(
          "ask",
          {
            ...answered,
            answer: "",
            abstained: true,
            message: "I could not find this in the workspace's sources.",
            cited: [],
            dropped_sentences: [],
          },
          { engine: "gpt-4.1-mini", degraded: true },
        ),
      ),
    });
    page(<AskPanel workspaceId="w1" />);
    expect(
      await screen.findByText(/I could not find this in the workspace's sources/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Answered by gpt-4.1-mini \(the AI model was unavailable\)/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Viewers can read the last answer but not ask questions."),
    ).toBeInTheDocument();
    expect(calls.some((c) => c.path.includes("evidence-packs"))).toBe(false);
  });
});

describe("AgentStatus", () => {
  it("lists what each agent did and why steps were skipped", async () => {
    mockApi({
      [`GET ${W}/agent-runs`]: runs(
        run("orchestration", {
          ranking: [],
          recommended_product_id: "p1",
          products: [
            {
              product_id: "p1",
              steps: [
                {
                  agent: "product_research",
                  status: "SUCCEEDED",
                  run_id: "a",
                  duration_ms: 31,
                  engine: "rules-v1",
                  reason: null,
                },
                {
                  agent: "compatibility",
                  status: "SKIPPED",
                  run_id: null,
                  duration_ms: 0,
                  engine: null,
                  reason: "no owned devices",
                },
                {
                  agent: "risk",
                  status: "FAILED",
                  run_id: "b",
                  duration_ms: 5,
                  engine: null,
                  reason: "TimeoutError",
                },
              ],
              synthesis: {
                product_id: "p1",
                product_name: "Aster Swift 14",
                verdict: "CONSIDER",
                score: 71,
                requirement_fit: 0.8,
                blockers: [],
                concerns: ["risk: no warranty information"],
                strengths: ["Light"],
                run_ids: {},
                summary: "Meets the must-haves; warranty unknown.",
              },
            },
          ],
          products_skipped: ["p9"],
          time_budget_exhausted: true,
          token_budget_exhausted: false,
          tokens_used: 0,
          elapsed_ms: 1234,
        }),
      ),
    });
    page(<AgentStatus workspaceId="w1" />);
    const article = await screen.findByRole("article", { name: "Aster Swift 14" });
    expect(within(article).getByText(/Worth considering/)).toBeInTheDocument();
    expect(
      within(article).getByText("Meets the must-haves; warranty unknown."),
    ).toBeInTheDocument();
    expect(
      within(article).getByText("Warranty and risk: no warranty information"),
    ).toBeInTheDocument();
    const steps = within(article)
      .getAllByRole("listitem")
      .map((li) => li.textContent);
    expect(steps).toEqual([
      "SpecificationsDone · built-in rules · 31 ms",
      "CompatibilitySkipped · no owned devices",
      "Warranty and riskFailed · TimeoutError",
    ]);
    expect(
      screen.getByText(/1\.2 seconds\. It ran out of time.*1 more products were over/),
    ).toBeInTheDocument();
  });

  it("renders nothing before any research has run", async () => {
    const calls = mockApi({ [`GET ${W}/agent-runs`]: runs() });
    const { container } = page(<AgentStatus workspaceId="w1" />);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(container).toBeEmptyDOMElement();
  });
});
