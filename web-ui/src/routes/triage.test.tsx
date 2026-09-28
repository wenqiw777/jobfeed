import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { RealJobDetailResponse, JobSummary } from "@/api/queries";
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
    location: "Seattle, WA",
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
    is_repost: true,
    repost_evidence: "Reposted 1 day ago",
  };
}

function detail(): RealJobDetailResponse {
  return {
    real_job_id: "1",
    identity_review_state: "clear",
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
    sources: [{
      job_id: "1", platform: "greenhouse", url: "https://example.com/1",
      apply_url: null, title: "Software Engineer", discovered_at: "2026-06-16T00:00:00Z",
      posted_at: null, closed_at: null, is_repost: false,
    }],
    identity_evidence: [],
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
    if (method === "GET" && url.startsWith("/api/real-jobs?")) {
      return json({
        jobs: [job("scored", "1", postedAt)],
        total: 1,
        tab_counts: { queue: 1, pending_jd: 0, all: 1, scored: 1, shortlisted: 0, archived: 0 },
      });
    }
    if (method === "GET" && url === "/api/real-jobs/1") return json(detail());
    if (url === "/api/real-jobs/1/transition") return json({ job_id: "1", status: "shortlisted" });
    if (url === "/api/real-jobs/bulk/transition") {
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
  expect(screen.getByText("Reposted")).toBeInTheDocument();
  expect(screen.getByText("Seattle, WA")).toBeInTheDocument();
  expect(screen.getByText("Jun 16, 2026")).toBeInTheDocument();
  expect(screen.queryByText("viewing", { exact: true })).not.toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "First seen" })).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "Added" })).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: /Pending JD/ })).not.toBeInTheDocument();
  expect(calls.some(({ url }) => url.startsWith("/api/real-jobs?"))).toBe(true);
});

test("shows a held real job without a canonical score and opens source audit", async () => {
  const held = {
    ...job("shortlisted"),
    identity_review_state: "evaluation_conflict",
    stage_a_score: null,
    stage_b_fit_score: null,
    verdict: null,
  };
  const heldDetail = {
    ...detail(),
    identity_review_state: "evaluation_conflict",
    evaluation: { stage_a: null, stage_b: null, stage_b_status: null },
    status: { ...detail().status, decision: "wait", status: "shortlisted" },
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/real-jobs?")) return json({
      jobs: [held], total: 1,
      tab_counts: { results: 0, wait: 1, applied: 0, ignored: 0 },
    });
    if (url === "/api/real-jobs/1") return json(heldDetail);
    if (url === "/api/jobs/1/audit") return json({
      ...detail(), evaluation: { stage_a: { score: 87, one_line: "Old source score" },
        stage_b: null, stage_b_status: null },
    });
    throw new Error(`unexpected fetch: ${url}`);
  }));
  renderPage();
  fireEvent.click(screen.getByRole("tab", { name: "Wait" }));
  await screen.findByTestId("job-row-1");
  expect(screen.getAllByText(/Needs review/).length).toBeGreaterThan(0);
  expect(screen.getByText(/No current canonical score/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "View source audit" }));
  expect(await screen.findByText(/Source audit score: 87/)).toBeInTheDocument();
});

test("shows preserved historical scores without a pending label", async () => {
  const pending = {
    ...job(), identity_review_state: "clear",
    evaluation_stale_reason: null,
    stage_a_score: 90, stage_b_fit_score: null, verdict: "apply",
  };
  const pendingDetail = {
    ...detail(), evaluation_stale_reason: null,
    stale_stage_a_score: null,
    evaluation: { stage_a: { score: 90, one_line: "Fit" }, stage_b: null, stage_b_status: null },
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/real-jobs?")) return json({
      jobs: [pending], total: 1,
      tab_counts: { results: 1, wait: 0, applied: 0, ignored: 0 },
    });
    if (url === "/api/real-jobs/1") return json(pendingDetail);
    if (url === "/api/jobs/1/audit") return json({
      ...detail(), evaluation: { stage_a: { score: 30, one_line: "Old source fit" },
        stage_b: null, stage_b_status: null },
    });
    throw new Error(`unexpected fetch: ${url}`);
  }));
  renderPage();
  await screen.findByTestId("job-row-1");
  expect(screen.queryByText(/Old score|re-evaluation pending/i)).not.toBeInTheDocument();
  expect(screen.getAllByText("90").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "View source audit" }));
  expect(await screen.findByText(/Source audit score: 30/)).toBeInTheDocument();
});

