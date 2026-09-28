import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Table, { type TableProps } from "@cloudscape-design/components/table";

import type { RealJobsListResponse } from "@/api/queries";
import { formatFirstSeenDate } from "@/lib/dates";
import { useDensity } from "@/lib/density";

type TriageJob = RealJobsListResponse["jobs"][number];

interface JobListProps {
  jobs: TriageJob[];
  isChecked: (id: string) => boolean;
  onOpen: (id: string) => void;
  onToggle: (id: string) => void;
  sort: JobListSort;
  onSort: (sort: JobListSort) => void;
}

export type JobListSort = "posted_asc" | "posted_desc" | "score_asc" | "score_desc";

/** Cloudscape table for one paginated Triage result page. */
export function JobList({
  jobs,
  isChecked,
  onOpen,
  onToggle,
  sort,
  onSort,
}: JobListProps) {
  const { density } = useDensity();
  const selectedItems = jobs.filter((job) => isChecked(job.id));
  const columnDefinitions: TableProps.ColumnDefinition<TriageJob>[] = [
    {
      id: "job",
      header: "Job",
      cell: (job) => (
        <div data-testid={`job-row-${job.id}`}>
          <Button
            variant="inline-link"
            ariaLabel={`Open ${job.company} ${job.title}`}
            onClick={() => onOpen(job.id)}
          >
            {job.company} · {job.title}
          </Button>
          {job.location ? (
            <Box color="text-body-secondary">{job.location}</Box>
          ) : null}
          {job.is_repost === true && <div><Badge color="grey">Reposted</Badge></div>}
          {job.identity_review_state !== "clear" && (
            <div><Badge color="blue">Needs review</Badge></div>
          )}
          {job.evaluation_stale_reason && (
            <div><Badge color="blue">Old score · re-evaluation pending</Badge></div>
          )}
        </div>
      ),
      width: 360,
    },
    {
      id: "verdict",
      header: "Recommendation",
      cell: (job) => <VerdictBadge job={job} />,
      width: 120,
    },
    {
      id: "score",
      header: "Fit score",
      sortingField: "score",
      cell: (job) => job.identity_review_state !== "clear"
        ? "Review pending"
        : job.evaluation_stale_reason === "legacy_policy_unknown"
          ? "Old score · pending"
        : job.stage_b_fit_score ?? job.stage_a_score ?? "—",
      width: 70,
    },
    {
      id: "posted",
      header: "First seen",
      sortingField: "posted",
      cell: (job) => (
        <Box color="text-body-secondary">
          {formatFirstSeenDate(job.discovered_at)}
        </Box>
      ),
      width: 120,
    },
  ];
  const sortingField = sort.startsWith("score") ? "score" : "posted";
  const sortingColumn = columnDefinitions.find(
    (column) => column.sortingField === sortingField,
  );

  return (
    <Table
      variant="embedded"
      trackBy="id"
      items={jobs}
      stripedRows
      wrapLines={false}
      contentDensity={density}
      sortingColumn={sortingColumn}
      sortingDescending={sort.endsWith("_desc")}
      onSortingChange={({ detail }) => {
        const field = detail.sortingColumn.sortingField;
        if (field === "score" || field === "posted") {
          onSort(`${field}_${detail.isDescending ? "desc" : "asc"}`);
        }
      }}
      selectionType="multi"
      selectedItems={selectedItems}
      onSelectionChange={({ detail }) => {
        const nextIds = new Set(detail.selectedItems.map((job) => job.id));
        for (const job of jobs) {
          if (nextIds.has(job.id) !== isChecked(job.id)) {
            onToggle(job.id);
          }
        }
      }}
      ariaLabels={{
        tableLabel: "Jobs",
        selectionGroupLabel: "Job selection",
        allItemsSelectionLabel: () => "Select current page",
        itemSelectionLabel: (_state, job) => `Select ${job.company} ${job.title}`,
      }}
      columnDefinitions={columnDefinitions}
    />
  );
}

function VerdictBadge({ job }: { job: TriageJob }) {
  if (job.identity_review_state !== "clear") {
    return <Badge color="blue">Needs review</Badge>;
  }
  if (job.evaluation_stale_reason && job.verdict === null) {
    return <Badge color="blue">Re-evaluation pending</Badge>;
  }
  if (job.stage_b_status === "error") {
    return <Badge color="red">Evaluation error</Badge>;
  }
  if (job.verdict === "apply") {
    return <Badge color="green">Apply</Badge>;
  }
  if (job.verdict === "consider") {
    return <Badge color="blue">Consider</Badge>;
  }
  if (job.verdict === "skip") {
    return <Badge color="grey">Skip</Badge>;
  }
  return <Badge color="grey">Not evaluated</Badge>;
}
