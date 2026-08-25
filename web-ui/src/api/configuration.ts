import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/api/client";
import type { components } from "@/api/types.gen";

export type ConfigurationResponse = components["schemas"]["ConfigurationResponse"];
export type EditableConfiguration = components["schemas"]["EditableConfiguration"];

export interface FinishOnboardingBody {
  configuration: EditableConfiguration;
  expected_jobs: number;
}

export type PlanUsageResponse = components["schemas"]["PlanUsageResponse"];

export type EvaluationCalibrationResponse =
  components["schemas"]["EvaluationCalibrationResponse"];

export const configurationKey = ["configuration"] as const;

export type CalibrationJobSample = components["schemas"]["CalibrationJobResponse"];
export type PersonalMLStatus = components["schemas"]["PersonalMLStatusResponse"];

/** Read effective local configuration and first-run completion state. */
export function useConfiguration() {
  return useQuery({
    queryKey: configurationKey,
    queryFn: () => apiFetch<ConfigurationResponse>("/api/config"),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** Read the user's teacher-label and shadow-validation progress. */
export function usePersonalMLStatus() {
  return useQuery({
    queryKey: ["personal-ml", "status"],
    queryFn: () => apiFetch<PersonalMLStatus>("/api/personal-ml/status"),
    staleTime: 30_000,
  });
}

/** Explicitly activate a threshold that the backend has rechecked as ready. */
export function useActivatePersonalML() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch<ConfigurationResponse>("/api/personal-ml/activate", {
        method: "POST",
      }),
    onSuccess: (configuration) => {
      queryClient.setQueryData(configurationKey, configuration);
      void queryClient.invalidateQueries({ queryKey: ["personal-ml", "status"] });
    },
  });
}

/** Read the signed-in provider's live plan window when it is exposed locally. */
export function useOnboardingPlanUsage() {
  return useQuery({
    queryKey: ["onboarding", "plan-usage"],
    queryFn: () => apiFetch<PlanUsageResponse>("/api/onboarding/plan-usage"),
    staleTime: 60_000,
  });
}

/** Load the mean-length representative from confirmed Indeed searches. */
export function useRandomCalibrationJob() {
  return useQuery({
    queryKey: ["onboarding", "calibration-job"],
    queryFn: () => apiFetch<CalibrationJobSample>("/api/onboarding/calibration-job"),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** Measure one real unified evaluation for the current draft. */
export function useEvaluationCalibration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobDescription: string) =>
      apiFetch<EvaluationCalibrationResponse>(
        "/api/onboarding/evaluation-calibration",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job_description: jobDescription }),
        },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["onboarding", "plan-usage"] });
    },
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

/** Atomically apply the completed onboarding draft and mark setup finished. */
export function useFinishOnboarding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: FinishOnboardingBody) =>
      apiFetch<ConfigurationResponse>("/api/onboarding/finish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    onSuccess: (configuration) => {
      queryClient.setQueryData(configurationKey, configuration);
    },
  });
}
