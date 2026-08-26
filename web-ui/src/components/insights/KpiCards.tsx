import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";

import type { InsightsOverviewResponse } from "@/api/queries";

/** Headline totals for the currently selected discovery period. */
export function KpiCards({ overview }: { overview: InsightsOverviewResponse }) {
  const { totals, window_days: windowDays } = overview;
  const selectedPeriod = windowDays === null ? "all time" : `last ${windowDays} days`;
  const items = [
    { label: "Discovered", value: totals.jobs, meta: selectedPeriod },
    { label: "Passed local filter", value: totals.ml_gate_passed, meta: selectedPeriod },
    { label: "Quick evaluated", value: totals.evaluated, meta: selectedPeriod },
    { label: "Detailed reviewed", value: totals.detailed_reviewed, meta: selectedPeriod },
    { label: "Applied", value: totals.applied, meta: selectedPeriod },
  ];
  return (
    <section aria-label="Key numbers">
      <Container header={<Header variant="h2">Key numbers</Header>}>
        <ColumnLayout columns={5} minColumnWidth={140} borders="vertical">
          {items.map(({ label, value, meta }) => (
            <div key={label}>
              <Box variant="awsui-key-label">{label}</Box>
              <Box variant="awsui-value-large" display="block" fontWeight="bold">{value.toLocaleString()}</Box>
              <Box variant="small" display="block" color="text-body-secondary">{meta}</Box>
            </div>
          ))}
        </ColumnLayout>
      </Container>
    </section>
  );
}
