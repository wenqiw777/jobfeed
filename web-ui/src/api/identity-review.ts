import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";

/** Queue membership is decided once by the server, including current filters. */
export function usePendingIdentityReview(enabled = true) {
  return useQuery({
    queryKey: ["identity-review", "pending-count"],
    queryFn: () => apiFetch<{ job_total: number; pending_job_ids: string[] }>("/api/identity-review?status=pending&limit=1"),
    enabled,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });
}
