import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { JobDetailResponse, JobSummary } from "@/api/queries";
import { DensityProvider } from "@/lib/density";
import TriagePage from "@/routes/triage";

const calls: { url: string; method: string; body: BodyInit | null | undefined }[] = [];

function job(
  status = "scored",
  id = "1",
  postedAt: string | null = "2026-06-16T00:00:00Z",
): JobSummary {
  return {
    id,
    company: "Readable Co",
    title: "Software Engineer",
    platform: "greenhouse",
    url: "https://example.com/1",
    status,
    decision: status === "ignored" ? "ignored" : "results",
    verdict: "apply",
    stage_a_score: 84,
    stage_b_fit_score: 92,
    stage_b_status: "completed",
    jd_quality: "full",
    company_norm: "readable co",
    title_norm: "software engineer",
    posted_at: postedAt,
    discovered_at: "2026-06-16T12:00:00Z",
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
      posted_at: null,
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
    status: { status: "scored", decision: "results", history: [], notes: null, next_followup_at: null, resume_variant: null },
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

function mockApi(postedAt: string | null = "2026-06-16T00:00:00Z"): void {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body });
    if (method === "GET" && url.startsWith("/api/jobs?")) {
      return json({
        jobs: [job("scored", "1", postedAt)],
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
  mockApi(null);
  renderPage();
  await screen.findByTestId("job-row-1");
  for (const label of ["Results", "Wait", "Applied", "Ignored"]) {
    expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
  }
  expect(screen.queryByText("viewing", { exact: true })).not.toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Posted" })).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "Added" })).not.toBeInTheDocument();
  expect(await screen.findByText("~Jun 16, 2026")).toBeInTheDocument();
  expect(await screen.findByText("Estimated from date added")).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: /Pending JD/ })).not.toBeInTheDocument();
});

test("Ignored requests one decision that includes archived workflow rows", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("tab", { name: "Ignored" }));
  await waitFor(() => {
    expect(calls.some(({ url }) => url.includes("decision=ignored"))).toBe(true);
  });
  expect(calls.some(({ url }) => url.includes("statuses=archived"))).toBe(false);
});

test("shows 50 results per page and paginates through the complete result set", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/jobs?")) {
      const params = new URLSearchParams(url.split("?")[1]);
      const offset = Number(params.get("offset"));
      const id = offset === 50 ? "2" : "1";
      return json({
        jobs: [job("scored", id)],
        total: 1938,
        tab_counts: { queue: 1938, pending_jd: 0, all: 1938, scored: 1938, shortlisted: 0, archived: 0 },
      });
    }
    if (url === "/api/jobs/1" || url === "/api/jobs/2") return json(detail());
    throw new Error(`unexpected fetch: ${url}`);
  }));

  renderPage();
  await screen.findByTestId("job-row-1");
  expect(calls).toEqual([]);
  expect(screen.getByLabelText("Results pagination")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Page 2" }));
  await screen.findByTestId("job-row-2");

  const requests = (fetch as ReturnType<typeof vi.fn>).mock.calls.map(([input]) => String(input));
  expect(requests.some((url) =>
    url.includes("limit=50")
    && url.includes("offset=50")
    && url.includes("sort=posted_desc")
  )).toBe(true);
});

test("sorts the full result set from the Fit score and Posted headers", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  await waitFor(() => {
    expect(calls.some((call) => call.url.includes("sort=posted_desc"))).toBe(true);
  });

  const table = screen.getByRole("table", { name: "Jobs" });
  fireEvent.click(within(table).getByRole("button", { name: /Fit score/ }));
  await waitFor(() => {
    expect(calls.some((call) => call.url.includes("sort=score_asc"))).toBe(true);
  });
  fireEvent.click(within(table).getByRole("button", { name: /Fit score/ }));
  await waitFor(() => {
    expect(calls.some((call) => call.url.includes("sort=score_desc"))).toBe(true);
  });
});

test("records Applied as a lightweight status without an application dialog", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("button", { name: "Mark as applied" }));
  await waitFor(() => {
    const call = calls.find((candidate) => candidate.url === "/api/jobs/1/transition");
    expect(JSON.parse(String(call?.body))).toMatchObject({ to: "applied" });
  });
  expect(calls.some((call) => call.url === "/api/jobs/1/apply")).toBe(false);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test.each([
  ["Move to Wait", "shortlisted"],
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

test("decision filters send compatible status groups and allow correcting mistakes", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("tab", { name: "Applied" }));
  await waitFor(() => {
    const filtered = calls.find((call) => call.url.includes("decision=applied"));
    expect(filtered?.url).toContain("decision=applied");
    expect(filtered?.url).not.toContain("statuses=");
  });
  expect(screen.queryByRole("button", { name: "Mark as applied" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Move to Results" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Move to Wait" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ignore" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Move to Results" }));
  await waitFor(() => {
    const call = calls.find((candidate) => candidate.url === "/api/jobs/1/transition");
    expect(JSON.parse(String(call?.body))).toMatchObject({ to: "scored" });
  });
});

test("bulk actions name both the destination and affected selection", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("checkbox", { name: /Select Readable Co/ }));
  const toolbar = screen.getByRole("toolbar", { name: "Bulk actions" });
  expect(toolbar).toHaveTextContent("Move selected to Wait");
  expect(toolbar).toHaveTextContent("Ignore selected");
  expect(
    within(toolbar).getByRole("group", { name: "Decision actions" }),
  ).toBeInTheDocument();
  expect(
    within(toolbar).getByRole("group", { name: "Selection controls" }),
  ).toBeInTheDocument();
  for (const label of ["Move selected to Wait", "Ignore selected", "Select this page", "Select all 1 result", "Clear selection"]) {
    expect(within(toolbar).getByRole("button", { name: label })).toBeInTheDocument();
  }
  expect(screen.queryByText("Shortlist")).not.toBeInTheDocument();
  expect(screen.queryByText("Skip")).not.toBeInTheDocument();
});

