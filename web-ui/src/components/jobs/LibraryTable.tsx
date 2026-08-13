import { useEffect, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import type { JobSummary } from "@/api/queries";
import { VerdictPill } from "@/components/jobs/VerdictPill";
import { formatRelativeAge } from "@/lib/dates";
import { useDensity } from "@/lib/density";
import { cn } from "@/lib/utils";

interface LibraryTableProps {
  jobs: JobSummary[];
  /** The row whose drawer is open (selection styling only). */
  activeId: string | null;
  /** Changes when the page/filters change — resets the scroll position.
   * Keyed on the query, not the rows: a background refetch must not
   * yank the reader back to the top. */
  pageKey: string;
  onOpen: (job: JobSummary) => void;
}

const ROW_PX = { compact: 32, comfortable: 46 } as const;

/**
 * Column track shared by the header and every row so they stay aligned.
 * Company | title (+closed badge) | decision | verdict | score | age.
 */
const GRID_COLS =
  "grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)_7.5rem_6.5rem_3rem_4rem] items-center gap-2";

/**
 * Full-width virtualized Library table (DESIGN.md: lookup zone, row click
 * opens a drawer). JobRow is a triage *list* row — no status, platform,
 * or closed columns — so Library carries its own thin row, reusing the
 * verdict pill, relative-age formatting, and density row heights.
 */
export function LibraryTable({ jobs, activeId, pageKey, onOpen }: LibraryTableProps) {
  const parentRef = useRef<HTMLDivElement>(null);
  const { density } = useDensity();
  const rowHeight = ROW_PX[density];

  // TanStack Virtual returns unmemoizable functions, so the React
  // Compiler skips this component — fine: it is small and re-renders
  // cheaply; the rows themselves stay virtualized.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: jobs.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  });

  // estimateSize is sampled at mount; a density flip must re-measure.
  useEffect(() => {
    virtualizer.measure();
  }, [virtualizer, rowHeight]);

  // A new page/filter starts reading from the top.
  useEffect(() => {
    virtualizer.scrollToOffset(0);
  }, [virtualizer, pageKey]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        aria-hidden="true"
        className={cn(GRID_COLS, "border-b border-border px-4 py-1.5 text-label uppercase tracking-wide text-mute")}
      >
        <span>Company</span>
        <span>Title</span>
        <span>Decision</span>
        <span>Verdict</span>
        <span className="text-right">Score</span>
        <span className="text-right">Age</span>
      </div>
      <div ref={parentRef} className="min-h-0 flex-1 overflow-y-auto">
        <div
          role="list"
          aria-label="Jobs"
          className="relative w-full"
          style={{ height: virtualizer.getTotalSize() }}
        >
          {virtualizer.getVirtualItems().map((item) => {
            const job = jobs[item.index];
            if (job === undefined) {
              return null;
            }
            return (
              <div
                key={job.id}
                className="absolute inset-x-0 top-0"
                style={{ height: item.size, transform: `translateY(${item.start}px)` }}
              >
                <LibraryRow job={job} isActive={job.id === activeId} onOpen={onOpen} />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function LibraryRow({
  job,
  isActive,
  onOpen,
}: {
  job: JobSummary;
  isActive: boolean;
  onOpen: (job: JobSummary) => void;
}) {
  const { rowHeightClass } = useDensity();
  const score = job.stage_b_fit_score ?? job.stage_a_score;

  return (
    <div role="listitem" data-testid={`job-row-${job.id}`} data-active={isActive || undefined}>
      <button
        type="button"
        aria-label={`Open ${job.company} ${job.title}`}
        onClick={() => onOpen(job)}
        className={cn(
          GRID_COLS,
          "w-full border-b border-hairline px-4 text-left text-compact focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
          rowHeightClass,
          isActive ? "bg-accent-bg ring-1 ring-inset ring-accent-border" : "hover:bg-bg",
        )}
      >
        <span className={cn("truncate font-medium", isActive ? "text-accent" : "text-ink")}>
          {job.company}
        </span>
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-ink-2">{job.title}</span>
          {/* Closed postings stay listed in Library, flagged per D10. */}
          {job.closed_at !== null && (
            <span className="shrink-0 rounded-pill bg-skip-bg px-1.5 py-px text-micro font-medium text-skip">
              closed
            </span>
          )}
        </span>
        <span className="truncate text-micro font-medium text-ink-2">{decisionLabel(job.status)}</span>
        <span>
          <VerdictPill verdict={job.verdict} stageBStatus={job.stage_b_status} />
        </span>
        <span className="text-right font-mono text-ink-2">{score ?? "—"}</span>
        <span className="text-right font-mono text-micro text-mute">
          {formatRelativeAge(job.discovered_at)}
        </span>
      </button>
    </div>
  );
}

function decisionLabel(status: string): string {
  if (["shortlisted", "awaiting_referral"].includes(status)) return "Wait";
  if (["applied", "interviewing", "offer", "rejected", "ghosted"].includes(status)) {
    return "Applied";
  }
  if (["ignored", "archived"].includes(status)) return "Ignored";
  return "—";
}
