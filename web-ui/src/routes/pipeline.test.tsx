import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type {
  AttentionResponse,
  InterviewRound,
  JobDetailResponse,
  JobSummary,
} from "@/api/queries";
import { Toaster } from "@/components/ui/toaster";
import { DensityProvider } from "@/lib/density";
import PipelinePage from "@/routes/pipeline";

type AttentionEntry = AttentionResponse["follow_up_today"][number];

function jobRow(id: string, status: string, over: Partial<JobSummary> = {}): JobSummary {
  return {
    id,
    company: `Co${id}`,
    title: `Title${id}`,
    platform: "greenhouse",
    url: `https://example.com/${id}`,
    status,
    verdict: null,
    stage_a_score: 80,
    stage_b_fit_score: null,
    stage_b_status: null,
    jd_quality: "full",
    company_norm: `co${id}`,
    title_norm: `title${id}`,
    posted_at: "2026-06-01T00:00:00Z",
    discovered_at: "2026-06-01T00:00:00Z",
    closed_at: null,
    ...over,
  };
}

function attentionEntry(jobId: string): AttentionEntry {
  return {
    job_id: jobId,
    company: `Co${jobId}`,
    title: `Title${jobId}`,
    status: "applied",
    reason: "follow-up due",
    days_since: 3,
    last_status_change_at: "2026-06-01T00:00:00Z",
    next_followup_at: null,
    notes: null,
    url: `https://example.com/${jobId}`,
  };
}

function detailFor(id: string, status: string): JobDetailResponse {
  return {
    job: {
      id,
      canonical_id: `c${id}`,
      company: `Co${id}`,
      title: `Title${id}`,
      location: "Remote — US",
      platform: "greenhouse",
      url: `https://example.com/${id}`,
      posted_at: "2026-06-01T00:00:00Z",
      discovered_at: "2026-06-01T00:00:00Z",
      closed_at: null,
      jd_quality: "full",
      jd_text: "JD body",
    },
    evaluation: { stage_a: null, stage_b_status: "pending", stage_b: null },
    status: { status, history: [], notes: null, next_followup_at: null, resume_variant: null },
    twins: [],
    interviews: [],
    application: null,
  };
}

interface ServerState {
  rows: JobSummary[];
  attention: AttentionResponse;
  interviews: Record<string, InterviewRound[]>;
}

interface RecordedCall {
  url: string;
  method: string;
  init: RequestInit | undefined;
}

const calls: RecordedCall[] = [];

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** Fixture matrix: two applied, one each of interviewing/offer/rejected/
 * ghosted — so Closed merges two statuses. going_ghosted stays empty to
 * pin the empty-bucket-hidden rule. */
function defaultState(): ServerState {
  return {
    rows: [
      jobRow("a1", "applied"),
      jobRow("a2", "applied"),
      jobRow("i1", "interviewing"),
      jobRow("o1", "offer"),
      jobRow("r1", "rejected"),
      jobRow("g1", "ghosted"),
    ],
    attention: {
      follow_up_today: [attentionEntry("a1")],
      interview_prep: [attentionEntry("i1")],
      going_ghosted: [],
      enrich_errors: [],
      low_quality_scored: [],
      stuck_scoring: [],
    },
    interviews: {},
  };
}

function handleInterviews(state: ServerState, url: string, method: string, init?: RequestInit) {
  const collection = /^\/api\/jobs\/(\w+)\/interviews$/.exec(url);
  if (collection !== null && method === "GET") {
    return json({ interviews: state.interviews[collection[1]!] ?? [] });
  }
  if (collection !== null && method === "POST") {
    const body = JSON.parse(String(init?.body)) as { label: string; scheduled_at: string | null };
    const rounds = (state.interviews[collection[1]!] ??= []);
    const round: InterviewRound = {
      round_index: rounds.length + 1,
      label: body.label,
      scheduled_at: body.scheduled_at,
      completed_at: null,
      notes: null,
    };
    rounds.push(round);
    return json(round);
  }
  const complete = /^\/api\/jobs\/(\w+)\/interviews\/(\d+)$/.exec(url);
  if (complete !== null && method === "PATCH") {
    const body = JSON.parse(String(init?.body)) as { notes: string | null };
    const round = state.interviews[complete[1]!]?.find(
      (item) => item.round_index === Number(complete[2]),
    );
    if (round === undefined) {
      throw new Error(`no round ${complete[2]} for ${complete[1]} in test state`);
    }
    round.completed_at = "2026-06-11T12:00:00Z";
    round.notes = body.notes;
    return json(round);
  }
  return null;
}

