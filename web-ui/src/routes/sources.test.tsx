import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { CompanyOut } from "@/api/queries";
import { Toaster } from "@/components/ui/toaster";
import SourcesPage from "@/routes/sources";

function company(slug: string, over: Partial<CompanyOut> = {}): CompanyOut {
  return { slug, vendor: "greenhouse", consecutive_discover_failures: 0, removed: false, ...over };
}

interface ServerState {
  companies: CompanyOut[];
  /** When set, POST /api/companies/bulk returns this instead of inserting
   * — failure-path tests hold and release the response themselves. */
  bulkOverride?: () => Promise<Response>;
}

interface RecordedCall {
  url: string;
  method: string;
  body: unknown;
}

const calls: RecordedCall[] = [];

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** Probe fixture: "bad*" entries fail, "miss*" resolve to no vendor, and
 * everything else resolves to its last URL segment on greenhouse. */
function probeResult(entry: string) {
  const slug = (entry.split("/").at(-1) ?? entry).trim().toLowerCase();
  if (slug.startsWith("bad")) {
    return { input: entry, slug: null, vendor: null, error: "probe timeout" };
  }
  if (slug.startsWith("miss")) {
    return { input: entry, slug, vendor: null, error: null };
  }
  return { input: entry, slug, vendor: "greenhouse", error: null };
}

/** Stateful fetch stub mirroring the companies wire contract: bulk skips
 * active slugs, DELETE soft-removes, the list filter honors
 * include_removed. */
function mockApi(state: ServerState): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = init?.body === undefined ? null : JSON.parse(String(init.body));
      calls.push({ url, method, body });

      if (method === "GET" && url.startsWith("/api/companies")) {
        const include = url.includes("include_removed=true");
        return json({
          companies: state.companies.filter((row) => include || !row.removed),
        });
      }
      if (method === "POST" && url === "/api/companies/probe") {
        const { entries } = body as { entries: string[] };
        return json({ results: entries.map(probeResult) });
      }
      if (method === "POST" && url === "/api/companies/bulk") {
        if (state.bulkOverride !== undefined) {
          return state.bulkOverride();
        }
        const { rows } = body as { rows: { slug: string; vendor: string }[] };
        const active = new Set(
          state.companies.filter((row) => !row.removed).map((row) => row.slug),
        );
        const fresh = rows.filter((row) => !active.has(row.slug));
        fresh.forEach((row) => state.companies.push(company(row.slug, { vendor: row.vendor })));
        return json({ inserted: fresh.length });
      }
      if (method === "POST" && url === "/api/companies") {
        const row = body as { slug: string; vendor: string };
        state.companies.push(company(row.slug, { vendor: row.vendor }));
        return json({ ...company(row.slug, { vendor: row.vendor }) });
      }
      const remove = /^\/api\/companies\/([\w-]+)$/.exec(url);
      if (remove !== null && method === "DELETE") {
        const row = state.companies.find((item) => item.slug === remove[1]);
        if (row !== undefined) {
          row.removed = true;
          row.vendor = null;
        }
        return json({ ok: true });
      }
      throw new Error(`unexpected fetch in test: ${method} ${url}`);
    }),
  );
}

function renderSources() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SourcesPage />
      <Toaster />
    </QueryClientProvider>,
  );
}

let state: ServerState;

beforeEach(() => {
  calls.length = 0;
  state = {
    companies: [
      company("co1"),
      company("co2", { vendor: "ashby", consecutive_discover_failures: 3 }),
      company("gone1", { vendor: null, removed: true }),
    ],
  };
  mockApi(state);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("renders the Cloudscape source workspace and tracked-company table", async () => {
  renderSources();

  expect(await screen.findByTestId("cloudscape-sources")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Job sources", level: 2 })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Check and add company boards", level: 2 })).toBeVisible();
  expect(screen.getByRole("heading", { name: /Tracked companies/, level: 2 })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "Company" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "Board provider" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "Scan health" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "Tracking status" })).toBeVisible();
  expect(
    screen.getByText("Use this form when you already know the company board."),
  ).toBeVisible();
});

