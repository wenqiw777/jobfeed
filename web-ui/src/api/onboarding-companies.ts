import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, apiPost } from "@/api/client";
import type { ProbeResponse } from "@/api/queries";

export interface CompanyRecommendation {
  name: string;
  slug: string;
  rationale: string;
}

export interface CompanyRecommendationState {
  profile_fingerprint: string | null;
  recommendations: CompanyRecommendation[];
}

export interface CatalogCompany {
  slug: string;
  vendor: "greenhouse" | "ashby" | "lever";
}

export interface CompanyCatalogState {
  source_counts: Record<string, number>;
  companies: CatalogCompany[];
}

const recommendationKey = ["onboarding", "companies", "recommendations"] as const;
const probeKey = ["onboarding", "companies", "probe"] as const;
const catalogKey = ["onboarding", "companies", "catalog"] as const;

/** Generate once per confirmed profile, then resume the private local draft. */
export function useCompanyRecommendations() {
  return useQuery({
    queryKey: recommendationKey,
    queryFn: () => apiPost<CompanyRecommendationState>(
      "/api/onboarding/companies/recommend",
      {},
    ),
  });
}

/** Probe every recommended slug without adding anything to the company store. */
export function useProbeRecommendedCompanies(recommendations: CompanyRecommendation[]) {
  const slugs = recommendations.map((recommendation) => recommendation.slug);
  return useQuery({
    queryKey: [...probeKey, slugs],
    queryFn: () => apiPost<ProbeResponse>("/api/companies/probe", { entries: slugs }),
    enabled: slugs.length > 0,
  });
}

/** Explicitly rerun both AI recommendation and real ATS probing. */
export function useRefreshCompanyRecommendations() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost<CompanyRecommendationState>(
      "/api/onboarding/companies/recommend?refresh=true",
      {},
    ),
    onSuccess: async (state) => {
      queryClient.setQueryData(recommendationKey, state);
      await queryClient.invalidateQueries({ queryKey: probeKey });
    },
  });
}

/** Load real ATS slugs from the configured public new-grad and internship lists. */
export function useCompanyCatalog() {
  return useQuery({
    queryKey: catalogKey,
    queryFn: () => apiFetch<CompanyCatalogState>("/api/onboarding/companies/catalog"),
    enabled: false,
  });
}
