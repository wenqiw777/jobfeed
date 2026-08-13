import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import App from "@/App";
import { DensityProvider } from "@/lib/density";

const CONFIG = {
  configured: false,
  llm: {
    stage_a: "codex-cli/gpt-5.4-mini",
    stage_b: "codex-cli/gpt-5.5",
    codex_timeout_s: 60,
    claude_timeout_s: 210,
    openai_compat_base_url: "https://api.openai.com/v1",
    openai_compat_api_key_env: "OPENAI_API_KEY",
    openai_compat_timeout_s: 60,
    max_concurrent: 4,
    master_resume_path: "resume.example.md",
    preamble_personal_path: null,
    max_daily_score_calls: 150,
    max_daily_cost_usd: 10,
  },
  scoring: {
    stage_a_threshold: 60,
    ml_gate_enabled: false,
    default_eval_limit: 150,
  },
  sources: {
    ats: {
      enabled: true,
      max_concurrent: 10,
      probe_ttl_days: 7,
      failure_threshold: 3,
      probe_timeout_s: 5,
      scan_timeout_s: 30,
      seed_companies: ["anthropic", "openai", "palantir"],
    },
    speedyapply: {
      enabled: false,
      search_urls: [],
      max_concurrent: 10,
      fetch_timeout_s: 30,
    },
    indeed: {
      enabled: false,
      search_urls: [],
      max_jobs: 100,
      hours_old: null,
      max_concurrent: 2,
      timeout_s: 60,
      repeat: 1,
      country_indeed: "usa",
    },
    linkedin_guest: {
      enabled: false,
      search_urls: [],
      max_jobs: 1000,
      pacing_s: 1,
      enrich_batch_limit: 500,
      enrich_after_scan: true,
      proxies: null,
      timeout_s: 15,
    },
    linkedin: {
      enabled: false,
      search_urls: [],
      max_jobs: 100,
      headless: true,
      tier2_cap: 30,
      profile_dir: "~/.cache/jobfeed/linkedin",
      lock_path: "~/.cache/jobfeed/enrich.lock",
    },
  },
  hard_filters: {
    title_blocklist: [],
    company_blocklist: [],
    location_allowlist: [],
    location_blocklist: [],
    posted_within_days: null,
    big_company_list: [],
    big_company_days: 90,
  },
  ml_gate: {
    model_dir: "models/ml_gate",
    model_version: "v20260601T170453Z",
    embedding_model: "all-MiniLM-L6-v2",
    embedding_max_chars: 2000,
    threshold_override: null,
    max_candidates: 5000,
  },
};

const calls: { url: string; method: string; body: unknown }[] = [];

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function mockApi(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = init?.body === undefined ? null : JSON.parse(String(init.body));
      calls.push({ url, method, body });
      if (url === "/api/config" && method === "GET") {
        return json(CONFIG);
      }
      if (url === "/api/config" && method === "PUT") {
        return json({ ...(body as object), configured: true });
      }
      if (url.startsWith("/api/jobs")) {
        return json({ jobs: [], total: 0, tab_counts: {} });
      }
      if (url.startsWith("/api/attention")) {
        return json({
          follow_up_today: [],
          interview_prep: [],
          going_ghosted: [],
          enrich_errors: [],
          low_quality_scored: [],
          stuck_scoring: [],
        });
      }
      throw new Error(`unexpected fetch in test: ${method} ${url}`);
    }),
  );
}

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <DensityProvider>
        <MemoryRouter initialEntries={["/"]}>
          <App />
        </MemoryRouter>
      </DensityProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls.length = 0;
  mockApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("fresh checkout is gated on configuration and saves without TOML editing", async () => {
  renderApp();

  expect(await screen.findByRole("heading", { name: "Set up your feed" })).toBeVisible();
  expect(screen.queryByRole("navigation", { name: "Zones" })).toBeNull();
  expect(screen.getByLabelText("Master resume")).toHaveValue("resume.example.md");

  fireEvent.change(screen.getByLabelText("Master resume"), {
    target: { value: "data/wenqi-resume.md" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "LinkedIn guest search" }));
  fireEvent.change(screen.getByLabelText("LinkedIn search URLs"), {
    target: {
      value: "https://www.linkedin.com/jobs/search/?keywords=founding%20engineer",
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save and open Jobfeed" }));

  await waitFor(() => {
    const request = calls.find(
      (call) => call.url === "/api/config" && call.method === "PUT",
    );
    expect(request).toBeDefined();
    const body = request?.body as typeof CONFIG;
    expect(body.llm.master_resume_path).toBe("data/wenqi-resume.md");
    expect(body.sources.linkedin_guest.enabled).toBe(true);
    expect(body.sources.linkedin_guest.search_urls).toEqual([
      "https://www.linkedin.com/jobs/search/?keywords=founding%20engineer",
    ]);
  });
  expect(await screen.findByRole("heading", { name: "Triage" })).toBeVisible();
});

test("enabled URL sources explain what is missing before save", async () => {
  renderApp();
  await screen.findByRole("heading", { name: "Set up your feed" });

  fireEvent.click(screen.getByRole("checkbox", { name: "Indeed search" }));
  fireEvent.click(screen.getByRole("button", { name: "Save and open Jobfeed" }));

  expect(await screen.findByText("Add at least one Indeed search URL.")).toBeVisible();
  expect(calls.some((call) => call.method === "PUT")).toBe(false);
});
