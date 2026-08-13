import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { useMemo, useRef, useState } from "react";

import { useAttention, useJobsList, type JobsQuery, type JobSummary } from "@/api/queries";
import { ArchiveSection } from "@/components/jobs/ArchiveSection";
import { AttentionBar, type AttentionBucket } from "@/components/jobs/AttentionBar";
import { DetailPane } from "@/components/jobs/DetailPane";
import { InterviewPanel } from "@/components/jobs/InterviewPanel";
import { RestoreSection } from "@/components/jobs/RestoreSection";
import { groupJobs, StatusGroups, type GroupKey } from "@/components/jobs/StatusGroups";
import { Skeleton } from "@/components/ui/skeleton";
import { useKeyboardMap } from "@/lib/keyboard";

/** Pipeline corpora are 10^2-scale (plan D10) — one page covers the zone. */
const PAGE_LIMIT = 200;

/**
 * Pipeline list params: committed rows are shown AS-IS — no hard
 * filters, no fold, no require_verdict. Those are triage-ingest
 * concerns; a job you applied to must never vanish because a filter
 * config changed. Statuses per D13: the four groups' members.
 */
const PIPELINE_QUERY: JobsQuery = {
  tab: "all",
  statuses: ["applied", "interviewing", "offer", "rejected", "ghosted"],
  limit: PAGE_LIMIT,
  offset: 0,
};

export default function PipelinePage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeBucket, setActiveBucket] = useState<AttentionBucket | null>(null);
  const [collapsedKeys, setCollapsedKeys] = useState<ReadonlySet<GroupKey>>(new Set());
  const detailRef = useRef<HTMLElement>(null);

  const list = useJobsList(PIPELINE_QUERY);
  const attention = useAttention();
  const jobs = useMemo(() => list.data?.jobs ?? [], [list.data]);

  // Chip filter narrows to the active bucket's job ids; the group split
  // runs on the filtered rows, so chips and groups compose.
  const filteredJobs = useMemo(() => {
    if (activeBucket === null || attention.data === undefined) {
      return jobs;
    }
    const ids = new Set(attention.data[activeBucket].map((item) => item.job_id));
    return jobs.filter((job) => ids.has(job.id));
  }, [jobs, activeBucket, attention.data]);
  const groups = useMemo(() => groupJobs(filteredJobs), [filteredJobs]);

  // What the eye sees: filtered rows of expanded groups, in group order.
  // This is the keyboard's row order and the selection-fallback pool.
  const visibleRows = useMemo(
    () => groups.filter((group) => !collapsedKeys.has(group.key)).flatMap((group) => group.jobs),
    [groups, collapsedKeys],
  );

  // Derived selection, mirroring triage: fall back to the first visible
  // row when nothing is selected or the row was filtered/collapsed away.
  const effectiveSelectedId =
    selectedId !== null && visibleRows.some((job) => job.id === selectedId)
      ? selectedId
      : (visibleRows[0]?.id ?? null);
  const selectedJob = visibleRows.find((job) => job.id === effectiveSelectedId) ?? null;

  const toggleBucket = (bucket: AttentionBucket) => {
    setActiveBucket((current) => (current === bucket ? null : bucket));
  };

  const toggleGroup = (key: GroupKey) => {
    setCollapsedKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const moveSelection = (delta: 1 | -1) => {
    if (visibleRows.length === 0) {
      return;
    }
    const index =
      effectiveSelectedId === null
        ? -1
        : visibleRows.findIndex((job) => job.id === effectiveSelectedId);
    const next = index === -1 ? 0 : Math.min(visibleRows.length - 1, Math.max(0, index + delta));
    setSelectedId(visibleRows[next]?.id ?? null);
  };

  // Movement + open only. NO decide keys (h/s/a are triage semantics —
  // pipeline rows are post-decision; status moves happen via the
  // interview panel and restore, not single-key triage actions).
  useKeyboardMap({
    ArrowDown: () => moveSelection(1),
    j: () => moveSelection(1),
    ArrowUp: () => moveSelection(-1),
    k: () => moveSelection(-1),
    Enter: () => detailRef.current?.focus(),
    o: () => {
      if (selectedJob !== null) {
        window.open(selectedJob.url, "_blank", "noopener");
      }
    },
  });

  return (
    <div className="jobfeed-decision-surface" data-testid="cloudscape-pipeline">
      <div className="jobfeed-decision-queue">
        <Container
          fitHeight
          header={
            <Header
              variant="h1"
              description="Follow every application from first outreach through interview, offer, or close."
              counter={list.data ? `(${list.data.total})` : undefined}
            >
              Application pipeline
            </Header>
          }
        >
          <SpaceBetween size="s">
            <AttentionBar
              attention={attention.data}
              activeBucket={activeBucket}
              onToggle={toggleBucket}
            />
            <ListBody
              list={list}
              groups={groups}
              isFiltered={activeBucket !== null}
              collapsedKeys={collapsedKeys}
              selectedId={effectiveSelectedId}
              onToggleGroup={toggleGroup}
              onOpen={setSelectedId}
            />
          </SpaceBetween>
        </Container>
      </div>
      <section
        ref={detailRef}
        tabIndex={-1}
        aria-label="Job detail"
        className="jobfeed-evidence-panel focus-visible:outline-none"
      >
        <Container
          fitHeight
          header={
            <Header
              variant="h2"
              description="Evidence, follow-ups, interview rounds, and status actions"
            >
              {selectedJob ? `Job detail — ${selectedJob.company}` : "Job detail"}
            </Header>
          }
        >
          <DetailPane
            jobId={effectiveSelectedId}
            showJdPaste={false}
            showIgnore={false}
            showDecide={false}
            emptyHint="Select a row — move with j/k, open the posting with o."
            extraSections={selectedJob !== null && <PipelineSections job={selectedJob} />}
          />
        </Container>
      </section>
    </div>
  );
}

