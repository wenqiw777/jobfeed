import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { runsKeys, type RunSummary } from "@/api/queries";
import { formatLocalDateTime, formatRelativeAge } from "@/lib/dates";
import { useSSE, type SSEState } from "@/lib/use-sse";

const SCAN_COUNTERS: { key: keyof RunSummary; label: string; color?: "red" }[] = [
  { key: "jobs_discovered", label: "discovered" },
  { key: "jobs_inserted", label: "inserted" },
  { key: "jobs_updated", label: "updated" },
  { key: "errors", label: "errors", color: "red" },
];

/** Identity and latest polled counters for an active run. */
export interface ActiveRun {
  run_id: string;
  source: string;
  started_at: string;
  counters: RunSummary;
}

interface LiveRunRowProps {
  run: ActiveRun;
  onDone?: () => void;
}

/** SSE-backed progress card, with active-run polling as a reliable fallback. */
export function LiveRunRow({ run, onDone }: LiveRunRowProps) {
  const queryClient = useQueryClient();
  const sse: SSEState<RunSummary> = useSSE<RunSummary>(
    `/api/runs/${run.run_id}/progress`,
  );
  const hasFiredDone = useRef(false);

  useEffect(() => {
    if (sse.isDone && !hasFiredDone.current) {
      hasFiredDone.current = true;
      void queryClient.invalidateQueries({ queryKey: runsKeys.list({}) });
      onDone?.();
    }
  }, [sse.isDone, queryClient, onDone]);

  const live = mergeProgress(run.counters, sse.data);
  return (
    <li data-testid={`live-run-${run.run_id}`}>
      <Container
        header={
          <Header
            variant="h3"
            description={`${formatLocalDateTime(run.started_at)} · ${run.run_id}`}
            actions={<LiveStatus sse={sse} />}
          >
            {run.source} run
          </Header>
        }
      >
        {run.source === "evaluate" ? (
          <EvaluateProgress run={live} />
        ) : (
          <ScanProgress run={live} isConnected={sse.isConnected} />
        )}
      </Container>
    </li>
  );
}

function EvaluateProgress({ run }: { run: RunSummary }) {
  return (
    <SpaceBetween size="m">
      <ProgressRail stage={run.progress_stage} />
      <ColumnLayout columns={2} variant="text-grid">
        <SpaceBetween size="s">
          <StageProgress
            label="ML gate"
            processed={run.ml_gate_processed}
            total={run.ml_gate_total}
            isDone={isAfter(run.progress_stage, "ml_gate")}
            isActive={isCurrent(run.progress_stage, "ml_gate")}
          />
          <StageProgress
            label="Stage A"
            processed={run.stage_a_processed}
            total={run.stage_a_total}
            isDone={isAfter(run.progress_stage, "stage_a")}
            isActive={isCurrent(run.progress_stage, "stage_a")}
          />
          <StageProgress
            label="Stage B"
            processed={run.stage_b_processed}
            total={run.stage_b_total}
            isDone={isAfter(run.progress_stage, "stage_b")}
            isActive={isCurrent(run.progress_stage, "stage_b")}
          />
        </SpaceBetween>
        <SpaceBetween size="s">
          <Box variant="awsui-key-label">What is happening</Box>
          <Box fontSize="heading-m" fontWeight="bold">{stageLabel(run.progress_stage)}</Box>
          <SpaceBetween direction="horizontal" size="s">
            <Badge color="blue">{formatCost(run.total_llm_cost_usd)}</Badge>
            <Badge color={run.errors > 0 ? "red" : "green"}>
              {run.errors} {run.errors === 1 ? "error" : "errors"}
            </Badge>
          </SpaceBetween>
          <Box color="text-body-secondary">
            Updated {formatLiveAge(run.progress_updated_at)}
          </Box>
        </SpaceBetween>
      </ColumnLayout>
    </SpaceBetween>
  );
}

function ProgressRail({ stage }: { stage: string | null | undefined }) {
  const steps = [
    ["ml_gate", "ML gate"],
    ["stage_a", "Stage A"],
    ["stage_b", "Stage B"],
    ["finalizing", "Complete"],
  ] as const;
  return (
    <ol className="jobfeed-progress-rail" aria-label="Evaluation stages">
      {steps.map(([key, label], index) => {
        const state = stepState(stage, key);
        return (
          <li key={key} data-state={state}>
            <span className="jobfeed-progress-node">{state === "done" ? "✓" : index + 1}</span>
            <span>{label}</span>
          </li>
        );
      })}
    </ol>
  );
}

