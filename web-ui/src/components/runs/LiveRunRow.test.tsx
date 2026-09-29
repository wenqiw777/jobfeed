import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";

import type { RunSummary } from "@/api/queries";
import { LiveRunRow } from "@/components/runs/LiveRunRow";

// ---------------------------------------------------------------------------
// EventSource stub (same shape as the use-sse test's fake).
// ---------------------------------------------------------------------------

type ESListener = (event: MessageEvent) => void;

class FakeEventSource {
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ESListener | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  private listeners = new Map<string, ESListener[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: ESListener): void {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  removeEventListener(): void {
    // Not needed for tests.
  }

  close(): void {
    this.closed = true;
  }

  _open(): void {
    this.onopen?.();
  }

  _message(data: string): void {
    this.onmessage?.(new MessageEvent("message", { data }));
  }

  _done(data = ""): void {
    for (const listener of this.listeners.get("done") ?? []) {
      listener(new MessageEvent("done", { data }));
    }
  }

  _error(): void {
    this.onerror?.(new Event("error"));
  }

  static instances: FakeEventSource[] = [];
  static reset(): void {
    FakeEventSource.instances = [];
  }
}

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
  // The reconnect backoff uses real timers otherwise.
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function runCounters(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: "r-live-1",
    source: "evaluate",
    started_at: "2026-06-10T08:00:00Z",
    finished_at: null,
    status: "running",
    jobs_discovered: 0,
    jobs_inserted: 0,
    jobs_updated: 0,
    jobs_filtered: 0,
    jobs_ml_gated: 0,
    jobs_seniority_filtered: 0,
    jobs_scored: 0,
    stage_a_scored: 0,
    stage_b_scored: 0,
    errors: 0,
    total_llm_cost_usd: 0,
    progress_stage: "preparing",
    evaluate_stage: "both",
    stage_a_total: null,
    stage_a_processed: 0,
    stage_b_total: null,
    stage_b_processed: 0,
    progress_updated_at: "2026-06-10T08:00:00Z",
    ...overrides,
  } as RunSummary;
}

const RUN = {
  run_id: "r-live-1",
  source: "ats",
  started_at: "2026-06-10T08:00:00Z",
  counters: runCounters({ source: "ats" }),
};

it("shows source warning reason without treating it as active work", () => {
  renderRow(vi.fn(), {...RUN, counters: runCounters({source: "all", scan_progress: {
    linkedin: {phase: "completed_with_warnings", processed: 750, total: 750,
      message: "Unconfirmed empty page; more results may exist"},
  }})});
  expect(screen.getByTestId("scan-source-linkedin")).toHaveTextContent("Unconfirmed empty page; more results may exist");
});

const EVALUATE_RUN = {
  ...RUN,
  source: "evaluate",
  counters: runCounters(),
};

test("a failed terminal event never shows Completed or Stop", () => {
  renderRow(vi.fn());
  act(() => FakeEventSource.instances[0]!._done(JSON.stringify(runCounters({status: "failed", errors: 1}))));
  expect(screen.getByText("Failed")).toBeVisible();
  expect(screen.queryByText("Completed")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: /stop/i})).not.toBeInTheDocument();
});

test("source warnings and interpretation phase remain visible", () => {
  renderRow(vi.fn(), {...RUN, counters: runCounters({scan_source: "linkedin", scan_phase: "interpreting",
    scan_processed: 2, scan_total: 5, scan_progress: {linkedin: {phase: "interpreting", processed: 2, total: 5}}})});
  expect(screen.getByText("LinkedIn · Interpreting job page evidence · 2 / 5")).toBeVisible();
  act(() => FakeEventSource.instances[0]!._done(JSON.stringify(runCounters({status: "succeeded",
    scan_progress: {linkedin: {phase: "completed_with_warnings", processed: 2, total: 1000}}}))));
  expect(screen.getByText("Completed with issues")).toBeVisible();
  expect(screen.queryByRole("button", {name: /stop/i})).not.toBeInTheDocument();
});