test.each([
  ["input_conflict", /inputs conflict/],
  ["input_missing", /input is missing/],
])("shows %s review state without a current score", async (state, message) => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/real-jobs?")) return json({
      jobs: [{ ...job("scored"), identity_review_state: state,
        stage_a_score: null, stage_b_fit_score: null, verdict: null }],
      total: 1, tab_counts: { results: 1, wait: 0, applied: 0, ignored: 0 },
    });
    if (url === "/api/real-jobs/1") return json({
      ...detail(), identity_review_state: state,
      evaluation: { stage_a: null, stage_b: null, stage_b_status: null },
    });
    throw new Error(`unexpected fetch: ${url}`);
  }));
  renderPage();
  await screen.findByTestId("job-row-1");
  expect(screen.getByText(message)).toBeInTheDocument();
  expect(screen.getByText("Review pending")).toBeInTheDocument();
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

test("shows 25 results per page and paginates through the complete result set", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/real-jobs?")) {
      const params = new URLSearchParams(url.split("?")[1]);
      const offset = Number(params.get("offset"));
      const id = offset === 25 ? "2" : "1";
      return json({
        jobs: [job("scored", id)],
        total: 1938,
        tab_counts: { queue: 1938, pending_jd: 0, all: 1938, scored: 1938, shortlisted: 0, archived: 0 },
      });
    }
    if (url === "/api/real-jobs/1" || url === "/api/real-jobs/2") return json(detail());
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
    url.includes("limit=25")
    && url.includes("offset=25")
    && url.includes("sort=triage_posted_desc")
  )).toBe(true);
});

test("waits for globally sorted results instead of showing a differently ordered provisional page", async () => {
  let releaseExact: (() => void) | undefined;
  const exactBlocked = new Promise<void>((resolve) => {
    releaseExact = resolve;
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/real-jobs?") && url.includes("fast=true")) {
      return json({
        jobs: [job()],
        total: 1938,
        total_is_exact: false,
        tab_counts: { queue: 1938, pending_jd: 0, all: 1938, scored: 1938, shortlisted: 0, archived: 0 },
      });
    }
    if (url.startsWith("/api/real-jobs?")) {
      await exactBlocked;
      return json({
        jobs: [job()],
        total: 566,
        total_is_exact: true,
        tab_counts: { queue: 566, pending_jd: 0, all: 566, scored: 566, shortlisted: 0, archived: 0 },
      });
    }
    if (url === "/api/real-jobs/1") return json(detail());
    throw new Error(`unexpected fetch: ${url}`);
  }));

  renderPage();

  expect(screen.queryByTestId("job-row-1")).not.toBeInTheDocument();
  expect((fetch as ReturnType<typeof vi.fn>).mock.calls.some(([url]) => String(url).includes("fast=true"))).toBe(false);
  expect(screen.queryByLabelText("Results pagination")).not.toBeInTheDocument();

  releaseExact?.();
  expect(await screen.findByText("566 jobs")).toBeInTheDocument();
  expect(screen.getByLabelText("Results pagination")).toBeInTheDocument();
});

test("sorts the full result set from the Fit score and Posted headers", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  await waitFor(() => {
    expect(calls.some((call) => call.url.includes("sort=triage_posted_desc"))).toBe(true);
  });

  const table = screen.getByRole("table", { name: "Jobs" });
  fireEvent.click(within(table).getByRole("button", { name: /Fit score/ }));
  await waitFor(() => {
    expect(calls.some((call) => call.url.includes("sort=triage_score_asc"))).toBe(true);
  });
  fireEvent.click(within(table).getByRole("button", { name: /Fit score/ }));
  await waitFor(() => {
    expect(calls.some((call) => call.url.includes("sort=triage_score_desc"))).toBe(true);
  });
});

test("records Applied as a lightweight status without an application dialog", async () => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("button", { name: "Mark as applied" }));
  await waitFor(() => {
    const call = calls.find((candidate) => candidate.url === "/api/real-jobs/1/transition");
    expect(JSON.parse(String(call?.body))).toMatchObject({ to: "applied" });
  });
  expect(calls.some((call) => call.url === "/api/real-jobs/1/apply")).toBe(false);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("hides a decided Result before the transition response finishes", async () => {
  let releaseTransition: (() => void) | undefined;
  const transitionBlocked = new Promise<void>((resolve) => {
    releaseTransition = resolve;
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "GET" && url.startsWith("/api/real-jobs?")) {
      return json({
        jobs: [job()],
        total: 1,
        total_is_exact: !url.includes("fast=true"),
        tab_counts: { queue: 1, pending_jd: 0, all: 1, scored: 1, shortlisted: 0, archived: 0 },
      });
    }
    if (method === "GET" && url === "/api/real-jobs/1") return json(detail());
    if (method === "POST" && url === "/api/real-jobs/1/transition") {
      await transitionBlocked;
      return json({ job_id: "1", status: "applied" });
    }
    throw new Error(`unexpected fetch: ${method} ${url}`);
  }));

  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("button", { name: "Mark as applied" }));

  await waitFor(() => {
    expect(screen.queryByTestId("job-row-1")).not.toBeInTheDocument();
  });
  expect(screen.getByText("0 jobs")).toBeInTheDocument();
  releaseTransition?.();
});

