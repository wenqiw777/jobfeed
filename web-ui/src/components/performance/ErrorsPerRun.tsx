import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";

import type { RunSummary } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";
import { formatRunAxisDateTime } from "@/lib/dates";

export function ErrorsPerRun({ runs }: { runs: RunSummary[] }) {
  const data = runs
    .filter((run) => run.errors > 0 || run.status === "failed")
    .slice(0, 3)
    .map((run) => ({
      id: run.run_id,
      started: formatRunAxisDateTime(run.started_at),
      status: run.status,
      errors: run.errors,
    }));
  if (data.length === 0) {
    return (
      <ChartCard title="Recent run errors">
        <ChartEmpty>No errors in this time range.</ChartEmpty>
      </ChartCard>
    );
  }

  return (
    <ChartCard title="Recent run errors">
      <Table
        variant="embedded"
        items={data}
        trackBy="id"
        contentDensity="compact"
        wrapLines
        ariaLabels={{ tableLabel: "Recent run errors" }}
        columnDefinitions={[
          {
            id: "run",
            header: "Run",
            width: 140,
            isRowHeader: true,
            cell: (row) => (
              <SpaceBetween size="xxs">
                <Box variant="small">{row.started}</Box>
                <Badge color={row.status === "failed" ? "red" : "grey"}>
                  {row.status === "failed" ? "Failed" : "Completed"}
                </Badge>
              </SpaceBetween>
            ),
          },
          {
            id: "errors",
            header: "Errors",
            width: 64,
            cell: (row) => row.errors.toLocaleString(),
          },
        ]}
      />
    </ChartCard>
  );
}
