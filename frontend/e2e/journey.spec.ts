import { expect, test, type Page } from "@playwright/test";

/*
 * One buyer's whole journey through a real browser, the Next.js server and the API:
 * account -> workspace -> requirements -> products with evidence -> comparison ->
 * grounded answers -> collaboration. It uses the offline engines' behaviour only in
 * ways that also hold with a language model (values come from the sources given here).
 */
const PASSWORD = "correct-horse-battery";
const SPEC_SHEET =
  "Aster Swift 14 specification sheet. Memory: 16 GB RAM. Weight: 1.2 kg. " +
  "Battery life: up to 12 hours of video playback. The display is a 14 inch panel.";

function section(page: Page, name: string) {
  return page
    .getByRole("navigation", { name: "Workspace sections" })
    .getByRole("link", { name, exact: true });
}

test("a buyer goes from a brief to a cited answer", async ({ page, browser }) => {
  const stamp = Date.now();
  const email = `e2e-${stamp}@example.com`;
  const aster = `Aster Swift 14 ${stamp}`;
  const cinder = `Cinder Lite 13 ${stamp}`;
  const consoleErrors: string[] = [];
  page.on("pageerror", (error) => consoleErrors.push(String(error)));

  await test.step("create an account and a workspace", async () => {
    await page.goto("/workspaces");
    await expect(page).toHaveURL(/\/login\?next=/);
    await page.getByRole("link", { name: "Create an account" }).click();
    await page.getByLabel("Your name").fill("Ada Lovelace");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();
    await page.getByLabel("New workspace").fill("Laptop for college");
    await page.getByRole("button", { name: "Create workspace" }).click();
    await page.getByRole("link", { name: "Laptop for college" }).click();
    await expect(page.getByText("Live. Here now: Ada Lovelace.")).toBeVisible();
    // The session lives in cookies page scripts cannot read.
    expect(await page.evaluate(() => document.cookie)).toBe("");
  });

  await test.step("write requirements", async () => {
    await section(page, "Requirements").click();
    await page
      .getByLabel(/Write it the way/)
      .fill(
        "Need a laptop under $1,500 for travel. Must have at least 16 GB RAM, ideally under 1.4 kg.",
      );
    await page.getByRole("button", { name: "Read my brief" }).click();
    await expect(page.getByRole("heading", { name: "Draft, not saved yet" })).toBeVisible();
    await page.getByRole("button", { name: "Save requirements" }).click();
    await expect(page.getByText("Saved as version 1.")).toBeVisible();
  });

  await test.step("add products, a source, a specification and a price", async () => {
    await section(page, "Products").click();
    for (const [brand, name] of [
      ["Aster", `Swift 14 ${stamp}`],
      ["Cinder", `Lite 13 ${stamp}`],
    ] as const) {
      await page.getByLabel("Brand").fill(brand);
      await page.getByLabel("Product name").fill(name);
      await page.getByLabel("Category").selectOption("laptop");
      await page.getByRole("button", { name: "Add to catalog and workspace" }).click();
      await expect(page.getByRole("heading", { name: `${brand} ${name}` })).toBeVisible();
    }

    await page.getByRole("button", { name: `Sources of ${aster}` }).click();
    await page.getByLabel("Title").fill("Aster spec sheet");
    await page.getByLabel("Text", { exact: true }).fill(SPEC_SHEET);
    await page.getByRole("button", { name: "Add source" }).click();
    const sources = page.getByRole("list", { name: `Sources for ${aster}` });
    await expect(sources.getByText("Aster spec sheet")).toBeVisible();
    await expect(sources.getByText(/· Read ·/)).toBeVisible();

    await page.getByRole("button", { name: `Prices of ${aster}` }).click();
    await page.getByLabel("Shop").fill("Example Store");
    await page.getByLabel("Price today").fill("1299");
    await page.getByRole("button", { name: "Record price" }).click();
    await expect(page.getByRole("img", { name: new RegExp(`Price of ${aster}`) })).toBeVisible();
    await expect(page.getByText("Lowest now")).toBeVisible();

    await page.getByRole("button", { name: `Specifications of ${cinder}` }).click();
    await page.getByLabel("Specification", { exact: true }).fill("ram_gb");
    await page.getByLabel("Value").fill("8");
    await page.getByLabel("Unit").fill("GB");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(
      page.getByRole("table", { name: `Specifications of ${cinder}` }).getByText("8 GB"),
    ).toBeVisible();
  });

  await test.step("search the evidence", async () => {
    await section(page, "Evidence").click();
    await page.getByLabel("Search for").fill("battery life");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.getByRole("heading", { name: /passages? for “battery life”/ })).toBeVisible();
    await expect(page.getByText(/up to 12 hours of video playback/)).toBeVisible();
  });

  await test.step("research and compare", async () => {
    await section(page, "Compare").click();
    await page.getByRole("button", { name: "Research and compare" }).click();
    const matrix = page.getByRole("region", { name: "Comparison matrix" });
    await expect(matrix).toBeVisible({ timeout: 90_000 });
    await expect(page.getByRole("heading", { name: `${aster} is the best match` })).toBeVisible();
    await expect(matrix.getByText("Ruled out: misses Memory")).toBeVisible();
    // The value read from the spec sheet carries a citation, so it is highlighted.
    await expect(matrix.locator(".mark", { hasText: "16 GB" })).toBeVisible();
    await expect(matrix.getByText("$1,299")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "How the products were researched" }),
    ).toBeVisible();
    await expect(
      page.getByRole("article", { name: aster }).getByText("Done").first(),
    ).toBeVisible();
  });

  await test.step("ask grounded questions", async () => {
    await section(page, "Ask").click();
    await page.getByLabel(/Answers come only from/).fill("What is the battery life?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByRole("heading", { name: "What is the battery life?" })).toBeVisible();
    await expect(page.getByRole("link", { name: /^Source E\d+$/ }).first()).toBeVisible();
    await expect(
      page
        .getByRole("listitem")
        .filter({ hasText: "Aster spec sheet" })
        .getByText(/12 hours/),
    ).toBeVisible();

    await page.getByLabel(/Answers come only from/).fill("Does it include a zebra saddle?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Does it include a zebra saddle?" }),
    ).toBeVisible();
    await expect(page.getByText(/Add a source that covers it/)).toBeVisible();
  });

  await test.step("vote, comment and see it live in a second window", async () => {
    await section(page, "Products").click();
    await page.getByRole("button", { name: new RegExp(`^Vote for ${aster}`) }).click();
    await expect(page.getByRole("button", { name: `Vote for ${aster}, 1 so far` })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await section(page, "Overview").click();
    await expect(page.getByRole("heading", { name: "Discussion" })).toBeVisible();
    const second = await browser.newContext({ storageState: await page.context().storageState() });
    const other = await second.newPage();
    await other.goto(page.url());
    await expect(other.getByText(/^Live\. Here now:/)).toBeVisible();
    await expect(other.getByText("No comments yet.")).toBeVisible();

    await page.getByLabel("Add a comment").fill("The Aster looks right to me.");
    await page.getByRole("button", { name: "Post comment" }).click();
    await expect(page.getByText("The Aster looks right to me.")).toBeVisible();
    // No reload in the other window: the WebSocket event makes it refetch.
    await expect(other.getByText("The Aster looks right to me.")).toBeVisible();
    await second.close();
  });

  await test.step("log out", async () => {
    await page.getByRole("button", { name: "Log out" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.goto("/workspaces");
    await expect(page).toHaveURL(/\/login\?next=/);
  });

  expect(consoleErrors).toEqual([]);
});
