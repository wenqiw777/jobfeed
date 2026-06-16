import { useMemo, useState } from "react";

import {
  useJobsList,
  useJobTransition,
  type JobsQuery,
  type JobSummary,
} from "@/api/queries";
import { ApplyDialog } from "@/components/jobs/ApplyDialog";
import { DetailPane, type DecideStatus } from "@/components/jobs/DetailPane";
import { LibraryTable } from "@/components/jobs/LibraryTable";
import { RestoreSection } from "@/components/jobs/RestoreSection";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/components/ui/use-toast";
import { useDebouncedCallback } from "@/lib/use-debounced";

type LibraryTab = Extract<
  NonNullable<JobsQuery["tab"]>,
  "all" | "scored" | "shortlisted" | "archived"
>;
type LibrarySort = NonNullable<JobsQuery["sort"]>;

const TABS: { value: LibraryTab; label: string }[] = [
  { value: "all", label: "All" },
  { value: "scored", label: "Scored" },
  // Server-side per D13: Shortlisted = {shortlisted, awaiting_referral},
  // Archived = {archived, ignored}.
  { value: "shortlisted", label: "Shortlisted" },
  { value: "archived", label: "Archived" },
];

/** The four A4 sort values, default first. */
const SORT_OPTIONS: { value: LibrarySort; label: string }[] = [
  { value: "discovered_desc", label: "Newest discovered" },
  { value: "posted_desc", label: "Newest posted" },
  { value: "score_desc", label: "Highest score" },
  { value: "company_asc", label: "Company A–Z" },
];

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 250;

/** Statuses still in the decide loop — the drawer offers triage actions. */
const DECIDABLE = new Set(["new", "scored", "shortlisted", "awaiting_referral"]);
/** Statuses POST /restore accepts — the drawer offers the restore card. */
function isRestorable(status: string): status is "ghosted" | "archived" {
  return status === "ghosted" || status === "archived";
}

interface LibraryQueryState {
  tab: LibraryTab;
  sort: LibrarySort;
  search: string;
  page: number;
}

/**
 * Library: every posting ever seen — full-width table, drawer detail.
 * No keyboard map on purpose: this is a lookup zone (table + drawer
 * interactions only); j/k decide flows belong to Triage and Pipeline.
 */
