import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
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
  totals: { jobs: 0, ml_gate_passed: 0, evaluated: 0, applied: 0 },
  verdict_distribution: {},
  status_distribution: {},
  daily: [],
};

function mockApi(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/config") {
        return jsonResponse({ configured: true });
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

test("shows the result count on Triage without a pipeline badge", async () => {
  renderShell();

  const nav = screen.getByRole("navigation", { name: "Zones" });
  const triage = within(nav).getByRole("link", { name: /Triage/ });
  await screen.findByRole("link", { name: /Triage 12/ });
  expect(triage).toHaveTextContent("12");
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
