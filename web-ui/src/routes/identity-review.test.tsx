import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import IdentityReviewPage from "@/routes/identity-review";

const job = (id: string) => ({ id, company: "United Airlines", title: "Associate Software Developer", location: "Houston, TX", status: "new", url: `https://careers.united.com/job/${id}`, sources: [{ id, platform: "linkedin", url: `https://linkedin.com/jobs/view/${id}`, jd_text: "Build software services.", jd_quality: "full" }] });
const pair = { id: "1:2", left: job("1"), right: job("2"), reasons: ["multiple_identity_candidates"] };
const posts: unknown[] = [];
beforeEach(() => {
  posts.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => {
    if (init?.method === "POST") { posts.push(JSON.parse(init.body)); return Response.json({ ok: true }); }
    return Response.json({ total: 1, job_total: 2, items: [pair] });
  }));
});
afterEach(() => vi.unstubAllGlobals());
function show() { render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><IdentityReviewPage /></QueryClientProvider>); }

test("compares sources and records an independent decision", async () => {
  show();
  await screen.findByRole("button", { name: "Keep separate" });
  expect(screen.getByText(/2 jobs need review/)).toBeVisible();
  expect(screen.getByText(/Scoring continues/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Keep separate" }));
  await waitFor(() => expect(posts).toEqual([{ decision: "separate" }]));
});

test("merge asks for confirmation with the selected target", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Merge into job #1" }));
  expect(posts).toEqual([]);
  expect(screen.getByRole("dialog")).toHaveTextContent("#1");
  fireEvent.click(screen.getByRole("button", { name: "Confirm merge" }));
  await waitFor(() => expect(posts).toEqual([{ decision: "merge", target_id: 1 }]));
});

test("shows a useful empty state", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ total: 0, job_total: 0, items: [] })));
  show();
  expect(await screen.findByText(/You're all caught up/)).toBeVisible();
});

test("shows fetch errors and lets the user retry", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({}, { status: 500 })));
  show();
  expect(await screen.findByText("Could not load reviews")).toBeVisible();
  expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
});

test("does not imply success when saving a decision fails", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => init?.method === "POST"
    ? Response.json({ error: { code: "conflict", message: "Scoring is running. Retry later.", request_id: "test" } }, { status: 409 })
    : Response.json({ total: 1, job_total: 2, items: [pair] })));
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Keep separate" }));
  expect(await screen.findByText("Scoring is running. Retry later.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Keep separate" })).toBeEnabled();
});
