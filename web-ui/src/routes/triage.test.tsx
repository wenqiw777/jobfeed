import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { BulkTransitionResponse, JobDetailResponse, JobSummary } from "@/api/queries";
import { Toaster } from "@/components/ui/toaster";
import { DensityProvider } from "@/lib/density";
import TriagePage from "@/routes/triage";

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

function detailFor(id: string): JobDetailResponse {
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
        strengths: [{ requirement: "Python", evidence: "8 years" }],
        gaps: [{ requirement: "Go", severity: "minor", mitigation: "ramp fast" }],
        hooks: { lead_with: "Lead with infra.", supporting: ["Owns CI"], avoid_mentioning: ["Java"] },
      },
    },
    status: { status: "scored", history: [], notes: null, next_followup_at: null, resume_variant: null },
    twins: [{ job_id: `t${id}`, platform: "lever", status: "new", url: "https://example.com/twin" }],
    interviews: [],
    application: null,
  };
}

interface ServerState {
  queue: JobSummary[];
  pending: JobSummary[];
  bulkResponse: BulkTransitionResponse;
  /** When false, decide endpoints leave the list untouched (collapse tests). */
  removeOnWrite: boolean;
  /** When set, the list response claims this total instead of the row count. */
  totalOverride: number | null;
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

function defaultState(): ServerState {
  return {
    queue: [jobRow("1"), jobRow("2", { verdict: "consider" }), jobRow("3", { verdict: "skip" })],
    pending: [
      jobRow("p1", { verdict: null, stage_a_score: null, stage_b_fit_score: null, jd_quality: "missing" }),
      jobRow("p2", { verdict: null, stage_a_score: null, stage_b_fit_score: null, jd_quality: null }),
    ],
    bulkResponse: { succeeded: 2, skipped: 1, failed: [{ id: "9", error: "boom" }], cascaded: 3 },
    removeOnWrite: true,
    totalOverride: null,
  };
}

function removeRow(state: ServerState, id: string): void {
  if (!state.removeOnWrite) {
    return;
  }
  state.queue = state.queue.filter((job) => job.id !== id);
  state.pending = state.pending.filter((job) => job.id !== id);
}

/** Stateful fetch stub: writes mutate the in-memory lists so the query
 * invalidation's refetch sees the post-write world. */
function mockApi(state: ServerState): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, init });

      if (method === "GET" && url.startsWith("/api/jobs?")) {
        const params = new URLSearchParams(url.slice(url.indexOf("?") + 1));
        const rows = params.get("tab") === "pending_jd" ? state.pending : state.queue;
        return json({
          jobs: rows,
          total: state.totalOverride ?? rows.length,
          tab_counts: { queue: state.queue.length, pending_jd: state.pending.length },
        });
      }
      // Before the single-transition regex: "bulk" also matches \w+.
      if (url === "/api/jobs/bulk/transition") {
        const body = JSON.parse(String(init?.body)) as { items: { id: string }[] };
        for (const item of body.items) {
          removeRow(state, item.id);
        }
        return json(state.bulkResponse);
      }
      const transition = /^\/api\/jobs\/(\w+)\/transition$/.exec(url);
      if (transition !== null) {
        removeRow(state, transition[1]!);
        return json({ job_id: transition[1], status: "archived" });
      }
      const jd = /^\/api\/jobs\/(\w+)\/jd$/.exec(url);
      if (jd !== null) {
        removeRow(state, jd[1]!);
        return json({ job_id: jd[1], jd_quality: "good" });
      }
      const apply = /^\/api\/jobs\/(\w+)\/apply$/.exec(url);
      if (apply !== null) {
        removeRow(state, apply[1]!);
        return json({ applied: true, reapply_notice: "Active application at Co1 (3d ago)" });
      }
      const detail = /^\/api\/jobs\/(\w+)$/.exec(url);
      if (detail !== null && method === "GET") {
        return json(detailFor(detail[1]!));
      }
      throw new Error(`unexpected fetch in test: ${method} ${url}`);
    }),
  );
}