/** Status-keyed detail extras: interview rounds while applied/interviewing, the
 * archive (abandon) action while still active (applied/interviewing), and
 * the restore card once ghosted. Keyed off the LIST row's status so the
 * seam needs no second detail fetch. */
function PipelineSections({ job }: { job: JobSummary }) {
  return (
    <>
      {(job.status === "applied" || job.status === "interviewing") && (
        <InterviewPanel jobId={job.id} />
      )}
      {(job.status === "applied" || job.status === "interviewing") && (
        <ArchiveSection jobId={job.id} status={job.status} />
      )}
      {job.status === "ghosted" && <RestoreSection jobId={job.id} status={job.status} />}
    </>
  );
}

interface ListBodyProps {
  list: ReturnType<typeof useJobsList>;
  groups: ReturnType<typeof groupJobs>;
  isFiltered: boolean;
  collapsedKeys: ReadonlySet<GroupKey>;
  selectedId: string | null;
  onToggleGroup: (key: GroupKey) => void;
  onOpen: (id: string) => void;
}

function ListBody({ list, groups, isFiltered, ...groupProps }: ListBodyProps) {
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
    return <Box color="text-status-error">{list.error.message}</Box>;
  }
  if (groups.length === 0) {
    return (
      <Box textAlign="center" padding={{ vertical: "xxl", horizontal: "l" }}>
        <SpaceBetween size="xxs">
          <Box variant="h3">
            {isFiltered ? "Nothing in this bucket" : "Nothing in flight"}
          </Box>
          <Box color="text-body-secondary">
            {isFiltered
              ? "The flagged jobs sit outside the pipeline statuses — clear the filter to see everything."
              : "Apply from Triage and applications track here: follow-ups, interviews, offers."}
          </Box>
        </SpaceBetween>
      </Box>
    );
  }
  return <StatusGroups groups={groups} {...groupProps} />;
}
