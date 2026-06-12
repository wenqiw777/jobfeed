import { useEffect, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import type { JobSummary } from "@/api/queries";
import { JobRow } from "@/components/jobs/JobRow";
import { useDensity } from "@/lib/density";

interface JobListProps {
  jobs: JobSummary[];
  selectedId: string | null;
  collapsingId: string | null;
  isChecked: (id: string) => boolean;
  onOpen: (id: string) => void;
  onToggle: (id: string) => void;
}

const ROW_PX = { compact: 32, comfortable: 46 } as const;

/** Virtualized triage list; rendered order mirrors the API order 1:1. */
export function JobList({
  jobs,
  selectedId,
  collapsingId,
  isChecked,
  onOpen,
  onToggle,
}: JobListProps) {
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

  // Keyboard moves must keep the active row inside the viewport.
  useEffect(() => {
    if (selectedId === null) {
      return;
    }
    const index = jobs.findIndex((job) => job.id === selectedId);
    if (index >= 0) {
      virtualizer.scrollToIndex(index);
    }
  }, [virtualizer, jobs, selectedId]);

  return (
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
              <JobRow
                job={job}
                isActive={job.id === selectedId}
                isChecked={isChecked(job.id)}
                isCollapsing={job.id === collapsingId}
                onOpen={onOpen}
                onToggle={onToggle}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
