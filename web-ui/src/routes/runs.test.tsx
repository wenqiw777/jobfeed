import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";

import type { RunSummary } from "@/api/queries";
import RunsPage from "@/routes/runs";

function runRow(id: string, over: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: id,
    started_at: "2026-06-10T08:00:00Z",
    finished_at: "2026-06-10T08:05:00Z",
    source: "ats",
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

interface ServerState {
  runs: RunSummary[];
}

const calls: string[] = [];

/** GET /api/runs mirroring the wire contract: newest-first array is the
 * server's order, limit/offset slice it, total is all-time. */
function mockApi(state: ServerState): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.startsWith("/api/runs?")) {
        const params = new URLSearchParams(url.slice(url.indexOf("?") + 1));
        const offset = Number(params.get("offset") ?? "0");
        const limit = Number(params.get("limit") ?? "25");
        return new Response(
          JSON.stringify({
            runs: state.runs.slice(offset, offset + limit),
            total: state.runs.length,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`unexpected fetch in test: ${url}`);
    }),
  );
}

function renderRuns() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RunsPage />
    </QueryClientProvider>,
  );
}

let state: ServerState;

beforeEach(() => {
  calls.length = 0;
  state = {
    runs: [
      runRow("r2", {
        started_at: "2026-06-10T09:30:00Z",
        jobs_discovered: 12,
        jobs_inserted: 4,
        stage_a_scored: 8,
        total_llm_cost_usd: 0.42,
      }),
      runRow("r1", { started_at: "2026-06-09T07:00:00Z", errors: 2, finished_at: null }),
    ],
  };
  mockApi(state);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
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
  // The Phase 9 hint stays quiet text, not a banner or dead button.
  expect(screen.getByText(/triggering scans from this page arrives with Phase 9/)).toBeInTheDocument();
});

test("rows show only non-zero counter chips; cost only when > 0", async () => {
  renderRuns();
  const row2 = await screen.findByTestId("run-row-r2");
  const row1 = screen.getByTestId("run-row-r1");

  // r2: discovered/inserted/stage A non-zero; the zero counters stay quiet.
  expect(within(row2).getByText("discovered")).toBeInTheDocument();
  expect(within(row2).getByText("inserted")).toBeInTheDocument();
  expect(within(row2).getByText("stage A")).toBeInTheDocument();
  expect(within(row2).queryByText("updated")).toBeNull();
  expect(within(row2).queryByText("errors")).toBeNull();
  expect(within(row2).getByText("$0.42")).toBeInTheDocument();

  // r1: only the errors chip — and no cost cell at $0.
  expect(within(row1).getByText("errors")).toBeInTheDocument();
  expect(within(row1).queryByText("discovered")).toBeNull();
  expect(within(row1).queryByText(/\$/)).toBeNull();
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
  expect(within(row).getByText("scored")).toBeInTheDocument();
  expect(within(row).getByText("finished")).toBeInTheDocument();
  expect(within(row).getByText("r2")).toBeInTheDocument();

  fireEvent.click(toggle);
  expect(within(row).queryByText("updated")).toBeNull();
});

test("pagination drives offset and the mono range label", async () => {
  state.runs = Array.from({ length: 30 }, (_, i) => runRow(`p${30 - i}`));
  renderRuns();
  await screen.findByText("1–25 of 30");
  expect(screen.getByRole("button", { name: "Prev" })).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("26–30 of 30");
  expect(calls.at(-1)).toContain("offset=25");
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: "Prev" }));
  await screen.findByText("1–25 of 30");
});

test("empty history teaches ./scan", async () => {
  state.runs = [];
  renderRuns();

  expect(await screen.findByText("No runs yet")).toBeInTheDocument();
  expect(screen.getByText("./scan")).toBeInTheDocument();
  expect(screen.getByText("0 runs")).toBeInTheDocument();
});
