import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import App from "@/App";
import type { components } from "@/api/types.gen";
import { DensityProvider } from "@/lib/density";

type JobProfile = components["schemas"]["JobProfile"];

const CONFIG = {
  configured: false,
  ml_gate_performance: {
    threshold: 0.19,
    recall: 0.9730377471539844,
    precision: 0.5438713998660415,
    f1: 0.6977443609022557,
    irrelevant_rejection: 0.7446090380648791,
    training_jobs: 7002,
  },
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
      max_jobs: 1000,
      title_keywords: ["Platform Engineer"],
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
      max_jobs: 1000,
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

const PROFILE: JobProfile = {
  desired_titles: ["Platform Engineer"],
  seniority_levels: ["Senior"],
  target_countries: ["United States"],
  target_locations: ["New York, NY"],
  work_modes: ["remote"],
  industries: ["Developer tools"],
  company_sizes: ["mid-size"],
  work_authorization: "Authorized in the US",
  hiring_timeline: "Available now",
  excluded_titles: [],
  excluded_companies: [],
  excluded_locations: [],
  excluded_keywords: [],
  maximum_posting_age_days: 14,
  resume_evidence: ["Built Python systems"],
};

const calls: { url: string; method: string; body: unknown }[] = [];
let currentConfig = structuredClone(CONFIG);
let providerState = {
  provider: null as string | null,
  connected: false,
  detail: null as string | null,
  models: [] as { id: string; label: string }[],
  has_secret: false,
  quick_model: null as string | null,
  detailed_model: null as string | null,
};
let providerNetworkError = false;
let providerSaveError = false;
let hasStoredCalibrationJob = true;
let resumeState: {
  original_name: string | null;
  extracted_text: string | null;
  profile: JobProfile | null;
  is_confirmed: boolean;
} = {
  original_name: null as string | null,
  extracted_text: null as string | null,
  profile: null,
  is_confirmed: false,
};
let searchState = {
  profile_fingerprint: "profile-test",
  searches: [
    ...searchPair("software", "Software Engineer", true),
    ...searchPair("backend", "Backend Engineer", true),
    ...searchPair("distributed", "Distributed Systems Engineer", true),
    ...searchPair("ai", "AI / LLM Engineer", false),
  ],
};

function searchPair(id: string, query: string, enabled: boolean) {
  const location = "New York, NY, United States";
  return [
    {
      id: `linkedin-${id}`,
      source: "linkedin_guest" as const,
      query,
      location,
      url: `https://www.linkedin.com/jobs/search/?keywords=${id}&location=New+York&f_TPR=r1209600`,
      enabled,
    },
    {
      id: `indeed-${id}`,
      source: "indeed" as const,
      query,
      location,
      url: `https://www.indeed.com/jobs?q=${id}&l=New+York&fromage=14`,
      enabled,
    },
  ];
}

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
      const body = init?.body === undefined
        ? null
        : init.body instanceof FormData
          ? init.body
          : JSON.parse(String(init.body));
      calls.push({ url, method, body });
      if (url === "/api/config" && method === "GET") {
        return json(currentConfig);
      }
      if (url === "/api/config" && method === "PUT") {
        return json({ ...(body as object), configured: true });
      }
      if (url === "/api/personal-ml/status" && method === "GET") {
        return json({
          state: "shadow",
          label_count: 500,
          ranking_count: 200,
          shadow_count: 200,
          next_target: 600,
          model_threshold: 0.61,
          quick_pass_recall: 0.93,
          quick_fail_rejection: 0.44,
          category_recall: 0.88,
          baseline_rejection: 0.08,
          estimated_call_reduction: 0.31,
          rolling_recall: 0.93,
        });
      }
      if (url === "/api/onboarding/finish" && method === "POST") {
        const configuration = (body as { configuration: object }).configuration;
        currentConfig = { ...configuration, configured: true } as typeof currentConfig;
        return json(currentConfig);
      }
      if (url === "/api/onboarding/plan-usage" && method === "GET") {
        return json({
          provider: "codex_cli",
          source: "live",
          plan_name: "Pro",
          used_percent: 35,
          remaining_percent: 65,
          window_minutes: 10080,
          resets_at: 1787196565,
          detail: "Live Codex allowance from this signed-in account.",
        });
      }
      if (url === "/api/onboarding/evaluation-calibration" && method === "POST") {
        return json({
          evaluation: {
            model: "gpt-5.6-terra",
            input_tokens: 3000,
            output_tokens: 500,
            cost_usd: 0.01,
            latency_ms: 4500,
          },
          allowance_before_percent: 35,
          allowance_after_percent: 35,
          allowance_resolution_percent: 1,
        });
      }
      if (url === "/api/onboarding/calibration-job" && method === "GET") {
        return json({
          id: "indeed-42",
          title: "Representative Platform Engineer",
          company: "Indeed Sample",
          url: "https://www.indeed.com/viewjob?jk=indeed-42",
          jd_text: "Indeed Sample is hiring a Platform Engineer for a real production role building reliable distributed services, APIs, data pipelines, observability, tests, and cloud infrastructure with a collaborative engineering team.",
        });
      }
      if (url === "/api/onboarding/provider" && method === "GET") {
        return json(providerState);
      }
      if (url === "/api/onboarding/provider/test" && method === "POST") {
        if (providerNetworkError) throw new Error("connection lost");
        if ((body as { api_key?: string }).api_key === "sk-ui-rejected") {
          return json({
            ...providerState,
            provider: "openai_api",
            detail: "The API key was rejected. Check it and retry.",
          });
        }
        providerState = {
          provider: "openai_api",
          connected: true,
          detail: "OpenAI API connection verified.",
          models: [
            { id: "gpt-5.6-terra", label: "GPT-5.6 Terra" },
            { id: "gpt-5.6-sol", label: "GPT-5.6 Sol" },
          ],
          has_secret: true,
          quick_model: null,
          detailed_model: null,
        };
        return json(providerState);
      }
      if (url === "/api/onboarding/provider/models" && method === "PUT") {
        if (providerSaveError) {
          return new Response(JSON.stringify({
            error: {
              code: "invalid_model_selection",
              message: "Choose models returned by the verified provider",
              request_id: "request-test",
            },
          }), { status: 422, headers: { "Content-Type": "application/json" } });
        }
        providerState = { ...providerState, ...(body as object) };
        return json(providerState);
      }
      if (url === "/api/onboarding/resume" && method === "GET") {
        return json(resumeState);
      }
      if (url === "/api/onboarding/resume" && method === "POST") {
        const file = (body as FormData).get("file") as File;
        resumeState = {
          original_name: file.name,
          extracted_text: await file.text(),
          profile: null,
          is_confirmed: false,
        };
        return json(resumeState);
      }
      if (url === "/api/onboarding/resume/analyze" && method === "POST") {
        resumeState = {
          ...resumeState,
          profile: PROFILE,
          is_confirmed: false,
        };
        return json(resumeState);
      }
      if (url === "/api/onboarding/profile" && method === "PUT") {
        resumeState = {
          ...resumeState,
          profile: body as JobProfile,
          is_confirmed: true,
        };
        return json(resumeState);
      }
      if (url === "/api/onboarding/searches" && method === "GET") {
        return json(searchState);
      }
      if (url === "/api/onboarding/searches" && method === "PUT") {
        searchState = { ...searchState, ...(body as typeof searchState) };
        return json(searchState);
      }
      if (url === "/api/onboarding/companies/recommend" && method === "POST") {
        return json({
          profile_fingerprint: "companies-test",
          recommendations: [
            {
              name: "Acme Systems",
              slug: "acme",
              rationale: "Strong platform engineering match.",
            },
            {
              name: "No Board Labs",
              slug: "no-board",
              rationale: "Relevant developer tools work.",
            },
          ],
        });
      }
      if (url === "/api/onboarding/companies/catalog" && method === "GET") {
        return json({
          source_counts: { "new-grad": 2 },
          companies: [
            { slug: "broad-one", vendor: "greenhouse" },
            { slug: "broad-two", vendor: "ashby" },
          ],
        });
      }
      if (url === "/api/companies/probe" && method === "POST") {
        return json({
          results: [
            { input: "acme", slug: "acme", vendor: "greenhouse", error: null },
            { input: "no-board", slug: "no-board", vendor: null, error: null },
          ],
        });
      }
      if (url === "/api/companies/bulk" && method === "POST") {
        return json({ inserted: (body as { rows: unknown[] }).rows.length });
      }
      if (url.startsWith("/api/companies?") && method === "GET") {
        return json({ companies: [], total: 0 });
      }
      if (url === "/api/jobs/real-1" && hasStoredCalibrationJob) {
        return json({
          job: {
            id: "real-1",
            title: "Backend Engineer",
            company: "Real Systems",
            url: "https://example.com/jobs/real-1",
            jd_text: "Real Systems is hiring a Backend Engineer to build production APIs, reliable data pipelines, and distributed services. You will work with Python, SQL, cloud infrastructure, observability, testing, and incident response while collaborating with product and platform teams.",
          },
        });
      }
      if (url.startsWith("/api/jobs?")) {
        return json({
          jobs: hasStoredCalibrationJob ? [
            {
              id: "real-1",
              title: "Backend Engineer",
              company: "Real Systems",
              jd_quality: "full",
            },
          ] : [],
          total: hasStoredCalibrationJob ? 1 : 0,
          tab_counts: {},
        });
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

function renderApp(initialEntry = "/") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <DensityProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <App />
        </MemoryRouter>
      </DensityProvider>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

beforeEach(() => {
  calls.length = 0;
  currentConfig = structuredClone(CONFIG);
  providerState = {
    provider: null,
    connected: false,
    detail: null,
    models: [],
    has_secret: false,
    quick_model: null,
    detailed_model: null,
  };
  providerNetworkError = false;
  providerSaveError = false;
  hasStoredCalibrationJob = true;
  resumeState = {
    original_name: null,
    extracted_text: null,
    profile: null,
    is_confirmed: false,
  };
  searchState = {
    profile_fingerprint: "profile-test",
    searches: [
      ...searchPair("software", "Software Engineer", false),
      ...searchPair("backend", "Backend Engineer", false),
      ...searchPair("distributed", "Distributed Systems Engineer", false),
      ...searchPair("ai", "AI / LLM Engineer", false),
    ],
  };
  mockApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("fresh checkout connects a provider, retries, and saves provider models", async () => {
  const { queryClient } = renderApp();

  expect(await screen.findByRole("heading", { name: "Connect an AI provider" })).toBeVisible();
  expect(screen.queryByRole("navigation", { name: "Zones" })).toBeNull();
  expect(screen.getByRole("button", { name: /AI provider/ })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Choose OpenAI API" })).toBeNull();

  fireEvent.mouseDown(screen.getByRole("button", { name: /AI provider/ }));
  fireEvent.mouseUp(await screen.findByRole("option", { name: /OpenAI API/ }));
  fireEvent.change(screen.getByLabelText("OpenAI API key"), {
    target: { value: "sk-ui-rejected" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  expect(await screen.findByText(/API key was rejected/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("OpenAI API key"), {
    target: { value: "sk-ui-secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));

  expect(await screen.findByText("OpenAI API connection verified.")).toBeVisible();
  expect(screen.getByRole("button", { name: /Evaluation model/ })).toBeVisible();
  expect(screen.queryByText(/Quick evaluation model/)).toBeNull();
  expect(screen.queryByText(/Detailed review model/)).toBeNull();
  fireEvent.mouseDown(screen.getByRole("button", { name: /Evaluation model/ }));
  fireEvent.mouseUp(await screen.findByRole("option", { name: "GPT-5.6 Sol" }));
  fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));

  await waitFor(() => {
    const request = calls.find(
      (call) => call.url === "/api/onboarding/provider/models" && call.method === "PUT",
    );
    expect(request).toBeDefined();
    expect(request?.body).toEqual({
      provider: "openai_api",
      quick_model: "gpt-5.6-sol",
      detailed_model: "gpt-5.6-sol",
    });
  });
  expect(
    await screen.findByRole("heading", { name: "Upload your résumé" }),
  ).toBeVisible();
  expect(calls.filter((call) => call.url.endsWith("/provider/test")).map((call) => call.body)).toEqual([
    { provider: "openai_api", api_key: "sk-ui-rejected" },
    { provider: "openai_api", api_key: "sk-ui-secret" },
  ]);
  expect(JSON.stringify(providerState)).not.toContain("sk-ui-secret");
  expect(
    JSON.stringify(queryClient.getMutationCache().getAll().map((mutation) => mutation.state.variables)),
  ).not.toContain("sk-ui-secret");
});

test("provider connection and model save failures stay visible", async () => {
  providerNetworkError = true;
  renderApp();
  await screen.findByRole("heading", { name: "Connect an AI provider" });
  fireEvent.mouseDown(screen.getByRole("button", { name: /AI provider/ }));
  fireEvent.mouseUp(await screen.findByRole("option", { name: /OpenAI API/ }));
  fireEvent.change(screen.getByLabelText("OpenAI API key"), {
    target: { value: "sk-visible-error-test" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  expect(await screen.findByText("connection lost")).toBeVisible();

  providerNetworkError = false;
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  await screen.findByText("OpenAI API connection verified.");
  providerSaveError = true;
  fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
  expect(
    await screen.findByText("Choose models returned by the verified provider"),
  ).toBeVisible();
});

test("résumé upload, analysis, edits, and confirmation stay resumable", async () => {
  providerState = {
    provider: "openai_api",
    connected: true,
    detail: "OpenAI API connection verified.",
    models: [
      { id: "gpt-5.6-terra", label: "GPT-5.6 Terra" },
      { id: "gpt-5.6-sol", label: "GPT-5.6 Sol" },
    ],
    has_secret: true,
    quick_model: "gpt-5.6-terra",
    detailed_model: "gpt-5.6-sol",
  };
  renderApp("/setup/resume");
  expect(
    await screen.findByRole("heading", { name: "Upload your résumé" }),
  ).toBeVisible();

  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  expect(input).not.toBeNull();
  fireEvent.change(input!, {
    target: { files: [new File(["Python platform engineer"], "candidate.md")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Upload and preview" }));
  expect(await screen.findByDisplayValue("Python platform engineer")).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "Analyze résumé" }));
  expect(await screen.findByRole("heading", { name: "Review job profile" })).toBeVisible();
  fireEvent.change(screen.getByLabelText("Desired job titles"), {
    target: { value: "Staff Platform Engineer" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Confirm profile" }));

  expect(
    await screen.findByRole("heading", {
      name: "Choose searches to run",
    }),
  ).toBeVisible();
  expect(screen.getByText("0 selected")).toBeVisible();
  expect(screen.queryByText(/Recommended #/)).toBeNull();
  expect(screen.queryByLabelText("Location")).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", {
    name: "Select Software Engineer",
  }));
  fireEvent.click(screen.getByRole("checkbox", {
    name: "Select AI / LLM Engineer",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Save 2 searches and continue",
  }));
  await waitFor(() => {
    const searchRequest = calls.find(
      (call) => call.url === "/api/onboarding/searches" && call.method === "PUT",
    );
    expect(searchRequest).toBeDefined();
    const savedSearches = (
      searchRequest!.body as { searches: { query: string; enabled: boolean }[] }
    ).searches;
    expect(savedSearches.filter((item) => item.query === "Software Engineer"))
      .toEqual(expect.arrayContaining([
        expect.objectContaining({ enabled: true }),
        expect.objectContaining({ enabled: true }),
      ]));
    expect(savedSearches.filter((item) => item.query === "AI / LLM Engineer"))
      .toEqual(expect.arrayContaining([
        expect.objectContaining({ enabled: true }),
        expect.objectContaining({ enabled: true }),
      ]));
  });
  expect(
    await screen.findByRole("heading", { name: "Choose companies to track" }),
  ).toBeVisible();
  expect(screen.getByText("Acme Systems")).toBeVisible();
  expect(screen.getByText("No Board Labs")).toBeVisible();
  expect(screen.getByRole("checkbox", { name: "Track Acme Systems" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Track No Board Labs" })).toBeDisabled();
  expect(screen.getByRole("link", { name: /More options below/ })).toHaveAttribute(
    "href",
    "#broad-company-catalog",
  );
  fireEvent.click(screen.getByRole("button", { name: "Add 1 company" }));
  await waitFor(() => {
    const addRequest = calls.find(
      (call) => call.url === "/api/companies/bulk" && call.method === "POST",
    );
    expect(addRequest?.body).toEqual({
      rows: [{ slug: "acme", vendor: "greenhouse" }],
    });
  });
  fireEvent.click(screen.getByRole("button", { name: "Load broad company catalog" }));
  fireEvent.click(await screen.findByRole("button", { name: "Add all 2 companies" }));
  await waitFor(() => {
    const broadRequest = calls.filter(
      (call) => call.url === "/api/companies/bulk" && call.method === "POST",
    )[1];
    expect(broadRequest?.body).toEqual({
      rows: [
        { slug: "broad-one", vendor: "greenhouse" },
        { slug: "broad-two", vendor: "ashby" },
      ],
    });
  });
  fireEvent.click(screen.getByRole("button", { name: "Continue to review setup" }));
  expect(
    await screen.findByRole("heading", { name: "Review remaining settings" }),
  ).toBeVisible();
  expect(screen.getByLabelText("Allowed locations")).toHaveValue("New York, NY");
  expect(screen.queryByLabelText("Expected job descriptions")).toBeNull();
  expect(screen.getByLabelText("Maximum unique jobs to evaluate per run"))
    .toHaveValue(150);
  expect(screen.getByText("150 evaluations")).toBeVisible();
  expect(screen.getByText("150 planned calls")).toBeVisible();
  expect(screen.queryByLabelText("Quick-to-detailed threshold")).toBeNull();
  expect(await screen.findByText("Pro plan")).toBeVisible();
  expect(screen.getByText("35% used · 65% remaining")).toBeVisible();
  expect(screen.getByText(/Personal filter learning starts after setup/)).toBeVisible();
  expect(screen.queryByRole("checkbox", { name: "Enable local ML filter" })).not.toBeInTheDocument();
  expect(
    await screen.findByText("Representative Platform Engineer at Indeed Sample"),
  ).toBeVisible();
  expect(
    (screen.getByLabelText("Representative job description") as HTMLTextAreaElement).value,
  ).toContain("real production role");
  fireEvent.click(screen.getByRole("button", { name: "Measure one evaluation" }));
  expect(await screen.findByText("About $0.0100 per JD")).toBeVisible();
  expect(screen.getByText("About $1.50 for 150 JDs")).toBeVisible();
  expect(screen.getByText("About 3,500 tokens per JD")).toBeVisible();
  expect(screen.getByText("No whole percentage point detected")).toBeVisible();
  fireEvent.change(screen.getByLabelText("Maximum unique jobs to evaluate per run"), {
    target: { value: "80" },
  });
  fireEvent.change(screen.getByLabelText("Daily model-call limit"), {
    target: { value: "70" },
  });
  fireEvent.change(screen.getByLabelText("Company career pages maximum jobs per scan"), {
    target: { value: "700" },
  });
  fireEvent.change(screen.getByLabelText("Indeed maximum jobs per scan"), {
    target: { value: "350" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Finish setup" }));
  expect(await screen.findByRole("heading", { name: "Setup complete" })).toBeVisible();
  const finishRequest = calls.find(
    (call) => call.url === "/api/onboarding/finish" && call.method === "POST",
  );
  expect(finishRequest?.body).toEqual(expect.objectContaining({
    expected_jobs: 80,
    configuration: expect.objectContaining({
      llm: expect.objectContaining({ max_daily_score_calls: 70 }),
      scoring: expect.objectContaining({
        default_eval_limit: 80,
        ml_gate_enabled: false,
      }),
      sources: expect.objectContaining({
        ats: expect.objectContaining({ max_jobs: 700 }),
        indeed: expect.objectContaining({ max_jobs: 350 }),
      }),
      hard_filters: expect.objectContaining({
        location_allowlist: ["New York, NY"],
        title_blocklist: [],
      }),
    }),
  }));
  const request = calls.find(
    (call) => call.url === "/api/onboarding/profile" && call.method === "PUT",
  );
  expect((request?.body as JobProfile).desired_titles).toEqual([
    "Staff Platform Engineer",
  ]);
});

test("custom search pairs generate both source URLs from the role", async () => {
  renderApp("/setup/searches");

  expect(await screen.findByRole("heading", { name: "Choose searches to run" }))
    .toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /Add a custom search pair/ }));
  expect(screen.queryByLabelText("LinkedIn Guest search URL")).toBeNull();
  expect(screen.queryByLabelText("Indeed search URL")).toBeNull();

  fireEvent.change(screen.getByLabelText("Role or query"), {
    target: { value: "Site Reliability Engineer" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add search pair" }));

  expect(screen.getByText("1 selected")).toBeVisible();
  fireEvent.click(screen.getByRole("button", {
    name: "Save 1 search and continue",
  }));
  await waitFor(() => {
    const request = calls.find(
      (call) => call.url === "/api/onboarding/searches" && call.method === "PUT",
    );
    const added = (
      request?.body as { searches: { query: string; source: string; url: string }[] }
    ).searches.filter((item) => item.query === "Site Reliability Engineer");
    expect(added).toEqual(expect.arrayContaining([
      expect.objectContaining({
        source: "linkedin_guest",
        url: "https://www.linkedin.com/jobs/search/?keywords=Site+Reliability+Engineer&location=New+York&f_TPR=r1209600",
      }),
      expect.objectContaining({
        source: "indeed",
        url: "https://www.indeed.com/jobs?q=Site+Reliability+Engineer&l=New+York&fromage=14",
      }),
    ]));
  });
});

test("Claude calibration estimates the selected five-hour plan allowance", async () => {
  providerState = {
    provider: "claude_cli",
    connected: true,
    detail: "Claude Code login verified.",
    models: [
      { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
      { id: "claude-opus-4-8", label: "Claude Opus 4.8" },
    ],
    has_secret: false,
    quick_model: "claude-sonnet-5",
    detailed_model: "claude-opus-4-8",
  };
  resumeState = {
    original_name: "candidate.md",
    extracted_text: "Python platform engineer",
    profile: PROFILE,
    is_confirmed: true,
  };
  renderApp("/setup/review");

  expect(
    await screen.findByRole("heading", { name: "Review remaining settings" }),
  ).toBeVisible();
  expect(screen.getByText("Claude Code 5-hour allowance")).toBeVisible();
  expect(screen.getAllByText("Max 5x · ≈$90")[0]).toBeVisible();
  await screen.findByText("Representative Platform Engineer at Indeed Sample");

  fireEvent.click(screen.getByRole("button", { name: "Measure one evaluation" }));
  expect(
    await screen.findByText("Estimated 1.67% of your 5-hour allowance"),
  ).toBeVisible();

  fireEvent.click(screen.getAllByText("Pro · ≈$18")[0]!);
  expect(
    screen.getByText("Estimated 8.33% of your 5-hour allowance"),
  ).toBeVisible();
});

test("Codex calibration shows projected allowance in the usage summary", async () => {
  providerState = {
    provider: "codex_cli",
    connected: true,
    detail: "Codex login verified.",
    models: [
      { id: "gpt-5.6-luna", label: "GPT-5.6 Luna" },
      { id: "gpt-5.6-terra", label: "GPT-5.6 Terra" },
    ],
    has_secret: false,
    quick_model: "gpt-5.6-luna",
    detailed_model: "gpt-5.6-terra",
  };
  resumeState = {
    original_name: "candidate.md",
    extracted_text: "Python platform engineer",
    profile: PROFILE,
    is_confirmed: true,
  };
  renderApp("/setup/review");

  expect(await screen.findByText("Measure one evaluation below")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Measure one evaluation" }));
  expect(
    await screen.findByText("Estimated 1.82% of your 7-day allowance"),
  ).toBeVisible();
});

test("review loads a representative JD from confirmed Indeed searches", async () => {
  hasStoredCalibrationJob = false;
  providerState = {
    provider: "codex_cli",
    connected: true,
    detail: "Codex login verified.",
    models: [{ id: "gpt-5.6-luna", label: "GPT-5.6 Luna" }],
    has_secret: false,
    quick_model: "gpt-5.6-luna",
    detailed_model: "gpt-5.6-terra",
  };
  resumeState = {
    original_name: "candidate.md",
    extracted_text: "Python platform engineer",
    profile: PROFILE,
    is_confirmed: true,
  };

  renderApp("/setup/review");

  expect(
    await screen.findByText("Representative job from your Indeed searches"),
  ).toBeVisible();
  expect(
    await screen.findByText("Representative Platform Engineer at Indeed Sample"),
  ).toBeVisible();
  expect(
    (screen.getByLabelText("Representative job description") as HTMLTextAreaElement).value,
  ).toContain("real production role");
  expect(calls.some((call) => call.url === "/api/onboarding/calibration-job")).toBe(true);
});

test("enabled URL sources explain what is missing before save", async () => {
  currentConfig = { ...structuredClone(CONFIG), configured: true };
  renderApp("/setup");
  await screen.findByRole("heading", { name: "Workspace settings" });

  fireEvent.click(screen.getByRole("checkbox", { name: "Indeed search" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  expect(await screen.findByText("Add at least one Indeed search URL.")).toBeVisible();
  expect(calls.some((call) => call.method === "PUT")).toBe(false);
});

test("trained personal ML can be explicitly enabled from workspace settings", async () => {
  currentConfig = { ...structuredClone(CONFIG), configured: true };
  renderApp("/setup");
  await screen.findByRole("heading", { name: "Workspace settings" });

  expect(screen.getByText(/Model evaluation at threshold 0\.19/)).toHaveTextContent(
    "97.3% recall · 74.5% irrelevant jobs rejected · 54.4% precision · 69.8% F1 · 7,002 labeled jobs",
  );

  fireEvent.click(screen.getByRole("checkbox", { name: "Enable personal ML filter" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  await waitFor(() => {
    const request = calls.find(
      (call) => call.url === "/api/config" && call.method === "PUT",
    );
    expect((request?.body as typeof CONFIG).scoring.ml_gate_enabled).toBe(true);
    expect(request?.body).not.toHaveProperty("ml_gate_performance");
  });
});

test("configured workspace settings still save and return to triage", async () => {
  currentConfig = {
    ...structuredClone(CONFIG),
    configured: true,
    sources: {
      ...structuredClone(CONFIG.sources),
      linkedin_guest: {
        ...structuredClone(CONFIG.sources.linkedin_guest),
        enabled: true,
        search_urls: ["https://www.linkedin.com/jobs/search/?keywords=platform"],
      },
      indeed: {
        ...structuredClone(CONFIG.sources.indeed),
        enabled: true,
        search_urls: ["https://www.indeed.com/jobs?q=platform"],
      },
      speedyapply: {
        ...structuredClone(CONFIG.sources.speedyapply),
        enabled: true,
        search_urls: ["https://example.com/jobs.md"],
      },
      linkedin: {
        ...structuredClone(CONFIG.sources.linkedin),
        enabled: true,
        search_urls: ["https://www.linkedin.com/jobs/search/?keywords=platform"],
      },
    },
  } as unknown as typeof currentConfig;
  renderApp("/setup");
  await screen.findByRole("heading", { name: "Workspace settings" });

  fireEvent.change(screen.getByLabelText("Resume file"), {
    target: { value: "data/candidate-resume.md" },
  });
  fireEvent.change(screen.getByLabelText("Company career pages maximum jobs per scan"), {
    target: { value: "600" },
  });
  fireEvent.change(screen.getByLabelText("LinkedIn guest maximum jobs per scan"), {
    target: { value: "500" },
  });
  fireEvent.change(screen.getByLabelText("Indeed maximum jobs per scan"), {
    target: { value: "400" },
  });
  fireEvent.change(screen.getByLabelText("SpeedyApply maximum jobs per scan"), {
    target: { value: "300" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Advanced settings/ }));
  fireEvent.change(screen.getByLabelText("Authenticated LinkedIn maximum jobs per scan"), {
    target: { value: "200" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  await waitFor(() => {
    const request = calls.find(
      (call) => call.url === "/api/config" && call.method === "PUT",
    );
    expect((request?.body as typeof CONFIG).llm.master_resume_path).toBe(
      "data/candidate-resume.md",
    );
    expect((request?.body as typeof CONFIG).sources.ats.max_jobs).toBe(600);
    expect((request?.body as typeof CONFIG).sources.linkedin_guest.max_jobs).toBe(500);
    expect((request?.body as typeof CONFIG).sources.indeed.max_jobs).toBe(400);
    expect((request?.body as typeof CONFIG).sources.speedyapply.max_jobs).toBe(300);
    expect((request?.body as typeof CONFIG).sources.linkedin.max_jobs).toBe(200);
  });
  expect(await screen.findByRole("heading", { name: "Triage" })).toBeVisible();
});
