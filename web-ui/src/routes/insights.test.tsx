import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { InsightsOverviewResponse } from "@/api/queries";
import InsightsPage from "@/routes/insights";

function overviewFixture(over: Partial<InsightsOverviewResponse> = {}): InsightsOverviewResponse {
  return {
    window_days: 30,
    totals: { jobs: 1200, ml_gate_passed: 400, evaluated: 240, applied: 30 },
    verdict_distribution: { apply: 40, consider: 80, skip: 100, below_threshold: 20 },
    status_distribution: { new: 50, scored: 100, applied: 30, rejected: 5 },
    daily: [
      { day: "2026-06-10", discovered: 12, evaluated: 6, applied: 2 },
      { day: "2026-06-08", discovered: 3, evaluated: 1, applied: 0 },
    ],
    applications: {
      applied_count: 12,
      response_count: 4,
      interview_count: 2,
      offer_count: 1,
      rejection_count: 1,
      median_days_to_response: 5.5,
      by_resume: {
        "": { sent: 2, responses: 0, interviews: 0, offers: 0, rejections: 1 },
        ml: { sent: 10, responses: 4, interviews: 2, offers: 1, rejections: 0 },
      },
    },
    ...over,
  };
}

function emptyOverview(): InsightsOverviewResponse {
  return overviewFixture({
    totals: { jobs: 0, ml_gate_passed: 0, evaluated: 0, applied: 0 },
    verdict_distribution: {},
    status_distribution: {},
    daily: [],
    applications: {
      applied_count: 0,
      response_count: 0,
      interview_count: 0,
      offer_count: 0,
      rejection_count: 0,
      median_days_to_response: null,
      by_resume: {},
    },
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
        const window = Number(new URLSearchParams(url.split("?")[1]).get("window"));
        return new Response(JSON.stringify({ ...overview, window_days: window }), {
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

/* recharts SVG layout depends on real measured dimensions, which the DOM
 * shim cannot guarantee — so chart assertions target resilient markers:
 * card headings, the HTML legends/captions, and table contents. */

test("KPI cards: all-time totals plus the windowed application count, labeled", async () => {
  renderInsights();
  expect(screen.getByTestId("cloudscape-insights")).toBeInTheDocument();

  const kpis = await screen.findByRole("region", { name: "Key numbers" });
  expect(calls[0]).toBe("/api/insights/overview?window=30");
  expect(within(kpis).getByText("1,200")).toBeInTheDocument(); // totals.jobs
  expect(within(kpis).getByText("400")).toBeInTheDocument(); // ml_gate_passed
  expect(within(kpis).getByText("12")).toBeInTheDocument(); // windowed applications
  expect(within(kpis).getAllByText("all-time")).toHaveLength(4);
  expect(within(kpis).getByText("last 30d")).toBeInTheDocument();
});

test("funnel: stage ladder with computed Stage B and apply-verdict values", async () => {
  renderInsights();

  const funnel = await screen.findByRole("region", { name: "Funnel" });
  const expected: [string, string][] = [
    ["Discovered", "1,200"],
    ["Gate passed", "400"],
    ["Stage A", "240"],
    ["Stage B", "220"], // apply 40 + consider 80 + skip 100, below_threshold excluded
    ["Apply verdict", "40"],
    ["Applied", "30"],
  ];
  for (const [stage, value] of expected) {
    const term = within(funnel).getByText(stage, { selector: "dt" });
    // dt and dd are adjacent inline nodes — no whitespace between them.
    expect(term.parentElement).toHaveTextContent(`${stage}${value}`);
  }
});

test("status donut: legend lists statuses with counts, no verdict groupings", async () => {
  renderInsights();

  const donut = await screen.findByRole("region", { name: "Statuses" });
  expect(within(donut).getByText("scored")).toBeInTheDocument();
  expect(within(donut).getByText("100")).toBeInTheDocument();
  expect(within(donut).getByText("rejected")).toBeInTheDocument();
  // below_threshold is a verdict display state, not a status.
  expect(within(donut).queryByText(/below/)).toBeNull();
});

test("daily timeline and by-resume table render; variant-less rows read unknown", async () => {
  renderInsights();

  const timeline = await screen.findByRole("region", { name: "Daily activity" });
  for (const series of ["discovered", "evaluated", "applied"]) {
    expect(within(timeline).getByText(series)).toBeInTheDocument();
  }

  const table = screen.getByRole("region", { name: "By resume" });
  const mlRow = within(table).getByText("ml").closest("tr");
  expect(mlRow).toHaveTextContent("ml104210");
  const unknownRow = within(table).getByText("unknown").closest("tr");
  expect(unknownRow).toHaveTextContent("unknown20001");
});

test("an empty window renders per-chart empty states, not crashes", async () => {
  mockApi(emptyOverview());
  renderInsights();

  expect(await screen.findByText(/No jobs discovered yet/)).toBeInTheDocument();
  expect(screen.getByText(/No tracked statuses yet/)).toBeInTheDocument();
  expect(screen.getByText(/No activity in this window/)).toBeInTheDocument();
  expect(screen.getByText(/No applications in this window/)).toBeInTheDocument();
  // KPI zeros still render (they are real numbers, not missing data).
  expect(screen.getAllByText("0").length).toBeGreaterThanOrEqual(5);
});

test("window presets re-query with ?window= and mark the active chip", async () => {
  renderInsights();
  await screen.findByText("last 30d");
  expect(screen.getByRole("button", { name: "30d" })).toHaveAttribute("aria-pressed", "true");

  fireEvent.click(screen.getByRole("button", { name: "60d" }));

  await waitFor(() =>
    expect(calls.at(-1)).toBe("/api/insights/overview?window=60"),
  );
  expect(screen.getByRole("button", { name: "60d" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "30d" })).toHaveAttribute("aria-pressed", "false");
  // The applications meta now labels the new window.
  expect(await screen.findByText("last 60d")).toBeInTheDocument();
});
