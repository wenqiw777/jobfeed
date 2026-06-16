import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { JobDetailResponse, JobSummary } from "@/api/queries";
import { Toaster } from "@/components/ui/toaster";
import { DensityProvider } from "@/lib/density";
import LibraryPage from "@/routes/library";

function jobRow(id: string, over: Partial<JobSummary> = {}): JobSummary {
  return {
    id,
    company: `Co${id}`,
    title: `Title${id}`,
    platform: "greenhouse",
    url: `https://example.com/${id}`,
    status: "scored",
    verdict: "apply",
    stage_a_score: 80,
    stage_b_fit_score: 90,
    stage_b_status: null,
    jd_quality: "full",
    company_norm: `co${id}`,
    title_norm: `title${id}`,
    posted_at: "2026-06-10T00:00:00Z",
    discovered_at: "2026-06-10T00:00:00Z",
    closed_at: null,
    ...over,
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
      posted_at: "2026-06-10T00:00:00Z",
      discovered_at: "2026-06-10T00:00:00Z",
      closed_at: null,
      jd_quality: "full",
      jd_text: "JD body",
    },
    evaluation: {
      stage_a: { score: 80, one_line: "Solid fit." },
      stage_b_status: "completed",
      stage_b: {
        fit_score: 90,
        verdict: "apply",
        jd_summary: "Backend role.",
        strengths: [],
        gaps: [],
        hooks: { lead_with: "Lead with infra.", supporting: [], avoid_mentioning: [] },
      },
    },
    status: { status, history: [], notes: null, next_followup_at: null, resume_variant: null },
    twins: [],
    interviews: [],
    application: null,
  };
}

/** D13 Library tab predicates, mirrored by the mock server. */
const TAB_STATUSES: Record<string, readonly string[] | null> = {
  all: null,
  scored: ["scored"],
  shortlisted: ["shortlisted", "awaiting_referral"],
  archived: ["archived", "ignored"],
};

interface ServerState {
  rows: JobSummary[];
}

interface RecordedCall {
  url: string;
  method: string;
  init: RequestInit | undefined;
}

const calls: RecordedCall[] = [];

/** When set, jobs-list responses wait on it — lets a test hold a request
 * open to observe the keepPreviousData placeholder window. */
let listGate: Promise<void> | null = null;

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** 55 scored fillers + one of each interesting shape, specials first so
 * they sit on page one. all=62, scored=56, shortlisted=2, archived=2. */
function defaultState(): ServerState {
  const fillers = Array.from({ length: 55 }, (_, i) =>
    jobRow(`f${i + 1}`, { company: `FillerCo${i + 1}` }),
  );
  return {
    rows: [
      jobRow("c1", { company: "Acme", title: "Platform Engineer", closed_at: "2026-06-09T00:00:00Z" }),
      jobRow("s1", { status: "shortlisted" }),
      jobRow("ar1", { status: "awaiting_referral" }),
      jobRow("a1", { status: "archived", verdict: "skip" }),
      jobRow("g1", { status: "ghosted" }),
      jobRow("i1", { status: "ignored", verdict: null }),
      jobRow("n1", { status: "new", verdict: null }),
      ...fillers,
    ],
  };
}

function searchFilter(rows: JobSummary[], search: string | null): JobSummary[] {
  if (search === null || search === "") {
    return rows;
  }
  const needle = search.toLowerCase();
  return rows.filter(
    (job) =>
      job.company.toLowerCase().includes(needle) || job.title.toLowerCase().includes(needle),
  );
}

function tabFilter(rows: JobSummary[], tab: string): JobSummary[] {
  const statuses = TAB_STATUSES[tab] ?? null;
  return statuses === null ? rows : rows.filter((job) => statuses.includes(job.status));
}

/** Stateful fetch stub mirroring the A4 list contract: tab + shared
 * search narrow the rows, tab_counts re-apply the shared filters per
 * tab, limit/offset slice server-side. */
function mockApi(state: ServerState): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, init });

      if (method === "GET" && url.startsWith("/api/jobs?")) {
        if (listGate !== null) {
          await listGate;
        }
        const params = new URLSearchParams(url.slice(url.indexOf("?") + 1));
        const shared = searchFilter(state.rows, params.get("search"));
        const matching = tabFilter(shared, params.get("tab") ?? "all");
        const offset = Number(params.get("offset") ?? "0");
        const limit = Number(params.get("limit") ?? "50");
        return json({
          jobs: matching.slice(offset, offset + limit),
          total: matching.length,
          tab_counts: Object.fromEntries(
            Object.keys(TAB_STATUSES).map((tab) => [tab, tabFilter(shared, tab).length]),
          ),
        });
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
        return json({ job_id: restore[1], status: "scored" });
      }
      const detail = /^\/api\/jobs\/(\w+)$/.exec(url);
      if (detail !== null && method === "GET") {
        const row = state.rows.find((job) => job.id === detail[1]);
        return json(detailFor(detail[1]!, row?.status ?? "scored"));
      }
      throw new Error(`unexpected fetch in test: ${method} ${url}`);
    }),
  );
}

