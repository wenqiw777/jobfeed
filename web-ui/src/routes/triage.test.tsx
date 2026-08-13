import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { JobDetailResponse, JobSummary } from "@/api/queries";
import { DensityProvider } from "@/lib/density";
import TriagePage from "@/routes/triage";

const calls: { url: string; method: string; body: BodyInit | null | undefined }[] = [];

function job(status = "scored"): JobSummary {
  return {
    id: "1",
    company: "Readable Co",
    title: "Software Engineer",
    platform: "greenhouse",
    url: "https://example.com/1",
    status,
    verdict: "apply",
    stage_a_score: 84,
    stage_b_fit_score: 92,
    stage_b_status: "completed",
    jd_quality: "full",
    company_norm: "readable co",
    title_norm: "software engineer",
    posted_at: "2026-06-16T00:00:00Z",
    discovered_at: "2026-06-16T00:00:00Z",
    closed_at: null,
  };
}

function detail(): JobDetailResponse {
  return {
    job: {
      id: "1",
      canonical_id: "canonical-1",
      company: "Readable Co",
      title: "Software Engineer",
      location: "Remote",
      platform: "greenhouse",
      url: "https://example.com/1",
      posted_at: "2026-06-16T00:00:00Z",
      discovered_at: "2026-06-16T00:00:00Z",
      closed_at: null,
      jd_quality: "full",
      jd_text: "Build clear software.",
    },
    evaluation: {
      stage_a: { score: 84, one_line: "Strong match." },
      stage_b_status: "completed",
      stage_b: {
        fit_score: 92,
        verdict: "apply",
        jd_summary: "A readable role summary.",
        strengths: [],
        gaps: [],
        hooks: { lead_with: "Systems work", supporting: [], avoid_mentioning: [] },
      },
    },
    status: { status: "scored", history: [], notes: null, next_followup_at: null, resume_variant: null },
    twins: [],
    interviews: [],
    application: null,
  };
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function mockApi(): void {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body });
    if (method === "GET" && url.startsWith("/api/jobs?")) {
      return json({
        jobs: [job()],
        total: 1,
        tab_counts: { queue: 1, pending_jd: 0, all: 1, scored: 1, shortlisted: 0, archived: 0 },
      });
    }
    if (method === "GET" && url === "/api/jobs/1") return json(detail());
    if (url === "/api/jobs/1/transition") return json({ job_id: "1", status: "shortlisted" });
    if (url === "/api/jobs/bulk/transition") {
      return json({ succeeded: 1, skipped: 0, failed: [], cascaded: 0 });
    }
    throw new Error(`unexpected fetch: ${method} ${url}`);
  }));
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DensityProvider><TriagePage /></DensityProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls.length = 0;
  mockApi();
});

afterEach(() => vi.unstubAllGlobals());

test("shows Results plus the three decision filters", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  for (const label of ["Results", "Wait", "Applied", "Ignored"]) {
    expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
  }
  expect(screen.queryByRole("tab", { name: /Pending JD/ })).not.toBeInTheDocument();
});

test("records Applied as a lightweight status without an application dialog", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("button", { name: "Applied" }));
  await waitFor(() => {
    const call = calls.find((candidate) => candidate.url === "/api/jobs/1/transition");
    expect(JSON.parse(String(call?.body))).toMatchObject({ to: "applied" });
  });
  expect(calls.some((call) => call.url === "/api/jobs/1/apply")).toBe(false);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test.each([
  ["Wait", "shortlisted"],
  ["Ignore", "ignored"],
])("records %s through the status endpoint", async (label, expectedStatus) => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("button", { name: label }));
  await waitFor(() => {
    const call = calls.find((candidate) => candidate.url === "/api/jobs/1/transition");
    expect(JSON.parse(String(call?.body))).toMatchObject({ to: expectedStatus });
  });
});

test("decision filters send compatible legacy status groups and hide actions", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("tab", { name: "Applied" }));
  await waitFor(() => {
    const filtered = calls.find((call) => call.url.includes("statuses=interviewing"));
    expect(filtered?.url).toContain("statuses=applied");
    expect(filtered?.url).toContain("statuses=offer");
  });
  expect(screen.queryByRole("button", { name: "Applied" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Wait" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Ignore" })).not.toBeInTheDocument();
});

test("bulk actions use the same Wait and Ignore language", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("checkbox", { name: /Select Readable Co/ }));
  expect(screen.getByRole("toolbar", { name: "Bulk actions" })).toHaveTextContent("Wait");
  expect(screen.getByRole("toolbar", { name: "Bulk actions" })).toHaveTextContent("Ignore");
  expect(screen.queryByText("Shortlist")).not.toBeInTheDocument();
  expect(screen.queryByText("Skip")).not.toBeInTheDocument();
});
