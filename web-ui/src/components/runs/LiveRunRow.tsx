/**
 * LiveRunRow — SSE-driven progress row for an active pipeline run.
 *
 * Connects to GET /api/runs/{run_id}/progress via useSSE.  Counter
 * updates animate via CSS transitions; `prefers-reduced-motion` is
 * respected by gating the transition on the media query.
 *
 * On `event: done` the hook receives the final counters and the parent
 * is expected to invalidate the runs query so the row transitions into
 * the history list.
 */
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { runsKeys, type RunSummary } from "@/api/queries";
import { Skeleton } from "@/components/ui/skeleton";
import { useSSE, type SSEState } from "@/lib/use-sse";
import { cn } from "@/lib/utils";
import { formatLocalDateTime } from "@/lib/dates";

/** The counters worth surfacing during a live run. */
const LIVE_COUNTERS: { key: keyof RunSummary; label: string; isDanger?: boolean }[] = [
  { key: "jobs_discovered", label: "discovered" },
  { key: "jobs_inserted", label: "inserted" },
  { key: "jobs_scored", label: "scored" },
  { key: "stage_a_scored", label: "stage A" },
  { key: "stage_b_scored", label: "stage B" },
  { key: "errors", label: "errors", isDanger: true },
];

export interface ActiveRun {
  run_id: string;
  source: string;
  started_at: string;
}

interface LiveRunRowProps {
  run: ActiveRun;
  /** Called once the SSE stream signals `done`. */
  onDone?: () => void;
}

export function LiveRunRow({ run, onDone }: LiveRunRowProps) {
  const queryClient = useQueryClient();
  const sse: SSEState<RunSummary> = useSSE<RunSummary>(
    `/api/runs/${run.run_id}/progress`,
  );

  // Fire the completion path exactly once, and only on a graceful
  // `event: done` — a transport error also drops isConnected, but the
  // hook keeps isDone false there (the stream will reconnect).
  const hasFiredDone = useRef(false);

  useEffect(() => {
    if (sse.isDone && !hasFiredDone.current) {
      hasFiredDone.current = true;
      void queryClient.invalidateQueries({ queryKey: runsKeys.list({}) });
      onDone?.();
    }
  }, [sse.isDone, queryClient, onDone]);

  return (
    <li
      data-testid={`live-run-${run.run_id}`}
      className="border-b border-accent/20 bg-accent-bg/30"
    >
      <div className="flex items-center gap-3 px-4 py-2">
        <Skeleton className="h-3.5 w-3.5 shrink-0 rounded-full" />
        <span className="font-mono text-compact text-ink">
          {formatLocalDateTime(run.started_at)}
        </span>
        <span className="text-compact text-ink-2">{run.source}</span>
        <span className="flex min-w-0 flex-wrap items-center gap-1">
          {LIVE_COUNTERS.filter(
            ({ key }) => sse.data !== null && (sse.data[key] as number) > 0,
          ).map(({ key, label, isDanger }) => (
            <LiveChip
              key={key}
              label={label}
              count={(sse.data![key] as number)}
              isDanger={isDanger === true}
            />
          ))}
        </span>
      </div>
    </li>
  );
}

function LiveChip({
  label,
  count,
  isDanger,
}: {
  label: string;
  count: number;
  isDanger: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-pill border px-1.5 py-px text-micro",
        "motion-safe:transition-all motion-safe:duration-200",
        isDanger
          ? "border-danger/30 bg-danger-bg text-danger"
          : "border-accent/30 bg-accent-bg text-accent",
      )}
    >
      <span className="font-mono font-medium">{count}</span>
      {label}
    </span>
  );
}
