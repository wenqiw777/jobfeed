import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { InsightsOverviewResponse } from "@/api/queries";
import { coverageStages } from "@/components/insights/EvaluationCoverage";
import InsightsPage from "@/routes/insights";

function overviewFixture(over: Partial<InsightsOverviewResponse> = {}): InsightsOverviewResponse {
  return {
    window_days: 30,
    totals: { jobs: 1200, ml_gate_passed: 400, evaluated: 240, detailed_reviewed: 120, applied: 30 },
    verdict_distribution: { apply: 40, consider: 80, skip: 100, below_threshold: 20 },
    decision_distribution: { results: 150, applied: 35, ignored: 20 },
    daily: [
      { day: "2026-06-10", discovered: 12, evaluated: 6, applied: 2 },
      { day: "2026-06-08", discovered: 3, evaluated: 1, applied: 0 },
    ],
    ...over,
  };
}

function emptyOverview(): InsightsOverviewResponse {
  return overviewFixture({
    totals: { jobs: 0, ml_gate_passed: 0, evaluated: 0, detailed_reviewed: 0, applied: 0 },
    verdict_distribution: {},
    decision_distribution: {},
    daily: [],
  });
}

const calls: string[] = [];

function mockApi(overview: InsightsOverviewResponse): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.startsWith("/api/insights/overview?")) {
        const windowValue = new URLSearchParams(url.split("?")[1]).get("window");
        const windowDays = windowValue === "all" ? null : Number(windowValue);
        return new Response(JSON.stringify({ ...overview, window_days: windowDays }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`unexpected fetch in test: ${url}`);
    }),
  );
}

function renderInsights() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <InsightsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls.length = 0;
  mockApi(overviewFixture());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/* Cloudscape chart layout depends on real measured dimensions, which the DOM
 * shim cannot guarantee. Assertions use accessible chart names and pure data. */

test("KPI cards: every value is labeled with the selected time range", async () => {
  renderInsights();
  expect(screen.getByTestId("cloudscape-insights")).toBeInTheDocument();

  const kpis = await screen.findByRole("region", { name: "Key numbers" });
  expect(calls[0]).toBe("/api/insights/overview?window=30");
  expect(within(kpis).getByText("1,200")).toBeInTheDocument(); // totals.jobs
  expect(within(kpis).getByText("400")).toBeInTheDocument(); // ml_gate_passed
  expect(within(kpis).getByText("Applied")).toBeInTheDocument();
  expect(within(kpis).queryByText("Applications submitted")).toBeNull();
  expect(within(kpis).getByText("Quick evaluated")).toBeInTheDocument();
  expect(within(kpis).getByText("Detailed reviewed")).toBeInTheDocument();
  expect(within(kpis).getAllByText("last 30 days")).toHaveLength(5);
});

test("evaluation coverage: shows absolute counts and shares of all discovered jobs", async () => {
  renderInsights();

  const coverage = await screen.findByRole("region", { name: "Evaluation coverage" });
  expect(within(coverage).getByRole("progressbar", { name: "Passed local filter" })).toBeInTheDocument();
  expect(within(coverage).getByText("400 of 1,200 jobs in selected period")).toBeInTheDocument();
  expect(within(coverage).getByText("33.3% of selected period")).toBeInTheDocument();
  expect(within(coverage).getByRole("progressbar", { name: "Detailed reviewed" })).toBeInTheDocument();
  expect(coverageStages(overviewFixture())).toEqual([
    { label: "Passed local filter", count: 400, percent: 33.3 },
    { label: "Quick evaluated", count: 240, percent: 20 },
    { label: "Detailed reviewed", count: 120, percent: 10 },
    { label: "Recommended apply", count: 40, percent: 3.3 },
    { label: "Applied", count: 30, percent: 2.5 },
  ]);
});

test("user decisions combine archived with ignored on a logarithmic chart", async () => {
  renderInsights();

  const chart = await screen.findByRole("region", { name: "User decisions" });
  expect(within(chart).getByRole("application", { name: "User decisions" })).toBeInTheDocument();
  expect(screen.getByText(/Archived postings are included in Ignored/i)).toBeInTheDocument();
  // below_threshold is a verdict display state, not a status.
  expect(within(chart).queryByText(/below/)).toBeNull();
});

test("the selected range does not render a duplicate activity summary card", async () => {
  renderInsights();

  await screen.findByRole("region", { name: "Evaluation coverage" });
  expect(screen.getByRole("region", { name: "User decisions" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "This period" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Applications by resume" })).toBeNull();
});

test("an empty window renders per-chart empty states, not crashes", async () => {
  mockApi(emptyOverview());
  renderInsights();

  expect(await screen.findByText(/No jobs discovered yet/)).toBeInTheDocument();
  expect(screen.getByText(/No job decisions yet/)).toBeInTheDocument();
  // KPI zeros still render (they are real numbers, not missing data).
  expect(screen.getAllByText("0").length).toBeGreaterThanOrEqual(4);
});

test("window presets re-query with ?window= and mark the active chip", async () => {
  renderInsights();
  expect(await screen.findAllByText("last 30 days")).toHaveLength(5);
  expect(screen.getByRole("button", { name: "7 days" })).toBeVisible();
  expect(screen.getByRole("button", { name: "30 days" })).toHaveAttribute("aria-pressed", "true");

  fireEvent.click(screen.getByRole("button", { name: "60 days" }));

  await waitFor(() =>
    expect(calls.at(-1)).toBe("/api/insights/overview?window=60"),
  );
  expect(screen.getByRole("button", { name: "60 days" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "30 days" })).toHaveAttribute("aria-pressed", "false");
  expect(await screen.findAllByText("last 60 days")).toHaveLength(5);
});

test("All time requests the unbounded cohort and labels every KPI", async () => {
  renderInsights();
  await screen.findAllByText("last 30 days");

  fireEvent.click(screen.getByRole("button", { name: "All time" }));

  await waitFor(() =>
    expect(calls.at(-1)).toBe("/api/insights/overview?window=all"),
  );
  expect(screen.getByRole("button", { name: "All time" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(await screen.findAllByText("all time")).toHaveLength(5);
  expect(screen.getByText(/share of all-time jobs/i)).toBeInTheDocument();
});
