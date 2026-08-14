import { useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Grid from "@cloudscape-design/components/grid";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";

import { useInsightsOverview } from "@/api/queries";
import { EvaluationCoverage } from "@/components/insights/EvaluationCoverage";
import { JobStatusChart } from "@/components/insights/JobStatusChart";
import { KpiCards } from "@/components/insights/KpiCards";

const WINDOW_PRESETS = [7, 30, 60, 90, "all"] as const;
type WindowPreset = (typeof WINDOW_PRESETS)[number];

/**
 * Insights: evaluation coverage for one selected discovery period.
 * The window presets re-query `?window=` and scope every visible metric.
 */
export default function InsightsPage() {
  const [windowDays, setWindowDays] = useState<WindowPreset>(30);
  const overview = useInsightsOverview(windowDays);

  return (
    <div data-testid="cloudscape-insights">
      <SpaceBetween size="l">
        <div role="group" aria-label="Time range">
          <SegmentedControl
            label="Time range"
            selectedId={String(windowDays)}
            options={WINDOW_PRESETS.map((preset) => ({
              id: String(preset),
              text: preset === "all" ? "All time" : `${preset} days`,
            }))}
            onChange={({ detail }) =>
              setWindowDays(
                detail.selectedId === "all"
                  ? "all"
                  : (Number(detail.selectedId) as WindowPreset),
              )
            }
          />
        </div>
        <Body overview={overview} />
      </SpaceBetween>
    </div>
  );
}

function Body({ overview }: { overview: ReturnType<typeof useInsightsOverview> }) {
  if (overview.isPending) {
    return <Box padding="xxl" textAlign="center"><Spinner size="large" /></Box>;
  }
  if (overview.isError) {
    return <Alert type="error" header="Insights unavailable">{overview.error.message}</Alert>;
  }
  const data = overview.data;
  return (
    <>
      <KpiCards overview={data} />
      <Grid gridDefinition={[
        { colspan: { default: 12, s: 8 } },
        { colspan: { default: 12, s: 4 } },
      ]}>
        <EvaluationCoverage overview={data} />
        <JobStatusChart distribution={data.status_distribution} />
      </Grid>
    </>
  );
}
