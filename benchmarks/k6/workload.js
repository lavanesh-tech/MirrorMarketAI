// MirrorMarket load test: a weighted mix of the requests a workspace really makes.
//
//   PROFILE=smoke     30 s at 5 requests/s      does everything work under light load?
//   PROFILE=load      2 min at RATE requests/s  latency at one steady rate
//   PROFILE=capacity  STEPS requests/s, one     where does it stop keeping up?
//                     after the other
//
// The arrival rate is fixed by the test, not by how fast the server answers (an "open"
// model). A closed loop of virtual users slows down when the server does, and then
// reports better latency than real users would see. Requests the load generator could
// not start in time are counted as "dropped", never silently skipped.
import http from "k6/http";
import exec from "k6/execution";
import { BASE, QUESTIONS, RESULTS_DIR, SEARCHES, authHeaders, ok, seedUser } from "./lib.js";

const PROFILE = __ENV.PROFILE || "smoke";
const USERS = Number(__ENV.USERS || (PROFILE === "smoke" ? 2 : 10));
const PRODUCTS = Number(__ENV.PRODUCTS || 3);
const RATE = Number(__ENV.RATE || 30);
const STEPS = (__ENV.STEPS || "25,50,100,150,200,300").split(",").map(Number);
const STEP_SECONDS = Number(__ENV.STEP_SECONDS || 30);
const GAP_SECONDS = 10; // lets requests of an overloaded step finish before the next

// name -> [weight, request]. Weights are shares of all requests (they sum to 100).
const OPERATIONS = {
  workspace_get: [14, (c) => http.get(`${BASE}/workspaces/${c.workspaceId}`, c.params("workspace_get"))],
  workspace_products: [
    10,
    (c) => http.get(`${BASE}/workspaces/${c.workspaceId}/products`, c.params("workspace_products")),
  ],
  product_get: [10, (c) => http.get(`${BASE}/products/${c.product()}`, c.params("product_get"))],
  comments_list: [
    8,
    (c) => http.get(`${BASE}/workspaces/${c.workspaceId}/comments`, c.params("comments_list")),
  ],
  votes_get: [5, (c) => http.get(`${BASE}/workspaces/${c.workspaceId}/votes`, c.params("votes_get"))],
  agent_runs_list: [
    5,
    (c) => http.get(`${BASE}/workspaces/${c.workspaceId}/agent-runs`, c.params("agent_runs_list")),
  ],
  requirements_get: [
    5,
    (c) => http.get(`${BASE}/workspaces/${c.workspaceId}/requirements`, c.params("requirements_get")),
  ],
  price_history: [
    10,
    (c) => http.get(`${BASE}/products/${c.product()}/prices?currency=USD`, c.params("price_history")),
  ],
  search: [
    12,
    (c) =>
      http.post(
        `${BASE}/workspaces/${c.workspaceId}/search`,
        JSON.stringify({ query: pick(SEARCHES), mode: "hybrid", limit: 10 }),
        c.params("search"),
      ),
  ],
  ask: [
    7,
    (c) =>
      http.post(
        `${BASE}/workspaces/${c.workspaceId}/ask`,
        JSON.stringify({ question: pick(QUESTIONS), product_ids: [c.product()] }),
        c.params("ask"),
      ),
  ],
  compare: [
    5,
    (c) => http.post(`${BASE}/workspaces/${c.workspaceId}/compare`, JSON.stringify({}), c.params("compare")),
  ],
  comment_create: [
    5,
    (c) =>
      http.post(
        `${BASE}/workspaces/${c.workspaceId}/comments`,
        JSON.stringify({ body: `Load test comment ${exec.scenario.iterationInTest}` }),
        c.params("comment_create"),
      ),
  ],
  vote: [
    3,
    (c) =>
      http.put(
        `${BASE}/workspaces/${c.workspaceId}/products/${c.product()}/vote`,
        JSON.stringify({ value: pick([1, -1]) }),
        c.params("vote"),
      ),
  ],
  analyze: [1, (c) => http.post(`${BASE}/workspaces/${c.workspaceId}/analyze`, null, c.params("analyze"))],
};

const NAMES = Object.keys(OPERATIONS);
const TOTAL_WEIGHT = NAMES.reduce((sum, name) => sum + OPERATIONS[name][0], 0);

function pick(list) {
  return list[Math.floor(Math.random() * list.length)];
}

function pickOperation() {
  let roll = Math.random() * TOTAL_WEIGHT;
  for (const name of NAMES) {
    roll -= OPERATIONS[name][0];
    if (roll < 0) return name;
  }
  return NAMES[0];
}

const SCENARIOS = {
  smoke: {
    executor: "constant-arrival-rate",
    rate: 5,
    timeUnit: "1s",
    duration: "30s",
    preAllocatedVUs: 10,
    maxVUs: 50,
  },
  load: {
    executor: "constant-arrival-rate",
    rate: RATE,
    timeUnit: "1s",
    duration: __ENV.DURATION || "2m",
    preAllocatedVUs: 50,
    maxVUs: 400,
  },
};

function capacityScenarios() {
  const scenarios = {};
  STEPS.forEach((rate, index) => {
    scenarios[`step_${rate}`] = {
      executor: "constant-arrival-rate",
      rate,
      timeUnit: "1s",
      duration: `${STEP_SECONDS}s`,
      startTime: `${index * (STEP_SECONDS + GAP_SECONDS)}s`,
      gracefulStop: `${GAP_SECONDS}s`,
      preAllocatedVUs: Math.min(1000, rate * 2),
      maxVUs: Math.min(2000, rate * 6),
    };
  });
  return scenarios;
}