function renderLibrary() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <DensityProvider>
        <LibraryPage />
        <Toaster />
      </DensityProvider>
    </QueryClientProvider>,
  );
}

function lastJobsUrl(): string {
  return calls.filter((call) => call.url.startsWith("/api/jobs?")).at(-1)?.url ?? "";
}

let state: ServerState;

beforeEach(() => {
  window.localStorage.clear();
  calls.length = 0;
  listGate = null;
  state = defaultState();
  mockApi(state);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("initial request: library params with no triage narrowing; range label", async () => {
  renderLibrary();
  await screen.findByTestId("job-row-c1");

  const url = lastJobsUrl();
  for (const param of ["tab=all", "sort=discovered_desc", "limit=50", "offset=0"]) {
    expect(url).toContain(param);
  }
  // Library shows everything — never the triage-ingest flags (A4).
  for (const param of ["apply_hard_filters", "dedupe", "require_verdict", "statuses", "search"]) {
    expect(url).not.toContain(param);
  }
  expect(screen.getByText("1–50 of 62")).toBeInTheDocument();
});

test("tab switch composes the tab param and resets the page", async () => {
  renderLibrary();
  await screen.findByTestId("job-row-c1");

  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(lastJobsUrl()).toContain("offset=50"));

  const tab = screen.getByRole("tab", { name: /Shortlisted/ });
  fireEvent.mouseDown(tab, { button: 0 });
  fireEvent.click(tab);
  await screen.findByTestId("job-row-s1");

  const url = lastJobsUrl();
  expect(url).toContain("tab=shortlisted");
  expect(url).toContain("offset=0");
  // Shortlisted = {shortlisted, awaiting_referral} server-side (D13).
  expect(screen.getByTestId("job-row-ar1")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Shortlisted 2" })).toBeInTheDocument();
});

test("search debounces into one request, resets the page, and recounts tabs", async () => {
  renderLibrary();
  await screen.findByTestId("job-row-c1");

  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(lastJobsUrl()).toContain("offset=50"));

  fireEvent.change(screen.getByLabelText("Search jobs"), { target: { value: "ac" } });
  fireEvent.change(screen.getByLabelText("Search jobs"), { target: { value: "acme" } });
  // Trailing-edge debounce: nothing hits the wire at keystroke time.
  expect(calls.some((call) => call.url.includes("search="))).toBe(false);

  await waitFor(() => expect(lastJobsUrl()).toContain("search=acme"));
  const url = lastJobsUrl();
  // The burst collapsed to the LAST value only, with the page reset.
  expect(calls.some((call) => call.url.includes("search=ac&"))).toBe(false);
  expect(url).toContain("offset=0");
  expect(url).toContain("tab=all");

  // tab_counts re-apply the shared search filter (A4): only Acme matches.
  expect(await screen.findByRole("tab", { name: "All 1" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Shortlisted 0" })).toBeInTheDocument();
  expect(screen.getByText("1–1 of 1")).toBeInTheDocument();
});

test("sort selection composes the sort param and resets the page", async () => {
  renderLibrary();
  await screen.findByTestId("job-row-c1");

  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(lastJobsUrl()).toContain("offset=50"));

  // Radix Select in happy-dom: open via the trigger's keyboard path
  // (pointerdown needs real pointer plumbing), then click the item —
  // its onClick selects for non-mouse pointer types, the test default.
  const trigger = screen.getByRole("combobox", { name: "Sort" });
  fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.click(await screen.findByRole("option", { name: "Highest score" }));

  await waitFor(() => expect(lastJobsUrl()).toContain("sort=score_desc"));
  expect(lastJobsUrl()).toContain("offset=0");
});

test("pagination drives offset and the mono range label", async () => {
  renderLibrary();
  await screen.findByTestId("job-row-c1");
  expect(screen.getByRole("button", { name: "Prev" })).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("51–62 of 62");
  expect(lastJobsUrl()).toContain("offset=50");
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: "Prev" }));
  await screen.findByText("1–50 of 62");
  expect(lastJobsUrl()).toContain("offset=0");
});

