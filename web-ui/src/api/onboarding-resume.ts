import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/api/client";
import type { components } from "@/api/types.gen";

export type JobProfile = components["schemas"]["JobProfile"];
export type ResumeState = components["schemas"]["ResumeStateResponse"];

export const resumeOnboardingKey = ["onboarding", "resume"] as const;

/** Read the resumable résumé preview and editable profile draft. */
export function useResumeOnboarding() {
  return useQuery({
    queryKey: resumeOnboardingKey,
    queryFn: () => apiFetch<ResumeState>("/api/onboarding/resume"),
  });
}

/** Upload one original résumé without exposing a local filesystem path. */
export function useUploadResume() {
  return useResumeMutation(async (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return apiFetch<ResumeState>("/api/onboarding/resume", {
      method: "POST",
      body,
    });
  });
}

/** Analyze the current résumé with the selected Detailed model. */
export function useAnalyzeResume() {
  return useResumeMutation(() =>
    apiFetch<ResumeState>("/api/onboarding/resume/analyze", {
      method: "POST",
    }),
  );
}

/** Confirm the user's edits instead of silently activating AI suggestions. */
export function useConfirmProfile() {
  return useResumeMutation((profile: JobProfile) =>
    apiFetch<ResumeState>("/api/onboarding/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    }),
  );
}

function useResumeMutation<T>(mutationFn: (value: T) => Promise<ResumeState>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: (state) => queryClient.setQueryData(resumeOnboardingKey, state),
  });
}
