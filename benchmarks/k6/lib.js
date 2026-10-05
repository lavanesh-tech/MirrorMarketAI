// Shared helpers for the k6 scripts: API calls, synthetic data, and seeding.
//
// Everything created here is synthetic and labelled as such in the report.
import http from "k6/http";
import { check, fail } from "k6";

export const BASE = (__ENV.BASE_URL || "http://api:8000").replace(/\/$/, "") + "/api/v1";
export const RESULTS_DIR = __ENV.RESULTS_DIR || "/results";
const PASSWORD = "load-test-horse-battery-staple";
const JSON_HEADERS = { "Content-Type": "application/json" };

export function authHeaders(token) {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

function must(response, what, expected) {
  if (response.status !== expected) {
    fail(`${what}: expected ${expected}, got ${response.status} ${String(response.body).slice(0, 300)}`);
  }
  return response.json();
}

// --- Synthetic documents ---------------------------------------------------------
// Long enough to be split into several chunks, with facts the questions ask about.

const FILLER = [
  "The chassis is machined aluminium and feels solid in the hand.",
  "The keyboard has 1.5 mm of travel and a full-height arrow cluster.",
  "The display covers the sRGB gamut and reaches 400 nits.",
  "Fan noise stays low under office workloads.",
  "The speakers are clear at moderate volume.",
  "The webcam is 1080p with a physical privacy shutter.",
  "Wi-Fi 6E and Bluetooth 5.3 are built in.",
  "The power adapter is a compact 65 W USB-C charger.",
  "The hinge opens to 180 degrees and holds its position.",
  "The trackpad is glass and tracks precisely.",
];

function specSheet(index) {
  const ram = [8, 16, 32][index % 3];
  const price = ["899", "1,199", "2,499"][index % 3];
  const facts = [
    `Memory is ${ram} GB LPDDR5.`,
    `It has ${index % 3 === 0 ? "one USB-C port" : "two USB-C ports"} and an HDMI 2.1 port.`,
    `It retails for $${price}.`,
    `Battery life is rated at ${10 + index} hours.`,
    "The warranty is 2 years and covers parts and labour.",
    "Returns are accepted within 30 days.",
    `It weighs ${(1.2 + index * 0.1).toFixed(1)} kg.`,
  ];
  return facts.concat(FILLER).concat(facts.slice(0, 3)).join(" ");
}

function reviews(index) {
  const lines = [
    "The battery is excellent and easily lasts a working day.",
    "The screen is bright and sharp.",
    "The keyboard is comfortable for long typing sessions.",
    index % 3 === 0 ? "It gets slow with many browser tabs open." : "Performance is smooth even with many apps open.",
    "The speakers are disappointing and sound thin.",
    "Support answered my question within a day.",
    "The fan is quiet most of the time.",
    "The trackpad is great and very precise.",
  ];
  return lines.concat(FILLER.slice(0, 5)).concat(lines).join(" ");
}

export const QUESTIONS = [
  "How much memory does it have?",
  "How long is the warranty?",
  "What do reviewers say about the battery?",
  "How many USB-C ports are there?",
  "What is the return policy?",
  "How much does it weigh?",
];

export const SEARCHES = [
  "memory LPDDR5",
  "USB-C ports HDMI",
  "battery life hours",
  "warranty parts labour",
  "speakers sound",
  "keyboard typing comfortable",
  "return policy days",
];

const BRIEF = "Laptop under $1,500 with at least 16 GB RAM. I have a USB-C dock. Good battery life would be nice.";

// --- Seeding ----------------------------------------------------------------------

function upload(token, productId, text, sourceType) {
  const response = http.post(
    `${BASE}/products/${productId}/sources/upload`,
    { file: http.file(text, "doc.txt", "text/plain"), source_type: sourceType },
    { headers: { Authorization: `Bearer ${token}` }, tags: { op: "setup" } },
  );
  const sourceId = must(response, "upload", 201).source.id;
  const embedded = http.post(`${BASE}/sources/${sourceId}/embed`, null, {
    headers: authHeaders(token),
    tags: { op: "setup" },
  });
  if (embedded.status >= 300) fail(`embed: ${embedded.status} ${embedded.body}`);
}

// One user with one workspace, `products` products with sources, prices, requirements
// and one finished analysis. Returns what the scenarios need.
export function seedUser(run, userIndex, products) {
  const tags = { tags: { op: "setup" } };
  const email = `load-${run}-${userIndex}@example.com`;
  must(
    http.post(
      `${BASE}/auth/register`,
      JSON.stringify({ email, password: PASSWORD, display_name: `Load ${userIndex}` }),
      { headers: JSON_HEADERS, ...tags },
    ),
    "register",
    201,
  );
  const token = must(
    http.post(`${BASE}/auth/login`, JSON.stringify({ email, password: PASSWORD }), {
      headers: JSON_HEADERS,
      ...tags,
    }),
    "login",
    200,
  ).access_token;
  const headers = { headers: authHeaders(token), ...tags };

  const workspaceId = must(
    http.post(`${BASE}/workspaces`, JSON.stringify({ name: `Load ${run}-${userIndex}` }), headers),
    "workspace",
    201,
  ).id;

  const productIds = [];
  for (let index = 0; index < products; index++) {
    const productId = must(
      http.post(
        `${BASE}/products`,
        JSON.stringify({ brand: `Load${run}u${userIndex}`, name: `Model ${index}`, category: "laptop" }),
        headers,
      ),
      "product",
      201,
    ).id;
    productIds.push(productId);
    must(
      http.post(`${BASE}/workspaces/${workspaceId}/products`, JSON.stringify({ product_id: productId }), headers),
      "add product",
      201,
    );
    upload(token, productId, specSheet(index), "MANUFACTURER_PAGE");
    upload(token, productId, reviews(index), "REVIEW");

    const observations = [];
    for (let day = 0; day < 30; day++) {
      observations.push({
        retailer: day % 2 ? "Example Store" : "Sample Shop",
        amount: ([899, 1199, 2499][index % 3] - day).toFixed(2),
        currency: "USD",
        observed_at: new Date(Date.now() - (day * 86400 + 3600) * 1000).toISOString(),
        in_stock: true,
      });
    }
    must(
      http.post(`${BASE}/products/${productId}/prices`, JSON.stringify({ observations }), headers),
      "prices",
      201,
    );
  }

  must(
    http.put(
      `${BASE}/workspaces/${workspaceId}/requirements`,
      JSON.stringify({ expected_version: 0, text: BRIEF }),
      headers,
    ),
    "requirements",
    201,
  );
  must(http.post(`${BASE}/workspaces/${workspaceId}/analyze`, null, headers), "analyze", 201);
  return { email, token, workspaceId, productIds };
}

export function ok(response, statuses) {
  return check(response, { "status is expected": (r) => statuses.includes(r.status) });
}