test("tracked companies search and paginate every 25 rows without hiding the total", async () => {
  state.companies = Array.from({ length: 205 }, (_, index) =>
    company(`company-${String(index).padStart(3, "0")}`),
  );
  renderSources();

  await screen.findByTestId("company-row-company-000");
  expect(screen.getAllByTestId(/^company-row-/)).toHaveLength(25);
  expect(screen.getByRole("heading", { name: "Tracked companies (205)" })).toBeVisible();
  expect(screen.getByText("1–25 of 205 companies")).toBeVisible();
  expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute(
    "aria-disabled",
    "true",
  );

  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByTestId("company-row-company-025")).toBeVisible();
  expect(screen.getAllByTestId(/^company-row-/)).toHaveLength(25);

  fireEvent.change(screen.getByRole("searchbox", { name: "Search companies" }), {
    target: { value: "company-199" },
  });
  expect(await screen.findByTestId("company-row-company-199")).toBeVisible();
  expect(screen.getAllByTestId(/^company-row-/)).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Next page" })).toBeNull();
});

test("companies table: slug/vendor columns, failure tint, removed via toggle only", async () => {
  renderSources();
  const row1 = await screen.findByTestId("company-row-co1");

  expect(within(row1).getByText("co1")).toBeInTheDocument();
  expect(screen.getByText("ashby")).toBeInTheDocument();
  // Non-zero failure counts get the consider-family warn tint; zero stays mute.
  expect(screen.getByText("3 consecutive failures")).toBeInTheDocument();
  expect(screen.getByText("Healthy · 0 failures")).toBeInTheDocument();

  // Soft-removed rows are hidden until the toggle re-includes them.
  expect(screen.queryByTestId("company-row-gone1")).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Show stopped companies" }));
  const removedRow = await screen.findByTestId("company-row-gone1");
  expect(calls.at(-1)?.url).toContain("include_removed=true");
  expect(screen.getByText("Stopped")).toBeInTheDocument();
  // No remove affordance on an already-removed row.
  expect(within(removedRow).queryByRole("button", { name: /Remove/ })).toBeNull();
});

test("remove is gated by the danger dialog; cancel sends no request", async () => {
  renderSources();
  await screen.findByTestId("company-row-co1");

  fireEvent.click(screen.getByRole("button", { name: "Stop tracking co1" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("Stop tracking co1?")).toBeInTheDocument();

  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.getByRole("dialog").className).toContain("hidden"));
  expect(calls.some((call) => call.method === "DELETE")).toBe(false);

  fireEvent.click(screen.getByRole("button", { name: "Stop tracking co1" }));
  const reopened = await screen.findByRole("dialog");
  fireEvent.click(within(reopened).getByRole("button", { name: "Stop tracking" }));

  expect(await screen.findByText("Stopped tracking co1")).toBeInTheDocument();
  const request = calls.find((call) => call.method === "DELETE");
  expect(request?.url).toBe("/api/companies/co1");
  // The invalidated list refetch drops the row (default view hides removed).
  await waitFor(() => expect(screen.queryByTestId("company-row-co1")).toBeNull());
});

test("single add: probe-one prefills slug + vendor, Add posts the pinned pair", async () => {
  renderSources();
  await screen.findByTestId("company-row-co1");

  const slugInput = screen.getByLabelText("Company slug");
  fireEvent.change(slugInput, { target: { value: "https://boards.greenhouse.io/Acme" } });
  fireEvent.click(screen.getByRole("button", { name: "Check board" }));

  // The probe resolved the URL — slug normalized, vendor pre-selected
  // (Add is gated on both, so enabled proves the vendor landed).
  await waitFor(() => expect(slugInput).toHaveValue("acme"));
  expect(screen.getByRole("button", { name: "Add company" })).toBeEnabled();
  const probeCall = calls.find((call) => call.url === "/api/companies/probe");
  expect(probeCall?.body).toEqual({ entries: ["https://boards.greenhouse.io/Acme"] });

  fireEvent.click(screen.getByRole("button", { name: "Add company" }));
  expect(await screen.findByText("acme added")).toBeInTheDocument();
  const post = calls.find((call) => call.method === "POST" && call.url === "/api/companies");
  expect(post?.body).toEqual({ slug: "acme", vendor: "greenhouse" });
  // companies invalidation lands the new row in the table.
  expect(await screen.findByTestId("company-row-acme")).toBeInTheDocument();
});

