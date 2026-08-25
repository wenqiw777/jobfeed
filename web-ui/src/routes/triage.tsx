import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import Grid from "@cloudscape-design/components/grid";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import Tabs from "@cloudscape-design/components/tabs";

import {
  useJobsList,
  useJobTransition,
  fetchAllMatchingJobIds,
  jobsKeys,
  type JobsQuery,
  type JobSummary,
} from "@/api/queries";
import { BulkBar } from "@/components/jobs/BulkBar";
import { DetailPane, type UserDecision } from "@/components/jobs/DetailPane";
import { JobList, type JobListSort } from "@/components/jobs/JobList";
import { toast } from "@/components/ui/use-toast";
import { useSelection } from "@/lib/use-selection";

type TriageFilter = "results" | "wait" | "applied" | "ignored";
type TriageSort = JobListSort;

const PAGE_LIMIT = 50;

/** Triage query with server-side pagination and user-selected ordering. */
function triageQuery(filter: TriageFilter, page: number, sort: TriageSort): JobsQuery {
  if (filter === "results") {
    return {
      tab: "queue",
      decision: "results",
      apply_hard_filters: true,
      dedupe: true,
      require_verdict: true,
      sort,
      limit: PAGE_LIMIT,
      offset: page * PAGE_LIMIT,
    };
  }
  return {
    tab: "all",
    decision: filter,
    sort,
    limit: PAGE_LIMIT,
    offset: page * PAGE_LIMIT,
  };
}

export default function TriagePage() {
  const [filter, setFilter] = useState<TriageFilter>("results");
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<TriageSort>("discovered_desc");
  const [isSelectingAll, setIsSelectingAll] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selection = useSelection();
  const queryClient = useQueryClient();

  const query = useMemo(() => triageQuery(filter, page, sort), [filter, page, sort]);
  const list = useJobsList(query, { keepPrevious: true });
  const jobs = useMemo(() => list.data?.jobs ?? [], [list.data]);
  // Decide/advance handlers read the displayed order through a ref so a
  // setTimeout never closes over a stale list. Synced in an effect (refs
  // must not be written during render); handlers only run after effects.
  const rowsRef = useRef<JobSummary[]>(jobs);
  useEffect(() => {
    rowsRef.current = jobs;
  });

  const transition = useJobTransition();

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
    setPage(0);
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

  const handleDecided = (id: string) => {
    advanceFrom(id);
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
          toast({
            variant: "destructive",
            title: "Decision could not be saved",
            description: error.message,
          }),
      },
    );
  };

  const onBulkResult = (failedIds: string[]) => {
    const failed = new Set(failedIds);
    const completedIds = selection.selectedIds.filter((id) => !failed.has(id));
    selection.deselectMany(completedIds);
    if (completedIds.includes(selectedId ?? "")) {
      setSelectedId(null);
    }
    queryClient.setQueryData(jobsKeys.list(query), (previous: typeof list.data) => {
      if (previous === undefined) {
        return previous;
      }
      const completedOnPage = previous.jobs.filter((job) => completedIds.includes(job.id));
      if (completedOnPage.length === 0) {
        return previous;
      }
      return {
        ...previous,
        jobs: previous.jobs.filter((job) => !completedIds.includes(job.id)),
        total: Math.max(0, previous.total - completedOnPage.length),
      };
    });
  };

  const selectAllMatching = async () => {
    if (list.data === undefined || isSelectingAll) {
      return;
    }
    setIsSelectingAll(true);
    try {
      selection.selectAll(
        await fetchAllMatchingJobIds(query, list.data.total),
      );
    } catch (error) {
      toast({
        variant: "destructive",
        title: "Jobs could not be selected",
        description: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setIsSelectingAll(false);
    }
  };

  return (
    <Grid
      gridDefinition={[
        { colspan: 7 },
        { colspan: 5 },
      ]}
    >
      <div data-testid="triage-decision-surface">
        <Tabs
          activeTabId={filter}
          variant="container"
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
      <section aria-label="Job detail">
        <Container
          header={
            <Header variant="h2" description="Review the recommendation, evidence, and your decision.">
              {selectedJob ? `${selectedJob.company} · ${selectedJob.title}` : "Job evidence"}
            </Header>
          }
        >
          <DetailPane
            jobId={effectiveSelectedId}
            decisionView={filter}
            isDeciding={transition.isPending}
            onDecide={decide}
            emptyHint="Select a result to review its evidence."
          />
        </Container>
      </section>
    </Grid>
  );

  function triageListContent() {
    return (
      <SpaceBetween size="xs">
        <BulkBar
          currentDecision={filter}
          selectedIds={selection.selectedIds}
          total={list.data?.total ?? 0}
          onSelectPage={() => selection.selectMany(jobs.map((job) => job.id))}
          isSelectingAll={isSelectingAll}
          onSelectAllMatching={() => void selectAllMatching()}
          onClear={selection.clear}
          onBulkResult={onBulkResult}
        />
        <ListBody
          filter={filter}
          list={list}
          jobs={jobs}
          isChecked={selection.isSelected}
          onOpen={setSelectedId}
          onToggle={selection.toggle}
          sort={sort}
          onSort={(nextSort) => {
            setSort(nextSort);
            setPage(0);
            setSelectedId(null);
          }}
        />
        {(list.data?.total ?? 0) > PAGE_LIMIT && (
          <SpaceBetween size="xs" alignItems="center">
            <Pagination
              currentPageIndex={page + 1}
              pagesCount={Math.ceil((list.data?.total ?? 0) / PAGE_LIMIT)}
              disabled={list.isFetching}
              ariaLabels={{
                paginationLabel: "Results pagination",
                previousPageLabel: "Previous page",
                nextPageLabel: "Next page",
                pageLabel: (pageNumber) => `Page ${pageNumber}`,
              }}
              onChange={({ detail }) => {
                setPage(detail.currentPageIndex - 1);
                setSelectedId(null);
              }}
            />
          </SpaceBetween>
        )}
      </SpaceBetween>
    );
  }
}

interface ListBodyProps {
  filter: TriageFilter;
  list: ReturnType<typeof useJobsList>;
  jobs: JobSummary[];
  isChecked: (id: string) => boolean;
  onOpen: (id: string) => void;
  onToggle: (id: string) => void;
  sort: TriageSort;
  onSort: (sort: TriageSort) => void;
}

function ListBody({ filter, list, jobs, ...rowProps }: ListBodyProps) {
  if (list.isPending) {
    return (
      <Box textAlign="center" padding="l">
        <Spinner size="large" />
      </Box>
    );
  }
  if (list.isError) {
    return <Alert type="error">{list.error.message}</Alert>;
  }
  if (jobs.length === 0) {
    return (
      <Box textAlign="center" padding="l">
        <SpaceBetween size="xs">
          <Box variant="strong">
            {filter === "results" ? "No results to review" : `No ${filter} jobs`}
          </Box>
          <Box color="text-body-secondary">
            {filter === "results"
              ? "Run a scan and evaluation to add filtered job results."
              : "Change the filter to see another decision."}
          </Box>
        </SpaceBetween>
      </Box>
    );
  }
  return <JobList jobs={jobs} {...rowProps} />;
}
