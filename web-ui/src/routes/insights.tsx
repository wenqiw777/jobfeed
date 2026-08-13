import { useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Grid from "@cloudscape-design/components/grid";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import SpaceBetween from "@cloudscape-design/components/space-between";

import { useInsightsOverview } from "@/api/queries";
import { ByResumeTable } from "@/components/insights/ByResumeTable";
import { DailyTimeline } from "@/components/insights/DailyTimeline";
import { KpiCards } from "@/components/insights/KpiCards";
import { SankeyFunnel } from "@/components/insights/SankeyFunnel";
import { StatusDonut } from "@/components/insights/StatusDonut";
import { Skeleton } from "@/components/ui/skeleton";

const WINDOW_PRESETS = [30, 60, 90] as const;

/**
 * Insights: the funnel at a glance. Read-only charts — no keyboard map
 * on purpose (nothing to decide here). The window presets re-query
 * `?window=`; totals stay all-time regardless (KPI cards label which).
 */
export default function InsightsPage() {
  const [windowDays, setWindowDays] = useState<number>(30);
  const overview = useInsightsOverview(windowDays);

  return (
    <div data-testid="cloudscape-insights" className="jobfeed-dashboard-page">
      <SpaceBetween size="l">
        <div role="group" aria-label="Window">
          <SegmentedControl label="Window" selectedId={String(windowDays)} options={WINDOW_PRESETS.map((preset) => ({ id: String(preset), text: `${preset}d` }))} onChange={({ detail }) => setWindowDays(Number(detail.selectedId))} />
        </div>
        <Body overview={overview} />
      </SpaceBetween>
    </div>
  );
}

function Body({ overview }: { overview: ReturnType<typeof useInsightsOverview> }) {
  if (overview.isPending) {
    return (
      <div className="flex flex-col gap-3" aria-label="Loading insights">
        <Skeleton className="h-20 w-full" />
        <div className="grid gap-3 lg:grid-cols-2">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (overview.isError) {
    return <Alert type="error" header="Insights unavailable">{overview.error.message}</Alert>;
  }
  const data = overview.data;
  return (
    <>
      <KpiCards overview={data} />
      <Grid gridDefinition={[{ colspan: { default: 12, l: 6 } }, { colspan: { default: 12, l: 6 } }] }>
        <SankeyFunnel overview={data} />
        <StatusDonut distribution={data.status_distribution} />
      </Grid>
      <DailyTimeline overview={data} />
      <ByResumeTable byResume={data.applications.by_resume} />
    </>
  );
}
