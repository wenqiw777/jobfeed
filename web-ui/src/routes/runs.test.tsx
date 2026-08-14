import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { RunSummary } from "@/api/queries";
import { Toaster } from "@/components/ui/toaster";
import { DensityProvider } from "@/lib/density";
import RunsPage from "@/routes/runs";

/** Minimal EventSource stub: LiveRunRow (rendered for active runs)
 * opens an SSE connection on mount; happy-dom has no EventSource. */
class StubEventSource {
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  addEventListener(): void {
    // Streaming frames aren't exercised by these tests.
  }
  removeEventListener(): void {
    // Not needed for tests.
  }
  close(): void {
    // Not needed for tests.
  }
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
    ml_gate_processed: 0,
    stage_a_processed: 0,
    stage_b_processed: 0,
    errors: 0,
    total_llm_cost_usd: 0,
    ...over,
  };
}

interface ServerState {
  runs: RunSummary[];
  activeRuns: { run_id: string; source: string; started_at: string; counters: RunSummary }[];
}

interface RecordedCall {
  url: string;
  method: string;
  body: unknown;
}

const calls: RecordedCall[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockApi(state: ServerState): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = init?.body === undefined ? null : JSON.parse(String(init.body));
      calls.push({ url, method, body });

      if (method === "GET" && url.startsWith("/api/runs?")) {
        const params = new URLSearchParams(url.slice(url.indexOf("?") + 1));
        const offset = Number(params.get("offset") ?? "0");
        const limit = Number(params.get("limit") ?? "25");
        return json({
          runs: state.runs.slice(offset, offset + limit),
          total: state.runs.length,
        });
      }
      if (method === "GET" && url === "/api/runs/active") {
        return json({ runs: state.activeRuns });
      }
      if (method === "POST" && url === "/api/runs/scan") {
        const { source } = body as { source: string };
        if (source === "conflict") {
          return json(
            { error: { code: "scan_already_running", message: "Scan already running", request_id: "req-1" } },
            409,
          );
        }
        return json({ run_id: "new-scan-1", status: "running" });
      }
      if (method === "POST" && url === "/api/runs/evaluate") {
        return json({ run_id: "new-eval-1", status: "running" });
      }
      throw new Error(`unexpected fetch in test: ${method} ${url}`);
    }),
  );
}

function renderRuns() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <DensityProvider>
        <RunsPage />
        <Toaster />
      </DensityProvider>
    </QueryClientProvider>,
  );
}

let state: ServerState;

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal("EventSource", StubEventSource);
  state = {
    runs: [
      runRow("r2", {
        started_at: "2026-06-10T09:30:00Z",
        jobs_discovered: 12,
        jobs_inserted: 4,
        jobs_updated: 3,
        jobs_ml_gated: 2,
        stage_a_scored: 8,
        stage_b_scored: 1,
        total_llm_cost_usd: 0.42,
      }),
      runRow("r1", { started_at: "2026-06-09T07:00:00Z", errors: 2, finished_at: null }),
    ],
    activeRuns: [],
  };
  mockApi(state);
});