function renderTriage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <DensityProvider>
        <TriagePage />
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

function press(key: string): void {
  fireEvent.keyDown(window, { key });
}

async function openPendingTab(): Promise<void> {
  const trigger = screen.getByRole("tab", { name: /Pending JD/ });
  fireEvent.mouseDown(trigger, { button: 0 });
  fireEvent.click(trigger);
  await screen.findByTestId("job-row-p1");
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

test("queue request carries the A4 params and rendered order mirrors API order", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");

  const listUrl = calls.find((call) => call.url.startsWith("/api/jobs?"))?.url ?? "";
  for (const param of [
    "tab=queue",
    "apply_hard_filters=true",
    "dedupe=true",
    "require_verdict=true",
    "limit=200",
  ]) {
    expect(listUrl).toContain(param);
  }
  // Queue keeps the server's fixed verdict-group order — no sort param.
  expect(listUrl).not.toContain("sort=");

  const list = screen.getByRole("list", { name: "Jobs" });
  const ids = within(list)
    .getAllByRole("listitem")
    .map((item) => item.getAttribute("data-testid"));
  expect(ids).toEqual(["job-row-1", "job-row-2", "job-row-3"]);
});

test("seeds selection on the first row; deciding advances to the next row", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");
  await waitFor(() => expect(activeRowId()).toBe("1"));

  fireEvent.click(screen.getByRole("button", { name: "Skip" }));
  await waitFor(() => expect(activeRowId()).toBe("2"));
  // The refetch dropped the decided row but selection stays pinned.
  await waitFor(() => expect(screen.queryByTestId("job-row-1")).toBeNull());
  expect(activeRowId()).toBe("2");
});

test("deciding a middle row advances to the row after it", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");

  fireEvent.click(screen.getByRole("button", { name: "Open Co2 Title2" }));
  expect(activeRowId()).toBe("2");

  fireEvent.click(screen.getByRole("button", { name: "Shortlist" }));
  await waitFor(() => expect(activeRowId()).toBe("3"));
});

test("deciding the last row falls back to the previous row", async () => {
  renderTriage();
  await screen.findByTestId("job-row-3");

  fireEvent.click(screen.getByRole("button", { name: "Open Co3 Title3" }));
  fireEvent.click(screen.getByRole("button", { name: "Skip" }));
  await waitFor(() => expect(activeRowId()).toBe("2"));
});

test("a decided row collapses for ~180ms, then the class clears", async () => {
  // Keep the row in the response so the collapse is observable.
  state.removeOnWrite = false;
  renderTriage();
  await screen.findByTestId("job-row-1");
  await waitFor(() => expect(activeRowId()).toBe("1"));

  fireEvent.click(screen.getByRole("button", { name: "Skip" }));
  await waitFor(() => {
    const row = screen.getByTestId("job-row-1");
    expect(row).toHaveAttribute("data-collapsing");
    expect(row.className).toContain("animate-row-collapse");
  });
  // Selection advanced immediately, not after the animation.
  expect(activeRowId()).toBe("2");
  await waitFor(() => {
    expect(screen.getByTestId("job-row-1")).not.toHaveAttribute("data-collapsing");
  });
});

test("prefers-reduced-motion skips the collapse class entirely", async () => {
  state.removeOnWrite = false;
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: true,
    addEventListener: () => {},
    removeEventListener: () => {},
  } as unknown as MediaQueryList);

  renderTriage();
  await screen.findByTestId("job-row-1");
  await waitFor(() => expect(activeRowId()).toBe("1"));

  fireEvent.click(screen.getByRole("button", { name: "Skip" }));
  await waitFor(() => expect(activeRowId()).toBe("2"));
  expect(document.querySelector("[data-collapsing]")).toBeNull();
});

