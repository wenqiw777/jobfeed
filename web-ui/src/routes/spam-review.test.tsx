import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import SpamReviewPage from "@/routes/spam-review";

const finding = { job_id: "12", company: "Example Publisher", title: "Software Engineer", disposition: "review", effective_disposition: "review", category: "possible_publisher_or_template", evidence: ["Recruiting on behalf of an unnamed client."], url: "https://example.com/job/12", job_decision: null, company_decision: null };
const posts: unknown[] = [];
beforeEach(() => {
  posts.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") { posts.push(JSON.parse(String(init.body))); return Response.json({ ok: true }); }
    return Response.json({ total: 1, items: [finding] });
  }));
});
afterEach(() => vi.unstubAllGlobals());
function show() { render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><SpamReviewPage /></QueryClientProvider>); }

test("shows evidence and keeps uncertainty visible until a decision", async () => {
  show();
  expect(await screen.findByText("Example Publisher")).toBeVisible();
  expect(screen.getByText(finding.evidence[0]!)).toBeVisible();
  expect(screen.getByText(/Uncertain postings stay in Results/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Keep job" }));
  await waitFor(() => expect(posts).toEqual([{ scope: "job", decision: "allow" }]));
});

test("company block requires a concrete company confirmation", async () => {
  show(); await screen.findByText("Example Publisher");
  fireEvent.click(screen.getByRole("button", { name: "Block company" }));
  expect(posts).toEqual([]);
  expect(screen.getByRole("dialog")).toHaveTextContent("Example Publisher");
  fireEvent.click(screen.getByRole("button", { name: "Confirm company block" }));
  await waitFor(() => expect(posts).toEqual([{ scope: "company", decision: "block" }]));
});