afterEach(() => {
  window.localStorage.removeItem("jobfeed:density");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("renders the Cloudscape run operations workspace and history table", async () => {
  renderRuns();

  expect(await screen.findByTestId("cloudscape-runs")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Scans and evaluations", level: 2 })).toBeVisible();
  expect(screen.getByRole("heading", { name: /Run history/, level: 2 })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "Started" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "Run type" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "Activity" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "Cost" })).toBeVisible();
});

test("run history table follows the selected row density", async () => {
  window.localStorage.setItem("jobfeed:density", "comfortable");
  renderRuns();

  expect(await screen.findByTestId("run-history-density")).toHaveAttribute(
    "data-density",
    "comfortable",
  );
});

test("rows mirror the server's newest-first order with mono timestamps", async () => {
  renderRuns();
  await screen.findByTestId("run-row-r2");

  const rows = screen.getAllByTestId(/^run-row-/);
  expect(rows.map((row) => row.getAttribute("data-testid"))).toEqual([
    "run-row-r2",
    "run-row-r1",
  ]);
  // Local-formatted wall clock in the mono column (TZ-agnostic shape).
  expect(
    within(rows[0]!).getByRole("button", { name: /Run started \d{4}-\d{2}-\d{2} \d{2}:\d{2}/ }),
  ).toBeInTheDocument();
  // The read-only hint has been removed.
  expect(screen.queryByText(/triggering scans from this page arrives with Phase 9/)).toBeNull();
});

test("activity badges use semantic colors and hide zero counters", async () => {
  renderRuns();
  await screen.findByTestId("run-row-r2");
  const row1 = screen.getByTestId("run-row-r1");
  const activity2 = screen.getByTestId("run-activity-r2");
  const activity1 = screen.getByTestId("run-activity-r1");

  // r2: discovered/inserted/stage A non-zero; the zero counters stay quiet.
  expect(within(activity2).getByText("12 discovered")).toBeInTheDocument();
  expect(within(activity2).getByText("4 new")).toBeInTheDocument();
  expect(within(activity2).getByText("3 updated")).toBeInTheDocument();
  expect(within(activity2).getByText("2 excluded by local filter")).toBeInTheDocument();
  expect(within(activity2).getByText("8 quick evaluations")).toBeInTheDocument();
  expect(within(activity2).getByText("1 detailed reviews")).toBeInTheDocument();
  expect(within(activity2).queryByText(/errors/)).toBeNull();
  expect(screen.getByText("$0.42")).toBeInTheDocument();

  expect(within(activity2).getByText("12 discovered").className).toContain("badge-color-blue");
  expect(within(activity2).getByText("4 new").className).toContain("badge-color-green");
  expect(within(activity2).getByText("3 updated").className).toContain("badge-color-grey");
  expect(within(activity2).getByText("2 excluded by local filter").className).toContain("badge-color-severity-neutral");
  expect(within(activity2).getByText("8 quick evaluations").className).toContain("badge-color-severity-low");
  expect(within(activity2).getByText("1 detailed reviews").className).toContain("badge-color-severity-medium");

  // r1: only the errors chip — and no cost cell at $0.
  expect(within(activity1).getByText("2 errors")).toBeInTheDocument();
  expect(within(activity1).getByText("2 errors").className).toContain("badge-color-red");
  expect(within(activity1).queryByText(/discovered/)).toBeNull();
  expect(within(row1).queryByText(/\$/)).toBeNull();
});

test("failed runs use an error indicator instead of a success check", async () => {
  state.runs = [runRow("failed-run", { status: "failed" })];
  renderRuns();

  const failed = await screen.findByText("Failed");
  expect(failed.closest('[class*="status-error"]')).not.toBeNull();
  expect(screen.getByText("No activity recorded")).toBeVisible();
  expect(screen.queryByText("No counters")).not.toBeInTheDocument();
});

test("expanding a row reveals the full counter grid, run id, and finished time", async () => {
  renderRuns();
  const row = await screen.findByTestId("run-row-r2");
  const toggle = within(row).getByRole("button", { name: /Run started/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");

  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  // Zero counters appear in the expanded grid even though their chips don't.
  expect(within(row).getByText("updated")).toBeInTheDocument();
  expect(within(row).getByText("evaluated")).toBeInTheDocument();
  expect(within(row).getByText("finished")).toBeInTheDocument();
  expect(within(row).getByText("r2")).toBeInTheDocument();

  fireEvent.click(toggle);
  expect(within(row).queryByText("updated")).toBeNull();
});

test("pagination drives offset and the mono range label", async () => {
  state.runs = Array.from({ length: 30 }, (_, i) => runRow(`p${30 - i}`));
  renderRuns();
  await screen.findByText("1–25 of 30");
  expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute("aria-disabled", "true");

  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByText("26–30 of 30");
  expect(calls.some((c) => c.url.includes("offset=25"))).toBe(true);
  expect(screen.getByRole("button", { name: "Next page" })).toHaveAttribute("aria-disabled", "true");

  fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
  await screen.findByText("1–25 of 30");
});

test("empty history points to the visible scan action", async () => {
  state.runs = [];
  renderRuns();

  expect(await screen.findByText("No runs yet")).toBeInTheDocument();
  expect(screen.getByText("Use Start scan above to collect jobs.")).toBeInTheDocument();
  expect(screen.getByText("0 runs")).toBeInTheDocument();
});

// ---------------------------------------------------------------------------
// Trigger buttons
// ---------------------------------------------------------------------------

test("trigger scan and evaluate buttons render in the header", async () => {
  renderRuns();
  await screen.findByTestId("run-row-r2");

  // Both trigger buttons are present.
  expect(screen.getByRole("button", { name: "Start scan" })).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Start evaluation" }).length).toBeGreaterThanOrEqual(1);

  // The read-only hint is gone.
  expect(screen.queryByText(/read-only/i)).toBeNull();
  expect(screen.queryByText(/Phase 9/)).toBeNull();
});

test("trigger evaluate dialog opens and submits with defaults", async () => {
  renderRuns();
  await screen.findByTestId("run-row-r2");

  fireEvent.click(screen.getAllByRole("button", { name: "Start evaluation" })[0]!);
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByRole("heading", { name: "Start evaluation" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: /Quick score and detailed review/ })).toBeInTheDocument();
  expect(screen.getByLabelText("Maximum jobs")).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Start evaluation" })).toBeInTheDocument();

  fireEvent.click(within(dialog).getByRole("button", { name: "Start evaluation" }));

  const postCall = await waitFor(() => {
    const call = calls.find((c) => c.method === "POST" && c.url === "/api/runs/evaluate");
    expect(call).toBeDefined();
    return call!;
  });
  expect(postCall.body).toEqual({ stage: "both" });
  expect(await screen.findByText(/Evaluation started/)).toBeInTheDocument();
});

test("trigger evaluate submits a non-default stage", async () => {
  renderRuns();
  await screen.findByTestId("run-row-r2");

  fireEvent.click(screen.getAllByRole("button", { name: "Start evaluation" })[0]!);
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(screen.getByRole("radio", { name: /Detailed review only/ }));
  fireEvent.change(screen.getByLabelText("Maximum jobs"), { target: { value: "1" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Start evaluation" }));

  const postCall = await waitFor(() => {
    const call = calls.find((c) => c.method === "POST" && c.url === "/api/runs/evaluate");
    expect(call).toBeDefined();
    return call!;
  });
  expect(postCall.body).toEqual({ stage: "b", limit: 1 });
});

test("evaluate 409 shows conflict toast", async () => {
  // Override mock to return 409 for evaluate.
  const baseFetch = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (method === "POST" && url === "/api/runs/evaluate") {
        return json(
          { error: { code: "evaluate_already_running", message: "Evaluate already running", request_id: "req-2" } },
          409,
        );
      }
      return baseFetch(input, init);
    }),
  );

  renderRuns();
  await screen.findByTestId("run-row-r2");

  fireEvent.click(screen.getAllByRole("button", { name: "Start evaluation" })[0]!);
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Start evaluation" }));

  expect(await screen.findByText("Evaluation already running")).toBeInTheDocument();
});

test("active runs query fetches GET /api/runs/active", async () => {
  renderRuns();
  await screen.findByTestId("run-row-r2");

  // The active runs endpoint was called.
  expect(calls.some((c) => c.url === "/api/runs/active" && c.method === "GET")).toBe(true);
});

test("invalid limit disables submit, shows inline error, and blocks the POST", async () => {
  renderRuns();
  await screen.findByTestId("run-row-r2");

  fireEvent.click(screen.getAllByRole("button", { name: "Start evaluation" })[0]!);
  const dialog = await screen.findByRole("dialog");

  fireEvent.change(screen.getByLabelText("Maximum jobs"), { target: { value: "0" } });
  expect(screen.getByText(/at least 1/)).toBeInTheDocument();
  expect(screen.getByLabelText("Maximum jobs")).toHaveAttribute("aria-invalid", "true");

  const submit = within(dialog).getByRole("button", { name: "Start evaluation" });
  expect(submit).toBeDisabled();
  fireEvent.click(submit);
  expect(calls.some((c) => c.method === "POST" && c.url === "/api/runs/evaluate")).toBe(false);

  // Clearing the field recovers.
  fireEvent.change(screen.getByLabelText("Maximum jobs"), { target: { value: "5" } });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(within(dialog).getByRole("button", { name: "Start evaluation" })).toBeEnabled();
});

test("a running run renders only as a live row, not in history too", async () => {
  // r2 is running: /api/runs/active serves it AND it still appears in
  // the /api/runs history payload for its entire duration.
  state.activeRuns = [
    {
      run_id: "r2",
      source: "ats",
      started_at: "2026-06-10T09:30:00Z",
      counters: runRow("r2", { status: "running", finished_at: null }),
    },
  ];
  renderRuns();

  await screen.findByTestId("live-run-r2");
  await screen.findByTestId("run-row-r1");
  // Deduped: the running run stays out of the history list.
  expect(screen.queryByTestId("run-row-r2")).toBeNull();
});
