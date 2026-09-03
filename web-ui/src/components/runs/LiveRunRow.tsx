import Badge from "@cloudscape-design/components/badge";
import type { BadgeProps } from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Steps, { type StepsProps } from "@cloudscape-design/components/steps";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { runsKeys, type RunSummary } from "@/api/queries";
import { RunActionButton } from "@/components/runs/RunActionButton";
import { formatLocalDateTime, formatRelativeAge } from "@/lib/dates";
import { useSSE, type SSEState } from "@/lib/use-sse";

const SCAN_COUNTERS: {
  key: keyof RunSummary;
  label: string;
  color: NonNullable<BadgeProps["color"]>;
}[] = [
  { key: "jobs_discovered", label: "discovered", color: "blue" },
  { key: "jobs_inserted", label: "inserted", color: "green" },
  { key: "jobs_updated", label: "updated", color: "grey" },
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
  const [lastScanItem, setLastScanItem] = useState<{
    phaseKey: string;
    jobId: string | null;
  }>({ phaseKey: "", jobId: null });

  useEffect(() => {
    if (sse.isDone && !hasFiredDone.current) {
      hasFiredDone.current = true;
      void queryClient.invalidateQueries({ queryKey: runsKeys.list({}) });
      onDone?.();
    }
  }, [sse.isDone, queryClient, onDone]);

  const live = mergeProgress(run.counters, sse.data);
  const phaseKey = `${live.scan_source ?? ""}:${live.scan_phase ?? ""}`;
  if (lastScanItem.phaseKey !== phaseKey) {
    setLastScanItem({ phaseKey, jobId: live.scan_current_job_id ?? null });
  } else if (
    live.scan_current_job_id
    && live.scan_current_job_id !== lastScanItem.jobId
  ) {
    setLastScanItem({ phaseKey, jobId: live.scan_current_job_id });
  }
  const currentJobId = live.scan_current_job_id
    ?? (lastScanItem.phaseKey === phaseKey ? lastScanItem.jobId : null);
  return (
    <li data-testid={`live-run-${run.run_id}`}>
      <Container
        header={
          <Header
            variant="h3"
            description={`${formatLocalDateTime(run.started_at)} · ${run.run_id}`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <LiveStatus sse={sse} />
                <RunActionButton runId={run.run_id} isRunning />
              </SpaceBetween>
            }
          >
            {liveRunLabel(run.source)}
          </Header>
        }
      >
        {run.source === "evaluate" ? (
          <EvaluateProgress run={live} />
        ) : (
          <ScanProgress
            run={live}
            isConnected={sse.isConnected}
            currentJobId={currentJobId}
          />
        )}
      </Container>
    </li>
  );
}

