/**
 * TanStack Query hooks + typed param builders.
 *
 * Only what the app shell needs lives here for now (sidebar badge
 * counts); zone tasks add their own hooks. Query strings are always
 * built via the shared `buildParams` encoder (directly, or through a
 * typed per-endpoint wrapper like `buildJobsParams`) — never
 * hand-concatenated — so the params stay typed against the generated
 * OpenAPI types.
 */
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { apiDelete, apiFetch, apiPost } from "./client";
import type { components, paths } from "./types.gen";

export type JobsListResponse = components["schemas"]["JobsListResponse"];
export type AttentionResponse = components["schemas"]["AttentionResponse"];
export type JobSummary = components["schemas"]["JobSummary"];
export type JobDetailResponse = components["schemas"]["JobDetailResponse"];
export type TransitionResponse = components["schemas"]["TransitionResponse"];
export type BulkTransitionResponse = components["schemas"]["BulkTransitionResponse"];
export type TransitionStatus = components["schemas"]["TransitionBody"]["to"];
export type InsightsOverviewResponse = components["schemas"]["InsightsOverviewResponse"];
export type RunsListResponse = components["schemas"]["RunsListResponse"];
export type RunSummary = components["schemas"]["RunSummary"];
export type RunNewJobSourcesResponse =
  components["schemas"]["RunNewJobSourcesResponse"];
export type CompaniesListResponse = components["schemas"]["CompaniesListResponse"];
export type CompanyOut = components["schemas"]["CompanyOut"];
export type CompanyVendor = components["schemas"]["CompanyAddBody"]["vendor"];
export type ProbeResponse = components["schemas"]["ProbeResponse"];
export type ProbeEntryResult = components["schemas"]["ProbeEntryResult"];
export type BulkInsertResponse = components["schemas"]["BulkInsertResponse"];
export type TriggerResponse = components["schemas"]["_TriggerResponse"];

/**
 * Structured query keys. Every jobs *list* key starts with "jobs" and
 * every *detail* key with "job", so decide mutations can invalidate the
 * whole list family without touching unrelated caches.
 */
export const jobsKeys = {
  lists: ["jobs"] as const,
  list: (query: JobsQuery) => ["jobs", "list", query] as const,
  detail: (id: string | null) => ["job", id] as const,
  // Workflow attention buckets — feed the Pipeline chips + sidebar badge,
  // so every workflow-state mutation invalidates this key.
  attention: ["attention"] as const,
};

/** Query params of GET /api/jobs, typed off the generated snapshot types. */
export type JobsQuery = NonNullable<paths["/api/jobs"]["get"]["parameters"]["query"]>;
/** Query params of GET /api/runs. */
export type RunsQuery = NonNullable<paths["/api/runs"]["get"]["parameters"]["query"]>;
/** Query params of GET /api/companies. */
export type CompaniesQuery = NonNullable<
  paths["/api/companies"]["get"]["parameters"]["query"]
>;

/** Shared encoder behind the typed per-endpoint builders below — callers
 * never hand-concatenate query strings. */
function buildParams(query: Record<string, unknown>): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined) {
      continue;
    }
    if (Array.isArray(value)) {
      // FastAPI list params (statuses) repeat the key per value.
      value.forEach((item) => params.append(key, String(item)));
    } else {
      params.append(key, String(value));
    }
  }
  return params;
}

export function buildJobsParams(query: JobsQuery): URLSearchParams {
  return buildParams(query);
}

/** One jobs-list page for the given typed params. `keepPrevious` holds the
 * last page on screen while a filter/page change refetches (Library) —
 * off by default so the triage zones keep their loading semantics. */