export default function LibraryPage() {
  const [state, setState] = useState<LibraryQueryState>({
    tab: "all",
    sort: "discovered_desc",
    search: "",
    page: 0,
  });
  const [searchInput, setSearchInput] = useState("");
  // Drawer target is a row snapshot (like triage's applyTarget): a
  // background refetch may drop the row from the page, but the open
  // drawer keeps showing the job the user clicked.
  const [drawerJob, setDrawerJob] = useState<JobSummary | null>(null);
  const [applyTarget, setApplyTarget] = useState<JobSummary | null>(null);

  // The settled search commits together with its page reset in ONE state
  // update — no transient request with the new search and a stale offset.
  const commitSearch = useDebouncedCallback((search: string) => {
    setState((current) =>
      current.search === search ? current : { ...current, search, page: 0 },
    );
  }, SEARCH_DEBOUNCE_MS);

  /** Tab/sort changes reset the page: offsets don't survive filter changes. */
  const applyFilter = (patch: Partial<Pick<LibraryQueryState, "tab" | "sort">>) => {
    setState((current) => ({ ...current, ...patch, page: 0 }));
  };

  // Library shows everything: no apply_hard_filters / dedupe /
  // require_verdict — those are triage-ingest concerns (plan A4).
  const query: JobsQuery = useMemo(
    () => ({
      tab: state.tab,
      sort: state.sort,
      search: state.search === "" ? undefined : state.search,
      limit: PAGE_SIZE,
      offset: state.page * PAGE_SIZE,
    }),
    [state],
  );
  const list = useJobsList(query, { keepPrevious: true });
  const transition = useJobTransition();

  // Stranded page: a decide/restore can empty the trailing page (total
  // shrank under the current offset), leaving a settled empty response
  // while rows still exist. Clamp back to the last valid page via the
  // adjust-state-during-render pattern (react.dev: "storing information
  // from previous renders"). Placeholder payloads are skipped — their
  // total belongs to the previous query — and the page guard terminates
  // the loop: the clamped query returns rows, so the condition goes false.
  const settled = list.isPlaceholderData ? undefined : list.data;
  if (settled !== undefined && settled.jobs.length === 0 && settled.total > 0) {
    const lastPage = Math.max(0, Math.ceil(settled.total / PAGE_SIZE) - 1);
    if (state.page > lastPage) {
      setState({ ...state, page: lastPage });
    }
  }

  const decide = (to: DecideStatus) => {
    if (drawerJob === null || transition.isPending) {
      return;
    }
    transition.mutate(
      { id: drawerJob.id, to },
      {
        // The decided row may leave the current tab — close the drawer
        // instead of pointing it at a row that just vanished.
        onSuccess: () => setDrawerJob(null),
        onError: (error) =>
          toast({ variant: "destructive", title: "Action failed", description: error.message }),
      },
    );
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-3 px-4 pt-2">
        <Tabs
          value={state.tab}
          onValueChange={(value) => applyFilter({ tab: value as LibraryTab })}
        >
          <TabsList>
            {TABS.map(({ value, label }) => (
              <TabsTrigger key={value} value={value}>
                {label} <TabCount value={list.data?.tab_counts[value]} />
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <div className="ml-auto flex items-center gap-2">
          <Input
            value={searchInput}
            onChange={(event) => {
              setSearchInput(event.target.value);
              commitSearch(event.target.value);
            }}
            placeholder="Search company or title"
            aria-label="Search jobs"
            className="w-60"
          />
          <Select
            value={state.sort}
            onValueChange={(value) => applyFilter({ sort: value as LibrarySort })}
          >
            <SelectTrigger className="w-44" aria-label="Sort">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map(({ value, label }) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <ListBody
        list={list}
        activeId={drawerJob?.id ?? null}
        pageKey={`${state.tab}|${state.sort}|${state.search}|${state.page}`}
        onOpen={setDrawerJob}
      />
      <Pager
        page={state.page}
        pageRows={list.data?.jobs.length ?? 0}
        total={list.data?.total ?? 0}
        isPlaceholder={list.isPlaceholderData}
        onPage={(page) => setState((current) => ({ ...current, page }))}
      />
      <Sheet
        open={drawerJob !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDrawerJob(null);
          }
        }}
      >
        <SheetContent className="p-0" aria-describedby={undefined}>
          <SheetTitle className="sr-only">Job detail</SheetTitle>
          {drawerJob !== null && (
            <DetailPane
              jobId={drawerJob.id}
              showJdPaste={false}
              showIgnore={false}
              showDecide={DECIDABLE.has(drawerJob.status)}
              isDeciding={transition.isPending}
              onDecide={decide}
              onOpenApply={() => setApplyTarget(drawerJob)}
              extraSections={
                isRestorable(drawerJob.status) && (
                  <RestoreSection
                    jobId={drawerJob.id}
                    status={drawerJob.status}
                    // The restored row leaves the snapshot's status — close
                    // the drawer instead of offering a second, stale Restore
                    // (mirrors decide-closes-drawer above).
                    onRestored={() => setDrawerJob(null)}
                  />
                )
              }
            />
          )}
        </SheetContent>
      </Sheet>
      <ApplyDialog
        job={applyTarget}
        open={applyTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setApplyTarget(null);
          }
        }}
        onApplied={() => setDrawerJob(null)}
      />
    </div>
  );
}

function TabCount({ value }: { value: number | undefined }) {
  if (value === undefined) {
    return null;
  }
  return <span className="font-mono text-micro text-mute">{value}</span>;
}

interface ListBodyProps {
  list: ReturnType<typeof useJobsList>;
  activeId: string | null;
  pageKey: string;
  onOpen: (job: JobSummary) => void;
}

function ListBody({ list, activeId, pageKey, onOpen }: ListBodyProps) {
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
  const jobs = list.data.jobs;
  if (jobs.length === 0) {
    return (
      <div className="grid flex-1 place-items-center px-6 text-center">
        <div>
          <p className="text-body-sm font-medium text-ink-2">Nothing here</p>
          <p className="mt-1 text-micro text-mute">
            Every posting ever seen lands in Library — try another tab or clear the search.
          </p>
        </div>
      </div>
    );
  }
  return <LibraryTable jobs={jobs} activeId={activeId} pageKey={pageKey} onOpen={onOpen} />;
}

interface PagerProps {
  page: number;
  pageRows: number;
  total: number;
  /** keepPreviousData is showing the previous query's page — its total
   * is stale, so Next must not page against it. */
  isPlaceholder: boolean;
  onPage: (page: number) => void;
}

/** Server-side pagination footer: prev/next plus the mono range label. */
function Pager({ page, pageRows, total, isPlaceholder, onPage }: PagerProps) {
  const start = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const end = page * PAGE_SIZE + pageRows;
  return (
    <div className="flex items-center justify-between border-t border-border px-4 py-2">
      <span className="font-mono text-micro text-mute">
        {total === 0 ? "0 results" : `${start}–${end} of ${total}`}
      </span>
      <div className="flex items-center gap-1.5">
        <Button size="sm" disabled={page === 0} onClick={() => onPage(page - 1)}>
          Prev
        </Button>
        <Button
          size="sm"
          disabled={isPlaceholder || end >= total}
          onClick={() => onPage(page + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
