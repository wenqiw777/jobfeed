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

import { apiDelete, apiFetch, apiPatch, apiPost, apiUpload } from "./client";
import type { components, paths } from "./types.gen";

export type JobsListResponse = components["schemas"]["JobsListResponse"];
export type AttentionResponse = components["schemas"]["AttentionResponse"];
export type JobSummary = components["schemas"]["JobSummary"];
export type JobDetailResponse = components["schemas"]["JobDetailResponse"];
export type TransitionResponse = components["schemas"]["TransitionResponse"];
export type BulkTransitionResponse = components["schemas"]["BulkTransitionResponse"];
export type JdPasteResponse = components["schemas"]["JdPasteResponse"];
export type ApplyResponse = components["schemas"]["ApplyResponse"];
export type TransitionStatus = components["schemas"]["TransitionBody"]["to"];
export type RestoreResponse = components["schemas"]["RestoreResponse"];
export type InterviewRound = components["schemas"]["InterviewRoundDetail"];
export type InterviewsListResponse = components["schemas"]["InterviewsListResponse"];
export type InsightsOverviewResponse = components["schemas"]["InsightsOverviewResponse"];
export type RunsListResponse = components["schemas"]["RunsListResponse"];
export type RunSummary = components["schemas"]["RunSummary"];
export type CompaniesListResponse = components["schemas"]["CompaniesListResponse"];
export type CompanyOut = components["schemas"]["CompanyOut"];
export type CompanyVendor = components["schemas"]["CompanyAddBody"]["vendor"];
export type ProbeResponse = components["schemas"]["ProbeResponse"];
export type ProbeEntryResult = components["schemas"]["ProbeEntryResult"];
export type BulkInsertResponse = components["schemas"]["BulkInsertResponse"];

/**
 * Structured query keys. Every jobs *list* key starts with "jobs" and
 * every *detail* key with "job", so decide mutations can invalidate the
 * whole list family without touching unrelated caches.
 */
export const jobsKeys = {
  lists: ["jobs"] as const,
  list: (query: JobsQuery) => ["jobs", "list", query] as const,
  detail: (id: string | null) => ["job", id] as const,
  // Nested under detail(id), so invalidating the detail key refreshes
  // the rounds too (TanStack prefix matching).
  interviews: (id: string | null) => ["job", id, "interviews"] as const,
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
  return useQuery({
    queryKey: jobsKeys.list(query),
    queryFn: () => apiFetch<JobsListResponse>(`/api/jobs?${buildJobsParams(query)}`),
    placeholderData: options.keepPrevious === true ? keepPreviousData : undefined,
  });
}

/** Full detail aggregation for one job; disabled while nothing is selected. */
export function useJobDetail(id: string | null) {
  return useQuery({
    queryKey: jobsKeys.detail(id),
    queryFn: () => apiFetch<JobDetailResponse>(`/api/jobs/${id}`),
    enabled: id !== null,
    // j/k navigation keeps the previous row's pane visible while the next
    // detail loads instead of flashing the skeleton.
    placeholderData: keepPreviousData,
  });
}

export interface TransitionVars {
  id: string;
  to: TransitionStatus;
  note?: string;
}

/** Single-row status transition; refreshes lists, the row's detail, and
 * the attention buckets (status drives bucket eligibility).
 * Named to avoid colliding with React's built-in useTransition. */
export function useJobTransition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, to, note }: TransitionVars) =>
      apiPost<TransitionResponse>(`/api/jobs/${id}/transition`, { to, note: note ?? null }),
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
    mutationFn: (items: { id: string; to: TransitionStatus }[]) =>
      apiPost<BulkTransitionResponse>("/api/jobs/bulk/transition", { items }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.lists });
      void queryClient.invalidateQueries({ queryKey: jobsKeys.attention });
    },
  });
}

/** Append a note (detail-only data — lists don't render notes). */
export function useNote() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) =>
      apiPost<unknown>(`/api/jobs/${id}/note`, { text }),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.detail(id) });
    },
  });
}

/** Set the follow-up time (`at` must be an offset-carrying ISO string).
 * Attention refreshes too: next_followup_at drives the follow-up bucket. */
export function useFollowup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, at }: { id: string; at: string }) =>
      apiPost<unknown>(`/api/jobs/${id}/followup`, { at }),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: jobsKeys.attention });
    },
  });
}

/** Paste JD text; the row leaves pending_jd, so lists refresh too. */
export function usePasteJd() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) =>
      apiPost<JdPasteResponse>(`/api/jobs/${id}/jd`, { text }),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.lists });
      void queryClient.invalidateQueries({ queryKey: jobsKeys.detail(id) });
    },
  });
}

/** Record an application (multipart upload, plan D8). */
export function useApply() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, form }: { id: string; form: FormData }) =>
      apiUpload<ApplyResponse>(`/api/jobs/${id}/apply`, form),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.lists });
      void queryClient.invalidateQueries({ queryKey: jobsKeys.detail(id) });
    },
  });
}

/** Interview rounds of one job; disabled while nothing is selected. */
export function useInterviews(id: string | null) {
  return useQuery({
    queryKey: jobsKeys.interviews(id),
    queryFn: () => apiFetch<InterviewsListResponse>(`/api/jobs/${id}/interviews`),
    enabled: id !== null,
  });
}

/** Add an interview round. Refreshes lists too: adding to an applied job
 * auto-transitions it to interviewing, which moves its pipeline group —
 * and into interview-prep attention scope. */
export function useAddInterview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, label, scheduledAt }: { id: string; label: string; scheduledAt: string | null }) =>
      apiPost<InterviewRound>(`/api/jobs/${id}/interviews`, {
        label,
        scheduled_at: scheduledAt,
      }),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.lists });
      void queryClient.invalidateQueries({ queryKey: jobsKeys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: jobsKeys.attention });
    },
  });
}

/** Complete an interview round with optional notes. Detail-only for lists,
 * but interview-prep attention keys off uncompleted rounds, so refresh it. */
export function useCompleteInterview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, roundIndex, notes }: { id: string; roundIndex: number; notes: string | null }) =>
      apiPatch<InterviewRound>(`/api/jobs/${id}/interviews/${roundIndex}`, { notes }),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: jobsKeys.attention });
    },
  });
}

/** Restore a ghosted/archived job to its last non-terminal status (the
 * server derives the target from history — never recomputed here). */
export function useRestore() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      apiPost<RestoreResponse>(`/api/jobs/${id}/restore`, {}),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.lists });
      void queryClient.invalidateQueries({ queryKey: jobsKeys.detail(id) });
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
 * Insights overview for one window. Totals and distributions are
 * all-time; `daily` and `applications` cover the window (backend
 * docstring). Window switches keep the previous charts up while the new
 * window loads instead of flashing skeletons.
 */
export function useInsightsOverview(windowDays: number) {
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

/** Runs list keys — read-only zone, nothing ever invalidates these. */
export const runsKeys = {
  list: (query: RunsQuery) => ["runs", "list", query] as const,
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
