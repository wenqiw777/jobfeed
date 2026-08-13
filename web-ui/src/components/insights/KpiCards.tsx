import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";

import type { InsightsOverviewResponse } from "@/api/queries";

/** Headline funnel totals with explicit all-time/window scopes. */
export function KpiCards({ overview }: { overview: InsightsOverviewResponse }) {
  const { totals, applications, window_days: windowDays } = overview;
  const items = [
    { label: "Discovered", value: totals.jobs, meta: "all-time" },
    { label: "Gate passed", value: totals.ml_gate_passed, meta: "all-time" },
    { label: "Evaluated", value: totals.evaluated, meta: "all-time" },
    { label: "Applied", value: totals.applied, meta: "all-time" },
    { label: "Applications", value: applications.applied_count, meta: `last ${windowDays}d` },
  ];
  return (
    <section aria-label="Key numbers">
      <Container header={<Header variant="h2">Key numbers</Header>}>
        <ColumnLayout columns={5} minColumnWidth={140} borders="vertical">
          {items.map(({ label, value, meta }) => (
            <div key={label}>
              <Box variant="awsui-key-label">{label}</Box>
              <Box variant="awsui-value-large" fontWeight="bold">{value.toLocaleString()}</Box>
              <Box variant="small" color="text-body-secondary">{meta}</Box>
            </div>
          ))}
        </ColumnLayout>
      </Container>
    </section>
  );
}