test("Next is disabled while the placeholder page shows a stale total", async () => {
  renderLibrary();
  await screen.findByTestId("job-row-c1");
  expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();

  // Hold the scored-tab request open: keepPreviousData keeps the all-tab
  // page (total 62) on screen as placeholder data while it loads.
  let release: () => void = () => {};
  listGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const tab = screen.getByRole("tab", { name: /Scored/ });
  fireEvent.mouseDown(tab, { button: 0 });
  fireEvent.click(tab);

  // The stale rows and total are still up — paging against them would
  // request a bogus offset on the new tab, so Next must be inert.
  await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeDisabled());
  expect(screen.getByText("1–50 of 62")).toBeInTheDocument();
  expect(screen.getByTestId("job-row-c1")).toBeInTheDocument();

  release();
  listGate = null;
  await screen.findByText("1–50 of 56");
  expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
});

test("emptying the last page clamps back to the final valid page", async () => {
  // 51 scored rows: the scored tab gets a 50-row page plus a 1-row page.
  // One archived extra keeps the all/scored totals distinct, so awaiting
  // "of 51" really means the scored response settled (not stale all data).
  state.rows = [
    ...Array.from({ length: 51 }, (_, i) => jobRow(`r${i + 1}`, { company: `RowCo${i + 1}` })),
    jobRow("x1", { status: "archived" }),
  ];
  renderLibrary();
  await screen.findByTestId("job-row-r1");

  const tab = screen.getByRole("tab", { name: /Scored/ });
  fireEvent.mouseDown(tab, { button: 0 });
  fireEvent.click(tab);
  await screen.findByText("1–50 of 51");

  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("51–51 of 51");

  // Decide away the only row of page 2: it leaves the scored tab, so the
  // refetched page is empty (total 50) and the offset is stranded.
  fireEvent.click(screen.getByRole("button", { name: "Open RowCo51 Titler51" }));
  const drawer = await screen.findByRole("dialog");
  fireEvent.click(await within(drawer).findByRole("button", { name: "Skip" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

  // The page clamps to the last valid one and its rows render — never a
  // stuck empty page with a "51–50 of 50" label.
  expect(await screen.findByText("1–50 of 50")).toBeInTheDocument();
  expect(screen.getByTestId("job-row-r1")).toBeInTheDocument();
  expect(screen.queryByText("51–50 of 50")).toBeNull();
  expect(lastJobsUrl()).toContain("offset=0");
});

test("closed rows carry the badge; row click opens the drawer hosting DetailPane", async () => {
  renderLibrary();
  const closedRow = await screen.findByTestId("job-row-c1");
  expect(within(closedRow).getByText("closed")).toBeInTheDocument();
  // Open rows don't.
  expect(within(screen.getByTestId("job-row-s1")).queryByText("closed")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Open Acme Platform Engineer" }));
  const drawer = await screen.findByRole("dialog");
  // The same DetailPane as the split panes: header + evaluation blocks.
  expect(await within(drawer).findByText("Coc1")).toBeInTheDocument();
  expect(within(drawer).getByText("Backend role.")).toBeInTheDocument();
  expect(within(drawer).getByText("open posting ↗")).toBeInTheDocument();
  expect(screen.getByTestId("job-row-c1")).toHaveAttribute("data-active");
});

test("decidable rows get decide actions; deciding closes the drawer", async () => {
  renderLibrary();
  await screen.findByTestId("job-row-s1");

  fireEvent.click(screen.getByRole("button", { name: "Open Cos1 Titles1" }));
  const drawer = await screen.findByRole("dialog");
  expect(await within(drawer).findByRole("button", { name: "Apply" })).toBeInTheDocument();
  expect(within(drawer).getByRole("button", { name: "Shortlist" })).toBeInTheDocument();
  // Ignore stays a pending-JD affordance, never a Library one.
  expect(within(drawer).queryByRole("button", { name: "Ignore" })).toBeNull();

  fireEvent.click(within(drawer).getByRole("button", { name: "Skip" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  const post = calls.find((call) => call.url === "/api/jobs/s1/transition");
  expect(JSON.parse(String(post?.init?.body))).toMatchObject({ to: "archived" });
});

test("archived rows get the restore card instead of decide actions", async () => {
  renderLibrary();
  await screen.findByTestId("job-row-a1");

  fireEvent.click(screen.getByRole("button", { name: "Open Coa1 Titlea1" }));
  const drawer = await screen.findByRole("dialog");
  expect(await within(drawer).findByRole("button", { name: "Restore" })).toBeInTheDocument();
  expect(within(drawer).queryByRole("button", { name: "Apply" })).toBeNull();
  expect(within(drawer).queryByRole("button", { name: "Shortlist" })).toBeNull();

  fireEvent.click(within(drawer).getByRole("button", { name: "Restore" }));
  expect(await screen.findByText("Restored to scored")).toBeInTheDocument();
  expect(calls.some((call) => call.url === "/api/jobs/a1/restore")).toBe(true);
  // The drawer snapshot's status is stale after the restore — the drawer
  // closes instead of offering a second Restore that would 409.
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