test("shows backlog candidates found for the run, not rows inspected or ML-gate input", () => {
  renderRow(vi.fn(), {
    ...EVALUATE_RUN,
    counters: runCounters({
      evaluation_scope: "backlog",
      evaluation_input_total: 131699,
      progress_stage: "preparing",
      stage_a_total: 48,
      ml_gate_total: 7,
    } as Partial<RunSummary>),
  });

  const row = screen.getByTestId("live-run-r-live-1");
  expect(row).toHaveTextContent("Historical backlog: 48 candidates found so far");
  expect(row).not.toHaveTextContent("131699 scanned so far");
  expect(row).not.toHaveTextContent("7 candidates found so far");
});

test("keeps future evaluation stages waiting until their candidate set is ready", () => {
  renderRow(vi.fn(), {
    ...EVALUATE_RUN,
    counters: runCounters({
      evaluation_scope: "backlog",
      progress_stage: "ml_gate",
      jobs_filtered: 764,
      stage_a_total: 3243,
      stage_a_processed: 0,
      ml_gate_total: 2479,
      ml_gate_processed: 2,
    } as Partial<RunSummary>),
  });

  expect(screen.getByRole("progressbar", {
    name: "SDE role filter: 2 / 2479",
  })).toBeVisible();
  expect(screen.getByRole("progressbar", {
    name: "Seniority filter: Waiting",
  })).toBeVisible();
  expect(screen.getByRole("progressbar", {
    name: "Quick evaluation: Waiting",
  })).toBeVisible();
});

test("shows this evaluation's recommendation counts during detailed review", () => {
  renderRow(vi.fn(), {
    ...EVALUATE_RUN,
    counters: runCounters({
      progress_stage: "stage_b",
      stage_b_processed: 4,
      verdict_counts: { apply: 2, consider: 1, skip: 1 },
    }),
  });
  const row = screen.getByTestId("live-run-r-live-1");
  expect(row).toHaveTextContent("2 Apply");
  expect(row).toHaveTextContent("1 Consider");
  expect(row).toHaveTextContent("1 Ignore");
});

function renderRow(onDone: () => void, run = RUN) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ul>
        <LiveRunRow run={run} onDone={onDone} />
      </ul>
    </QueryClientProvider>,
  );
}

test("renders four independent source lanes simultaneously", () => {
  renderRow(vi.fn(), {...RUN, counters: runCounters({source: "all", scan_progress: {
    jobright: {phase: "fetching", processed: 20, total: 100},
    "linkedin-extension": {phase: "saving", processed: 10, total: 50},
    handshake: {phase: "completed", processed: 50, total: 50},
    speedyapply: {phase: "browser_enrichment", processed: 3, total: 20},
  }})});
  for (const source of ["jobright", "linkedin-extension", "handshake", "speedyapply"]) {
    expect(screen.getByTestId(`scan-source-${source}`)).toBeInTheDocument();
  }
  expect(screen.getByTestId("scan-source-speedyapply")).toHaveTextContent("browser enrichment · 3 / 20");
  expect(screen.getByTestId("scan-source-speedyapply")).toHaveTextContent("GitHub job lists");
});

test("fires onDone once when the stream ends with event: done", () => {
  const onDone = vi.fn();
  renderRow(onDone);
  const es = FakeEventSource.instances[0]!;

  act(() => es._open());
  act(() => es._message(JSON.stringify({ jobs_discovered: 3 })));
  expect(onDone).not.toHaveBeenCalled();

  act(() => es._done(JSON.stringify({ jobs_discovered: 5 })));
  expect(onDone).toHaveBeenCalledTimes(1);
});

test("does NOT fire onDone on a transport error", () => {
  const onDone = vi.fn();
  renderRow(onDone);
  const es = FakeEventSource.instances[0]!;

  act(() => es._open());
  act(() => es._message(JSON.stringify({ jobs_discovered: 3 })));

  // Connection drops with stale data on screen: isConnected goes false
  // but isDone stays false — the completion path must not fire.
  act(() => es._error());
  expect(onDone).not.toHaveBeenCalled();

  // Still not fired after the backoff reconnect kicks in.
  act(() => {
    vi.advanceTimersByTime(1_000);
  });
  expect(onDone).not.toHaveBeenCalled();
});