export function useJobsList(query: JobsQuery, options: { keepPrevious?: boolean } = {}) {
  const fastEligible =
    query.offset === 0
    && query.tab === "queue"
    && query.apply_hard_filters === true
    && query.dedupe === true
    && query.require_verdict === true;
  const fastQuery = useQuery({
    queryKey: [...jobsKeys.list(query), "fast"],
    queryFn: () => apiFetch<JobsListResponse>(
      `/api/jobs?${buildJobsParams({ ...query, fast: true })}`,
    ),
    enabled: fastEligible,
  });
  const exactQuery = useQuery({
    queryKey: jobsKeys.list(query),
    queryFn: () => apiFetch<JobsListResponse>(`/api/jobs?${buildJobsParams(query)}`),
    placeholderData: options.keepPrevious === true ? keepPreviousData : undefined,
    enabled: !fastEligible || fastQuery.data !== undefined,
  });

  if (!fastEligible || exactQuery.data !== undefined) {
    return exactQuery;
  }
  return fastQuery;
}

/** Load every job id matching a jobs query, across the API's 10k-row windows. */
export async function fetchAllMatchingJobIds(
  query: JobsQuery,
  total: number,
): Promise<string[]> {
  const ids: string[] = [];
  const windowSize = 10_000;
  for (let offset = 0; offset < total; offset += windowSize) {
    const page = await apiFetch<JobsListResponse>(
      `/api/jobs?${buildJobsParams({
        ...query,
        limit: Math.min(windowSize, total - offset),
        offset,
      })}`,
    );
    ids.push(...page.jobs.map((job) => job.id));
  }
  return ids;
}

/** Full detail aggregation for one job; disabled while nothing is selected. */
export function useJobDetail(id: string | null) {
  return useQuery({
    queryKey: jobsKeys.detail(id),
    queryFn: () => apiFetch<JobDetailResponse>(`/api/jobs/${id}`),
    enabled: id !== null,
    // Selection changes keep the previous row's pane visible while the next
    // detail loads instead of flashing the skeleton.
    placeholderData: keepPreviousData,
  });
}

export interface TransitionVars {
  id: string;
  to: TransitionStatus;
  note?: string;
  /** Administrative escape hatch; ordinary Triage decisions omit it. */
  force?: boolean;
}

export interface BulkTransitionVars {
  items: { id: string; to: TransitionStatus }[];
  /** Administrative escape hatch; ordinary Triage decisions omit it. */
  force?: boolean;
}

/** Single-row status transition; refreshes lists, the row's detail, and
 * the attention buckets (status drives bucket eligibility).
 * Named to avoid colliding with React's built-in useTransition. */
export function useJobTransition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, to, note, force }: TransitionVars) =>
      apiPost<TransitionResponse>(`/api/jobs/${id}/transition`, {
        to,
        note: note ?? null,
        force: force ?? false,
      }),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.lists });
      void queryClient.invalidateQueries({ queryKey: jobsKeys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: jobsKeys.attention });
    },
  });
}

/** Bulk transition (twin cascade included); refreshes all jobs lists and
 * the attention buckets. */
export function useBulkTransition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ items, force }: BulkTransitionVars) =>
      apiPost<BulkTransitionResponse>("/api/jobs/bulk/transition", {
        items,
        force: force ?? false,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.lists });
      void queryClient.invalidateQueries({ queryKey: jobsKeys.attention });
    },
  });
}

/**
 * Global tab counts for the sidebar.
 *
 * Per the API contract (plan A4): tab_counts come back on every /api/jobs
 * response, each tab computed with its own predicate plus the request's
 * shared filters. This request is deliberately neutral — no statuses, no
 * require_verdict, limit=0 (counts only, no rows) — so the counts are
 * global, not narrowed to any zone's view.
 */
export function useJobsTabCounts() {
  return useQuery({
    queryKey: ["jobs", "tab-counts"],
    queryFn: () =>
      apiFetch<JobsListResponse>(`/api/jobs?${buildJobsParams({ limit: 0 })}`),
  });
}

/**
 * Insights overview for one discovery cohort. Every metric uses the selected
 * window; `all` removes the lower cutoff. Window switches keep the previous
 * charts up while the new window loads instead of flashing skeletons.
 */
export function useInsightsOverview(windowDays: number | "all") {
  return useQuery({
    queryKey: ["insights", "overview", windowDays] as const,
    queryFn: () =>
      apiFetch<InsightsOverviewResponse>(`/api/insights/overview?window=${windowDays}`),
    placeholderData: keepPreviousData,
  });
}

