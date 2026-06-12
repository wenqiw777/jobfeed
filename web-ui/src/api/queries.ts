/**
 * TanStack Query hooks + typed param builders.
 *
 * Only what the app shell needs lives here for now (sidebar badge
 * counts); zone tasks add their own hooks. Filters are always built via
 * buildJobsParams — never hand-concatenated query strings — so the
 * params stay typed against the generated OpenAPI types.
 */
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { apiFetch, apiPost, apiUpload } from "./client";
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

/**
 * Structured query keys. Every jobs *list* key starts with "jobs" and
 * every *detail* key with "job", so decide mutations can invalidate the
 * whole list family without touching unrelated caches.
 */
export const jobsKeys = {
  lists: ["jobs"] as const,
  list: (query: JobsQuery) => ["jobs", "list", query] as const,
  detail: (id: string | null) => ["job", id] as const,
};

/** Query params of GET /api/jobs, typed off the generated snapshot types. */
export type JobsQuery = NonNullable<paths["/api/jobs"]["get"]["parameters"]["query"]>;

export function buildJobsParams(query: JobsQuery): URLSearchParams {
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

/** One jobs-list page for the given typed params. */
export function useJobsList(query: JobsQuery) {
  return useQuery({
    queryKey: jobsKeys.list(query),
    queryFn: () => apiFetch<JobsListResponse>(`/api/jobs?${buildJobsParams(query)}`),
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

/** Single-row status transition; refreshes lists and the row's detail.
 * Named to avoid colliding with React's built-in useTransition. */
export function useJobTransition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, to, note }: TransitionVars) =>
      apiPost<TransitionResponse>(`/api/jobs/${id}/transition`, { to, note: note ?? null }),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.lists });
      void queryClient.invalidateQueries({ queryKey: jobsKeys.detail(id) });
    },
  });
}

/** Bulk transition (twin cascade included); refreshes all jobs lists. */
export function useBulkTransition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (items: { id: string; to: TransitionStatus }[]) =>
      apiPost<BulkTransitionResponse>("/api/jobs/bulk/transition", { items }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.lists });
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

/** Set the follow-up time (`at` must be an offset-carrying ISO string). */
export function useFollowup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, at }: { id: string; at: string }) =>
      apiPost<unknown>(`/api/jobs/${id}/followup`, { at }),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: jobsKeys.detail(id) });
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

/** Attention buckets (GET /api/attention) for the Pipeline badge + bar. */
export function useAttention() {
  return useQuery({
    queryKey: ["attention"],
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
