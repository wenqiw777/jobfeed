import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table, { type TableProps } from "@cloudscape-design/components/table";

import type { JobsQuery, JobSummary } from "@/api/queries";
import { VerdictPill } from "@/components/jobs/VerdictPill";
import { formatEstimatedPostedDate, formatRelativeAge } from "@/lib/dates";
import { useDensity } from "@/lib/density";

interface LibraryTableProps {
  jobs: JobSummary[];
  onOpen: (job: JobSummary) => void;
  sort: LibrarySort;
  onSort: (sort: LibrarySort) => void;
}

type LibrarySort = NonNullable<JobsQuery["sort"]>;

/** Cloudscape table for one Library page. */
export function LibraryTable({ jobs, onOpen, sort, onSort }: LibraryTableProps) {
  const { density } = useDensity();
  const columnDefinitions: TableProps.ColumnDefinition<JobSummary>[] = [
    {
      id: "company",
      header: "Company",
      cell: (job) => (
        <span data-testid={`job-row-${job.id}`}>
          <Button
            variant="inline-link"
            ariaLabel={`Open ${job.company} ${job.title}`}
            onClick={() => onOpen(job)}
          >
            {job.company}
          </Button>
        </span>
      ),
      width: 180,
    },
    {
      id: "title",
      header: "Title",
      cell: (job) => (
        <SpaceBetween direction="horizontal" size="xs">
          <Box key="title">{job.title}</Box>
          {job.closed_at !== null && <Badge key="closed" color="grey">Closed</Badge>}
        </SpaceBetween>
      ),
      width: 320,
    },
    { id: "decision", header: "Your decision", cell: (job) => decisionLabel(job.status) },
    {
      id: "verdict",
      header: "Recommendation",
      cell: (job) => (
        <VerdictPill verdict={job.verdict} stageBStatus={job.stage_b_status} />
      ),
    },
    {
      id: "score",
      header: "Fit score",
      sortingField: "score",
      cell: (job) => job.stage_b_fit_score ?? job.stage_a_score ?? "—",
    },
    {
      id: "posted",
      header: "Posted",
      sortingField: "posted",
      cell: (job) => job.posted_at === null
        ? formatEstimatedPostedDate(job.discovered_at)
        : formatRelativeAge(job.posted_at),
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
      stickyHeader
      wrapLines={false}
      resizableColumns
      contentDensity={density}
      sortingColumn={sortingColumn}
      sortingDescending={sort.endsWith("_desc")}
      onSortingChange={({ detail }) => {
        const field = detail.sortingColumn.sortingField;
        if (field === "score" || field === "posted") {
          onSort(`${field}_${detail.isDescending ? "desc" : "asc"}`);
        }
      }}
      ariaLabels={{ tableLabel: "Library jobs" }}
      columnDefinitions={columnDefinitions}
    />
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