/** Stateful fetch stub: writes mutate the in-memory state so the query
 * invalidation's refetch sees the post-write world. */
function mockApi(state: ServerState): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, init });

      if (method === "GET" && url.startsWith("/api/jobs?")) {
        return json({ jobs: state.rows, total: state.rows.length, tab_counts: {} });
      }
      if (method === "GET" && url === "/api/attention") {
        return json(state.attention);
      }
      const interviewResponse = handleInterviews(state, url, method, init);
      if (interviewResponse !== null) {
        return interviewResponse;
      }
      const transition = /^\/api\/jobs\/(\w+)\/transition$/.exec(url);
      if (transition !== null && method === "POST") {
        const body = JSON.parse(String(init?.body)) as { to: string };
        const row = state.rows.find((job) => job.id === transition[1]);
        if (row !== undefined) {
          row.status = body.to;
        }
        return json({ job_id: transition[1], status: body.to });
      }
      const restore = /^\/api\/jobs\/(\w+)\/restore$/.exec(url);
      if (restore !== null && method === "POST") {
        const row = state.rows.find((job) => job.id === restore[1]);
        if (row !== undefined) {
          row.status = "applied";
        }
        // The restored job re-enters attention scope; only an invalidated
        // ["attention"] refetch can surface this bucket change in the UI.
        state.attention.follow_up_today.push(attentionEntry(restore[1]!));
        return json({ job_id: restore[1], status: "applied" });
      }
      const detail = /^\/api\/jobs\/(\w+)$/.exec(url);
      if (detail !== null && method === "GET") {
        const row = state.rows.find((job) => job.id === detail[1]);
        return json(detailFor(detail[1]!, row?.status ?? "applied"));
      }
      throw new Error(`unexpected fetch in test: ${method} ${url}`);
    }),
  );
}

function renderPipeline() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <DensityProvider>
        <PipelinePage />
        <Toaster />
      </DensityProvider>
    </QueryClientProvider>,
  );
  return { ...result, queryClient };
}

function activeRowId(): string | null {
  const active = document.querySelector('[role="listitem"][data-active]');
  return active?.getAttribute("data-testid")?.replace("job-row-", "") ?? null;
}

function visibleRowIds(): string[] {
  return Array.from(document.querySelectorAll('[role="listitem"]')).map(
    (item) => item.getAttribute("data-testid")?.replace("job-row-", "") ?? "",
  );
}

async function openRow(id: string): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: `Open Co${id} Title${id}` }));
  await waitFor(() => expect(activeRowId()).toBe(id));
}

let state: ServerState;

