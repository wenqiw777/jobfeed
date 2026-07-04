import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type {
  FunnelResponse,
  FunnelRow,
  LLMDailyStatsRow,
  LLMStatsResponse,
  PerformanceOverviewResponse,
  RunsListResponse,
  RunSummary,
  StepTimingRow,
  StepTimingsResponse,
} from "@/api/queries";
import { ZONES } from "@/components/shell/zones";
import PerformancePage from "@/routes/performance";

// ---------------------------------------------------------------------------
// Fixtures — step timings follow the real backend contract: exactly two
// step_type values exist, `source_fetch` (step_name = source name, one
// row per source per scan) and `stage` (step_name in funnel / stage_a /
// stage_b, the evaluate phases). Nothing else is ever emitted.
// ---------------------------------------------------------------------------

function overviewFixture(
  over: Partial<PerformanceOverviewResponse> = {},
): PerformanceOverviewResponse {
  return {
    avg_scan_duration_ms: 4200,
    avg_eval_duration_ms: 12500,
    total_llm_cost_usd: 1.85,
    error_rate: 0.03,
    // Deltas are raw ratios ((cur − prev) / prev), exactly as the
    // backend's get_performance_overview computes them — never
    // percent-scale numbers.
    scan_duration_delta: -0.052,
    eval_duration_delta: 0.121,
    cost_delta: -0.08,
    error_rate_delta: 0.025,
    ...over,
  };
}

function timingRow(over: Partial<StepTimingRow> = {}): StepTimingRow {
  return {
    run_id: "run-abc-123",
    step_type: "source_fetch",
    step_name: "ats",
    elapsed_ms: 500,
    is_error: false,
    created_at: "2026-06-10T08:00:00Z",
    ...over,
  };
}

function llmRow(over: Partial<LLMDailyStatsRow> = {}): LLMDailyStatsRow {
  return {
    day: "2026-06-10",
    p50_latency_ms: 1200,
    p95_latency_ms: 3400,
    avg_input_tokens: 800,
    avg_output_tokens: 200,
    ...over,
  };
}

function funnelRow(over: Partial<FunnelRow> = {}): FunnelRow {
  return {
    run_id: "run-abc-123",
    total_candidates: 100,
    after_filter: 80,
    after_gate: 40,
    scored: 20,
    ...over,
  };
}

function runRow(id: string, over: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: id,
    started_at: "2026-06-10T08:00:00Z",
    finished_at: "2026-06-10T08:05:00Z",
    source: "ats",
    status: "finished",
    jobs_discovered: 0,
    jobs_inserted: 0,
    jobs_updated: 0,
    jobs_filtered: 0,
    jobs_ml_gated: 0,
    jobs_scored: 0,
    stage_a_scored: 0,
    stage_b_scored: 0,
    errors: 0,
    total_llm_cost_usd: 0,
    ...over,
  };
}

// ---------------------------------------------------------------------------
// Mock API
// ---------------------------------------------------------------------------

interface MockData {
  overview: PerformanceOverviewResponse;
  timings: StepTimingsResponse;
  llmStats: LLMStatsResponse;
  funnel: FunnelResponse;
  runs: RunsListResponse;
}

const calls: string[] = [];

function mockApi(data: MockData): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.startsWith("/api/performance/overview?")) {
        return new Response(JSON.stringify(data.overview), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.startsWith("/api/performance/step-timings?")) {
        return new Response(JSON.stringify(data.timings), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.startsWith("/api/performance/llm-stats?")) {
        return new Response(JSON.stringify(data.llmStats), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.startsWith("/api/performance/funnel?")) {
        return new Response(JSON.stringify(data.funnel), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.startsWith("/api/runs?")) {
        return new Response(JSON.stringify(data.runs), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`unexpected fetch in test: ${url}`);
    }),
  );
}

function renderPerformance() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PerformancePage />
    </QueryClientProvider>,
  );
}

let defaultData: MockData;