function EvaluateProgress({ run }: { run: RunSummary }) {
  const displayStage = evaluateDisplayStage(run);
  const candidatePreparationDone = isAfter(displayStage, "preparing");
  const seniorityTotal = seniorityCandidateTotal(run);
  const seniorityIsDone = isAfter(displayStage, "seniority_gate");
  const seniorityIsActive = isCurrent(displayStage, "seniority_gate");
  const preparationExcluded = preparationExcludedCount(run);
  return (
    <SpaceBetween size="m">
      <ProgressRail stage={displayStage} />
      <ColumnLayout columns={2} variant="text-grid">
        <SpaceBetween size="s">
          <StageProgress
            label="Candidate preparation"
            processed={candidatePreparationDone ? (run.ml_gate_total ?? 0) : 0}
            total={run.ml_gate_total}
            isDone={candidatePreparationDone}
            isActive={isCurrent(displayStage, "preparing")}
            detail={candidatePreparationDetail(run, candidatePreparationDone)}
          />
          <StageProgress
            label="SDE role filter"
            processed={run.ml_gate_processed}
            total={run.ml_gate_total}
            isDone={isAfter(displayStage, "ml_gate")}
            isActive={isCurrent(displayStage, "ml_gate")}
          />
          <StageProgress
            label="Seniority filter"
            processed={seniorityIsDone ? (seniorityTotal ?? 0) : 0}
            total={seniorityTotal}
            isDone={seniorityIsDone}
            isActive={seniorityIsActive}
            detail={seniorityIsActive && seniorityTotal !== null
              ? `Screening ${seniorityTotal} candidates`
              : seniorityIsActive ? "Preparing candidates" : undefined}
          />
          <StageProgress
            label="Quick evaluation"
            processed={run.stage_a_processed}
            total={run.stage_a_total}
            isDone={isAfter(displayStage, "stage_a")}
            isActive={isCurrent(displayStage, "stage_a")}
          />
          <StageProgress
            label="Detailed review"
            processed={run.stage_b_processed}
            total={run.stage_b_total}
            isDone={isAfter(displayStage, "stage_b")}
            isActive={isCurrent(displayStage, "stage_b")}
          />
        </SpaceBetween>
        <SpaceBetween size="s">
          <Box variant="awsui-key-label">What is happening</Box>
          <Box fontSize="heading-m" fontWeight="bold">{stageLabel(displayStage)}</Box>
          <SpaceBetween direction="horizontal" size="s">
            {preparationExcluded !== null && preparationExcluded > 0 && (
              <Badge color="severity-neutral">
                {preparationExcluded} not eligible or duplicate
              </Badge>
            )}
            {run.jobs_filtered > 0 && (
              <Badge color="severity-neutral">
                {run.jobs_filtered} excluded by job rules
              </Badge>
            )}
          </SpaceBetween>
          <SpaceBetween direction="horizontal" size="s">
            <Badge color="severity-neutral">
              {run.jobs_ml_gated} excluded by SDE role filter
            </Badge>
            <Badge color="severity-neutral">
              {run.jobs_seniority_filtered} excluded by seniority filter
            </Badge>
          </SpaceBetween>
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
    ["preparing", "Candidate preparation"],
    ["ml_gate", "SDE role filter"],
    ["seniority_gate", "Seniority filter"],
    ["stage_a", "Quick evaluation"],
    ["stage_b", "Detailed review"],
    ["finalizing", "Complete"],
  ] as const;
  return (
    <Steps
      ariaLabel="Evaluation progress"
      orientation="horizontal"
      connectorLines="visible"
      steps={steps.map(([key, label]) => ({
        status: stepStatus(stage, key),
        header: label,
      }))}
    />
  );
}

function StageProgress({
  label,
  processed,
  total,
  isDone,
  isActive,
  detail,
}: {
  label: string;
  processed: number | undefined;
  total: number | null | undefined;
  isDone: boolean;
  isActive: boolean;
  detail?: string;
}) {
  const value = processed ?? 0;
  const knownTotal = total ?? null;
  const percentage = knownTotal === null || knownTotal === 0
    ? (isDone ? 100 : 0)
    : Math.min(100, value / knownTotal * 100);
  const additionalInfo = detail ?? (knownTotal === null
    ? (isActive ? "Preparing queue" : "Waiting")
    : `${value} / ${knownTotal}`);
  return (
    <ProgressBar
      value={percentage}
      variant="key-value"
      label={label}
      additionalInfo={additionalInfo}
      ariaLabel={`${label}: ${additionalInfo}`}
    />
  );
}

