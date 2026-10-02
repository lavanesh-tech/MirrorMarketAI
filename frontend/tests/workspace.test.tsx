import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ComparisonPanel } from "@/components/comparison-panel";
import { ProductsPanel } from "@/components/products-panel";
import { RequirementsPanel } from "@/components/requirements-panel";
import { WorkspaceShell } from "@/components/workspace-shell";
import { parseComparison, standing, weightChanges } from "@/lib/comparison";
import {
  budgetText,
  canEdit,
  canRun,
  criterionLabel,
  criterionTarget,
  formatMoney,
  trimNumber,
} from "@/lib/requirements";
import { productSchema, specificationSchema } from "@/lib/validation";

import { apiError, json, mockApi } from "./helpers";

let pathname = "/workspaces/w1";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn(), push: vi.fn() }),
  usePathname: () => pathname,
}));

function page(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

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
const noRequirements = apiError("requirement_not_found", "No requirements yet.", 404);

const ram = {
  key: "ram_gb",
  operator: ">=",
  value_number: "16.000000",
  value_text: null,
  unit: "GB",
  priority: "MUST",
  weight: 3,
};
const weight = {
  key: "weight_kg",
  operator: "<=",
  value_number: "1.400000",
  value_text: null,
  unit: "kg",
  priority: "SHOULD",
  weight: 3,
};
const spec = {
  category: "laptop",
  budget: { currency: "USD", max_amount: "1500.00", min_amount: null },
  criteria: [ram, weight],
  excluded_brands: [],
  use_cases: ["travel"],
  owned_devices: [],
  notes: null,
};
const requirements = (version = 1) =>
  json({
    workspace_id: "w1",
    current_version: version,
    degraded: false,
    current: {
      id: "v1",
      version,
      raw_text: "A laptop under $1,500",
      spec,
      unparsed: [],
      extractor: "rules",
      change_note: null,
      created_by_id: "u1",
      created_at: "2026-10-01T00:00:00Z",
    },
  });

const product = (id: string, brand: string, name: string) => ({
  id,
  brand,
  name,
  category: "laptop",
  created_at: "2026-10-01T00:00:00Z",
});
const inWorkspace = (...items: ReturnType<typeof product>[]) =>
  json(
    items.map((item) => ({
      product: item,
      variant_id: null,
      notes: null,
      added_by_id: "u1",
      added_at: "2026-10-01T00:00:00Z",
    })),
  );
const aster = product("p1", "Aster", "Swift 14");
const cinder = product("p2", "Cinder", "Lite 13");

const cell = (
  key: string,
  status: string,
  value: string | null,
  contribution: number,
  citations: string[] = [],
) => ({
  key,
  status,
  value,
  utility: 1,
  contribution,
  citations,
});
const comparisonOutput = {
  criteria: [
    { key: "ram_gb", label: "Memory", weight: 5, hard: true },
    { key: "weight_kg", label: "Weight", weight: 3, hard: false },
    { key: "price", label: "Price", weight: 3, hard: true },
  ],
  products: [
    {
      product_id: "p1",
      name: "Aster Swift 14",
      eligible: true,
      violations: [],
      unknown_hard: [],
      score: 86.4,
      rank: 1,
      cells: [
        cell("ram_gb", "MET", "16", 40, ["E1"]),
        cell("weight_kg", "MET", "1.2", 25),
        cell("price", "MET", "1299", 21.4),
      ],
    },
    {
      product_id: "p2",
      name: "Cinder Lite 13",
      eligible: false,
      violations: ["ram_gb"],
      unknown_hard: [],
      score: 52,
      rank: 2,
      cells: [
        cell("ram_gb", "UNMET", "8", 0),
        cell("weight_kg", "UNKNOWN", null, 0),
        cell("price", "MET", "899", 27),
      ],
    },
  ],
  winner_product_id: "p1",
  margin: null,
  sensitivity: { stable: false, winner_changes_with: ["weight_kg x2"] },
};
const run = (id: string, requirement_version = 1, output: unknown = comparisonOutput) => ({
  id,
  agent: "comparison",
  product_id: null,
  evidence_pack_id: null,
  requirement_version,
  status: "SUCCEEDED",
  engine: "rules",
  degraded: false,
  output,
  validation: null,
  error: null,
  duration_ms: 12,
  tokens_used: 0,
  created_by_id: "u1",
  created_at: "2026-10-02T00:00:00Z",
});
const runs = (...items: ReturnType<typeof run>[]) =>
  json({ items, page: { total: items.length, limit: 1, offset: 0 } });

beforeEach(() => {
  pathname = "/workspaces/w1";
});
afterEach(() => vi.unstubAllGlobals());

describe("requirement wording", () => {
  it("labels criteria and targets the way a buyer would say them", () => {
    expect(criterionLabel("ram_gb")).toBe("Memory");
    expect(criterionLabel("has_backlit_keyboard")).toBe("Backlit keyboard");
    expect(criterionTarget(ram as never)).toBe("at least 16 GB");
    expect(criterionTarget(weight as never)).toBe("at most 1.4 kg");
    const feature = { ...ram, key: "has_touchscreen", value_number: null, unit: null };
    expect(criterionTarget({ ...feature, operator: "=", value_text: "no" } as never)).toBe("No");
    expect(criterionTarget({ ...feature, operator: "!=", value_text: "no" } as never)).toBe("Yes");
    expect(criterionTarget({ ...feature, operator: "!=", value_text: "Dell" } as never)).toBe(
      "not Dell",
    );
  });

  it("formats numbers and money without losing or inventing digits", () => {
    expect(trimNumber("16.000000")).toBe("16");
    expect(trimNumber("1.400000")).toBe("1.4");
    expect(trimNumber("100")).toBe("100");
    expect(trimNumber("OLED")).toBe("OLED");
    expect(formatMoney("1299", "USD")).toBe("$1,299");
    expect(formatMoney("1299.5", "USD")).toBe("$1,299.50");
    expect(formatMoney("abc", "USD")).toBe("abc USD");
    expect(budgetText({ currency: "USD", max_amount: "1500.00" })).toBe("Up to $1,500");
    expect(budgetText({ currency: "USD", min_amount: "500", max_amount: "1500" })).toBe(
      "$500 to $1,500",
    );
    expect(budgetText({ currency: "USD", min_amount: "500" })).toBe("At least $500");
  });

  it("matches the API's role rules", () => {
    expect(["OWNER", "EDITOR", "MEMBER", "VIEWER"].map((r) => canEdit(r as never))).toEqual([
      true,
      true,
      false,
      false,
    ]);
    expect(["OWNER", "EDITOR", "MEMBER", "VIEWER"].map((r) => canRun(r as never))).toEqual([
      true,
      true,
      true,
      false,
    ]);
  });
});

describe("form validation", () => {
  it("stores a numeric specification as a number and anything else as text", () => {
    expect(specificationSchema.parse({ key: "ram_gb", value: " 16 ", unit: "GB" })).toEqual({
      key: "ram_gb",
      unit: "GB",
      value_number: "16",
    });
    expect(specificationSchema.parse({ key: "panel", value: "OLED", unit: "" })).toEqual({
      key: "panel",
      unit: null,
      value_text: "OLED",
    });
    expect(specificationSchema.safeParse({ key: "RAM GB", value: "16", unit: "" }).success).toBe(
      false,
    );
  });

  it("requires a brand, a name and a known category", () => {
    expect(productSchema.safeParse({ brand: "", name: "", category: "" }).success).toBe(false);
    expect(productSchema.safeParse({ brand: "A", name: "B", category: "spaceship" }).success).toBe(
      false,
    );
    expect(productSchema.parse({ brand: " A ", name: "B", category: "laptop" }).brand).toBe("A");
  });
});

describe("comparison parsing", () => {
  it("rejects output that is not a comparison", () => {
    expect(parseComparison(null)).toBeNull();
    expect(parseComparison({ products: [] })).toBeNull();
    expect(parseComparison({ ...comparisonOutput, products: [{ name: 1 }] })).toBeNull();
  });

  it("explains where each product stands and what would change the winner", () => {
    const comparison = parseComparison(comparisonOutput)!;
    expect(standing(comparison.products[0]!, comparison)).toBe("Best match");
    expect(standing(comparison.products[1]!, comparison)).toBe("Ruled out: misses Memory");
    const unsure = { ...comparison.products[0]!, product_id: "p9", unknown_hard: ["price"] };
    expect(standing(unsure, comparison)).toBe("Can't confirm: Price");
    expect(standing({ ...unsure, unknown_hard: [] }, comparison)).toBe("Meets the must-haves");
    expect(weightChanges(comparison)).toEqual(["double the importance of Weight"]);
    expect(
      weightChanges({
        ...comparison,
        sensitivity: { stable: false, winner_changes_with: ["price x0.5"] },
      }),
    ).toEqual(["halve the importance of Price"]);
  });
});

describe("WorkspaceShell", () => {
  it("marks the current section and links to the others", async () => {
    pathname = "/workspaces/w1/products";
    mockApi({ [`GET ${W}`]: workspace("EDITOR") });
    page(
      <WorkspaceShell workspaceId="w1">
        <p>content</p>
      </WorkspaceShell>,
    );
    const nav = await screen.findByRole("navigation", { name: "Workspace sections" });
    expect(within(nav).getByRole("link", { name: "Products" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Compare" })).toHaveAttribute(
      "href",
      "/workspaces/w1/compare",
    );
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
    expect(screen.getByText("You are editor here.")).toBeInTheDocument();
    expect(screen.getByText("content")).toBeInTheDocument();
  });

  it("explains a workspace the visitor cannot see, without rendering the page", async () => {
    mockApi({ [`GET ${W}`]: apiError("workspace_not_found", "Not found.", 404) });
    page(
      <WorkspaceShell workspaceId="w1">
        <p>content</p>
      </WorkspaceShell>,
    );
    expect(await screen.findByRole("heading", { name: "Workspace not found" })).toBeInTheDocument();
    expect(screen.queryByText("content")).not.toBeInTheDocument();
  });
});

describe("RequirementsPanel", () => {
  it("reads a brief, lets the buyer adjust the draft and saves it as version 1", async () => {
    const calls = mockApi({
      [`GET ${W}`]: workspace(),
      [`GET ${W}/requirements`]: noRequirements,
      [`POST ${W}/requirements/extract`]: json({
        spec,
        unparsed: ["I love purple unicorns."],
        extractor: "rules",
        degraded: false,
      }),
      [`PUT ${W}/requirements`]: () => requirements(1),
      [`GET ${W}/requirements/versions`]: json({
        items: [],
        page: { total: 0, limit: 20, offset: 0 },
      }),
    });
    page(<RequirementsPanel workspaceId="w1" />);
    await userEvent.type(await screen.findByLabelText(/Write it the way/), "A laptop under $1,500");
    await userEvent.click(screen.getByRole("button", { name: "Read my brief" }));

    expect(
      await screen.findByRole("heading", { name: "Draft, not saved yet" }),
    ).toBeInTheDocument();
    expect(screen.getByText("I love purple unicorns.")).toBeInTheDocument();
    expect(screen.getByText("Up to $1,500")).toBeInTheDocument();
    // A must-have has no importance: it rules products out instead of scoring.
    expect(screen.queryByLabelText("Importance of Memory")).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Priority of Weight"), "MUST");
    await userEvent.selectOptions(screen.getByLabelText("Priority of Memory"), "SHOULD");
    await userEvent.selectOptions(screen.getByLabelText("Importance of Memory"), "5");
    await userEvent.click(screen.getByRole("button", { name: "Save requirements" }));

    expect(await screen.findByText("Saved as version 1.")).toBeInTheDocument();
    const saved = calls.find((c) => c.method === "PUT")!.body as {
      expected_version: number;
      text: string;
      spec: { criteria: Array<{ key: string; priority: string; weight: number }> };
    };
    expect(saved.expected_version).toBe(0);
    expect(saved.text).toBe("A laptop under $1,500");
    expect(saved.spec.criteria.map((c) => [c.key, c.priority, c.weight])).toEqual([
      ["ram_gb", "SHOULD", 5],
      ["weight_kg", "MUST", 3],
    ]);
    expect(screen.queryByRole("heading", { name: "Draft, not saved yet" })).not.toBeInTheDocument();
  });

  it("does not call the server for an empty brief", async () => {
    const calls = mockApi({ [`GET ${W}`]: workspace(), [`GET ${W}/requirements`]: noRequirements });
    page(<RequirementsPanel workspaceId="w1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Read my brief" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Describe what you need first.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("keeps the draft and shows the newer version when someone else saved first", async () => {
    let version = 1;
    const calls = mockApi({
      [`GET ${W}`]: workspace(),
      [`GET ${W}/requirements`]: () => requirements(version),
      [`GET ${W}/requirements/versions`]: json({
        items: [],
        page: { total: 0, limit: 20, offset: 0 },
      }),
      [`PUT ${W}/requirements`]: () => {
        version = 2;
        return apiError("requirement_version_conflict", "Current version is 2.", 409);
      },
    });
    page(<RequirementsPanel workspaceId="w1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Change priorities" }));
    await userEvent.click(screen.getByLabelText("Remove Weight"));
    await userEvent.click(screen.getByRole("button", { name: "Save requirements" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Someone saved a newer version");
    expect(await screen.findByText(/Version 2, saved/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Draft, not saved yet" })).toBeInTheDocument();
    const put = calls.find((c) => c.method === "PUT")!.body as {
      expected_version: number;
      spec: { criteria: unknown[] };
    };
    expect(put.expected_version).toBe(1);
    expect(put.spec.criteria).toHaveLength(1);
  });

  it("says when a save changed nothing", async () => {
    mockApi({
      [`GET ${W}`]: workspace(),
      [`GET ${W}/requirements`]: requirements(3),
      [`GET ${W}/requirements/versions`]: json({
        items: [],
        page: { total: 0, limit: 20, offset: 0 },
      }),
      [`PUT ${W}/requirements`]: requirements(3),
    });
    page(<RequirementsPanel workspaceId="w1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Change priorities" }));
    await userEvent.click(screen.getByRole("button", { name: "Save requirements" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Nothing changed");
  });

  it("is read-only for members who cannot edit", async () => {
    mockApi({
      [`GET ${W}`]: workspace("MEMBER"),
      [`GET ${W}/requirements`]: requirements(2),
      [`GET ${W}/requirements/versions`]: json({
        items: [
          {
            version: 2,
            extractor: "manual",
            change_note: "Raised the budget",
            created_by_id: "u1",
            created_at: "2026-10-02T00:00:00Z",
          },
          {
            version: 1,
            extractor: "rules",
            change_note: null,
            created_by_id: "u1",
            created_at: "2026-10-01T00:00:00Z",
          },
        ],
        page: { total: 2, limit: 20, offset: 0 },
      }),
    });
    page(<RequirementsPanel workspaceId="w1" />);
    expect(await screen.findByText("at least 16 GB")).toBeInTheDocument();
    expect(screen.getByText("Must have")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Read my brief" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change priorities" })).not.toBeInTheDocument();
    expect(await screen.findByText(/Raised the budget/)).toBeInTheDocument();
  });
});

describe("ProductsPanel", () => {
  const detail = (created_by_id: string, specifications: unknown[] = []) =>
    json({
      ...aster,
      description: null,
      created_by_id,
      variants: [],
      identifiers: [],
      specifications,
    });

  it("creates a product, adds it to the workspace and shows it", async () => {
    let added = false;
    const calls = mockApi({
      [`GET ${W}`]: workspace(),
      [`GET ${W}/requirements`]: noRequirements,
      [`GET ${W}/products`]: () => (added ? inWorkspace(aster) : inWorkspace()),
      "POST /api/v1/products": () =>
        json(
          {
            ...aster,
            description: null,
            created_by_id: "u1",
            variants: [],
            identifiers: [],
            specifications: [],
          },
          201,
        ),
      [`POST ${W}/products`]: () => {
        added = true;
        return json(
          {
            product: aster,
            variant_id: null,
            notes: null,
            added_by_id: "u1",
            added_at: "2026-10-01T00:00:00Z",
          },
          201,
        );
      },
    });
    page(<ProductsPanel workspaceId="w1" />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Add to catalog and workspace" }),
    );
    expect(screen.getByText("Enter the brand.")).toBeInTheDocument();
    expect(screen.getByText("Choose a category.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await userEvent.type(screen.getByLabelText("Brand"), "Aster");
    await userEvent.type(screen.getByLabelText("Product name"), "Swift 14");
    await userEvent.selectOptions(screen.getByLabelText("Category"), "laptop");
    await userEvent.click(screen.getByRole("button", { name: "Add to catalog and workspace" }));

    expect(await screen.findByRole("heading", { name: "Aster Swift 14" })).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/v1/products" && c.method === "POST")!.body).toEqual({
      brand: "Aster",
      name: "Swift 14",
      category: "laptop",
    });
    expect(calls.find((c) => c.path === `${W}/products` && c.method === "POST")!.body).toEqual({
      product_id: "p1",
    });
  });

  it("points to the search when the product already exists in the catalog", async () => {
    mockApi({
      [`GET ${W}`]: workspace(),
      [`GET ${W}/requirements`]: noRequirements,
      [`GET ${W}/products`]: inWorkspace(),
      "POST /api/v1/products": apiError("product_already_exists", "Exists.", 409),
    });
    page(<ProductsPanel workspaceId="w1" />);
    await userEvent.type(await screen.findByLabelText("Brand"), "Aster");
    await userEvent.type(screen.getByLabelText("Product name"), "Swift 14");
    await userEvent.selectOptions(screen.getByLabelText("Category"), "laptop");
    await userEvent.click(screen.getByRole("button", { name: "Add to catalog and workspace" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already in the catalog");
  });

  it("searches the catalog, marks products already added and adds the others", async () => {
    const calls = mockApi({
      [`GET ${W}`]: workspace(),
      [`GET ${W}/requirements`]: noRequirements,
      [`GET ${W}/products`]: inWorkspace(aster),
      "GET /api/v1/products": json({
        items: [aster, cinder],
        page: { total: 2, limit: 10, offset: 0 },
      }),
      [`POST ${W}/products`]: json(
        {
          product: cinder,
          variant_id: null,
          notes: null,
          added_by_id: "u1",
          added_at: "2026-10-01T00:00:00Z",
        },
        201,
      ),
    });
    page(<ProductsPanel workspaceId="w1" />);
    await userEvent.type(await screen.findByRole("searchbox"), "lite");
    await userEvent.click(screen.getByRole("button", { name: "Search the catalog" }));
    const results = await screen.findByRole("list", { name: "Search results" });
    expect(within(results).getByText("Added")).toBeInTheDocument();
    expect(calls.find((c) => c.path.startsWith("/api/v1/products?"))!.path).toContain("q=lite");
    await userEvent.click(within(results).getByRole("button", { name: "Add Cinder Lite 13" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({ product_id: "p2" }),
    );
  });

  it("removes a product and shows specifications with what the requirements still need", async () => {
    const calls = mockApi({
      [`GET ${W}`]: workspace(),
      "GET /api/v1/auth/me": me,
      [`GET ${W}/requirements`]: requirements(),
      [`GET ${W}/products`]: inWorkspace(aster),
      "GET /api/v1/products/p1": detail("u1", [
        {
          key: "ram_gb",
          value_number: "16.000000",
          value_text: null,
          unit: "GB",
          variant_id: null,
        },
        {
          key: "price",
          value_number: "1299.000000",
          value_text: null,
          unit: "usd",
          variant_id: null,
        },
      ]),
      "PUT /api/v1/products/p1/specifications": detail("u1", []),
      [`DELETE ${W}/products/p1`]: new Response(null, { status: 204 }),
    });
    page(<ProductsPanel workspaceId="w1" />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Specifications of Aster Swift 14" }),
    );
    const table = await screen.findByRole("table", { name: "Specifications of Aster Swift 14" });
    expect(within(table).getByText("16 GB")).toBeInTheDocument();
    expect(within(table).getByText("$1,299")).toBeInTheDocument();
    expect(screen.getByText("Missing for your requirements: Weight.")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Specification"), "weight_kg");
    await userEvent.type(screen.getByLabelText("Value"), "1.2");
    await userEvent.type(screen.getByLabelText("Unit"), "kg");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
        specifications: [{ key: "weight_kg", unit: "kg", value_number: "1.2" }],
      }),
    );

    await userEvent.click(screen.getByRole("button", { name: "Remove Aster Swift 14" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("lets only the product's creator edit its specifications", async () => {
    mockApi({
      [`GET ${W}`]: workspace("MEMBER"),
      "GET /api/v1/auth/me": me,
      [`GET ${W}/requirements`]: noRequirements,
      [`GET ${W}/products`]: inWorkspace(aster),
      "GET /api/v1/products/p1": detail("someone-else"),
    });
    page(<ProductsPanel workspaceId="w1" />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Specifications of Aster Swift 14" }),
    );
    expect(await screen.findByText(/Only the person who added this product/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Specification")).not.toBeInTheDocument();
    // A member cannot change the product list either.
    expect(screen.queryByRole("button", { name: "Remove Aster Swift 14" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Add a product" })).not.toBeInTheDocument();
  });
});

describe("ComparisonPanel", () => {
  const agentRuns = `GET ${W}/agent-runs`;

  it("says what is missing before a comparison is possible", async () => {
    mockApi({
      [`GET ${W}`]: workspace(),
      [`GET ${W}/requirements`]: requirements(),
      [`GET ${W}/products`]: inWorkspace(),
      [agentRuns]: runs(),
    });
    page(<ComparisonPanel workspaceId="w1" />);
    expect(
      await screen.findByRole("heading", { name: "Not ready to compare yet" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/with a budget or at least one criterion \(done\)/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Research and compare" })).not.toBeInTheDocument();
  });

  it("researches first, then compares, and renders the matrix", async () => {
    const calls = mockApi({
      [`GET ${W}`]: workspace(),
      [`GET ${W}/requirements`]: requirements(),
      [`GET ${W}/products`]: inWorkspace(aster, cinder),
      [agentRuns]: runs(),
      [`POST ${W}/analyze`]: json({ ...run("a1"), agent: "orchestration" }, 201),
      [`POST ${W}/compare`]: json(run("c1"), 201),
    });
    page(<ComparisonPanel workspaceId="w1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Research and compare" }));

    const matrix = await screen.findByRole("region", { name: "Comparison matrix" });
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([
      `${W}/analyze`,
      `${W}/compare`,
    ]);
    expect(
      screen.getByRole("heading", { name: "Aster Swift 14 is the best match" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/the only product that meets the must-haves/)).toBeInTheDocument();
    expect(screen.getByText(/if you double the importance of Weight/)).toBeInTheDocument();

    const columns = within(matrix).getAllByRole("columnheader");
    expect(columns[1]).toHaveTextContent("Best match");
    expect(columns[2]).toHaveTextContent("Ruled out: misses Memory");
    const memory = within(matrix).getByRole("row", { name: /^Memory/ });
    expect(within(memory).getByText("16 GB")).toHaveClass("mark"); // cited value
    expect(within(memory).getByText("8 GB")).not.toHaveClass("mark");
    expect(within(memory).getByText("Misses")).toBeInTheDocument();
    expect(within(matrix).getByText("$1,299")).toBeInTheDocument();
    expect(within(matrix).getByText("Not found")).toBeInTheDocument();
  });

  it("rescores with changed importance without researching again", async () => {
    const calls = mockApi({
      [`GET ${W}`]: workspace("MEMBER"),
      [`GET ${W}/requirements`]: requirements(),
      [`GET ${W}/products`]: inWorkspace(aster, cinder),
      [agentRuns]: runs(run("c1")),
      [`POST ${W}/compare`]: json(run("c2"), 201),
    });
    page(<ComparisonPanel workspaceId="w1" />);
    const memory = await screen.findByLabelText("Memory");
    expect(memory).toHaveValue(5);
    await userEvent.clear(memory);
    await userEvent.type(memory, "99");
    await userEvent.click(
      screen.getByRole("checkbox", { name: /Treat the budget as a must-have/ }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Rescore with these settings" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([`${W}/compare`]);
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      weights: { ram_gb: 10, weight_kg: 3, price: 3 }, // clamped to the API's maximum
      budget_is_hard: false,
      product_ids: [],
    });
  });

  it("flags a comparison made against older requirements and keeps viewers read-only", async () => {
    mockApi({
      [`GET ${W}`]: workspace("VIEWER"),
      [`GET ${W}/requirements`]: requirements(2),
      [`GET ${W}/products`]: inWorkspace(aster, cinder),
      [agentRuns]: runs(run("c1", 1)),
    });
    page(<ComparisonPanel workspaceId="w1" />);
    expect(await screen.findByRole("status")).toHaveTextContent("it used version 1");
    expect(screen.getByText("Viewers can read comparisons but not run them.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Research and compare" })).not.toBeInTheDocument();
  });

  it("reports a failed run and treats unreadable output as no comparison", async () => {
    mockApi({
      [`GET ${W}`]: workspace(),
      [`GET ${W}/requirements`]: requirements(),
      [`GET ${W}/products`]: inWorkspace(aster),
      [agentRuns]: runs(run("c1", 1, { unexpected: true })),
      [`POST ${W}/analyze`]: apiError("rate_limited", "Too many requests. Try again shortly.", 429),
    });
    page(<ComparisonPanel workspaceId="w1" />);
    expect(await screen.findByRole("heading", { name: "No comparison yet" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Research and compare" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests");
  });
});
