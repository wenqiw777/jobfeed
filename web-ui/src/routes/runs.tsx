import { useCallback, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { useActiveRuns, useRuns, type RunSummary } from "@/api/queries";
import { LiveRunRow } from "@/components/runs/LiveRunRow";
import { TriggerEvaluateButton } from "@/components/runs/TriggerEvaluateDialog";
import { TriggerScanButton } from "@/components/runs/TriggerScanDialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatLocalDateTime } from "@/lib/dates";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;

const GRID_COLS = "grid grid-cols-[1.5rem_10rem_8rem_minmax(0,1fr)_5rem] items-center gap-2";

type CounterKey = Extract<
  keyof RunSummary,
  | "jobs_discovered"
  | "jobs_inserted"
  | "jobs_updated"
  | "jobs_filtered"
  | "jobs_ml_gated"
  | "jobs_scored"
  | "stage_a_scored"
  | "stage_b_scored"
  | "errors"
>;

/** Every counter in pipeline order — the expanded grid shows all of them,
 * zeros included. */
const FULL_COUNTERS: { key: CounterKey; label: string }[] = [
  { key: "jobs_discovered", label: "discovered" },
  { key: "jobs_inserted", label: "inserted" },
  { key: "jobs_updated", label: "updated" },
  { key: "jobs_filtered", label: "filtered" },
  { key: "jobs_ml_gated", label: "gated" },
  { key: "jobs_scored", label: "scored" },
  { key: "stage_a_scored", label: "stage A" },
  { key: "stage_b_scored", label: "stage B" },
  { key: "errors", label: "errors" },
];

/** Row chips: only non-zero ones render (quiet rows), and the stage A/B
 * split already accounts for `jobs_scored`, so the total chip is dropped. */
const COUNTERS = FULL_COUNTERS.filter(({ key }) => key !== "jobs_scored");

function formatCost(usd: number): string {
  // Stage runs cost fractions of a cent — keep small non-zero amounts
  // honest without rendering a "$0.0000" zero.
  return `$${usd.toFixed(usd > 0 && usd < 0.01 ? 4 : 2)}`;
}

/**
 * Runs: pipeline run history and live progress.
 *
 * Trigger buttons fire scans and evaluates; active runs stream progress
 * via SSE at the top of the list, then roll into the history below.
 */
export default function RunsPage() {
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const runs = useRuns({ limit: PAGE_SIZE, offset: page * PAGE_SIZE });
  const activeRuns = useActiveRuns();

  const handleDone = useCallback(() => {
    void activeRuns.refetch();
  }, [activeRuns]);

  // A running run is served by both /api/runs/active and /api/runs for
  // its entire duration — keep it in the live list only.
  const activeIds = new Set(activeRuns.data?.runs.map((ar) => ar.run_id) ?? []);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header: trigger buttons */}
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <span className="text-label uppercase tracking-wide text-mute">Runs</span>
        <div className="flex items-center gap-1.5">
          <TriggerScanButton />
          <TriggerEvaluateButton />
        </div>
      </div>

      {/* Active runs (live SSE rows) */}
      {activeRuns.data && activeRuns.data.runs.length > 0 && (
        <ul aria-label="Active runs">
          {activeRuns.data.runs.map((ar) => (
            <LiveRunRow
              key={ar.run_id}
              run={ar}
              onDone={handleDone}
            />
          ))}
        </ul>
      )}

      <Body runs={runs} activeIds={activeIds} expandedId={expandedId} onToggle={setExpandedId} />
      <div className="flex items-center justify-between border-t border-border px-4 py-2">
        <span className="font-mono text-micro text-mute">
          <RangeLabel page={page} pageRows={runs.data?.runs.length ?? 0} total={runs.data?.total ?? 0} />
        </span>
        <div className="flex items-center gap-1.5">
          <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
            Prev
          </Button>
          <Button
            size="sm"
            disabled={
              // The placeholder page carries the previous query's total —
              // never page against it (Library's pager guard).
              runs.isPlaceholderData ||
              page * PAGE_SIZE + (runs.data?.runs.length ?? 0) >= (runs.data?.total ?? 0)
            }
            onClick={() => setPage(page + 1)}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}

function RangeLabel({ page, pageRows, total }: { page: number; pageRows: number; total: number }) {
  if (total === 0) {
    return <>0 runs</>;
  }
  return (
    <>
      {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + pageRows} of {total}
    </>
  );
}

