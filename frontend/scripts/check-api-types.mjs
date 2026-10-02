// Fails when src/lib/api/schema.d.ts no longer matches docs/api/openapi.json.
// The backend contract and the frontend types must change in the same commit.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const committed = "src/lib/api/schema.d.ts";
const dir = mkdtempSync(join(tmpdir(), "mm-api-types-"));
const fresh = join(dir, "schema.d.ts");
try {
  const bin = (name) => join("node_modules", ".bin", name);
  execFileSync(bin("openapi-typescript"), ["../docs/api/openapi.json", "-o", fresh], {
    stdio: "pipe",
  });
  execFileSync(bin("prettier"), ["--write", "--config", ".prettierrc.json", fresh], {
    stdio: "pipe",
  });
  if (readFileSync(fresh, "utf8") !== readFileSync(committed, "utf8")) {
    console.error(`${committed} is stale. Run: npm run api:types`);
    process.exit(1);
  }
  console.log("API types match docs/api/openapi.json");
} finally {
  rmSync(dir, { recursive: true, force: true });
}
