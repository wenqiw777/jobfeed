import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/api/client";

export type SearchSource = "linkedin_guest" | "indeed";

export interface SearchSuggestion {
  id: string;
  source: SearchSource;
  query: string;
  location: string;
  url: string;
  enabled: boolean;
}

export interface SearchDraftState {
  profile_fingerprint: string | null;
  searches: SearchSuggestion[];
}

export const onboardingSearchesKey = ["onboarding", "searches"] as const;

/** Generate or resume search suggestions for the confirmed profile. */
export function useOnboardingSearches() {
  return useQuery({
    queryKey: onboardingSearchesKey,
    queryFn: () => apiFetch<SearchDraftState>("/api/onboarding/searches"),
  });
}

/** Persist the user's edited, added, enabled, and disabled searches. */
export function useSaveOnboardingSearches() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (searches: SearchSuggestion[]) =>
      apiFetch<SearchDraftState>("/api/onboarding/searches", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ searches }),
      }),
    onSuccess: (state) => queryClient.setQueryData(onboardingSearchesKey, state),
  });
}
