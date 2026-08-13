import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";

import type { RunSummary } from "@/api/queries";
import { formatLocalDateTime } from "@/lib/dates";

type CounterKey = Extract<
  keyof RunSummary,
  | "jobs_discovered"
  | "jobs_inserted"
  | "jobs_updated"
  | "jobs_filtered"
  | "jobs_ml_gated"
  | "jobs_scored"
  | "stage_a_scored"
  | "stage_b_scored"
  | "errors"
>;

const FULL_COUNTERS: { key: CounterKey; label: string }[] = [
  { key: "jobs_discovered", label: "discovered" },
  { key: "jobs_inserted", label: "inserted" },
  { key: "jobs_updated", label: "updated" },
  { key: "jobs_filtered", label: "filtered" },
  { key: "jobs_ml_gated", label: "gated" },
  { key: "jobs_scored", label: "scored" },
  { key: "stage_a_scored", label: "stage A" },
  { key: "stage_b_scored", label: "stage B" },
  { key: "errors", label: "errors" },
];

const SUMMARY_COUNTERS = FULL_COUNTERS.filter(({ key }) => key !== "jobs_scored");

interface RunHistoryTableProps {
  runs: RunSummary[];
  total: number;
  page: number;
  pageSize: number;
  isLoading: boolean;
  isPlaceholder: boolean;
  error: Error | null;
  hasActiveRuns: boolean;
  expandedId: string | null;
  onExpandedChange: (runId: string | null) => void;
  onPageChange: (page: number) => void;
}

/** Cloudscape table for finished and failed pipeline runs. */
export function RunHistoryTable(props: RunHistoryTableProps) {
  const pagesCount = Math.max(1, Math.ceil(props.total / props.pageSize));
  return (
    <SpaceBetween size="s">
      <Table
        items={props.runs}
        trackBy="run_id"
        loading={props.isLoading}
        loadingText="Loading runs"
        skeleton={{ totalRows: 6 }}
        contentDensity="compact"
        ariaLabels={{ tableLabel: "Run history" }}
        header={
          <Header
            variant="h2"
            counter={`(${props.total})`}
            description="Newest first. Expand a timestamp to inspect the complete counter set and run identity."
          >
            Run history
          </Header>
        }
        columnDefinitions={[
          {
            id: "started",
            header: "Started",
            width: 270,
            isRowHeader: true,
            verticalAlign: "top",
            cell: (run) => <StartedCell run={run} isExpanded={props.expandedId === run.run_id} onToggle={() => props.onExpandedChange(props.expandedId === run.run_id ? null : run.run_id)} />,
          },
          { id: "source", header: "Source", cell: (run) => run.source },
          { id: "status", header: "Status", cell: (run) => <RunStatus run={run} /> },
          {
            id: "activity",
            header: "Activity",
            cell: (run) => (
              <div data-testid={`run-activity-${run.run_id}`}><Activity run={run} /></div>
            ),
          },
          {
            id: "cost",
            header: "Cost",
            cell: (run) => run.total_llm_cost_usd > 0 ? formatCost(run.total_llm_cost_usd) : "—",
          },
        ]}
        pagination={
          <Pagination
            currentPageIndex={props.page + 1}
            pagesCount={pagesCount}
            pagesVariant="compact"
            disabled={props.isPlaceholder}
            ariaLabels={{
              paginationLabel: "Run history pages",
              previousPageLabel: "Prev",
              nextPageLabel: "Next",
              pageLabel: (page) => `Page ${page}`,
            }}
            onChange={({ detail }) => props.onPageChange(detail.currentPageIndex - 1)}
          />
        }
        empty={null}
      />
      {!props.isLoading && props.runs.length === 0 && (
        <EmptyHistory hasActiveRuns={props.hasActiveRuns} />
      )}
      <RangeLabel
        page={props.page}
        pageSize={props.pageSize}
        pageRows={props.runs.length}
        total={props.total}
      />
      {props.error !== null && <Alert type="error" header="Run history could not be loaded">{props.error.message}</Alert>}
    </SpaceBetween>
  );
}

function StartedCell({ run, isExpanded, onToggle }: { run: RunSummary; isExpanded: boolean; onToggle: () => void }) {
  return (
    <div data-testid={`run-row-${run.run_id}`}>
      <Button
        variant="inline-link"
        iconName={isExpanded ? "angle-down" : "angle-right"}
        ariaLabel={`Run started ${formatLocalDateTime(run.started_at)}`}
        ariaExpanded={isExpanded}
        onClick={onToggle}
      >
        {formatLocalDateTime(run.started_at)}
      </Button>
      {isExpanded && <RunDetail run={run} />}
    </div>
  );
}

function RunDetail({ run }: { run: RunSummary }) {
  return (
    <Box margin={{ top: "s" }}>
      <SpaceBetween size="s">
        <ColumnLayout columns={3} variant="text-grid">
          {FULL_COUNTERS.map(({ key, label }) => (
            <div key={key}><Box variant="awsui-key-label">{label}</Box><Box>{run[key]}</Box></div>
          ))}
          <div><Box variant="awsui-key-label">finished</Box><Box>{formatLocalDateTime(run.finished_at)}</Box></div>
        </ColumnLayout>
        <Box variant="code">{run.run_id}</Box>
      </SpaceBetween>
    </Box>
  );
}

function Activity({ run }: { run: RunSummary }) {
  const counters = SUMMARY_COUNTERS.filter(({ key }) => run[key] > 0);
  if (counters.length === 0) return <Box color="text-body-secondary">No counters</Box>;
  return (
    <SpaceBetween direction="horizontal" size="xxs">
      {counters.map(({ key, label }) => (
        <Badge key={key} color={key === "errors" ? "red" : "blue"}>{run[key]} {label}</Badge>
      ))}
    </SpaceBetween>
  );
}

function RunStatus({ run }: { run: RunSummary }) {
  if (run.errors > 0) return <StatusIndicator type="warning">{run.status}</StatusIndicator>;
  if (run.finished_at === null) return <StatusIndicator type="in-progress">{run.status}</StatusIndicator>;
  return <StatusIndicator type="success">{run.status}</StatusIndicator>;
}

function EmptyHistory({ hasActiveRuns }: { hasActiveRuns: boolean }) {
  return (
    <Box textAlign="center" color="inherit">
      <SpaceBetween size="xs">
        <b>{hasActiveRuns ? "First run in progress" : "No runs yet"}</b>
        <Box variant="p" color="text-body-secondary">
          {hasActiveRuns
            ? "Live counters appear above; completed evidence will land here."
            : <>Start Scan above or run <code>./scan</code> from the repo root.</>}
        </Box>
      </SpaceBetween>
    </Box>
  );
}

function RangeLabel({ page, pageSize, pageRows, total }: { page: number; pageSize: number; pageRows: number; total: number }) {
  if (total === 0) return <Box color="text-body-secondary">0 runs</Box>;
  return <Box color="text-body-secondary">{page * pageSize + 1}–{page * pageSize + pageRows} of {total}</Box>;
}

function formatCost(usd: number): string {
  return `$${usd.toFixed(usd > 0 && usd < 0.01 ? 4 : 2)}`;
}
