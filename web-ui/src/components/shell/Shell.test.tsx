import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import App from "@/App";
import { configurationKey } from "@/api/configuration";
import { DensityProvider } from "@/lib/density";

const TAB_COUNTS = {
  queue: 12,
  pending_jd: 3,
  all: 41,
  scored: 20,
  shortlisted: 4,
  archived: 9,
};

const ATTENTION = {
  follow_up_today: [attentionEntry("j1"), attentionEntry("j2")],
  interview_prep: [attentionEntry("j3")],
  going_ghosted: [attentionEntry("j4")],
  enrich_errors: [],
  low_quality_scored: [],
  stuck_scoring: [],
};
const apiCalls: { url: string; method: string }[] = [];

function attentionEntry(jobId: string) {
  return {
    job_id: jobId,
    title: "Engineer",
    company: "Acme",
    url: "https://example.com",
    status: "applied",
    last_status_change_at: "2026-06-10T00:00:00Z",
    next_followup_at: null,
    notes: null,
    reason: "follow_up_due",
    days_since: 3,
  };
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const EMPTY_OVERVIEW = {
  window_days: 30,
  totals: { jobs: 0, ml_gate_passed: 0, evaluated: 0, detailed_reviewed: 0, applied: 0 },
  verdict_distribution: {},
  decision_distribution: {},
  daily: [],
};

function mockApi(personalML: Record<string, unknown> = { state: "collecting" }): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      apiCalls.push({ url, method });
      if (url === "/api/config") {
        return jsonResponse({ configured: true });
      }
      if (url === "/api/personal-ml/status") {
        return jsonResponse({
          label_count: 40,
          ranking_count: 0,
          shadow_count: 0,
          next_target: 100,
          model_threshold: null,
          quick_pass_recall: null,
          quick_fail_rejection: null,
          category_recall: null,
          baseline_rejection: null,
          estimated_call_reduction: null,
          rolling_recall: null,
          ...personalML,
        });
      }
      if (url === "/api/personal-ml/activate" && method === "POST") {
        return jsonResponse({ configured: true, scoring: { ml_gate_enabled: true } });
      }
      if (url.startsWith("/api/jobs")) {
        return jsonResponse({ jobs: [], total: 0, tab_counts: TAB_COUNTS });
      }
      if (url.startsWith("/api/attention")) {
        return jsonResponse(ATTENTION);
      }
      if (url.startsWith("/api/runs")) {
        return jsonResponse({ runs: [], total: 0 });
      }
      if (url.startsWith("/api/companies")) {
        return jsonResponse({ companies: [] });
      }
      if (url.startsWith("/api/insights/overview")) {
        return jsonResponse(EMPTY_OVERVIEW);
      }
      throw new Error(`unexpected fetch in test: ${url}`);
    }),
  );
}

function renderShell(initialPath = "/triage") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(configurationKey, { configured: true });
  return render(
    <QueryClientProvider client={queryClient}>
      <DensityProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <App />
        </MemoryRouter>
      </DensityProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  apiCalls.length = 0;
  mockApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("renders the product zones inside a nav landmark", () => {
  renderShell();

  expect(screen.getByTestId("jobfeed-workspace-layout")).toBeInTheDocument();
  const nav = screen.getByRole("navigation", { name: "Zones" });
  for (const label of ["Triage", "Library", "Insights", "Runs", "Sources"]) {
    expect(within(nav).getByRole("link", { name: new RegExp(label) })).toBeInTheDocument();
  }
});

test("does not show the misleading raw pipeline count on Triage", () => {
  renderShell();

  const nav = screen.getByRole("navigation", { name: "Zones" });
  const triage = within(nav).getByRole("link", { name: /Triage/ });
  expect(triage).toHaveTextContent("Triage");
  expect(triage).not.toHaveTextContent("12");
  expect(within(nav).queryByRole("link", { name: /Pipeline/ })).not.toBeInTheDocument();
});

test("/ redirects to /triage and marks it the current zone", () => {
  renderShell("/");

  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Triage");
  const nav = screen.getByRole("navigation", { name: "Zones" });
  expect(within(nav).getByRole("link", { name: /Triage/ })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

test("top bar title follows the active zone", () => {
  renderShell("/runs");

  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Runs");
});

test("top bar does not expose keyboard shortcut legends", () => {
  renderShell("/triage");
  expect(screen.queryByText(/j\/k|a apply|h shortlist|s skip/)).not.toBeInTheDocument();
});

test("application pipeline is not a product zone and legacy links return to triage", () => {
  renderShell("/pipeline");

  expect(screen.queryByRole("link", { name: /Pipeline/ })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Triage");
});

test("lazy-loaded insights route renders after its chunk resolves", async () => {
  renderShell("/insights");

  // The route is React.lazy (charts split out of the eager bundle) —
  // its content can only appear after the dynamic import settles.
  expect(await screen.findByRole("group", { name: "Time range" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Insights");
});

test("view menu density toggle switches the content density attribute", async () => {
  renderShell();

  const surface = screen.getByTestId("jobfeed-route-surface");
  expect(surface).toHaveAttribute("data-density", "compact");

  fireEvent.click(screen.getByRole("button", { name: "Row density" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Comfortable rows" }));

  expect(surface).toHaveAttribute("data-density", "comfortable");
  expect(window.localStorage.getItem("jobfeed:density")).toBe("comfortable");
});

test("shows a persistent readiness notice with measured shadow evidence", async () => {
  mockApi({
    state: "ready",
    label_count: 500,
    ranking_count: 200,
    shadow_count: 200,
    next_target: null,
    model_threshold: 0.71,
    quick_pass_recall: 0.96,
    quick_fail_rejection: 0.44,
    category_recall: 0.92,
    baseline_rejection: 0.08,
    estimated_call_reduction: 0.31,
    rolling_recall: 0.96,
  });

  renderShell();

  expect(await screen.findByText("Your personal job filter is ready")).toBeVisible();
  expect(screen.getAllByText(/Validated on 200 future jobs/)[0]).toHaveTextContent(
    "96% recall · 44% irrelevant jobs rejected · about 31% fewer Quick evaluations",
  );
  expect(screen.getByRole("button", { name: "Review and turn on" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Keep learning" })).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "Review and turn on" }));
  const dialog = await screen.findByRole("dialog", { name: "Turn on personal job filtering" });
  expect(within(dialog).getByText(/Jobs rejected by the filter remain recoverable in the Job library/)).toBeVisible();
  fireEvent.click(within(dialog).getByRole("button", { name: "Turn on personal filter" }));
  await waitFor(() => {
    expect(apiCalls).toContainEqual({ url: "/api/personal-ml/activate", method: "POST" });
  });
});