test("restores an optimistically hidden Result when the transition fails", async () => {
  let releaseTransition: (() => void) | undefined;
  const transitionBlocked = new Promise<void>((resolve) => {
    releaseTransition = resolve;
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "GET" && url.startsWith("/api/real-jobs?")) {
      return json({
        jobs: [job()],
        total: 1,
        total_is_exact: !url.includes("fast=true"),
        tab_counts: { queue: 1, pending_jd: 0, all: 1, scored: 1, shortlisted: 0, archived: 0 },
      });
    }
    if (method === "GET" && url === "/api/real-jobs/1") return json(detail());
    if (method === "POST" && url === "/api/real-jobs/1/transition") {
      await transitionBlocked;
      return new Response(JSON.stringify({ detail: "save failed" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      });
    }
    throw new Error(`unexpected fetch: ${method} ${url}`);
  }));

  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("button", { name: "Mark as applied" }));
  await waitFor(() => {
    expect(screen.queryByTestId("job-row-1")).not.toBeInTheDocument();
  });

  releaseTransition?.();
  expect(await screen.findByTestId("job-row-1")).toBeInTheDocument();
  expect(screen.getByText("1 jobs")).toBeInTheDocument();
});

test("Mark as applied removes the Result before the exact refresh finishes", async () => {
  let wasApplied = false;
  let releaseRefresh: (() => void) | undefined;
  const refreshBlocked = new Promise<void>((resolve) => {
    releaseRefresh = resolve;
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const method = url === "/api/real-jobs/1/transition" ? "POST" : "GET";
    if (url.startsWith("/api/real-jobs?")) {
      if (wasApplied) await refreshBlocked;
      return json({
        jobs: wasApplied ? [] : [job()],
        total: wasApplied ? 0 : 1,
        tab_counts: { queue: wasApplied ? 0 : 1, pending_jd: 0, all: 1, scored: wasApplied ? 0 : 1, shortlisted: 0, archived: 0 },
      });
    }
    if (url === "/api/real-jobs/1") return json(detail());
    if (method === "POST") {
      wasApplied = true;
      return json({ job_id: "1", status: "applied" });
    }
    throw new Error(`unexpected fetch: ${url}`);
  }));

  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("button", { name: "Mark as applied" }));

  await waitFor(() => {
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls.some(
      ([input]) => String(input) === "/api/real-jobs/1/transition",
    )).toBe(true);
  });
  await waitFor(() => {
    expect(screen.queryByTestId("job-row-1")).not.toBeInTheDocument();
  });
  expect(screen.getByText("0 jobs")).toBeInTheDocument();
  releaseRefresh?.();
});