/** Attention buckets (GET /api/attention) for the Pipeline badge + bar. */
export function useAttention() {
  return useQuery({
    queryKey: jobsKeys.attention,
    queryFn: () => apiFetch<AttentionResponse>("/api/attention"),
  });
}

/**
 * The Pipeline sidebar badge: total of the three *workflow* buckets
 * (follow-up due / interview prep / going ghosted) — the actionable items
 * the Pipeline zone's attention bar surfaces. Pipeline-health buckets
 * (enrich errors etc.) are diagnostics, not badge-worthy work items.
 */
export function workflowAttentionTotal(attention: AttentionResponse): number {
  return (
    attention.follow_up_today.length +
    attention.interview_prep.length +
    attention.going_ghosted.length
  );
}

/** Runs list keys — trigger mutations invalidate after firing. */
export const runsKeys = {
  list: (query: RunsQuery) => ["runs", "list", query] as const,
  active: ["runs", "active"] as const,
};

/** One runs-list page, newest first (server order). keepPreviousData
 * holds the current page while the next one loads (Library's pager
 * semantics, including the placeholder Next-guard). */
export function useRuns(query: RunsQuery) {
  return useQuery({
    queryKey: runsKeys.list(query),
    queryFn: () => apiFetch<RunsListResponse>(`/api/runs?${buildParams(query)}`),
    placeholderData: keepPreviousData,
  });
}

/** Exact first-insert attribution for one expanded historical scan. */
export function useRunNewJobSources(runId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["runs", runId, "new-job-sources"] as const,
    queryFn: () =>
      apiFetch<RunNewJobSourcesResponse>(`/api/runs/${runId}/new-job-sources`),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** Companies keys: every mutation below invalidates the whole family. */
export const companiesKeys = {
  all: ["companies"] as const,
  list: (query: CompaniesQuery) => ["companies", "list", query] as const,
};

/** Tracked ATS companies, slug-ascending (server order). */
export function useCompanies(includeRemoved = false) {
  // Omit the param at its server default so both spellings of "off"
  // share one cache entry.
  const query: CompaniesQuery = includeRemoved ? { include_removed: true } : {};
  return useQuery({
    queryKey: companiesKeys.list(query),
    queryFn: () =>
      apiFetch<CompaniesListResponse>(`/api/companies?${buildParams(query)}`),
  });
}

/** Track one company with a pinned vendor (upsert; restores removed slugs). */
export function useAddCompany() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, vendor }: { slug: string; vendor: CompanyVendor }) =>
      apiPost<CompanyOut>("/api/companies", { slug, vendor }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: companiesKeys.all });
    },
  });
}

/** Bulk upsert (the probe-flow confirm). The server skips slugs that are
 * already active — `inserted` is the honest count to surface. */
export function useBulkAddCompanies() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (rows: { slug: string; vendor: CompanyVendor }[]) =>
      apiPost<BulkInsertResponse>("/api/companies/bulk", { rows }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: companiesKeys.all });
    },
  });
}