beforeEach(() => {
  calls.length = 0;
  defaultData = {
    overview: overviewFixture(),
    timings: {
      timings: [
        // One scan run: one source_fetch row per source, one errored.
        timingRow(),
        timingRow({ step_name: "indeed", elapsed_ms: 800 }),
        timingRow({ step_name: "indeed", elapsed_ms: 120, is_error: true }),
        // One evaluate run: the three stage phases.
        timingRow({ run_id: "run-eval-1", step_type: "stage", step_name: "funnel", elapsed_ms: 400 }),
        timingRow({ run_id: "run-eval-1", step_type: "stage", step_name: "stage_a", elapsed_ms: 2000 }),
        timingRow({ run_id: "run-eval-1", step_type: "stage", step_name: "stage_b", elapsed_ms: 5000 }),
      ],
    },
    llmStats: { stats: [llmRow()] },
    funnel: { funnel: [funnelRow()] },
    runs: { runs: [runRow("run-err-1", { errors: 2 }), runRow("run-ok-1")], total: 2 },
  };
  mockApi(defaultData);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("all chart panels render with mocked data", async () => {
  renderPerformance();

  // KPI cards.
  const kpis = await screen.findByRole("region", { name: "Performance KPIs" });
  expect(kpis).toBeInTheDocument();
  expect(screen.getByText("4.2s")).toBeInTheDocument(); // avg_scan_duration_ms = 4200
  expect(screen.getByText("12.5s")).toBeInTheDocument(); // avg_eval_duration_ms = 12500
  expect(screen.getByText("$1.85")).toBeInTheDocument(); // total_llm_cost_usd
  expect(screen.getByText("3.0%")).toBeInTheDocument(); // error_rate

  // Chart sections by heading. Gate substeps and per-source errors have
  // no honest producer (that detail level lives in logs/OTel spans), so
  // no such panels exist.
  const panels = [
    "Scan duration by source",
    "Evaluate breakdown",
    "Gate pass / fail",
    "LLM latency",
    "Daily token usage",
    "Token usage",
    "Funnel conversion",
    "Errors per run",
  ];
  for (const title of panels) {
    expect(screen.getByRole("region", { name: title })).toBeInTheDocument();
  }
  expect(screen.queryByRole("region", { name: "Gate substeps" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Errors by source" })).toBeNull();
});

test("empty state renders per panel, not crashes", async () => {
  mockApi({
    overview: overviewFixture({
      avg_scan_duration_ms: 0,
      avg_eval_duration_ms: 0,
      total_llm_cost_usd: 0,
      error_rate: 0,
      scan_duration_delta: null,
      eval_duration_delta: null,
      cost_delta: null,
      error_rate_delta: null,
    }),
    timings: { timings: [] },
    llmStats: { stats: [] },
    funnel: { funnel: [] },
    runs: { runs: [], total: 0 },
  });
  renderPerformance();

  // Each chart should show its empty message.
  expect(await screen.findByText(/No scan timings/)).toBeInTheDocument();
  expect(screen.getByText(/No evaluate timings/)).toBeInTheDocument();
  expect(screen.getByText(/No gate data/)).toBeInTheDocument();
  expect(screen.getByText(/No LLM latency data/)).toBeInTheDocument();
  expect(screen.getByText(/No LLM token data/)).toBeInTheDocument();
  expect(screen.getByText(/No token data/)).toBeInTheDocument();
  expect(screen.getByText(/No funnel data/)).toBeInTheDocument();
  expect(screen.getByText(/No errors in this period/)).toBeInTheDocument();

  // KPI zeros still render (two 0ms: scan + eval).
  expect(screen.getAllByText("0ms")).toHaveLength(2);
  expect(screen.getByText("$0.00")).toBeInTheDocument();
  expect(screen.getByText("0.0%")).toBeInTheDocument();
});

test("legacy invented step types and unknown stage names are ignored", async () => {
  // These step_type values were never emitted by the backend — they
  // existed only in old frontend fixtures. Unknown stage names must be
  // dropped, not folded into the funnel bucket.
  mockApi({
    ...defaultData,
    timings: {
      timings: [
        timingRow({ step_type: "scan" }),
        timingRow({ step_type: "evaluate", step_name: "stage_a_eval" }),
        timingRow({ step_type: "gate", step_name: "extract" }),
        timingRow({ step_type: "stage", step_name: "mystery_phase" }),
      ],
    },
  });
  renderPerformance();

  expect(await screen.findByText(/No scan timings/)).toBeInTheDocument();
  expect(screen.getByText(/No evaluate timings/)).toBeInTheDocument();
});

test("gate pass / fail derives from funnel stats, not step timings", async () => {
  // after_filter=80 enter the gate, after_gate=40 pass → 40 fail. No
  // gate step timings exist at all — the panel must still render.
  mockApi({ ...defaultData, timings: { timings: [] } });
  renderPerformance();

  const card = await screen.findByRole("region", { name: "Gate pass / fail" });
  expect(within(card).queryByText(/No gate data/)).toBeNull();
  expect(within(card).getByTestId("gate-pass-fail")).toBeInTheDocument();
  expect(within(card).getByText("pass")).toBeInTheDocument();
  expect(within(card).getByText("fail")).toBeInTheDocument();
});

test("errors per run charts the runs' error counters", async () => {
  renderPerformance();

  const card = await screen.findByRole("region", { name: "Errors per run" });
  // run-err-1 has errors=2 → chart renders; run-ok-1 (0 errors) is quiet.
  expect(within(card).getByTestId("errors-per-run")).toBeInTheDocument();
  expect(calls.some((c) => c.startsWith("/api/runs?"))).toBe(true);
});

test("errors per run honors the time window and refetches on switch", async () => {
  renderPerformance();
  await screen.findByRole("region", { name: "Errors per run" });

  // The runs request is bounded to the default 30d window.
  expect(
    calls.some((c) => c.startsWith("/api/runs?") && c.includes("days=30")),
  ).toBe(true);

  // Switching the window re-fires the runs query with the new bound.
  fireEvent.click(screen.getByRole("button", { name: "7d" }));
  await waitFor(() =>
    expect(
      calls.some((c) => c.startsWith("/api/runs?") && c.includes("days=7")),
    ).toBe(true),
  );
});

test("time filter switches trigger new queries", async () => {
  renderPerformance();
  await screen.findByRole("region", { name: "Performance KPIs" });

  // Default 30d.
  expect(screen.getByRole("button", { name: "30d" })).toHaveAttribute("aria-pressed", "true");
  expect(calls.some((c) => c.includes("window=30"))).toBe(true);

  // Switch to 7d.
  fireEvent.click(screen.getByRole("button", { name: "7d" }));
  await waitFor(() =>
    expect(calls.some((c) => c.includes("window=7"))).toBe(true),
  );
  expect(screen.getByRole("button", { name: "7d" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "30d" })).toHaveAttribute("aria-pressed", "false");
});

test("nav includes Performance entry in zones", () => {
  const labels = ZONES.map((z) => z.label);
  expect(labels).toContain("Performance");
  const perfZone = ZONES.find((z) => z.label === "Performance")!;
  expect(perfZone.path).toBe("/performance");
  // Performance sits between Runs and Sources.
  const runsIdx = labels.indexOf("Runs");
  const perfIdx = labels.indexOf("Performance");
  const sourcesIdx = labels.indexOf("Sources");
  expect(perfIdx).toBe(runsIdx + 1);
  expect(sourcesIdx).toBe(perfIdx + 1);
});

test("funnel conversion shows stage labels and values in the legend", async () => {
  renderPerformance();
  const funnelCard = await screen.findByRole("region", { name: "Funnel conversion" });
  expect(funnelCard).toBeInTheDocument();
  // The dl summary carries stage labels as <dt> elements (scoped to the
  // card to avoid recharts SVG duplicates).
  const card = within(funnelCard);
  expect(card.getByText("Candidates", { selector: "dt" })).toBeInTheDocument();
  expect(card.getByText("After filter", { selector: "dt" })).toBeInTheDocument();
  expect(card.getByText("After gate", { selector: "dt" })).toBeInTheDocument();
  expect(card.getByText("Scored", { selector: "dt" })).toBeInTheDocument();
});

test("delta arrows show directional coloring", async () => {
  renderPerformance();
  await screen.findByRole("region", { name: "Performance KPIs" });

  // Cost delta is -0.08 = -8% (down is good for cost -> green/apply).
  const downArrows = screen.getAllByText(/↓/);
  expect(downArrows.length).toBeGreaterThanOrEqual(1);

  // Error rate delta is +0.025 = +2.5% (up is bad for errors -> red/danger).
  const upArrows = screen.getAllByText(/↑/);
  expect(upArrows.length).toBeGreaterThanOrEqual(1);
});

test("delta ratios are scaled to percents (0.08 → 8%, not 0.1%)", async () => {
  renderPerformance();
  await screen.findByRole("region", { name: "Performance KPIs" });

  // cost_delta -0.08 → "↓8%"; the old unscaled formatter rendered "↓0.1%".
  expect(screen.getByText("↓8%")).toBeInTheDocument();
  expect(screen.queryByText("↓0.1%")).toBeNull();
  // error_rate_delta 0.025 → 2.5, ≥1 rounds to whole percents: "↑3%".
  expect(screen.getByText("↑3%")).toBeInTheDocument();
});
