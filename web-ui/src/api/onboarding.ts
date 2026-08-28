import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/api/client";
import type { components } from "@/api/types.gen";

export type ProviderName = components["schemas"]["ProviderConnectionBody"]["provider"];
export type ProviderState = components["schemas"]["ProviderStateResponse"];
export type ProviderModelsBody = components["schemas"]["ProviderModelsBody"];

export const providerOnboardingKey = ["onboarding", "provider"] as const;

/** Read secret-free, resumable provider onboarding state. */
export function useProviderOnboarding() {
  return useQuery({
    queryKey: providerOnboardingKey,
    queryFn: () => apiFetch<ProviderState>("/api/onboarding/provider"),
  });
}

/** Test one provider connection and cache its redacted model catalog. */
export function testProviderConnection(
  provider: ProviderName,
  apiKey?: string,
  aws?: { region: string; profile?: string },
) {
  return apiFetch<ProviderState>("/api/onboarding/provider/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider,
      api_key: apiKey || null,
      ...(aws ? { region: aws.region, profile: aws.profile || null } : {}),
    }),
  });
}

/** Save Quick and Detailed models from the verified provider catalog. */
export function useSaveProviderModels() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ProviderModelsBody) =>
      apiFetch<ProviderState>("/api/onboarding/provider/models", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    onSuccess: (state) => queryClient.setQueryData(providerOnboardingKey, state),
  });
}
