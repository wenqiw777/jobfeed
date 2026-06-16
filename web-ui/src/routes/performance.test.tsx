import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type {
  FunnelResponse,
  FunnelRow,
  LLMDailyStatsRow,
  LLMStatsResponse,
  PerformanceOverviewResponse,
  StepTimingRow,
  StepTimingsResponse,
} from "@/api/queries";
import { ZONES } from "@/components/shell/zones";
import PerformancePage from "@/routes/performance";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function overviewFixture(
  over: Partial<PerformanceOverviewResponse> = {},
): PerformanceOverviewResponse {
  return {
    avg_scan_duration_ms: 4200,
    avg_eval_duration_ms: 12500,
    total_llm_cost_usd: 1.85,
    error_rate: 0.03,
    scan_duration_delta: -5.2,
    eval_duration_delta: 12.1,
    cost_delta: -8.0,
    error_rate_delta: 2.5,
    ...over,
  };
}

function timingRow(over: Partial<StepTimingRow> = {}): StepTimingRow {
  return {
    run_id: "run-abc-123",
    step_type: "scan",
    step_name: "ats_fetch",
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

// ---------------------------------------------------------------------------
// Mock API
// ---------------------------------------------------------------------------

interface MockData {
  overview: PerformanceOverviewResponse;
  timings: StepTimingsResponse;
  llmStats: LLMStatsResponse;
  funnel: FunnelResponse;
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
        timingRow(),
        timingRow({ step_type: "gate", step_name: "extract", is_error: false }),
        timingRow({ step_type: "gate", step_name: "embed", is_error: false, elapsed_ms: 300 }),
        timingRow({ step_type: "gate", step_name: "predict", is_error: false, elapsed_ms: 100 }),
        timingRow({ step_type: "gate", step_name: "extract", is_error: true }),
        timingRow({ step_type: "evaluate", step_name: "stage_a_eval", elapsed_ms: 2000 }),
      ],
    },
    llmStats: { stats: [llmRow()] },
    funnel: { funnel: [funnelRow()] },
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

  // Chart sections by heading.
  const panels = [
    "Scan duration",
    "Evaluate breakdown",
    "Gate pass / fail",
    "Gate substeps",
    "LLM latency",
    "Daily LLM cost proxy",
    "Token usage",
    "Funnel conversion",
    "Errors by source",
    "Errors per run",
  ];
  for (const title of panels) {
    expect(screen.getByRole("region", { name: title })).toBeInTheDocument();
  }
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
  });
  renderPerformance();

  // Each chart should show its empty message.
  expect(await screen.findByText(/No scan timings/)).toBeInTheDocument();
  expect(screen.getByText(/No evaluate timings/)).toBeInTheDocument();
  expect(screen.getByText(/No gate data/)).toBeInTheDocument();
  expect(screen.getByText(/No gate substep data/)).toBeInTheDocument();
  expect(screen.getByText(/No LLM latency data/)).toBeInTheDocument();
  expect(screen.getByText(/No LLM cost data/)).toBeInTheDocument();
  expect(screen.getByText(/No token data/)).toBeInTheDocument();
  expect(screen.getByText(/No funnel data/)).toBeInTheDocument();
  // Both error panels share the same text, so use getAllByText.
  expect(screen.getAllByText(/No errors in this period/)).toHaveLength(2);

  // KPI zeros still render (two 0ms: scan + eval).
  expect(screen.getAllByText("0ms")).toHaveLength(2);
  expect(screen.getByText("$0.00")).toBeInTheDocument();
  expect(screen.getByText("0.0%")).toBeInTheDocument();
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
  const within = await import("@testing-library/react").then((m) => m.within);
  const card = within(funnelCard);
  expect(card.getByText("Candidates", { selector: "dt" })).toBeInTheDocument();
  expect(card.getByText("After filter", { selector: "dt" })).toBeInTheDocument();
  expect(card.getByText("After gate", { selector: "dt" })).toBeInTheDocument();
  expect(card.getByText("Scored", { selector: "dt" })).toBeInTheDocument();
});

test("delta arrows show directional coloring", async () => {
  renderPerformance();
  await screen.findByRole("region", { name: "Performance KPIs" });

  // Cost delta is -8% (down is good for cost -> green/apply).
  const downArrows = screen.getAllByText(/↓/);
  expect(downArrows.length).toBeGreaterThanOrEqual(1);

  // Error rate delta is +2.5% (up is bad for errors -> red/danger).
  const upArrows = screen.getAllByText(/↑/);
  expect(upArrows.length).toBeGreaterThanOrEqual(1);
});
