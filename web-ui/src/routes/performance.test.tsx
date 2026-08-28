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
    model: "gpt-mini",
    stage: "a",
    p50_latency_ms: 1200,
    p95_latency_ms: 3400,
    call_count: 1,
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
    jobs_seniority_filtered: 0,
    jobs_scored: 0,
    stage_a_scored: 0,
    stage_b_scored: 0,
    ml_gate_processed: 0,
    stage_a_processed: 0,
    stage_b_processed: 0,
    scan_processed: 0,
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
  expect(screen.getByTestId("cloudscape-performance")).toBeInTheDocument();

  // KPI cards.
  const kpis = await screen.findByRole("region", { name: "Performance summary" });
  expect(kpis).toBeInTheDocument();
  expect(screen.getByText("4.2s")).toBeInTheDocument(); // avg_scan_duration_ms = 4200
  expect(screen.getByText("12.5s")).toBeInTheDocument(); // avg_eval_duration_ms = 12500
  expect(screen.getByText("$1.85")).toBeInTheDocument(); // total_llm_cost_usd
  expect(screen.getByText("3.0%")).toBeInTheDocument(); // error_rate

  // Chart sections by heading. Gate substeps and per-source errors have
  // no honest producer (that detail level lives in logs/OTel spans), so
  // no such panels exist.
  const panels = [
    "Scan time by source",
    "Evaluation time by step",
    "Local filter results",
    "Model response time",
    "Daily average tokens per model call",
    "Overall average token mix per model call",
    "Evaluation conversion",
    "Recent run errors",
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
  expect(await screen.findByText(/No scan timing data/)).toBeInTheDocument();
  expect(screen.getByText(/No evaluation timing data/)).toBeInTheDocument();
  expect(screen.getByText(/No local filter data/)).toBeInTheDocument();
  expect(screen.getByText(/No model timing data/)).toBeInTheDocument();
  expect(screen.getAllByText(/No token data in this time range/)).toHaveLength(2);
  expect(screen.getByText(/No evaluation funnel data/)).toBeInTheDocument();
  expect(screen.getByText(/No errors in this time range/)).toBeInTheDocument();

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

  expect(await screen.findByText(/No scan timing data/)).toBeInTheDocument();
  expect(screen.getByText(/No evaluation timing data/)).toBeInTheDocument();
});

test("gate pass / fail derives from funnel stats, not step timings", async () => {
  // after_filter=80 enter the gate, after_gate=40 pass → 40 fail. No
  // gate step timings exist at all — the panel must still render.
  mockApi({ ...defaultData, timings: { timings: [] } });
  renderPerformance();

  const card = await screen.findByRole("region", { name: "Local filter results" });
  expect(within(card).queryByText(/No local filter data/)).toBeNull();
  expect(within(card).getByRole("application", { name: "Local filter results" })).toBeInTheDocument();
  expect(within(card).getAllByText("Passed").length).toBeGreaterThan(0);
  expect(within(card).getAllByText("Excluded").length).toBeGreaterThan(0);
  expect(within(card).getByText("50%")).toBeInTheDocument();
});

test("performance uses the visualization matching each metric", async () => {
  renderPerformance();

  const scanCard = await screen.findByRole("region", { name: "Scan time by source" });
  expect(within(scanCard).getByRole("table", { name: "Scan time by source" })).toBeInTheDocument();
  expect(within(scanCard).getByText("Source")).toBeInTheDocument();
  expect(within(scanCard).getByText("Average")).toBeInTheDocument();

  const timingCard = screen.getByRole("region", { name: "Evaluation time by step" });
  expect(within(timingCard).getByRole("table", { name: "Evaluation time by step" })).toBeInTheDocument();
  expect(within(timingCard).getByText("Average per run that recorded this step.")).toBeInTheDocument();
  expect(within(timingCard).getAllByText("Quick evaluation").length).toBeGreaterThan(0);

  const modelCard = screen.getByRole("region", { name: "Model response time" });
  expect(within(modelCard).getByRole("table", { name: "Model response time" })).toBeInTheDocument();
  expect(within(modelCard).getByText("gpt-mini")).toBeInTheDocument();
  expect(within(modelCard).getByText("Quick evaluation")).toBeInTheDocument();

  const mixCard = screen.getByRole("region", { name: "Overall average token mix per model call" });
  expect(within(mixCard).getByRole("application", { name: "Overall average token mix per model call" })).toBeInTheDocument();
  expect(within(mixCard).getAllByText("Input").length).toBeGreaterThan(0);
  expect(within(mixCard).getAllByText("Output").length).toBeGreaterThan(0);
});

test("token chart uses compact axis labels", async () => {
  mockApi({
    ...defaultData,
    llmStats: {
      stats: [llmRow({ avg_input_tokens: 26_345, avg_output_tokens: 1_000 })],
    },
  });
  renderPerformance();

  const card = await screen.findByRole("region", { name: "Daily average tokens per model call" });
  expect(within(card).getAllByText("20K").length).toBeGreaterThan(0);
  expect(within(card).queryByText("20,000")).toBeNull();
});

test("overall token mix is weighted by model-call count", async () => {
  mockApi({
    ...defaultData,
    llmStats: {
      stats: [
        llmRow({ day: "2026-06-10", call_count: 1, avg_input_tokens: 100, avg_output_tokens: 50 }),
        llmRow({ day: "2026-06-11", call_count: 9, avg_input_tokens: 1_000, avg_output_tokens: 500 }),
      ],
    },
  });
  renderPerformance();

  const card = await screen.findByRole("region", { name: "Overall average token mix per model call" });
  expect(within(card).getAllByText("910 tokens").length).toBeGreaterThan(0);
  expect(within(card).getAllByText("455 tokens").length).toBeGreaterThan(0);
});