test("bulk action toasts all four counters and re-seeds selection after refetch", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");

  fireEvent.click(screen.getByRole("checkbox", { name: "Select Co1 Title1" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Select Co2 Title2" }));
  expect(screen.getByText("2 selected")).toBeInTheDocument();

  const bulkBar = screen.getByRole("toolbar", { name: "Bulk actions" });
  fireEvent.click(within(bulkBar).getByRole("button", { name: "Skip" }));
  expect(
    await screen.findByText("2 succeeded · 1 skipped · 1 failed · 3 cascaded"),
  ).toBeInTheDocument();

  // Bulk POST carried every selected id with the archive target.
  const bulkCall = calls.find((call) => call.url === "/api/jobs/bulk/transition");
  const body = JSON.parse(String(bulkCall?.init?.body)) as { items: unknown[] };
  expect(body.items).toEqual(
    expect.arrayContaining([
      { id: "1", to: "archived" },
      { id: "2", to: "archived" },
    ]),
  );

  // Selection cleared (bar gone) and re-seeded onto the refetched list.
  await waitFor(() => expect(screen.queryByText("2 selected")).toBeNull());
  await waitFor(() => expect(activeRowId()).toBe("3"));
});

test("select-all says 'matching' when the page covers the total, and selects every row", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");

  fireEvent.click(screen.getByRole("checkbox", { name: "Select Co1 Title1" }));
  fireEvent.click(screen.getByRole("button", { name: "Select all 3 matching" }));
  expect(screen.getByText("3 selected")).toBeInTheDocument();
});

test("select-all says 'loaded' when the total exceeds the page — never overstates", async () => {
  state.totalOverride = 50;
  renderTriage();
  await screen.findByTestId("job-row-1");

  fireEvent.click(screen.getByRole("checkbox", { name: "Select Co1 Title1" }));
  // The action can only reach the 3 loaded rows, so the label must not
  // claim all 50 matching.
  expect(screen.queryByRole("button", { name: /matching/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Select all 3 loaded" }));
  expect(screen.getByText("3 selected")).toBeInTheDocument();
});

test("pending JD tab: zone params, paste card, quality toast, row removal", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");
  // Queue detail never shows the paste card or the Ignore action.
  expect(screen.queryByText(/JD missing/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Ignore" })).toBeNull();

  await openPendingTab();
  const pendingUrl =
    calls.filter((call) => call.url.includes("tab=pending_jd")).at(-1)?.url ?? "";
  expect(pendingUrl).toContain("apply_hard_filters=true");
  expect(pendingUrl).not.toContain("require_verdict");
  expect(pendingUrl).not.toContain("dedupe");

  // Ignore (the pending-JD flavor of skip) appears in the detail actions.
  expect(await screen.findByRole("button", { name: "Ignore" })).toBeInTheDocument();

  const textarea = await screen.findByLabelText("JD text");
  fireEvent.change(textarea, { target: { value: "Full JD body pasted by hand" } });
  fireEvent.click(screen.getByRole("button", { name: "Save JD" }));

  expect(await screen.findByText("Assessed quality: good")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByTestId("job-row-p1")).toBeNull());
  expect(screen.getByTestId("job-row-p2")).toBeInTheDocument();
});

test("compact density renders 32px single-line rows", async () => {
  renderTriage();
  const row = await screen.findByTestId("job-row-1");

  const button = within(row).getByRole("button", { name: "Open Co1 Title1" });
  expect(button.className).toContain("h-row-compact");
  // Single line: company and title share one truncating span.
  expect(within(row).getByText("Co1").parentElement).toHaveTextContent("Co1 · Title1");
});

test("comfortable density renders 46px two-line rows", async () => {
  window.localStorage.setItem("jobfeed:density", "comfortable");
  renderTriage();
  const row = await screen.findByTestId("job-row-1");

  const button = within(row).getByRole("button", { name: "Open Co1 Title1" });
  expect(button.className).toContain("h-row-comfortable");
  // Two lines: the title element no longer contains the company.
  expect(within(row).getByText("Title1")).not.toHaveTextContent("Co1");
  expect(within(row).getByText("Co1")).toBeInTheDocument();
});

test("apply dialog posts multipart and toasts the reapply notice", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");
  await waitFor(() => expect(activeRowId()).toBe("1"));

  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByTestId("cloudscape-apply-form")).toBeInTheDocument();
  expect(within(dialog).getByText(/Apply — Co1 · Title1/)).toBeInTheDocument();

  fireEvent.change(within(dialog).getByLabelText(/Notes/), {
    target: { value: "warm intro from K" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "Record application" }));

  expect(await screen.findByText("Application recorded")).toBeInTheDocument();
  expect(screen.getByText("Active application at Co1 (3d ago)")).toBeInTheDocument();

  const applyCall = calls.find((call) => call.url === "/api/jobs/1/apply");
  expect(applyCall?.init?.body).toBeInstanceOf(FormData);
  const form = applyCall?.init?.body as FormData;
  expect(form.get("notes")).toBe("warm intro from K");

  // The applied row left the queue and selection advanced.
  await waitFor(() => expect(screen.queryByTestId("job-row-1")).toBeNull());
  await waitFor(() => expect(activeRowId()).toBe("2"));
});

test("apply dialog stays pinned to its job when a refetch re-seeds selection", async () => {
  const { queryClient } = renderTriage();
  await screen.findByTestId("job-row-1");
  await waitFor(() => expect(activeRowId()).toBe("1"));

  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/Apply — Co1 · Title1/)).toBeInTheDocument();

  // Background refetch drops row 1 — the live selection re-seeds to row 2,
  // but the open dialog must keep targeting the snapshotted job.
  state.queue = state.queue.filter((job) => job.id !== "1");
  await act(async () => {
    await queryClient.invalidateQueries();
  });
  await waitFor(() => expect(screen.queryByTestId("job-row-1")).toBeNull());
  expect(within(dialog).getByText(/Apply — Co1 · Title1/)).toBeInTheDocument();

  fireEvent.click(within(dialog).getByRole("button", { name: "Record application" }));
  expect(await screen.findByText("Application recorded")).toBeInTheDocument();

  const applyCalls = calls.filter((call) => /\/api\/jobs\/\w+\/apply$/.test(call.url));
  expect(applyCalls.map((call) => call.url)).toEqual(["/api/jobs/1/apply"]);
});

