import { useState } from "react";

import {
  useFunnelStats,
  useLLMStats,
  usePerformanceOverview,
  useStepTimings,
} from "@/api/queries";
import { DailyCost } from "@/components/performance/DailyCost";
import { ErrorsPerRun } from "@/components/performance/ErrorsPerRun";
import { EvaluateBreakdown } from "@/components/performance/EvaluateBreakdown";
import { FunnelConversion } from "@/components/performance/FunnelConversion";
import { GatePassFail } from "@/components/performance/GatePassFail";
import { GateSubstep } from "@/components/performance/GateSubstep";
import { KpiCards } from "@/components/performance/KpiCards";
import { LlmLatency } from "@/components/performance/LlmLatency";
import { PerSourceErrors } from "@/components/performance/PerSourceErrors";
import { ScanSourceDuration } from "@/components/performance/ScanSourceDuration";
import { TimeFilter } from "@/components/performance/TimeFilter";
import { TokenUsage } from "@/components/performance/TokenUsage";
import { Skeleton } from "@/components/ui/skeleton";

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

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto px-4 py-3">
      <TimeFilter windowDays={windowDays} onChange={setWindowDays} />
      <Body
        overview={overview}
        stepTimings={stepTimings}
        llmStats={llmStats}
        funnel={funnel}
      />
    </div>
  );
}

function Body({
  overview,
  stepTimings,
  llmStats,
  funnel,
}: {
  overview: ReturnType<typeof usePerformanceOverview>;
  stepTimings: ReturnType<typeof useStepTimings>;
  llmStats: ReturnType<typeof useLLMStats>;
  funnel: ReturnType<typeof useFunnelStats>;
}) {
  const isPending =
    overview.isPending || stepTimings.isPending || llmStats.isPending || funnel.isPending;
  const firstError = overview.error ?? stepTimings.error ?? llmStats.error ?? funnel.error;

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

  return (
    <>
      <KpiCards overview={overviewData} />
      <div className="grid gap-3 lg:grid-cols-2">
        <ScanSourceDuration timings={timings} />
        <EvaluateBreakdown timings={timings} />
        <GatePassFail timings={timings} />
        <GateSubstep timings={timings} />
        <LlmLatency stats={stats} />
        <DailyCost stats={stats} />
        <TokenUsage stats={stats} />
        <FunnelConversion funnel={funnelData} />
        <PerSourceErrors timings={timings} />
        <ErrorsPerRun timings={timings} />
      </div>
    </>
  );
}