function scenarios() {
  return PROFILE === "capacity" ? capacityScenarios() : { [PROFILE]: SCENARIOS[PROFILE] };
}

// A threshold on a tagged sub-metric is what makes k6 report that sub-metric, so every
// operation (and every capacity step) gets one that always passes. Only the smoke and
// load profiles have limits that can fail the run.
function thresholds() {
  const strict = PROFILE !== "capacity";
  const limits = {
    checks: strict ? ["rate>0.99"] : ["rate>=0"],
    "http_req_failed{phase:test}": strict ? ["rate<0.01"] : ["rate>=0"],
    "http_req_duration{phase:test}": ["max>=0"],
    "http_reqs{phase:test}": ["count>=0"],
  };
  const groups = NAMES.map((name) => `op:${name}`);
  if (PROFILE === "capacity") {
    for (const rate of STEPS) {
      groups.push(`scenario:step_${rate}`);
      limits[`dropped_iterations{scenario:step_${rate}}`] = ["count>=0"];
    }
  }
  for (const group of groups) {
    limits[`http_req_duration{${group}}`] = ["max>=0"];
    limits[`http_req_failed{${group}}`] = ["rate>=0"];
    limits[`http_reqs{${group}}`] = ["count>=0"];
  }
  return limits;
}

export const options = {
  scenarios: scenarios(),
  thresholds: thresholds(),
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
  setupTimeout: "10m",
};

export function setup() {
  const run = Date.now().toString(36);
  const users = [];
  for (let index = 0; index < USERS; index++) users.push(seedUser(run, index, PRODUCTS));
  return { users, profile: PROFILE, startedAt: new Date().toISOString() };
}

export default function (data) {
  const user = data.users[exec.scenario.iterationInTest % data.users.length];
  const name = pickOperation();
  const context = {
    workspaceId: user.workspaceId,
    product: () => pick(user.productIds),
    // Bodies are not needed to judge a response, so they are not kept in memory.
    params: (op) => ({
      headers: authHeaders(user.token),
      tags: { op, phase: "test" },
      responseType: "none",
    }),
  };
  const response = OPERATIONS[name][1](context);
  ok(response, [200, 201, 204]);
}

export function handleSummary(data) {
  const out = {
    profile: PROFILE,
    config: {
      users: USERS,
      products_per_user: PRODUCTS,
      rate: RATE,
      steps: STEPS,
      step_seconds: STEP_SECONDS,
      weights: weights(),
    },
    scenarios: scenarios(),
    state: data.state,
    metrics: data.metrics,
  };
  return {
    stdout: brief(data),
    [`${RESULTS_DIR}/${PROFILE}.json`]: JSON.stringify(out, null, 2),
  };
}

function weights() {
  const shares = {};
  for (const name of NAMES) shares[name] = OPERATIONS[name][0];
  return shares;
}

// A short text summary; the full numbers are in the JSON file and docs/PERFORMANCE.md.
function brief(data) {
  const value = (metric, key) => {
    const found = data.metrics[metric];
    return found && found.values[key] !== undefined ? found.values[key] : NaN;
  };
  if (!(value("http_reqs{phase:test}", "count") > 0)) {
    return "\nno test requests were made (setup failed?); see the error above\n";
  }
  const ms = (number) => (Number.isNaN(number) ? "n/a" : `${number.toFixed(1)} ms`);
  const lines = [
    "",
    `profile ${PROFILE}: ${value("http_reqs{phase:test}", "count")} requests, ` +
      `${value("http_reqs{phase:test}", "rate").toFixed(1)} per second, ` +
      `${(100 * value("http_req_failed{phase:test}", "rate")).toFixed(2)}% failed, ` +
      `${value("dropped_iterations", "count") || 0} dropped`,
    `all requests: p50 ${ms(value("http_req_duration{phase:test}", "med"))}, ` +
      `p95 ${ms(value("http_req_duration{phase:test}", "p(95)"))}, ` +
      `p99 ${ms(value("http_req_duration{phase:test}", "p(99)"))}`,
  ];
  for (const name of NAMES) {
    const metric = `http_req_duration{op:${name}}`;
    lines.push(
      `  ${name.padEnd(20)} n=${String(value(`http_reqs{op:${name}}`, "count")).padStart(6)}` +
        `  p50 ${ms(value(metric, "med")).padStart(10)}  p95 ${ms(value(metric, "p(95)")).padStart(10)}`,
    );
  }
  if (PROFILE === "capacity") {
    lines.push("", "  target/s  achieved/s   failed   dropped       p50        p95");
    for (const rate of STEPS) {
      const tag = `{scenario:step_${rate}}`;
      lines.push(
        `  ${String(rate).padStart(8)}  ${(value(`http_reqs${tag}`, "count") / STEP_SECONDS).toFixed(1).padStart(10)}` +
          `  ${(100 * value(`http_req_failed${tag}`, "rate")).toFixed(2).padStart(6)}%` +
          `  ${String(value(`dropped_iterations${tag}`, "count") || 0).padStart(8)}` +
          `  ${ms(value(`http_req_duration${tag}`, "med")).padStart(8)}  ${ms(value(`http_req_duration${tag}`, "p(95)")).padStart(9)}`,
      );
    }
  }
  const failed = Object.keys(data.metrics).filter((name) =>
    Object.values(data.metrics[name].thresholds || {}).some((threshold) => !threshold.ok),
  );
  lines.push(failed.length ? `THRESHOLDS FAILED: ${failed.join(", ")}` : "thresholds passed", "");
  return lines.join("\n");
}
