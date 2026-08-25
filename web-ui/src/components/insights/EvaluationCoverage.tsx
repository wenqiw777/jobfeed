import ProgressBar from "@cloudscape-design/components/progress-bar";
import SpaceBetween from "@cloudscape-design/components/space-between";

import type { InsightsOverviewResponse } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

export interface CoverageStage {
  label: string;
  count: number;
  percent: number;
}

/** Returns comparable coverage rates against jobs in the selected period. */
export function coverageStages(overview: InsightsOverviewResponse): CoverageStage[] {
  const total = overview.totals.jobs;
  const percentOfDiscovered = (count: number): number =>
    total === 0 ? 0 : Math.round((count / total) * 1000) / 10;
  return [
    { label: "Passed local filter", count: overview.totals.ml_gate_passed, percent: percentOfDiscovered(overview.totals.ml_gate_passed) },
    { label: "Evaluated", count: overview.totals.evaluated, percent: percentOfDiscovered(overview.totals.evaluated) },
    { label: "Strong match", count: overview.verdict_distribution.strong_match ?? 0, percent: percentOfDiscovered(overview.verdict_distribution.strong_match ?? 0) },
    { label: "Applied", count: overview.totals.applied, percent: percentOfDiscovered(overview.totals.applied) },
  ];
}

/** Displays selected-period coverage without implying a strict funnel. */
export function EvaluationCoverage({ overview }: { overview: InsightsOverviewResponse }) {
  const stages = coverageStages(overview);
  const allTime = overview.window_days === null;
  if (overview.totals.jobs === 0) {
    return <ChartCard title="Evaluation coverage"><ChartEmpty>No jobs discovered yet.</ChartEmpty></ChartCard>;
  }
  const periodJobs = overview.totals.jobs.toLocaleString();
  return (
    <ChartCard
      title="Evaluation coverage"
      description={
        allTime
          ? "Each measure is a share of all-time jobs."
          : "Each measure is a share of jobs in the selected period."
      }
    >
      <SpaceBetween size="m">
        {stages.map(({ label, count, percent }) => (
          <ProgressBar
            key={label}
            ariaLabel={label}
            label={label}
            value={percent}
            description={`${count.toLocaleString()} of ${periodJobs} ${allTime ? "all-time jobs" : "jobs in selected period"}`}
            additionalInfo={`${percent.toFixed(1)}% of ${allTime ? "all time" : "selected period"}`}
          />
        ))}
      </SpaceBetween>
    </ChartCard>
  );
}