test("cancelling the apply dialog discards the draft — reopening posts a clean form", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");
  await waitFor(() => expect(activeRowId()).toBe("1"));

  // Draft an application for job 1, then cancel out of the dialog.
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  const firstDialog = await screen.findByRole("dialog");
  expect(within(firstDialog).getByText(/Apply — Co1 · Title1/)).toBeInTheDocument();
  fireEvent.change(within(firstDialog).getByLabelText(/Resume variant/), {
    target: { value: "stale-variant" },
  });
  fireEvent.change(within(firstDialog).getByLabelText(/Notes/), {
    target: { value: "stale note meant for job 1" },
  });
  fireEvent.click(within(firstDialog).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

  // Reopen for job 2 and submit without touching any field.
  fireEvent.click(screen.getByRole("button", { name: "Open Co2 Title2" }));
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  const secondDialog = await screen.findByRole("dialog");
  expect(within(secondDialog).getByText(/Apply — Co2 · Title2/)).toBeInTheDocument();
  expect(within(secondDialog).getByLabelText(/Resume variant/)).toHaveValue("");
  expect(within(secondDialog).getByLabelText(/Notes/)).toHaveValue("");
  fireEvent.click(within(secondDialog).getByRole("button", { name: "Record application" }));
  // Toast text is asserted elsewhere (and lingers in module state across
  // tests) — wait on the POST itself, which is what this test pins.
  await waitFor(() => {
    expect(calls.some((call) => /\/api\/jobs\/\w+\/apply$/.test(call.url))).toBe(true);
  });

  // The cancelled draft never rode into job 2's application audit: one
  // POST, to job 2, with an empty multipart body.
  const applyCalls = calls.filter((call) => /\/api\/jobs\/\w+\/apply$/.test(call.url));
  expect(applyCalls.map((call) => call.url)).toEqual(["/api/jobs/2/apply"]);
  const form = applyCalls[0]?.init?.body as FormData;
  expect(Array.from(form.keys())).toEqual([]);
});