test("old server rows never render NaN while waiting for the backend reload", async () => {
  const legacyRow = {
    day: "2026-06-10",
    p50_latency_ms: 12_000,
    p95_latency_ms: 30_000,
    avg_input_tokens: 800,
    avg_output_tokens: 200,
  } as unknown as LLMDailyStatsRow;
  mockApi({
    ...defaultData,
    llmStats: { stats: [legacyRow] },
  });
  renderPerformance();

  const modelCard = await screen.findByRole("region", { name: "Model response time" });
  expect(within(modelCard).getByText("Model unavailable")).toBeInTheDocument();
  expect(within(modelCard).getByText("Calls unavailable")).toBeInTheDocument();
  expect(within(modelCard).queryByText(/NaN/)).toBeNull();

  const tokenCard = screen.getByRole("region", { name: "Overall average token mix per model call" });
  expect(within(tokenCard).queryByText(/NaN/)).toBeNull();
});

test("recent run errors lists readable times, error counters, and failed runs", async () => {
  mockApi({
    ...defaultData,
    runs: {
      runs: [runRow("run-err-1", { errors: 186 }), runRow("run-failed", { status: "failed" })],
      total: 2,
    },
  });
  renderPerformance();

  const card = await screen.findByRole("region", { name: "Recent run errors" });
  expect(within(card).getByRole("table", { name: "Recent run errors" })).toBeInTheDocument();
  expect(within(card).getByRole("columnheader", { name: "Run" })).toBeInTheDocument();
  expect(within(card).getByRole("columnheader", { name: "Errors" })).toBeInTheDocument();
  expect(within(card).queryByRole("columnheader", { name: "Time" })).toBeNull();
  expect(within(card).queryByRole("columnheader", { name: "Status" })).toBeNull();
  expect(within(card).getByText("186")).toBeInTheDocument();
  expect(within(card).getByText("Failed")).toBeInTheDocument();
  expect(within(card).queryByText("run-err-")).toBeNull();
  expect(calls.some((c) => c.startsWith("/api/runs?"))).toBe(true);
});

test("errors per run honors the time window and refetches on switch", async () => {
  renderPerformance();
  await screen.findByRole("region", { name: "Recent run errors" });

  // The default 90d view includes older failures still visible in Runs.
  expect(
    calls.some((c) => c.startsWith("/api/runs?") && c.includes("days=90")),
  ).toBe(true);

  // Switching the window re-fires the runs query with the new bound.
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  await waitFor(() =>
    expect(
      calls.some((c) => c.startsWith("/api/runs?") && c.includes("days=7")),
    ).toBe(true),
  );
});

test("time filter switches trigger new queries", async () => {
  renderPerformance();
  await screen.findByRole("region", { name: "Performance summary" });

  // Default 90d keeps persisted failures in view.
  expect(screen.getByRole("button", { name: "90 days" })).toHaveAttribute("aria-pressed", "true");
  expect(calls.some((c) => c.includes("window=90"))).toBe(true);

  // Switch to 7d.
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  await waitFor(() =>
    expect(calls.some((c) => c.includes("window=7"))).toBe(true),
  );
  expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "90 days" })).toHaveAttribute("aria-pressed", "false");
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

test("evaluation conversion shows only meaningful progressive stages", async () => {
  renderPerformance();
  const funnelCard = await screen.findByRole("region", { name: "Evaluation conversion" });
  expect(funnelCard).toBeInTheDocument();
  const card = within(funnelCard);
  for (const label of ["Candidates", "Passed local filter", "Evaluated"]) {
    expect(card.getAllByText(label).length).toBeGreaterThanOrEqual(1);
  }
  expect(card.queryByText("Passed job filters")).toBeNull();
  expect(card.getAllByRole("progressbar")).toHaveLength(3);
});

test("delta arrows show directional coloring", async () => {
  renderPerformance();
  await screen.findByRole("region", { name: "Performance summary" });

  // Cost delta is -0.08 = -8% (down is good for cost -> green/apply).
  const decreases = screen.getAllByText(/Decreased/);
  expect(decreases.length).toBeGreaterThanOrEqual(1);

  // Error rate delta is +0.025 = +2.5% (up is bad for errors -> red/danger).
  const increases = screen.getAllByText(/Increased/);
  expect(increases.length).toBeGreaterThanOrEqual(1);
});

test("delta ratios are scaled to percents (0.08 → 8%, not 0.1%)", async () => {
  renderPerformance();
  await screen.findByRole("region", { name: "Performance summary" });

  expect(screen.getByText("Decreased 8%")).toBeInTheDocument();
  expect(screen.queryByText("Decreased 0.1%")).toBeNull();
  expect(screen.getByText("Increased 3%")).toBeInTheDocument();
});

test("long evaluation durations use days and hours instead of a large seconds count", async () => {
  mockApi({
    ...defaultData,
    overview: overviewFixture({ avg_eval_duration_ms: 438_944_300 }),
  });
  renderPerformance();

  expect(await screen.findByText("5d 1h 55m")).toBeInTheDocument();
  expect(screen.queryByText("438944.3s")).toBeNull();
});

test("normal and minute-scale average durations stay in seconds", async () => {
  mockApi({
    ...defaultData,
    overview: overviewFixture({
      avg_scan_duration_ms: 119_200,
      avg_eval_duration_ms: 693_800,
    }),
  });
  renderPerformance();

  expect(await screen.findByText("119.2s")).toBeInTheDocument();
  expect(screen.getByText("693.8s")).toBeInTheDocument();
  expect(screen.queryByText("1m")).toBeNull();
  expect(screen.queryByText("11m")).toBeNull();
});
