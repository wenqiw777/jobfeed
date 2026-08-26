import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { JobSummary } from "@/api/queries";
import { DensityProvider } from "@/lib/density";
import LibraryPage from "@/routes/library";

const calls: string[] = [];

function row(status: string, postedAt: string | null = "2026-06-16T00:00:00Z"): JobSummary {
  return {
    id: "1", company: "Acme", title: "Engineer", platform: "lever",
    url: "https://example.com", status, decision: "results", verdict: "apply", stage_a_score: 80,
    stage_b_fit_score: 90, stage_b_status: "completed", jd_quality: "full",
    company_norm: "acme", title_norm: "engineer", posted_at: postedAt,
    discovered_at: "2026-06-16T12:00:00Z", closed_at: null,
  };
}

function mockApi(postedAt: string | null = "2026-06-16T00:00:00Z"): void {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.startsWith("/api/jobs?")) {
      const params = new URLSearchParams(url.split("?")[1]);
      const status = params.get("decision") ?? "results";
      return new Response(JSON.stringify({
        jobs: [row(status, postedAt)], total: 1,
        tab_counts: { queue: 1, pending_jd: 0, all: 1, scored: 1, shortlisted: 0, archived: 0 },
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    throw new Error(`unexpected fetch: ${url}`);
  }));
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><DensityProvider><LibraryPage /></DensityProvider></QueryClientProvider>);
}

beforeEach(() => { calls.length = 0; mockApi(); });
afterEach(() => vi.unstubAllGlobals());

test("offers only the simplified decision filters", async () => {
  mockApi(null);
  renderPage();
  await screen.findByTestId("job-row-1");
  for (const label of ["All", "Wait", "Applied", "Ignored"]) {
    expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
  }
  expect(screen.queryByText("viewing", { exact: true })).not.toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Posted" })).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "Added" })).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: /Shortlisted|Archived|Scored/ })).not.toBeInTheDocument();
  expect(await screen.findByText("~Jun 16, 2026")).toBeInTheDocument();
});

test("Applied filter groups historical application statuses", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("tab", { name: "Applied" }));
  await waitFor(() => {
    const url = calls.find((value) => value.includes("decision=applied"));
    expect(url).toBeDefined();
  });
  expect(screen.getByText("Applied")).toBeInTheDocument();
});

test("uses 50 rows for every library page", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  expect(calls.some((url) => url.includes("limit=50"))).toBe(true);
});

test("sorts all postings from the Fit score and Posted headers", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");

  const table = screen.getByRole("table", { name: "Library jobs" });
  const scoreSort = within(table).getByText("Fit score").closest("[role=button]");
  expect(scoreSort).not.toBeNull();
  fireEvent.click(scoreSort!);
  await waitFor(() => expect(calls.some((url) => url.includes("sort=score_asc"))).toBe(true));
  fireEvent.click(scoreSort!);
  await waitFor(() => expect(calls.some((url) => url.includes("sort=score_desc"))).toBe(true));
});
