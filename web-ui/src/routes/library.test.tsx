import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { JobSummary } from "@/api/queries";
import { DensityProvider } from "@/lib/density";
import LibraryPage from "@/routes/library";

const calls: string[] = [];

function row(status: string): JobSummary {
  return {
    id: "1", company: "Acme", title: "Engineer", platform: "lever",
    url: "https://example.com", status, verdict: "apply", stage_a_score: 80,
    stage_b_fit_score: 90, stage_b_status: "completed", jd_quality: "full",
    company_norm: "acme", title_norm: "engineer", posted_at: "2026-06-16T00:00:00Z",
    discovered_at: "2026-06-16T00:00:00Z", closed_at: null,
  };
}

function mockApi(): void {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.startsWith("/api/jobs?")) {
      const params = new URLSearchParams(url.split("?")[1]);
      const status = params.getAll("statuses")[0] ?? "scored";
      return new Response(JSON.stringify({
        jobs: [row(status)], total: 1,
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
  renderPage();
  await screen.findByTestId("job-row-1");
  for (const label of ["All", "Wait", "Applied", "Ignored"]) {
    expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
  }
  expect(screen.queryByRole("tab", { name: /Shortlisted|Archived|Scored/ })).not.toBeInTheDocument();
});

test("Applied filter groups historical application statuses", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("tab", { name: "Applied" }));
  await waitFor(() => {
    const url = calls.find((value) => value.includes("statuses=interviewing"));
    expect(url).toContain("statuses=applied");
    expect(url).toContain("statuses=offer");
    expect(url).toContain("statuses=rejected");
  });
  expect(screen.getByText("Applied")).toBeInTheDocument();
});
