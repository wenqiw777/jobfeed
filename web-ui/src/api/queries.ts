/**
 * TanStack Query hooks + typed param builders.
 *
 * Only what the app shell needs lives here for now (sidebar badge
 * counts); zone tasks add their own hooks. Filters are always built via
 * buildJobsParams — never hand-concatenated query strings — so the
 * params stay typed against the generated OpenAPI types.
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "./client";
import type { components, paths } from "./types.gen";

export type JobsListResponse = components["schemas"]["JobsListResponse"];
export type AttentionResponse = components["schemas"]["AttentionResponse"];

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