beforeEach(() => {
  window.localStorage.clear();
  calls.length = 0;
  state = defaultState();
  mockApi(state);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("committed-rows params, D13 groups with counts, flattened row order", async () => {
  renderPipeline();
  await screen.findByTestId("job-row-a1");

  expect(screen.getByTestId("cloudscape-pipeline")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /^Application pipeline/ })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /Job detail/ })).toBeInTheDocument();

  const listUrl = calls.find((call) => call.url.startsWith("/api/jobs?"))?.url ?? "";
  for (const param of [
    "tab=all",
    "statuses=applied",
    "statuses=interviewing",
    "statuses=offer",
    "statuses=rejected",
    "statuses=ghosted",
    "limit=200",
  ]) {
    expect(listUrl).toContain(param);
  }
  // Committed rows are shown as-is — no triage-ingest narrowing.
  for (const param of ["apply_hard_filters", "require_verdict", "dedupe", "fold"]) {
    expect(listUrl).not.toContain(param);
  }

  // Four groups with counts; Closed merges rejected + ghosted (D13).
  expect(screen.getByRole("button", { name: "Applied 2" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Interviewing 1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Offer 1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Closed 2" })).toBeInTheDocument();
  expect(visibleRowIds()).toEqual(["a1", "a2", "i1", "o1", "r1", "g1"]);
});

test("chips filter to the bucket's jobs and clear restores; empty bucket hidden", async () => {
  renderPipeline();
  await screen.findByTestId("job-row-a1");

  // going_ghosted is empty -> its chip never renders.
  expect(screen.queryByRole("button", { name: /Going ghosted/ })).toBeNull();

  const chip = screen.getByRole("button", { name: "Follow-up due 1" });
  fireEvent.click(chip);
  expect(chip).toHaveAttribute("aria-pressed", "true");
  // Only the bucket's job remains; emptied groups drop their headers.
  expect(visibleRowIds()).toEqual(["a1"]);
  expect(screen.getByRole("button", { name: "Applied 1" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Interviewing/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Closed/ })).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Clear filter" }));
  expect(visibleRowIds()).toEqual(["a1", "a2", "i1", "o1", "r1", "g1"]);
});

test("clicking the active chip again clears the filter", async () => {
  renderPipeline();
  await screen.findByTestId("job-row-a1");

  fireEvent.click(screen.getByRole("button", { name: "Interview prep 1" }));
  expect(visibleRowIds()).toEqual(["i1"]);
  fireEvent.click(screen.getByRole("button", { name: "Interview prep 1" }));
  expect(visibleRowIds()).toEqual(["a1", "a2", "i1", "o1", "r1", "g1"]);
});

test("collapsing a group hides its rows but keeps the header count", async () => {
  renderPipeline();
  await screen.findByTestId("job-row-a1");

  const header = screen.getByRole("button", { name: "Applied 2" });
  fireEvent.click(header);
  expect(header).toHaveAttribute("aria-expanded", "false");
  expect(visibleRowIds()).toEqual(["i1", "o1", "r1", "g1"]);
  expect(screen.getByRole("button", { name: "Applied 2" })).toBeInTheDocument();

  fireEvent.click(header);
  expect(visibleRowIds()).toEqual(["a1", "a2", "i1", "o1", "r1", "g1"]);
});

test("row clicks select visible jobs and details expose the posting link", async () => {
  renderPipeline();
  await screen.findByTestId("job-row-a1");
  await waitFor(() => expect(activeRowId()).toBe("a1"));

  // Collapsing the selected row's group re-seeds onto the first visible row.
  fireEvent.click(screen.getByRole("button", { name: "Applied 2" }));
  await waitFor(() => expect(activeRowId()).toBe("i1"));

  fireEvent.click(screen.getByRole("button", { name: "Open Coo1 Titleo1" }));
  expect(activeRowId()).toBe("o1");
  await waitFor(() =>
    expect(screen.getByRole("link", { name: /open posting/ })).toHaveAttribute(
      "href",
      "https://example.com/o1",
    ),
  );
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 200));
  });
});

test("interview add posts the round and it appears in the panel", async () => {
  renderPipeline();
  await screen.findByTestId("job-row-a1");
  await openRow("i1");

  // The interviewing job mounts the panel through extraSections.
  const panel = await screen.findByRole("region", { name: "Interviews" });
  fireEvent.change(within(panel).getByLabelText("Round label"), {
    target: { value: "Onsite" },
  });
  fireEvent.click(within(panel).getByRole("button", { name: "Add round" }));

  expect(await within(panel).findByText("Onsite")).toBeInTheDocument();
  expect(within(panel).getByText("#1")).toBeInTheDocument();
  const post = calls.find(
    (call) => call.url === "/api/jobs/i1/interviews" && call.method === "POST",
  );
  expect(JSON.parse(String(post?.init?.body))).toEqual({ label: "Onsite", scheduled_at: null });
});

test("an applied job can add the first interview round", async () => {
  renderPipeline();
  await screen.findByTestId("job-row-a1");
  await openRow("a1");

  const panel = await screen.findByRole("region", { name: "Interviews" });
  fireEvent.change(within(panel).getByLabelText("Round label"), {
    target: { value: "Recruiter screen" },
  });
  fireEvent.click(within(panel).getByRole("button", { name: "Add round" }));

  expect(await within(panel).findByText("Recruiter screen")).toBeInTheDocument();
  const post = calls.find(
    (call) => call.url === "/api/jobs/a1/interviews" && call.method === "POST",
  );
  expect(JSON.parse(String(post?.init?.body))).toEqual({
    label: "Recruiter screen",
    scheduled_at: null,
  });
});

