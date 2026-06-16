/**
 * buildJobsParams contract: list params repeat the key per value (FastAPI
 * style), scalars serialize, and absent values never reach the URL.
 */
import { buildJobsParams } from "./queries";

test("array params repeat the key per value", () => {
  const params = buildJobsParams({ statuses: ["new", "triaged"] });

  expect(params.getAll("statuses")).toEqual(["new", "triaged"]);
  expect(params.toString()).toBe("statuses=new&statuses=triaged");
});

test("booleans and numbers serialize as strings", () => {
  const params = buildJobsParams({
    require_verdict: true,
    apply_hard_filters: false,
    limit: 0,
    posted_within_days: 14,
  });

  expect(params.get("require_verdict")).toBe("true");
  expect(params.get("apply_hard_filters")).toBe("false");
  expect(params.get("limit")).toBe("0");
  expect(params.get("posted_within_days")).toBe("14");
});

test("null and undefined values are skipped entirely", () => {
  const params = buildJobsParams({
    tab: "queue",
    statuses: null,
    search: undefined,
    posted_within_days: null,
  });

  expect(params.toString()).toBe("tab=queue");
  expect(params.has("statuses")).toBe(false);
  expect(params.has("search")).toBe(false);
  expect(params.has("posted_within_days")).toBe(false);
});

test("empty array contributes no params", () => {
  expect(buildJobsParams({ statuses: [] }).toString()).toBe("");
});
