import { useState } from "react";

import {
  useFunnelStats,
  useLLMStats,
  usePerformanceOverview,
  useRuns,
  useStepTimings,
} from "@/api/queries";
import { DailyTokens } from "@/components/performance/DailyTokens";
import { ErrorsPerRun } from "@/components/performance/ErrorsPerRun";
import { EvaluateBreakdown } from "@/components/performance/EvaluateBreakdown";
import { FunnelConversion } from "@/components/performance/FunnelConversion";
import { GatePassFail } from "@/components/performance/GatePassFail";
import { KpiCards } from "@/components/performance/KpiCards";
import { LlmLatency } from "@/components/performance/LlmLatency";
import { ScanSourceDuration } from "@/components/performance/ScanSourceDuration";
import { TimeFilter } from "@/components/performance/TimeFilter";
import { TokenUsage } from "@/components/performance/TokenUsage";
import { Skeleton } from "@/components/ui/skeleton";

/** Display cap for the per-run error chart (runs, not step timings,
 * carry the handled-error counters); the runs query is additionally
 * bounded to the page's time window via `days`. */
const ERROR_RUNS_LIMIT = 20;

/**
 * Performance zone: operational metrics and charts.
 * Lazy-loaded like Insights to keep the recharts bundle out of the
 * eager chunk.
 */
export default function PerformancePage() {
  const [windowDays, setWindowDays] = useState<number>(30);
  const overview = usePerformanceOverview(windowDays);
  const stepTimings = useStepTimings(windowDays);
  const llmStats = useLLMStats(windowDays);
  const funnel = useFunnelStats(windowDays);
  // `days` keeps ErrorsPerRun on the same 7/30/90d window as the other
  // panels; it lives in the query object (= the query key), so window
  // switches refetch automatically.
  const runs = useRuns({ limit: ERROR_RUNS_LIMIT, days: windowDays });

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto px-4 py-3">
      <TimeFilter windowDays={windowDays} onChange={setWindowDays} />
      <Body
        overview={overview}
        stepTimings={stepTimings}
        llmStats={llmStats}
        funnel={funnel}
        runs={runs}
      />
    </div>
  );
}

function Body({
  overview,
  stepTimings,
  llmStats,
  funnel,
  runs,
}: {
  overview: ReturnType<typeof usePerformanceOverview>;
  stepTimings: ReturnType<typeof useStepTimings>;
  llmStats: ReturnType<typeof useLLMStats>;
  funnel: ReturnType<typeof useFunnelStats>;
  runs: ReturnType<typeof useRuns>;
}) {
  const isPending =
    overview.isPending ||
    stepTimings.isPending ||
    llmStats.isPending ||
    funnel.isPending ||
    runs.isPending;
  const firstError =
    overview.error ?? stepTimings.error ?? llmStats.error ?? funnel.error ?? runs.error;

  if (isPending) {
    return (
      <div className="flex flex-col gap-3" aria-label="Loading performance">
        <Skeleton className="h-20 w-full" />
        <div className="grid gap-3 lg:grid-cols-2">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }
  if (firstError) {
    return <p className="py-6 text-body-sm text-danger">{firstError.message}</p>;
  }

  const overviewData = overview.data!;
  const timings = stepTimings.data!.timings;
  const stats = llmStats.data!.stats;
  const funnelData = funnel.data!.funnel;
  const runRows = runs.data!.runs;

  return (
    <>
      <KpiCards overview={overviewData} />
      <div className="grid gap-3 lg:grid-cols-2">
        <ScanSourceDuration timings={timings} />
        <EvaluateBreakdown timings={timings} />
        <GatePassFail funnel={funnelData} />
        <LlmLatency stats={stats} />
        <DailyTokens stats={stats} />
        <TokenUsage stats={stats} />
        <FunnelConversion funnel={funnelData} />
        <ErrorsPerRun runs={runRows} />
      </div>
    </>
  );
}