function ScanProgress({
  run,
  isConnected,
  currentJobId,
}: {
  run: RunSummary;
  isConnected: boolean;
  currentJobId: string | null;
}) {
  const counters = SCAN_COUNTERS.filter(({ key }) => (run[key] as number) > 0);
  const activity = scanActivity(run);
  return (
    <ColumnLayout columns={3} variant="text-grid">
      <div>
        <Box variant="awsui-key-label">Progress stream</Box>
        <Box>{isConnected ? "Receiving live counters" : "Connecting to live counters"}</Box>
      </div>
      <div>
        <Box variant="awsui-key-label">What is happening</Box>
        <Box>{activity ?? "Starting sources"}</Box>
        <Box color="text-body-secondary">
          {currentJobId ? `Listing ${currentJobId}` : "\u00a0"}
        </Box>
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

function scanActivity(run: RunSummary): string | null {
  if (!run.scan_source || !run.scan_phase) return null;
  const source = {
    jobright: "Jobright",
    linkedin_guest: "LinkedIn Guest",
  }[run.scan_source] ?? run.scan_source;
  const phase = {
    fetching: "Fetching listings",
    saving: "Saving listings",
    completed: "Source complete",
    enriching_job_descriptions: "Enriching job descriptions",
  }[run.scan_phase] ?? run.scan_phase;
  if (run.scan_total === null || run.scan_total === undefined) {
    return `${source} · ${phase}`;
  }
  return `${source} · ${phase} · ${run.scan_processed} / ${run.scan_total}`;
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

const STAGE_ORDER = [
  "preparing",
  "ml_gate",
  "seniority_gate",
  "stage_a",
  "stage_b",
  "finalizing",
];

function evaluateDisplayStage(run: RunSummary): string | null | undefined {
  if (
    run.progress_stage === "ml_gate"
    && run.ml_gate_total !== null
    && run.ml_gate_total !== undefined
    && (run.ml_gate_processed ?? 0) >= run.ml_gate_total
  ) {
    return "seniority_gate";
  }
  return run.progress_stage;
}

function seniorityCandidateTotal(run: RunSummary): number | null {
  if (run.ml_gate_total === null || run.ml_gate_total === undefined) return null;
  return Math.max(0, run.ml_gate_total - run.jobs_ml_gated);
}

function candidatePreparationDetail(
  run: RunSummary,
  isDone: boolean,
): string {
  const candidateTotal = run.ml_gate_total;
  if (run.evaluation_scope === "latest_scan") {
    const inputTotal = run.evaluation_input_total;
    if (inputTotal !== null && inputTotal !== undefined) {
      if (!isDone || candidateTotal === null || candidateTotal === undefined) {
        return `Preparing ${inputTotal} latest-scan listings`;
      }
      return `Latest scan: ${inputTotal} listings → ${candidateTotal} candidates`;
    }
    return isDone && candidateTotal !== null && candidateTotal !== undefined
      ? `Latest scan → ${candidateTotal} candidates`
      : "Preparing latest scan";
  }
  const scopeLabel = run.evaluation_scope === "backlog"
    ? "Historical backlog"
    : "Evaluation candidates";
  if (!isDone || candidateTotal === null || candidateTotal === undefined) {
    return `Preparing ${scopeLabel.toLowerCase()}`;
  }
  return `${scopeLabel} → ${candidateTotal} candidates`;
}

function preparationExcludedCount(run: RunSummary): number | null {
  if (
    run.evaluation_scope !== "latest_scan"
    || run.evaluation_input_total === null
    || run.evaluation_input_total === undefined
    || run.ml_gate_total === null
    || run.ml_gate_total === undefined
  ) {
    return null;
  }
  return Math.max(
    0,
    run.evaluation_input_total - run.jobs_filtered - run.ml_gate_total,
  );
}

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

function stepStatus(current: string | null | undefined, step: string): StepsProps.Status {
  const state = stepState(current, step);
  if (state === "done") return "success";
  if (state === "active") return "in-progress";
  return "pending";
}

function stageLabel(stage: string | null | undefined): string {
  const labels: Record<string, string> = {
    preparing: "Preparing evaluation",
    ml_gate: "Applying local filters",
    seniority_gate: "Applying seniority filter",
    stage_a: "Running quick evaluation",
    stage_b: "Running detailed review",
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

function liveRunLabel(source: string): string {
  if (source === "evaluate") return "Evaluation in progress";
  if (source === "all") return "Scanning all sources";
  return `Scanning ${source}`;
}
