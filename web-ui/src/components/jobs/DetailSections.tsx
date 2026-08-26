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

/** Stage A and Stage B evidence rendered with Cloudscape containers. */
export function EvaluationSections({ evaluation }: { evaluation: Evaluation }) {
  const stageA = evaluation.stage_a;
  const stageB = evaluation.stage_b;
  if (stageA === null && stageB === null) return null;
  return (
    <SpaceBetween size="m">
      {stageA !== null && (
        <Container header={<Header variant="h3">Quick evaluation</Header>}>
          <Box>{stageA.one_line}</Box>
        </Container>
      )}
      {stageB !== null && <StageBBlocks stageB={stageB} />}
    </SpaceBetween>
  );
}

function StageBBlocks({ stageB }: { stageB: NonNullable<Evaluation["stage_b"]> }) {
  const strengths = stageB.strengths ?? [];
  const gaps = stageB.gaps ?? [];
  const summary = stageB.jd_summary ?? "";
  return (
    <SpaceBetween size="m">
      {summary !== "" && (
        <Container header={<SectionLabel>Job description summary</SectionLabel>}>
          <Box>{summary}</Box>
        </Container>
      )}
      {strengths.length > 0 && (
        <Container header={<SectionLabel>Strengths</SectionLabel>}>
          <KeyValuePairs
            columns={1}
            items={strengths.map((item) => ({
              label: item.requirement,
              value: item.evidence,
            }))}
          />
        </Container>
      )}
      {gaps.length > 0 && (
        <Container header={<SectionLabel>Gaps</SectionLabel>}>
          <KeyValuePairs
            columns={1}
            items={gaps.map((item) => ({
              label: `${item.requirement} · ${item.severity}`,
              value: item.mitigation,
            }))}
          />
        </Container>
      )}
      <HooksBlock hooks={stageB.hooks} />
    </SpaceBetween>
  );
}

function HooksBlock({ hooks }: { hooks: NonNullable<Evaluation["stage_b"]>["hooks"] }) {
  if (hooks === undefined || hooks.lead_with === "") return null;
  const items = [{ label: "Lead with", value: hooks.lead_with }];
  if (hooks.supporting.length > 0) {
    items.push({ label: "Supporting", value: hooks.supporting.join(" · ") });
  }
  if (hooks.avoid_mentioning.length > 0) {
    items.push({ label: "Avoid mentioning", value: hooks.avoid_mentioning.join(" · ") });
  }
  return (
    <Container header={<SectionLabel>Resume guidance</SectionLabel>}>
      <KeyValuePairs columns={1} items={items} />
    </Container>
  );
}