function StageProgress({
  label,
  processed,
  total,
  isDone,
  isActive,
}: {
  label: string;
  processed: number | undefined;
  total: number | null | undefined;
  isDone: boolean;
  isActive: boolean;
}) {
  const value = processed ?? 0;
  const knownTotal = total ?? null;
  const percentage = knownTotal === null || knownTotal === 0
    ? (isDone ? 100 : 0)
    : Math.min(100, value / knownTotal * 100);
  const detail = knownTotal === null
    ? (isActive ? "Preparing queue" : "Waiting")
    : `${value} / ${knownTotal}`;
  return (
    <ProgressBar
      value={percentage}
      variant="key-value"
      label={label}
      additionalInfo={detail}
      ariaLabel={`${label}: ${detail}`}
    />
  );
}

function ScanProgress({ run, isConnected }: { run: RunSummary; isConnected: boolean }) {
  const counters = SCAN_COUNTERS.filter(({ key }) => (run[key] as number) > 0);
  return (
    <ColumnLayout columns={2} variant="text-grid">
      <div>
        <Box variant="awsui-key-label">Progress stream</Box>
        <Box>{isConnected ? "Receiving live counters" : "Connecting to live counters"}</Box>
      </div>
      <div>
        <Box variant="awsui-key-label">Activity</Box>
        <SpaceBetween direction="horizontal" size="xs">
          {counters.length === 0 ? (
            <Box color="text-body-secondary">Waiting for the first update</Box>
          ) : counters.map(({ key, label, color }) => (
            <Badge key={key} color={color}>{String(run[key])} {label}</Badge>
          ))}
        </SpaceBetween>
      </div>
    </ColumnLayout>
  );
}

function mergeProgress(polled: RunSummary, streamed: RunSummary | null): RunSummary {
  if (streamed === null) return polled;
  const polledTime = Date.parse(polled.progress_updated_at ?? "");
  const streamedTime = Date.parse(streamed.progress_updated_at ?? "");
  if (!Number.isNaN(polledTime) && !Number.isNaN(streamedTime)) {
    return streamedTime >= polledTime ? streamed : polled;
  }
  return streamed;
}

const STAGE_ORDER = ["preparing", "ml_gate", "stage_a", "stage_b", "finalizing"];

function stageIndex(stage: string | null | undefined): number {
  const index = STAGE_ORDER.indexOf(stage ?? "preparing");
  return index === -1 ? 0 : index;
}

function isAfter(current: string | null | undefined, stage: string): boolean {
  return stageIndex(current) > stageIndex(stage);
}

function isCurrent(current: string | null | undefined, stage: string): boolean {
  return stageIndex(current) === stageIndex(stage);
}

function stepState(
  current: string | null | undefined,
  step: string,
): "pending" | "active" | "done" {
  const currentIndex = stageIndex(current);
  const stepIndex = stageIndex(step);
  if (currentIndex > stepIndex) return "done";
  if (currentIndex === stepIndex) return "active";
  return "pending";
}

function stageLabel(stage: string | null | undefined): string {
  const labels: Record<string, string> = {
    preparing: "Preparing evaluation",
    ml_gate: "ML gate · filtering locally",
    stage_a: "Stage A · fast score",
    stage_b: "Stage B · deep review",
    finalizing: "Saving final results",
  };
  return labels[stage ?? "preparing"] ?? "Evaluation running";
}

function formatCost(usd: number): string {
  return `$${usd.toFixed(usd > 0 && usd < 0.01 ? 4 : 2)}`;
}

function formatLiveAge(iso: string | null | undefined, now = new Date()): string {
  if (iso === null || iso === undefined) return "—";
  const elapsedSeconds = Math.max(0, Math.floor((now.getTime() - Date.parse(iso)) / 1000));
  if (Number.isNaN(elapsedSeconds)) return "—";
  if (elapsedSeconds < 10) return "just now";
  if (elapsedSeconds < 60) return `${elapsedSeconds}s ago`;
  if (elapsedSeconds < 3_600) return `${Math.floor(elapsedSeconds / 60)}m ago`;
  return `${formatRelativeAge(iso, now)} ago`;
}

function LiveStatus({ sse }: { sse: SSEState<RunSummary> }) {
  if (sse.isDone) return <StatusIndicator type="success">Completed</StatusIndicator>;
  if (sse.error !== null) return <StatusIndicator type="warning">Reconnecting</StatusIndicator>;
  return <StatusIndicator type="in-progress">Running</StatusIndicator>;
}