interface BodyProps {
  runs: ReturnType<typeof useRuns>;
  /** run_ids currently rendered as live rows — excluded from history. */
  activeIds: Set<string>;
  expandedId: string | null;
  onToggle: (runId: string | null) => void;
}

function Body({ runs, activeIds, expandedId, onToggle }: BodyProps) {
  if (runs.isPending) {
    return (
      <div className="flex flex-col gap-1 px-4 py-3" aria-label="Loading runs">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-7 w-full" />
        ))}
      </div>
    );
  }
  if (runs.isError) {
    return <p className="px-4 py-6 text-body-sm text-danger">{runs.error.message}</p>;
  }
  const historyRuns = runs.data.runs.filter((run) => !activeIds.has(run.run_id));
  if (historyRuns.length === 0) {
    // With live rows on screen the "No runs yet" teaching state would
    // lie — the first run is happening right now.
    if (activeIds.size > 0) {
      return <div className="min-h-0 flex-1" />;
    }
    return (
      <div className="grid flex-1 place-items-center px-6 text-center">
        <div>
          <p className="text-body-sm font-medium text-ink-2">No runs yet</p>
          <p className="mt-1 text-micro text-mute">
            Run <span className="font-mono text-ink-2">./scan</span> from the repo root —
            every pipeline run lands here with its counters.
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div
        aria-hidden="true"
        className={cn(
          GRID_COLS,
          "border-b border-border px-4 py-1.5 text-label uppercase tracking-wide text-mute",
        )}
      >
        <span />
        <span>Started</span>
        <span>Source</span>
        <span>Counters</span>
        <span className="text-right">Cost</span>
      </div>
      <ul aria-label="Runs">
        {historyRuns.map((run) => (
          <RunRow
            key={run.run_id}
            run={run}
            isExpanded={run.run_id === expandedId}
            onToggle={() => onToggle(run.run_id === expandedId ? null : run.run_id)}
          />
        ))}
      </ul>
    </div>
  );
}

function RunRow({
  run,
  isExpanded,
  onToggle,
}: {
  run: RunSummary;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const Chevron = isExpanded ? ChevronDown : ChevronRight;
  return (
    <li data-testid={`run-row-${run.run_id}`} className="border-b border-hairline">
      <button
        type="button"
        aria-expanded={isExpanded}
        aria-label={`Run started ${formatLocalDateTime(run.started_at)}`}
        onClick={onToggle}
        className={cn(
          GRID_COLS,
          "w-full px-4 py-1.5 text-left text-compact",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
          isExpanded ? "bg-accent-bg/50" : "hover:bg-bg",
        )}
      >
        <Chevron aria-hidden="true" className="h-3.5 w-3.5 text-mute" />
        <span className="font-mono text-ink">{formatLocalDateTime(run.started_at)}</span>
        <span className="truncate text-ink-2">{run.source}</span>
        <span className="flex min-w-0 flex-wrap items-center gap-1">
          {COUNTERS.filter(({ key }) => run[key] > 0).map(({ key, label }) => (
            <CounterChip key={key} label={label} count={run[key]} isDanger={key === "errors"} />
          ))}
        </span>
        <span className="text-right font-mono text-micro text-ink-2">
          {run.total_llm_cost_usd > 0 ? formatCost(run.total_llm_cost_usd) : ""}
        </span>
      </button>
      {isExpanded && <RunDetail run={run} />}
    </li>
  );
}

function CounterChip({
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
        isDanger ? "border-danger/30 bg-danger-bg text-danger" : "border-border text-mute",
      )}
    >
      <span className="font-mono font-medium">{count}</span>
      {label}
    </span>
  );
}

/** Expanded row: the full counter grid plus identity and timing. */
function RunDetail({ run }: { run: RunSummary }) {
  return (
    <div className="border-t border-hairline bg-bg/60 px-4 py-2.5 pl-10">
      <dl className="grid grid-cols-3 gap-x-6 gap-y-1.5 sm:grid-cols-5">
        {FULL_COUNTERS.map(({ key, label }) => (
          <Fact key={key} label={label} value={String(run[key])} />
        ))}
        <Fact label="cost" value={formatCost(run.total_llm_cost_usd)} />
        <Fact label="finished" value={formatLocalDateTime(run.finished_at)} />
      </dl>
      <p className="mt-2 font-mono text-micro text-mute">{run.run_id}</p>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-micro uppercase tracking-wide text-mute">{label}</dt>
      <dd className="font-mono text-compact text-ink">{value}</dd>
    </div>
  );
}