test("probe flow: 20-line paste → per-entry results → confirm inserts only checked rows", async () => {
  renderSources();
  await screen.findByTestId("company-row-co1");

  // 16 resolvable (co1/co2 already tracked), 3 errors, 1 vendor miss.
  const entries = [
    ...Array.from({ length: 16 }, (_, i) => `co${i + 1}`),
    "bad1",
    "bad2",
    "bad3",
    "miss1",
  ];
  expect(entries).toHaveLength(20);
  fireEvent.change(screen.getByLabelText("Company entries"), {
    target: { value: entries.join("\n") },
  });
  fireEvent.click(screen.getByRole("button", { name: "Check boards" }));

  // Per-entry status: resolved rows pre-checked, errors flagged and
  // uncheckable, the definitive miss labeled (not an error).
  expect(await screen.findByRole("heading", { name: /16 of 20 selected/ })).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Add co3" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Add bad1" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "Add bad1" })).not.toBeChecked();
  expect(screen.getAllByText("probe timeout")).toHaveLength(3);
  expect(screen.getByRole("checkbox", { name: "Add miss1" })).toBeDisabled();
  expect(screen.getByText("No supported board detected")).toBeInTheDocument();

  // Uncheck one resolved row; confirm must post exactly the checked 15.
  fireEvent.click(screen.getByRole("checkbox", { name: "Add co16" }));
  expect(screen.getByRole("heading", { name: /15 of 20 selected/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Add 15 selected companies" }));

  const bulk = await waitFor(() => {
    const call = calls.find((item) => item.url === "/api/companies/bulk");
    expect(call).toBeDefined();
    return call!;
  });
  const rows = (bulk.body as { rows: { slug: string; vendor: string }[] }).rows;
  expect(rows).toHaveLength(15);
  expect(rows.map((row) => row.slug)).toContain("co3");
  expect(rows.map((row) => row.slug)).not.toContain("co16");
  expect(rows.map((row) => row.slug)).not.toContain("bad1");

  // co1 + co2 were already tracked: 13 inserted, delta surfaced honestly.
  expect(await screen.findByText("13 added")).toBeInTheDocument();
  expect(screen.getByText("2 already tracked")).toBeInTheDocument();

  // The flow resets to a fresh paste box and the table picks up the rows.
  await waitFor(() => expect(screen.getByLabelText("Company entries")).toHaveValue(""));
  expect(await screen.findByTestId("company-row-co3")).toBeInTheDocument();
});

test("bulk confirm failure: error toast, review stays interactive for a retry", async () => {
  // Hold the bulk response so the pending label is observable, then fail it.
  let failBulk!: () => void;
  state.bulkOverride = () =>
    new Promise((resolve) => {
      failBulk = () =>
        resolve(
          new Response(
            JSON.stringify({
              error: { code: "internal_error", message: "store exploded", request_id: "req-1" },
            }),
            { status: 500, headers: { "Content-Type": "application/json" } },
          ),
        );
    });

  renderSources();
  await screen.findByTestId("company-row-co1");

  fireEvent.change(screen.getByLabelText("Company entries"), {
    target: { value: "alpha\nbeta" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Check boards" }));
  expect(await screen.findByRole("heading", { name: /2 of 2 selected/ })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Add 2 selected companies" }));
  expect(await screen.findByText("Adding companies")).toBeInTheDocument();
  failBulk();

  // The failure surfaces as a toast…
  expect(await screen.findByText("Companies could not be added")).toBeInTheDocument();
  expect(screen.getByText("store exploded")).toBeInTheDocument();
  // …and the review is still interactive, not wedged: the confirm button is
  // back from its pending label and enabled, the result rows still render.
  expect(screen.getByRole("button", { name: "Add 2 selected companies" })).toBeEnabled();
  expect(screen.getByRole("heading", { name: /2 of 2 selected/ })).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Add alpha" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Add beta" })).toBeChecked();
});

test("probe review: select all / none drive the confirm button", async () => {
  renderSources();
  await screen.findByTestId("company-row-co1");

  fireEvent.change(screen.getByLabelText("Company entries"), {
    target: { value: "alpha\nbeta\nbad9" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Check boards" }));
  expect(await screen.findByRole("heading", { name: /2 of 3 selected/ })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
  expect(screen.getByRole("heading", { name: /0 of 3 selected/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add 0 selected companies" })).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: "Select all matched" }));
  expect(screen.getByRole("heading", { name: /2 of 3 selected/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add 2 selected companies" })).toBeEnabled();
});
