import { useState } from "react";

import { useInsightsOverview } from "@/api/queries";
import { ByResumeTable } from "@/components/insights/ByResumeTable";
import { DailyTimeline } from "@/components/insights/DailyTimeline";
import { KpiCards } from "@/components/insights/KpiCards";
import { SankeyFunnel } from "@/components/insights/SankeyFunnel";
import { StatusDonut } from "@/components/insights/StatusDonut";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

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
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto px-4 py-3">
      <div className="flex items-center gap-1.5" role="group" aria-label="Window">
        {WINDOW_PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            aria-pressed={windowDays === preset}
            onClick={() => setWindowDays(preset)}
            className={cn(
              "rounded-pill border px-2.5 py-0.5 font-mono text-micro transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
              windowDays === preset
                ? "border-accent-border bg-accent-bg text-accent"
                : "border-border text-mute hover:border-border-strong hover:text-ink-2",
            )}
          >
            {preset}d
          </button>
        ))}
      </div>
      <Body overview={overview} />
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
    return <p className="py-6 text-body-sm text-danger">{overview.error.message}</p>;
  }
  const data = overview.data;
  return (
    <>
      <KpiCards overview={data} />
      <div className="grid gap-3 lg:grid-cols-2">
        <SankeyFunnel overview={data} />
        <StatusDonut distribution={data.status_distribution} />
      </div>
      <DailyTimeline overview={data} />
      <ByResumeTable byResume={data.applications.by_resume} />
    </>
  );
}
