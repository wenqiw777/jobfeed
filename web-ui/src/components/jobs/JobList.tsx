import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Table, { type TableProps } from "@cloudscape-design/components/table";

import type { JobSummary } from "@/api/queries";
import { formatEstimatedPostedDate, formatRelativeAge } from "@/lib/dates";
import { useDensity } from "@/lib/density";

interface JobListProps {
  jobs: JobSummary[];
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
  const columnDefinitions: TableProps.ColumnDefinition<JobSummary>[] = [
    {
      id: "job",
      header: "Job",
      cell: (job) => (
        <span data-testid={`job-row-${job.id}`}>
          <Button
            variant="inline-link"
            ariaLabel={`Open ${job.company} ${job.title}`}
            onClick={() => onOpen(job.id)}
          >
            {job.company} · {job.title}
          </Button>
        </span>
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
      cell: (job) => job.evaluation_score ?? "—",
      width: 70,
    },
    {
      id: "posted",
      header: "Posted",
      sortingField: "posted",
      cell: (job) => (
        <Box color="text-body-secondary">
          {job.posted_at === null
            ? formatEstimatedPostedDate(job.discovered_at)
            : formatRelativeAge(job.posted_at)}
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

function VerdictBadge({ job }: { job: JobSummary }) {
  if (job.evaluation_status === "error") {
    return <Badge color="red">Evaluation error</Badge>;
  }
  if (job.evaluation_verdict === "strong_match") {
    return <Badge color="green">Strong match</Badge>;
  }
  if (job.evaluation_verdict === "possible_match") {
    return <Badge color="blue">Possible match</Badge>;
  }
  if (job.evaluation_verdict === "weak_match") {
    return <Badge color="grey">Weak match</Badge>;
  }
  if (job.evaluation_verdict === "ineligible") {
    return <Badge color="red">Ineligible</Badge>;
  }
  return <Badge color="grey">Not evaluated</Badge>;
}