test.each([
  ["Move to Wait", "shortlisted"],
  ["Ignore", "ignored"],
])("records %s through the status endpoint", async (label, expectedStatus) => {
  renderPage();
  await screen.findByTestId("job-row-1");
  fireEvent.click(screen.getByRole("button", { name: label }));
  await waitFor(() => {
    const call = calls.find((candidate) => candidate.url === "/api/real-jobs/1/transition");
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
    expect(filtered?.url).not.toContain("dedupe=");
    expect(filtered?.url).not.toContain("statuses=");
  });
  expect(screen.queryByRole("button", { name: "Mark as applied" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Move to Results" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Move to Wait" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ignore" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Move to Results" }));
  await waitFor(() => {
    const call = calls.find((candidate) => candidate.url === "/api/real-jobs/1/transition");
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

test("select all uses one canonical selection snapshot for bulk IDs", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/real-jobs?")) {
      return json({ jobs: [job("scored", "10")], total: 3, tab_counts: { results: 3, wait: 0, applied: 0, ignored: 0 } });
    }
    if (url === "/api/real-jobs/10") return json(detail());
    if (url.startsWith("/api/real-jobs/selection?")) {
      return json({ real_job_ids: ["10", "11", "12"], total: 3 });
    }
    if (url === "/api/real-jobs/bulk/transition") {
      return json({ succeeded: 3, skipped: 0, failed: [], cascaded: 0 });
    }
    throw new Error(`unexpected fetch: ${url}`);
  }));
  renderPage();
  await screen.findByTestId("job-row-10");
  fireEvent.click(screen.getByRole("checkbox", { name: /Select Readable Co/ }));
  fireEvent.click(screen.getByRole("button", { name: "Select all 3 results" }));
  await waitFor(() => {
    expect(screen.getByRole("toolbar", { name: "Bulk actions" })).toHaveTextContent("3 selected");
  });
  const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls;
  expect(calls.filter(([input]) => String(input).startsWith("/api/real-jobs/selection?"))).toHaveLength(1);
  expect(calls.filter(([input]) => String(input).startsWith("/api/real-jobs?")).length).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "Ignore selected" }));
  await waitFor(() => {
    const bulk = calls.find(([input]) => String(input) === "/api/real-jobs/bulk/transition");
    expect(JSON.parse(String(bulk?.[1]?.body)).items.map(({ id }: { id: string }) => id)).toEqual(["10", "11", "12"]);
  });
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
    if (url.startsWith("/api/real-jobs?")) {
      if (wasIgnored) await refreshBlocked;
      return json({
        jobs: wasIgnored ? [] : [job()],
        total: wasIgnored ? 0 : 1,
        tab_counts: { queue: wasIgnored ? 0 : 1, pending_jd: 0, all: 1, scored: wasIgnored ? 0 : 1, shortlisted: 0, archived: wasIgnored ? 1 : 0 },
      });
    }
    if (url === "/api/real-jobs/1") return json(detail());
    if (url === "/api/real-jobs/bulk/transition") {
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
      ([input]) => String(input) === "/api/real-jobs/bulk/transition",
    )).toBe(true);
  });
  await waitFor(() => {
    expect(screen.queryByTestId("job-row-1")).not.toBeInTheDocument();
  });
  expect(screen.getByText("0 jobs")).toBeInTheDocument();
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
    if (url.startsWith("/api/real-jobs?")) {
      if (wasSubmitted) await refreshBlocked;
      return json({
        jobs: [job(), job("scored", "2")],
        total: 2,
        tab_counts: { queue: 2, pending_jd: 0, all: 2, scored: 2, shortlisted: 0, archived: 0 },
      });
    }
    if (url === "/api/real-jobs/1" || url === "/api/real-jobs/2") return json(detail());
    if (url === "/api/real-jobs/bulk/transition") {
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
      ([input]) => String(input) === "/api/real-jobs/bulk/transition",
    )).toBe(true);
  });
  await waitFor(() => {
    expect(screen.queryByTestId("job-row-1")).not.toBeInTheDocument();
  });
  expect(screen.getByTestId("job-row-2")).toBeInTheDocument();
  expect(screen.getByRole("toolbar", { name: "Bulk actions" })).toHaveTextContent("1 selected");
  releaseRefresh?.();
});

test("bulk move refreshes the selected real job detail", async () => {
  let status = "scored";
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/real-jobs?")) {
      const decision = new URLSearchParams(url.split("?")[1]).get("decision");
      const visible = (decision === "results" && status === "scored")
        || (decision === "wait" && status === "shortlisted");
      return json({
        jobs: visible ? [job(status)] : [], total: visible ? 1 : 0,
        tab_counts: { results: status === "scored" ? 1 : 0, wait: status === "shortlisted" ? 1 : 0, applied: 0, ignored: 0 },
      });
    }
    if (url === "/api/real-jobs/1") {
      return json({ ...detail(), status: { ...detail().status, status, decision: status === "scored" ? "results" : "wait" } });
    }
    if (url === "/api/real-jobs/bulk/transition") {
      status = "shortlisted";
      return json({ succeeded: 1, skipped: 0, failed: [], cascaded: 0 });
    }
    throw new Error(`unexpected fetch: ${url}`);
  }));

  renderPage();
  await screen.findByText("Ready for decision");
  fireEvent.click(screen.getByRole("checkbox", { name: /Select Readable Co/ }));
  fireEvent.click(screen.getByRole("button", { name: "Move selected to Wait" }));
  fireEvent.click(screen.getByRole("tab", { name: "Wait" }));
  await screen.findByTestId("job-row-1");
  await waitFor(() => expect(screen.getByText("Wait")).toBeInTheDocument());
  expect(screen.queryByText("Ready for decision")).not.toBeInTheDocument();
});

test("select all loads every matching real ID, not only the current page", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/real-jobs?")) {
      return json({
        jobs: [job()],
        total: 2,
        tab_counts: { queue: 2, pending_jd: 0, all: 2, scored: 2, shortlisted: 0, archived: 0 },
      });
    }
    if (url.startsWith("/api/real-jobs/selection?")) {
      return json({ real_job_ids: ["1", "2"], total: 2 });
    }
    if (url === "/api/real-jobs/1") return json(detail());
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
  expect(requests.filter((url) => url.startsWith("/api/real-jobs/selection?"))).toHaveLength(1);
  expect(requests.some((url) => {
    if (!url.startsWith("/api/real-jobs?")) return false;
    const params = new URLSearchParams(url.split("?")[1]);
    return params.get("limit") === "2" && params.get("offset") === "0";
  })).toBe(false);
});