test("deciding the only row clears selection and shows the empty states", async () => {
  state.queue = [jobRow("1")];
  renderTriage();
  await screen.findByTestId("job-row-1");
  await waitFor(() => expect(activeRowId()).toBe("1"));

  fireEvent.click(screen.getByRole("button", { name: "Skip" }));
  await waitFor(() => expect(screen.queryByTestId("job-row-1")).toBeNull());
  expect(activeRowId()).toBeNull();
  expect(screen.getByText("Queue clear")).toBeInTheDocument();
  expect(screen.getByText("Select a row to review its evidence and decide.")).toBeInTheDocument();
});

test("detail exposes the selected posting as an external link", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");
  await waitFor(() => expect(activeRowId()).toBe("1"));

  expect(screen.getByRole("link", { name: /open posting/ })).toHaveAttribute(
    "href",
    "https://example.com/1",
  );
});

function transitionBody(url: string): Record<string, unknown> {
  return JSON.parse(
    String(calls.find((call) => call.url === url)?.init?.body),
  ) as Record<string, unknown>;
}

test("pending JD hides the decide trio; Ignore sends a forced transition", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");
  await openPendingTab();

  // An un-scored row can't be applied/shortlisted/skipped — the detail
  // pane offers only the paste card and the dismiss-as-junk Ignore.
  const ignore = await screen.findByRole("button", { name: "Ignore" });
  expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Shortlist" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();

  // The queue decide keys are unbound here: nothing fires, no apply dialog.
  press("h");
  press("s");
  press("a");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(calls.filter((call) => call.url.endsWith("/transition"))).toHaveLength(0);

  fireEvent.click(ignore);
  await waitFor(() => expect(screen.queryByTestId("job-row-p1")).toBeNull());
  expect(transitionBody("/api/jobs/p1/transition")).toMatchObject({
    to: "ignored",
    force: true,
  });
});

test("single-key shortcuts do not trigger queue or pending decisions", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");
  await waitFor(() => expect(activeRowId()).toBe("1"));

  press("i");
  expect(calls.filter((call) => call.url.endsWith("/transition"))).toHaveLength(0);

  await openPendingTab();
  press("i");
  press("j");
  press("k");
  expect(calls.filter((call) => call.url.endsWith("/transition"))).toHaveLength(0);
  expect(activeRowId()).toBe("p1");
});

test("queue decides stay non-forced", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");
  await waitFor(() => expect(activeRowId()).toBe("1"));

  fireEvent.click(screen.getByRole("button", { name: "Shortlist" }));
  await waitFor(() =>
    expect(calls.some((call) => call.url === "/api/jobs/1/transition")).toBe(true),
  );
  expect(transitionBody("/api/jobs/1/transition")).toMatchObject({
    to: "shortlisted",
    force: false,
  });
});

test("pending JD bulk bar offers only the forced Ignore", async () => {
  renderTriage();
  await screen.findByTestId("job-row-1");
  await openPendingTab();

  fireEvent.click(screen.getByRole("checkbox", { name: "Select Cop1 Titlep1" }));
  const bulkBar = screen.getByRole("toolbar", { name: "Bulk actions" });
  expect(within(bulkBar).queryByRole("button", { name: "Shortlist" })).toBeNull();
  expect(within(bulkBar).queryByRole("button", { name: "Skip" })).toBeNull();

  fireEvent.click(within(bulkBar).getByRole("button", { name: "Ignore" }));
  await waitFor(() =>
    expect(calls.some((call) => call.url === "/api/jobs/bulk/transition")).toBe(true),
  );
  expect(transitionBody("/api/jobs/bulk/transition")).toMatchObject({
    items: [{ id: "p1", to: "ignored" }],
    force: true,
  });
});