test("bulk actions hide the current tab's no-op destination", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("tab", { name: "Ignored" }));
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("checkbox", { name: /Select Readable Co/ }));

  const toolbar = screen.getByRole("toolbar", { name: "Bulk actions" });
  expect(within(toolbar).queryByRole("button", { name: "Ignore selected" })).toBeNull();
  expect(
    within(toolbar).getByRole("button", { name: "Move selected to Wait" }),
  ).toBeInTheDocument();
});

test("bulk Ignore removes successful Results immediately and updates the count", async () => {
  let wasIgnored = false;
  let releaseRefresh: (() => void) | undefined;
  const refreshBlocked = new Promise<void>((resolve) => {
    releaseRefresh = resolve;
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/jobs?")) {
      if (wasIgnored) await refreshBlocked;
      return json({
        jobs: wasIgnored ? [] : [job()],
        total: wasIgnored ? 0 : 1,
        tab_counts: { queue: wasIgnored ? 0 : 1, pending_jd: 0, all: 1, scored: wasIgnored ? 0 : 1, shortlisted: 0, archived: wasIgnored ? 1 : 0 },
      });
    }
    if (url === "/api/jobs/1") return json(detail());
    if (url === "/api/jobs/bulk/transition") {
      wasIgnored = true;
      return json({ succeeded: 1, skipped: 0, failed: [], cascaded: 0 });
    }
    throw new Error(`unexpected fetch: ${url}`);
  }));

  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("checkbox", { name: /Select Readable Co/ }));
  fireEvent.click(screen.getByRole("button", { name: "Ignore selected" }));

  await waitFor(() => {
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls.some(
      ([input]) => String(input) === "/api/jobs/bulk/transition",
    )).toBe(true);
  });
  await waitFor(() => {
    expect(screen.queryByTestId("job-row-1")).not.toBeInTheDocument();
  });
  expect(screen.getByText("0 postings")).toBeInTheDocument();
  expect(screen.queryByRole("toolbar", { name: "Bulk actions" })).not.toBeInTheDocument();
  releaseRefresh?.();
});

test("bulk Ignore retains failed selections and reports the partial failure", async () => {
  let wasSubmitted = false;
  let releaseRefresh: (() => void) | undefined;
  const refreshBlocked = new Promise<void>((resolve) => {
    releaseRefresh = resolve;
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/jobs?")) {
      if (wasSubmitted) await refreshBlocked;
      return json({
        jobs: [job(), job("scored", "2")],
        total: 2,
        tab_counts: { queue: 2, pending_jd: 0, all: 2, scored: 2, shortlisted: 0, archived: 0 },
      });
    }
    if (url === "/api/jobs/1" || url === "/api/jobs/2") return json(detail());
    if (url === "/api/jobs/bulk/transition") {
      wasSubmitted = true;
      return json({
        succeeded: 1,
        skipped: 0,
        failed: [{ id: "2", error: "transition blocked" }],
        cascaded: 0,
      });
    }
    throw new Error(`unexpected fetch: ${url}`);
  }));

  renderPage();
  await screen.findByTestId("job-row-1");
  const selectionBoxes = screen.getAllByRole("checkbox", { name: /Select Readable Co/ });
  fireEvent.click(selectionBoxes[0]!);
  fireEvent.click(selectionBoxes[1]!);
  await waitFor(() => {
    expect(screen.getByRole("toolbar", { name: "Bulk actions" })).toHaveTextContent("2 selected");
  });
  fireEvent.click(screen.getByRole("button", { name: "Ignore selected" }));

  await waitFor(() => {
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls.some(
      ([input]) => String(input) === "/api/jobs/bulk/transition",
    )).toBe(true);
  });
  await waitFor(() => {
    expect(screen.queryByTestId("job-row-1")).not.toBeInTheDocument();
  });
  expect(screen.getByTestId("job-row-2")).toBeInTheDocument();
  expect(screen.getByRole("toolbar", { name: "Bulk actions" })).toHaveTextContent("1 selected");
  releaseRefresh?.();
});

test("select all loads every matching result, not only the current page", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/jobs?")) {
      const params = new URLSearchParams(url.split("?")[1]);
      const jobs = params.get("limit") === "2" ? [job(), job("scored", "2")] : [job()];
      return json({
        jobs,
        total: 2,
        tab_counts: { queue: 2, pending_jd: 0, all: 2, scored: 2, shortlisted: 0, archived: 0 },
      });
    }
    if (url === "/api/jobs/1") return json(detail());
    throw new Error(`unexpected fetch: ${url}`);
  }));

  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("checkbox", { name: /Select Readable Co/ }));
  fireEvent.click(screen.getByRole("button", { name: "Select all 2 results" }));

  await waitFor(() => {
    expect(screen.getByRole("toolbar", { name: "Bulk actions" })).toHaveTextContent(
      "2 selected",
    );
  });
  const requests = (fetch as ReturnType<typeof vi.fn>).mock.calls.map(([input]) => String(input));
  expect(requests.some((url) => url.includes("limit=2") && url.includes("offset=0"))).toBe(true);
});
