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
  applications: {
    applied_count: 0,
    response_count: 0,
    interview_count: 0,
    offer_count: 0,
    rejection_count: 0,
    median_days_to_response: null,
    by_resume: {},
  },
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

test("renders the six zone links inside a nav landmark", () => {
  renderShell();

  const nav = screen.getByRole("navigation", { name: "Zones" });
  for (const label of ["Triage", "Pipeline", "Library", "Insights", "Runs", "Sources"]) {
    expect(within(nav).getByRole("link", { name: new RegExp(label) })).toBeInTheDocument();
  }
});

test("shows the queue count on Triage and the workflow attention total on Pipeline", async () => {
  renderShell();

  const nav = screen.getByRole("navigation", { name: "Zones" });
  const triage = within(nav).getByRole("link", { name: /Triage/ });
  const pipeline = within(nav).getByRole("link", { name: /Pipeline/ });

  expect(await within(triage).findByText("12")).toBeInTheDocument();
  // 2 follow-ups + 1 interview prep + 1 going ghosted = 4.
  expect(await within(pipeline).findByText("4")).toBeInTheDocument();
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

test("top bar keyboard hints follow the active zone's keyboard map", () => {
  const triage = renderShell("/triage");
  expect(
    screen.getByText("j/k move · a apply · h shortlist · s skip"),
  ).toBeInTheDocument();
  triage.unmount();

  const pipeline = renderShell("/pipeline");
  expect(screen.getByText("j/k move · enter detail · o open")).toBeInTheDocument();
  pipeline.unmount();

  // Library has no keyboard map — no hints at all.
  renderShell("/library");
  expect(screen.queryByText(/j\/k move/)).not.toBeInTheDocument();
});

test("lazy-loaded insights route renders after its chunk resolves", async () => {
  renderShell("/insights");

  // The route is React.lazy (recharts split out of the eager bundle) —
  // its content can only appear after the dynamic import settles.
  expect(await screen.findByRole("group", { name: "Window" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Insights");
});

test("view menu density toggle switches the content density attribute", async () => {
  renderShell();

  const main = screen.getByRole("main");
  expect(main).toHaveAttribute("data-density", "compact");

  // Radix dropdown triggers open on pointerdown, not click.
  fireEvent.pointerDown(
    screen.getByRole("button", { name: "View" }),
    { button: 0, ctrlKey: false },
  );
  fireEvent.click(await screen.findByRole("menuitemradio", { name: "Comfortable" }));

  expect(main).toHaveAttribute("data-density", "comfortable");
  expect(window.localStorage.getItem("jobfeed:density")).toBe("comfortable");
});
