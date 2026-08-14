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

const EVALUATE_RUN = {
  ...RUN,
  source: "evaluate",
  counters: runCounters(),
};

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
