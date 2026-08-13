import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/api/client";
import type { components } from "@/api/types.gen";

export type ConfigurationResponse = components["schemas"]["ConfigurationResponse"];
export type EditableConfiguration = components["schemas"]["EditableConfiguration"];

export const configurationKey = ["configuration"] as const;

/** Read effective local configuration and first-run completion state. */
export function useConfiguration() {
  return useQuery({
    queryKey: configurationKey,
    queryFn: () => apiFetch<ConfigurationResponse>("/api/config"),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** Persist all GUI-managed settings and update the onboarding gate. */
export function useSaveConfiguration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (configuration: EditableConfiguration) =>
      apiFetch<ConfigurationResponse>("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(configuration),
      }),
    onSuccess: (configuration) => {
      queryClient.setQueryData(configurationKey, configuration);
    },
  });
}
