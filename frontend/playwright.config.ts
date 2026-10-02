import { defineConfig, devices } from "@playwright/test";

/*
 * End-to-end tests drive a real browser against the real stack. Start the API first
 * (`make up` in the repo root); this config builds nothing, it starts the already
 * built web app (`npm run build`) unless one is running on the port.
 */
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { baseURL, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run start",
    url: `${baseURL}/login`,
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
