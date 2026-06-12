/**
 * CI staleness gate for src/api/types.gen.ts.
 *
 * Regenerates types from the committed OpenAPI snapshot into a temp file
 * and byte-compares it with the committed types.gen.ts. Exits 1 when they
 * differ (someone changed the snapshot without running `npm run gen:api`).
 *
 * Note this guards the *committed file*; `npm run build` independently
 * catches stale types at type-check time because prebuild regenerates
 * types.gen.ts before tsc runs.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const snapshot = resolve(root, "../src/jobfeed/web/openapi.json");
const committed = join(root, "src", "api", "types.gen.ts");

const tmp = mkdtempSync(join(tmpdir(), "jobfeed-types-"));
const fresh = join(tmp, "types.gen.ts");

try {
  execFileSync("npx", ["--no-install", "openapi-typescript", snapshot, "-o", fresh], {
    cwd: root,
    stdio: ["ignore", "ignore", "inherit"],
  });
  if (readFileSync(fresh, "utf8") !== readFileSync(committed, "utf8")) {
    console.error(
      "src/api/types.gen.ts is stale vs src/jobfeed/web/openapi.json.\n" +
        "Run `npm run gen:api` (in web-ui/) and commit the result.",
    );
    process.exit(1);
  }
  console.log("types.gen.ts is up to date with the OpenAPI snapshot.");
} finally {
  rmSync(tmp, { recursive: true, force: true });
}
