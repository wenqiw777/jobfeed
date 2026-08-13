import { useEffect, useMemo, useRef, useState } from "react";
import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Tabs from "@cloudscape-design/components/tabs";

import {
  useJobsList,
  useJobTransition,
  type JobsQuery,
  type JobSummary,
} from "@/api/queries";
import { BulkBar } from "@/components/jobs/BulkBar";
import { DetailPane, type UserDecision } from "@/components/jobs/DetailPane";
import { JobList } from "@/components/jobs/JobList";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/use-toast";
import { useSelection } from "@/lib/use-selection";

type TriageFilter = "results" | "wait" | "applied" | "ignored";

/** Triage corpora are 10^2-scale (plan D10) — one page covers the zone. */
const PAGE_LIMIT = 200;
const COLLAPSE_MS = 180;

/**
 * A4 zone params. Queue omits `sort` on purpose: queue/pending_jd always
 * use the server's fixed verdict-group order.
 */
function triageQuery(filter: TriageFilter): JobsQuery {
  if (filter === "results") {
    return {
      tab: "queue",
      apply_hard_filters: true,
      dedupe: true,
      require_verdict: true,
      limit: PAGE_LIMIT,
      offset: 0,
    };
  }
  const statuses = {
    wait: ["shortlisted", "awaiting_referral"],
    applied: ["applied", "interviewing", "offer", "rejected", "ghosted"],
    ignored: ["ignored", "archived"],
  }[filter];
  return {
    tab: "all",
    statuses,
    sort: "discovered_desc",
    limit: PAGE_LIMIT,
    offset: 0,
  };
}

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export default function TriagePage() {
  const [filter, setFilter] = useState<TriageFilter>("results");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapsingId, setCollapsingId] = useState<string | null>(null);
  const selection = useSelection();
  const collapseTimerRef = useRef<number | null>(null);

  const list = useJobsList(triageQuery(filter));
  const jobs = useMemo(() => list.data?.jobs ?? [], [list.data]);
  // Decide/advance handlers read the displayed order through a ref so a
  // setTimeout never closes over a stale list. Synced in an effect (refs
  // must not be written during render); handlers only run after effects.
  const rowsRef = useRef<JobSummary[]>(jobs);
  useEffect(() => {
    rowsRef.current = jobs;
  });

  const transition = useJobTransition();

  // The collapse timer must not outlive the page (setState after unmount).
  useEffect(
    () => () => {
      if (collapseTimerRef.current !== null) {
        window.clearTimeout(collapseTimerRef.current);
      }
    },
    [],
  );

  // Selection is derived, not effect-seeded: when nothing is selected or
  // the selected id dropped out of a refetched list, fall back to the
  // first row (the legacy seed + onBulkCleared re-seed semantics).
  const effectiveSelectedId =
    selectedId !== null && jobs.some((job) => job.id === selectedId)
      ? selectedId
      : (jobs[0]?.id ?? null);
  const selectedJob = jobs.find((job) => job.id === effectiveSelectedId) ?? null;

  const switchFilter = (next: TriageFilter) => {
    setFilter(next);
    setSelectedId(null);
    selection.clear();
  };

  /** Move selection to the next row in displayed order (previous when
   * the decided row was last; cleared when it was alone). Decisions only
   * ever target the effective selected row, so the set is unconditional. */
  const advanceFrom = (id: string) => {
    const rows = rowsRef.current;
    const index = rows.findIndex((job) => job.id === id);
    const next = index === -1 ? undefined : (rows[index + 1] ?? rows[index - 1]);
    setSelectedId(next?.id ?? null);
  };

  /** A decided row advances selection immediately and collapses for
   * ~180ms — except under prefers-reduced-motion, where it just snaps. */
  const handleDecided = (id: string) => {
    advanceFrom(id);
    if (prefersReducedMotion()) {
      return;
    }
    setCollapsingId(id);
    if (collapseTimerRef.current !== null) {
      window.clearTimeout(collapseTimerRef.current);
    }
    collapseTimerRef.current = window.setTimeout(() => {
      collapseTimerRef.current = null;
      setCollapsingId((current) => (current === id ? null : current));
    }, COLLAPSE_MS);
  };

  const decide = (to: UserDecision) => {
    const id = effectiveSelectedId;
    if (id === null || transition.isPending) {
      return;
    }
    transition.mutate(
      { id, to },
      {
        onSuccess: () => handleDecided(id),
        onError: (error) =>
          toast({ variant: "destructive", title: "Action failed", description: error.message }),
      },
    );
  };

  const onBulkCleared = () => {
    selection.clear();
    setSelectedId(null);
  };

  return (
    <div className="jobfeed-decision-surface" data-testid="triage-decision-surface">
      <div className="jobfeed-decision-queue">
        <Tabs
          activeTabId={filter}
          variant="container"
          fitHeight
          disableContentPaddings
          onChange={({ detail }) => switchFilter(detail.activeTabId as TriageFilter)}
          tabs={[
            {
              id: "results",
              label: "Results",
              content: triageListContent(),
            },
            {
              id: "wait",
              label: "Wait",
              content: triageListContent(),
            },
            {
              id: "applied",
              label: "Applied",
              content: triageListContent(),
            },
            {
              id: "ignored",
              label: "Ignored",
              content: triageListContent(),
            },
          ]}
          actions={
            <Box variant="small" color="text-body-secondary">
              {list.data !== undefined ? `${list.data.total} postings` : "Loading"}
            </Box>
          }
        />
      </div>
      <section aria-label="Job detail" className="jobfeed-evidence-panel">
        <Container
          header={
            <Header variant="h2" description="Scores, source evidence, and decision">
              {selectedJob ? `${selectedJob.company} · ${selectedJob.title}` : "Job evidence"}
            </Header>
          }
        >
          <DetailPane
            jobId={effectiveSelectedId}
            showDecide={filter === "results"}
            isDeciding={transition.isPending}
            onDecide={decide}
            emptyHint="Select a result to review its evidence."
          />
        </Container>
      </section>
    </div>
  );

  function triageListContent() {
    return (
      <div className="jobfeed-tab-content">
        <BulkBar
          selectedIds={selection.selectedIds}
          total={list.data?.total ?? 0}
          loadedCount={jobs.length}
          onSelectPage={() => selection.selectMany(jobs.map((job) => job.id))}
          onSelectAllMatching={() => selection.selectAll(jobs.map((job) => job.id))}
          onClear={selection.clear}
          onBulkCleared={onBulkCleared}
        />
        <ListBody
          filter={filter}
          list={list}
          jobs={jobs}
          selectedId={effectiveSelectedId}
          collapsingId={collapsingId}
          isChecked={selection.isSelected}
          onOpen={setSelectedId}
          onToggle={selection.toggle}
        />
      </div>
    );
  }
}

interface ListBodyProps {
  filter: TriageFilter;
  list: ReturnType<typeof useJobsList>;
  jobs: JobSummary[];
  selectedId: string | null;
  collapsingId: string | null;
  isChecked: (id: string) => boolean;
  onOpen: (id: string) => void;
  onToggle: (id: string) => void;
}

function ListBody({ filter, list, jobs, ...rowProps }: ListBodyProps) {
  if (list.isPending) {
    return (
      <div className="flex flex-col gap-1 px-4 py-3" aria-label="Loading jobs">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-7 w-full" />
        ))}
      </div>
    );
  }
  if (list.isError) {
    return <p className="px-4 py-6 text-body-sm text-danger">{list.error.message}</p>;
  }
  if (jobs.length === 0) {
    return (
      <div className="grid flex-1 place-items-center px-6 text-center">
        <div>
          <p className="text-body-sm font-medium text-ink-2">
            {filter === "results" ? "No results to review" : `No ${filter} jobs`}
          </p>
          <p className="mt-1 text-micro text-mute">
            {filter === "results"
              ? "Run a scan and evaluation to add filtered job results."
              : "Change the filter to see another decision."}
          </p>
        </div>
      </div>
    );
  }
  return <JobList jobs={jobs} {...rowProps} />;
}