test("interview submit uses the datetime value visible in the native control", async () => {
  renderPipeline();
  await screen.findByTestId("job-row-a1");
  await openRow("a1");

  const panel = await screen.findByRole("region", { name: "Interviews" });
  const scheduled = within(panel).getByLabelText("Scheduled time") as HTMLInputElement;
  fireEvent.change(within(panel).getByLabelText("Round label"), {
    target: { value: "Technical screen" },
  });
  scheduled.value = "2026-08-20T14:30";
  expect(scheduled).toHaveValue("2026-08-20T14:30");
  fireEvent.click(within(panel).getByRole("button", { name: "Add round" }));

  await within(panel).findByText("Technical screen");
  const post = calls.find(
    (call) => call.url === "/api/jobs/a1/interviews" && call.method === "POST",
  );
  expect(JSON.parse(String(post?.init?.body))).toEqual({
    label: "Technical screen",
    scheduled_at: new Date("2026-08-20T14:30").toISOString(),
  });
});

test("completing a round marks it done and shows the notes", async () => {
  state.interviews["i1"] = [
    {
      round_index: 1,
      label: "Phone Screen",
      scheduled_at: "2026-06-12T15:00:00Z",
      completed_at: null,
      notes: null,
    },
  ];
  renderPipeline();
  await screen.findByTestId("job-row-a1");
  await openRow("i1");

  const panel = await screen.findByRole("region", { name: "Interviews" });
  await within(panel).findByText("Phone Screen");
  fireEvent.change(within(panel).getByLabelText("Notes for round 1"), {
    target: { value: "went well" },
  });
  fireEvent.click(within(panel).getByRole("button", { name: "Mark done" }));

  expect(await within(panel).findByText("done")).toBeInTheDocument();
  expect(within(panel).getByText("went well")).toBeInTheDocument();
  expect(within(panel).queryByRole("button", { name: "Mark done" })).toBeNull();
  const patch = calls.find((call) => call.method === "PATCH");
  expect(patch?.url).toBe("/api/jobs/i1/interviews/1");
});

test("a ghosted row exposes restore; POST /restore refreshes the groups", async () => {
  renderPipeline();
  await screen.findByTestId("job-row-a1");
  await openRow("g1");

  fireEvent.click(await screen.findByRole("button", { name: "Restore" }));

  expect(await screen.findByText("Restored to applied")).toBeInTheDocument();
  const post = calls.find((call) => call.method === "POST" && call.url.endsWith("/restore"));
  expect(post?.url).toBe("/api/jobs/g1/restore");
  // The invalidated list refetch moved the row: Applied 3, Closed shrinks to 1.
  expect(await screen.findByRole("button", { name: "Applied 3" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Closed 1" })).toBeInTheDocument();
  // The attention refetch picked up the server-side bucket change: the
  // restore write pushed g1 into follow_up_today, so the chip count bumps.
  expect(await screen.findByRole("button", { name: "Follow-up due 2" })).toBeInTheDocument();
});

test("an applied row exposes Archive; the action transitions to archived (non-forced)", async () => {
  renderPipeline();
  await screen.findByTestId("job-row-a1");
  await openRow("a1");

  fireEvent.click(await screen.findByRole("button", { name: "Archive" }));

  expect(await screen.findByText("Archived")).toBeInTheDocument();
  const post = calls.find((call) => call.method === "POST" && call.url.endsWith("/transition"));
  expect(post?.url).toBe("/api/jobs/a1/transition");
  expect(JSON.parse(String(post?.init?.body))).toEqual({
    to: "archived",
    note: null,
    force: false,
  });
  // The invalidated list refetch moved a1 out of the active groups: Applied
  // drops from 2 to 1.
  expect(await screen.findByRole("button", { name: "Applied 1" })).toBeInTheDocument();
});

test("pipeline detail keeps the followup picker but hides decide actions", async () => {
  renderPipeline();
  await screen.findByTestId("job-row-a1");

  expect(await screen.findByLabelText("Custom follow-up date")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Shortlist" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
});