/** Soft-remove a tracked company (destructive; gate behind the dialog). */
export function useRemoveCompany() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ slug }: { slug: string }) =>
      apiDelete<unknown>(`/api/companies/${encodeURIComponent(slug)}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: companiesKeys.all });
    },
  });
}

/** Resolve pasted entries to slug+vendor. Read-only on the server (no
 * writes), so nothing is invalidated. */
export function useProbeCompanies() {
  return useMutation({
    mutationFn: (entries: string[]) =>
      apiPost<ProbeResponse>("/api/companies/probe", { entries }),
  });
}

// ---------------------------------------------------------------------------
// Runs: active + trigger mutations
// ---------------------------------------------------------------------------

/** Active run shape returned by GET /api/runs/active. The OpenAPI schema
 * types the response as a generic dict, so we declare the concrete shape
 * here for frontend consumption. */
export interface ActiveRunEntry {
  run_id: string;
  source: string;
  started_at: string;
  counters: RunSummary;
}

export interface ActiveRunsResponse {
  runs: ActiveRunEntry[];
}

/** Poll active runs (short interval while any are running). */
export function useActiveRuns() {
  return useQuery({
    queryKey: runsKeys.active,
    queryFn: () => apiFetch<ActiveRunsResponse>("/api/runs/active"),
    // 3 s while runs are active, checked via refetchInterval callback.
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && data.runs.length > 0 ? 3_000 : false;
    },
  });
}

/** Trigger a scan run. Invalidates both the active and list queries on
 * success so the UI picks up the new run immediately. */
export function useTriggerScan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ source }: { source: string }) =>
      apiPost<TriggerResponse>("/api/runs/scan", { source }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: runsKeys.active });
      void queryClient.invalidateQueries({ queryKey: runsKeys.list({}) });
    },
  });
}

function invalidateRuns(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: runsKeys.active });
  void queryClient.invalidateQueries({ queryKey: runsKeys.list({}) });
}

export function useStopRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ runId }: { runId: string }) =>
      apiPost<{ run_id: string; status: string }>(`/api/runs/${runId}/stop`, {}),
    onSuccess: () => invalidateRuns(queryClient),
  });
}

export function useRetryRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ runId }: { runId: string }) =>
      apiPost<TriggerResponse>(`/api/runs/${runId}/retry`, {}),
    onSuccess: () => invalidateRuns(queryClient),
  });
}

// ---------------------------------------------------------------------------
// Performance
// ---------------------------------------------------------------------------

export type PerformanceOverviewResponse = components["schemas"]["PerformanceOverviewResponse"];
export type StepTimingsResponse = components["schemas"]["StepTimingsResponse"];
export type StepTimingRow = components["schemas"]["StepTimingRow"];
export type LLMStatsResponse = components["schemas"]["LLMStatsResponse"];
export type LLMDailyStatsRow = components["schemas"]["LLMDailyStatsRow"];
export type FunnelResponse = components["schemas"]["FunnelResponse"];
export type FunnelRow = components["schemas"]["FunnelRow"];

export const performanceKeys = {
  overview: (window: number) => ["performance", "overview", window] as const,
  stepTimings: (window: number, stepType?: string) =>
    ["performance", "step-timings", window, stepType] as const,
  llmStats: (window: number) => ["performance", "llm-stats", window] as const,
  funnel: (window: number) => ["performance", "funnel", window] as const,
};

export function usePerformanceOverview(window: number) {
  return useQuery({
    queryKey: performanceKeys.overview(window),
    queryFn: () =>
      apiFetch<PerformanceOverviewResponse>(
        `/api/performance/overview?${buildParams({ window })}`,
      ),
    placeholderData: keepPreviousData,
  });
}

export function useStepTimings(window: number, stepType?: string) {
  return useQuery({
    queryKey: performanceKeys.stepTimings(window, stepType),
    queryFn: () =>
      apiFetch<StepTimingsResponse>(
        `/api/performance/step-timings?${buildParams({ window, step_type: stepType })}`,
      ),
    placeholderData: keepPreviousData,
  });
}

export function useLLMStats(window: number) {
  return useQuery({
    queryKey: performanceKeys.llmStats(window),
    queryFn: () =>
      apiFetch<LLMStatsResponse>(
        `/api/performance/llm-stats?${buildParams({ window })}`,
      ),
    placeholderData: keepPreviousData,
  });
}

export function useFunnelStats(window: number) {
  return useQuery({
    queryKey: performanceKeys.funnel(window),
    queryFn: () =>
      apiFetch<FunnelResponse>(
        `/api/performance/funnel?${buildParams({ window })}`,
      ),
    placeholderData: keepPreviousData,
  });
}

/** Trigger an evaluate run. Same cache invalidation as scan. */
export function useTriggerEvaluate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      stage,
      scope,
      limit,
    }: {
      stage: "a" | "b" | "both";
      scope: "latest_scan" | "backlog";
      limit: number | null;
    }) =>
      apiPost<TriggerResponse>("/api/runs/evaluate", {
        stage,
        scope,
        limit: limit ?? undefined,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: runsKeys.active });
      void queryClient.invalidateQueries({ queryKey: runsKeys.list({}) });
    },
  });
}
