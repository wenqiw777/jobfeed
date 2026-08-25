import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import type { ReactNode } from "react";

import type { JobDetailResponse } from "@/api/queries";

type Evaluation = JobDetailResponse["evaluation"];
type Twins = JobDetailResponse["twins"];

export function SectionLabel({ children }: { children: ReactNode }) {
  return <Header variant="h3">{children}</Header>;
}

/** Same posting on other platforms. */
export function TwinsLine({ twins }: { twins: Twins }) {
  if (twins.length === 0) return null;
  const uniqueTwins = twins.filter(
    (twin, index) => twins.findIndex((candidate) => candidate.platform === twin.platform) === index,
  );
  return (
    <SpaceBetween direction="horizontal" size="xs">
      <Box color="text-body-secondary">Also seen on:</Box>
      {uniqueTwins.map((twin) => (
        <Link
          key={twin.job_id}
          href={twin.url}
          external
          externalIconAriaLabel="Opens in a new tab"
        >
          {twin.platform} ({twin.status})
        </Link>
      ))}
    </SpaceBetween>
  );
}

/** Evidence from the canonical unified evaluator. */
export function EvaluationSections({ evaluation }: { evaluation: Evaluation }) {
  const hasSummary = evaluation.one_line !== null || evaluation.summary !== null;
  if (
    !hasSummary
    && evaluation.eligibility_checks.length === 0
    && evaluation.requirements.length === 0
  ) return null;

  return (
    <SpaceBetween size="m">
      {hasSummary && (
        <Container header={<SectionLabel>Evaluation summary</SectionLabel>}>
          <SpaceBetween size="xs">
            {evaluation.one_line !== null && <Box variant="strong">{evaluation.one_line}</Box>}
            {evaluation.summary !== null && <Box>{evaluation.summary}</Box>}
          </SpaceBetween>
        </Container>
      )}
      {evaluation.eligibility_checks.length > 0 && (
        <Container header={<SectionLabel>Eligibility checks</SectionLabel>}>
          <KeyValuePairs
            columns={1}
            items={evaluation.eligibility_checks.map((check, index) => ({
              label: check.requirement ?? check.kind ?? `Check ${index + 1}`,
              value: evidenceValue(
                check.status ?? null,
                check.candidate_evidence ?? null,
                check.reason ?? null,
              ),
            }))}
          />
        </Container>
      )}
      {evaluation.requirements.length > 0 && (
        <Container header={<SectionLabel>Requirement evidence</SectionLabel>}>
          <KeyValuePairs
            columns={1}
            items={evaluation.requirements.map((requirement, index) => ({
              label: requirementLabel(requirement, index),
              value: evidenceValue(
                requirement.match ?? null,
                requirement.resume_evidence ?? null,
                requirement.evidence_type ?? null,
              ),
            }))}
          />
        </Container>
      )}
    </SpaceBetween>
  );
}

function requirementLabel(
  requirement: Evaluation["requirements"][number],
  index: number,
): string {
  return [
    requirement.requirement ?? `Requirement ${index + 1}`,
    requirement.priority,
    requirement.category,
  ].filter((item): item is string => item != null).join(" · ");
}

function evidenceValue(
  status: string | null,
  evidence: string | null,
  reason: string | null,
): ReactNode {
  const parts = [status, evidence, reason].filter((item): item is string => item !== null);
  return parts.length > 0 ? parts.join(" · ") : "—";
}
