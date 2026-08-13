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
import { ApplyDialog } from "@/components/jobs/ApplyDialog";
import { BulkBar } from "@/components/jobs/BulkBar";
import { DetailPane, type DecideStatus } from "@/components/jobs/DetailPane";
import { JobList } from "@/components/jobs/JobList";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/use-toast";
import { useKeyboardMap } from "@/lib/keyboard";
import { useSelection } from "@/lib/use-selection";

type TriageTab = "queue" | "pending_jd";

/** Triage corpora are 10^2-scale (plan D10) — one page covers the zone. */
const PAGE_LIMIT = 200;
const COLLAPSE_MS = 180;

/**
 * A4 zone params. Queue omits `sort` on purpose: queue/pending_jd always
 * use the server's fixed verdict-group order.
 */
function triageQuery(tab: TriageTab): JobsQuery {
  if (tab === "queue") {
    return {
      tab: "queue",
      apply_hard_filters: true,
      dedupe: true,
      require_verdict: true,
      limit: PAGE_LIMIT,
      offset: 0,
    };
  }
  return { tab: "pending_jd", apply_hard_filters: true, limit: PAGE_LIMIT, offset: 0 };
}

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export default function TriagePage() {
  const [tab, setTab] = useState<TriageTab>("queue");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapsingId, setCollapsingId] = useState<string | null>(null);
  // Snapshot of the dialog's job, taken when it opens (null = closed). A
  // background refetch can re-seed the live selection mid-dialog; the
  // application audit must stay pinned to the job the user opened it for.
  const [applyTarget, setApplyTarget] = useState<JobSummary | null>(null);
  const selection = useSelection();
  const detailRef = useRef<HTMLElement>(null);
  const collapseTimerRef = useRef<number | null>(null);

  const list = useJobsList(triageQuery(tab));
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

  const switchTab = (next: TriageTab) => {
    setTab(next);
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

  const decide = (to: DecideStatus) => {
    const id = effectiveSelectedId;
    if (id === null || transition.isPending) {
      return;
    }
    // Pending-JD rows are unscored 'new' rows whose only legal move is
    // new→scored, so the dismiss-as-junk Ignore must bypass the graph.
    // Queue decides stay graph-checked (never forced).
    const force = tab === "pending_jd" && to === "ignored";
    transition.mutate(
      { id, to, force },
      {
        onSuccess: () => handleDecided(id),
        onError: (error) =>
          toast({ variant: "destructive", title: "Action failed", description: error.message }),
      },
    );
  };

  const moveSelection = (delta: 1 | -1) => {
    const rows = rowsRef.current;
    if (rows.length === 0) {
      return;
    }
    const index =
      effectiveSelectedId === null
        ? -1
        : rows.findIndex((job) => job.id === effectiveSelectedId);
    const next = index === -1 ? 0 : Math.min(rows.length - 1, Math.max(0, index + delta));
    setSelectedId(rows[next]?.id ?? null);
  };

  // n/f reach into the detail pane by data attribute — the pane's inner
  // sections would otherwise need ref plumbing through three layers.
  const focusKbdTarget = (name: string) => {
    document.querySelector<HTMLElement>(`[data-kbd-target="${name}"]`)?.focus();
  };

  // Decide keys are per-tab: a/h/s only make sense for scored queue rows;
  // a pending-JD row's sole decision is the (forced) Ignore on i. Movement,
  // open, and the focus jumps stay shared.
  const decideKeys: Record<string, () => void> =
    tab === "queue"
      ? {
          a: () => {
            if (selectedJob !== null) {
              setApplyTarget(selectedJob);
            }
          },
          h: () => decide("shortlisted"),
          s: () => decide("archived"),
        }
      : { i: () => decide("ignored") };

  useKeyboardMap(
    {
      ArrowDown: () => moveSelection(1),
      j: () => moveSelection(1),
      ArrowUp: () => moveSelection(-1),
      k: () => moveSelection(-1),
      Enter: () => detailRef.current?.focus(),
      n: () => focusKbdTarget("note"),
      f: () => focusKbdTarget("followup"),
      o: () => {
        if (selectedJob !== null) {
          window.open(selectedJob.url, "_blank", "noopener");
        }
      },
      ...decideKeys,
    },
    { enabled: applyTarget === null },
  );

  const onBulkCleared = () => {
    selection.clear();
    setSelectedId(null);
  };

  return (
    <div className="jobfeed-decision-surface" data-testid="triage-decision-surface">
      <div className="jobfeed-decision-queue">
        <Tabs
          activeTabId={tab}
          variant="container"
          fitHeight
          disableContentPaddings
          onChange={({ detail }) => switchTab(detail.activeTabId as TriageTab)}
          tabs={[
            {
              id: "queue",
              label: <TabLabel label="Queue" value={list.data?.tab_counts.queue} />,
              content: triageListContent(),
            },
            {
              id: "pending_jd",
              label: <TabLabel label="Pending JD" value={list.data?.tab_counts.pending_jd} />,
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
      <section ref={detailRef} tabIndex={-1} aria-label="Job detail" className="jobfeed-evidence-panel">
        <Container
          header={
            <Header variant="h2" description="Scores, source evidence, notes, and next action">
              {selectedJob ? `${selectedJob.company} · ${selectedJob.title}` : "Job evidence"}
            </Header>
          }
        >
          <DetailPane
            jobId={effectiveSelectedId}
            showJdPaste={tab === "pending_jd"}
            showIgnore={tab === "pending_jd"}
            showDecide={tab !== "pending_jd"}
            isDeciding={transition.isPending}
            onDecide={decide}
            onOpenApply={() => {
              if (selectedJob !== null) setApplyTarget(selectedJob);
            }}
            emptyHint={tab === "pending_jd" ? "Select a row — move with j/k, paste a JD or ignore with i." : undefined}
          />
        </Container>
      </section>
      <ApplyDialog
        job={applyTarget}
        open={applyTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setApplyTarget(null);
          }
        }}
        onApplied={handleDecided}
      />
    </div>
  );

  function triageListContent() {
    return (
      <div className="jobfeed-tab-content">
        <BulkBar
          selectedIds={selection.selectedIds}
          total={list.data?.total ?? 0}
          loadedCount={jobs.length}
          ignoreOnly={tab === "pending_jd"}
          onSelectPage={() => selection.selectMany(jobs.map((job) => job.id))}
          onSelectAllMatching={() => selection.selectAll(jobs.map((job) => job.id))}
          onClear={selection.clear}
          onBulkCleared={onBulkCleared}
        />
        <ListBody
          tab={tab}
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

function TabLabel({ label, value }: { label: string; value: number | undefined }) {
  return <span>{label}{value === undefined ? "" : ` (${value})`}</span>;
}

interface ListBodyProps {
  tab: TriageTab;
  list: ReturnType<typeof useJobsList>;
  jobs: JobSummary[];
  selectedId: string | null;
  collapsingId: string | null;
  isChecked: (id: string) => boolean;
  onOpen: (id: string) => void;
  onToggle: (id: string) => void;
}

function ListBody({ tab, list, jobs, ...rowProps }: ListBodyProps) {
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
            {tab === "queue" ? "Queue clear" : "Nothing waiting on a JD"}
          </p>
          <p className="mt-1 text-micro text-mute">
            {tab === "queue"
              ? "Scan results land here sorted by verdict — decide with a / h / s."
              : "Rows whose JD couldn't be fetched queue here for a manual paste."}
          </p>
        </div>
      </div>
    );
  }
  return <JobList jobs={jobs} {...rowProps} />;
}