test("renders non-zero live counters as chips", () => {
  renderRow(vi.fn());
  const es = FakeEventSource.instances[0]!;

  act(() => es._open());
  act(() => es._message(JSON.stringify({ jobs_discovered: 4, errors: 0 })));

  const row = screen.getByTestId("live-run-r-live-1");
  expect(row).toHaveTextContent("discovered");
  expect(row).not.toHaveTextContent("errors");
});

test("shows the current scan source, phase, and bounded progress", () => {
  renderRow(vi.fn(), {
    ...RUN,
    counters: runCounters({
      source: "all",
      scan_source: "linkedin_guest",
      scan_phase: "enriching_job_descriptions",
      scan_total: 8000,
      scan_processed: 218,
      scan_current_job_id: "li-123",
    } as Partial<RunSummary>),
  });

  const row = screen.getByTestId("live-run-r-live-1");
  expect(row).toHaveTextContent("LinkedIn Guest · Enriching job descriptions · 218 / 8000");
  expect(row).toHaveTextContent("Listing li-123");
});

test("renders the real evaluate phase, denominators, cost, and errors", () => {
  renderRow(vi.fn(), {
    ...EVALUATE_RUN,
    counters: runCounters({
      progress_stage: "stage_b",
      stage_a_total: 150,
      stage_a_processed: 150,
      stage_a_scored: 150,
      stage_b_total: 62,
      stage_b_processed: 20,
      stage_b_scored: 19,
      errors: 1,
      total_llm_cost_usd: 4.25,
    } as Partial<RunSummary>),
  });

  const row = screen.getByTestId("live-run-r-live-1");
  expect(row).toHaveTextContent("Running detailed review");
  expect(row).toHaveTextContent("150 / 150");
  expect(row).toHaveTextContent("20 / 62");
  expect(row).not.toHaveTextContent("0 processed");
  expect(row).toHaveTextContent("$4.25");
  expect(row).toHaveTextContent("1 error");
});

test("shows seniority as its own active phase after the SDE role filter", () => {
  renderRow(vi.fn(), {
    ...EVALUATE_RUN,
    counters: runCounters({
      progress_stage: "ml_gate",
      ml_gate_total: 1290,
      ml_gate_processed: 1290,
      jobs_ml_gated: 320,
      jobs_seniority_filtered: 0,
    } as Partial<RunSummary>),
  });

  const row = screen.getByTestId("live-run-r-live-1");
  expect(row).toHaveTextContent("Seniority filter");
  expect(row).toHaveTextContent("Applying seniority filter");
  expect(screen.getByRole("progressbar", {
    name: "Seniority filter: Screening 970 candidates",
  })).toBeVisible();
});

test("shows seniority progress complete when quick evaluation starts", () => {
  renderRow(vi.fn(), {
    ...EVALUATE_RUN,
    counters: runCounters({
      progress_stage: "stage_a",
      ml_gate_total: 1290,
      ml_gate_processed: 1290,
      jobs_ml_gated: 320,
      jobs_seniority_filtered: 481,
      stage_a_total: 489,
      stage_a_processed: 0,
    } as Partial<RunSummary>),
  });

  expect(screen.getByRole("progressbar", {
    name: "Seniority filter: 970 / 970",
  })).toBeVisible();
});

test("merges a newer SSE update with polled active-run counters", () => {
  renderRow(vi.fn(), {
    ...EVALUATE_RUN,
    counters: runCounters({
      progress_stage: "stage_b",
      stage_a_total: 150,
      stage_a_processed: 150,
      stage_b_total: 62,
      stage_b_processed: 20,
      stage_b_scored: 19,
      total_llm_cost_usd: 4.25,
    } as Partial<RunSummary>),
  });
  const es = FakeEventSource.instances[0]!;

  act(() => es._open());
  act(() => es._message(JSON.stringify({
    ...runCounters(),
    progress_stage: "stage_b",
    stage_a_total: 150,
    stage_a_processed: 150,
    stage_b_total: 62,
    stage_b_processed: 25,
    stage_b_scored: 24,
    total_llm_cost_usd: 5.5,
  })));

  const row = screen.getByTestId("live-run-r-live-1");
  expect(row).toHaveTextContent("25 / 62");
  expect(row).toHaveTextContent("$5.50");
});
