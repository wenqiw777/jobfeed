import Table from "@cloudscape-design/components/table";

import type { StepTimingRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

function aggregate(timings: StepTimingRow[]) {
  const bySource = new Map<string, { total: number; count: number }>();
  for (const row of timings.filter((item) => item.step_type === "source_fetch")) {
    const value = bySource.get(row.step_name) ?? { total: 0, count: 0 };
    value.total += row.elapsed_ms;
    value.count += 1;
    bySource.set(row.step_name, value);
  }
  return [...bySource]
    .map(([source, value]) => ({
      source,
      seconds: value.total / value.count / 1000,
    }))
    .sort((a, b) => b.seconds - a.seconds);
}

export function ScanSourceDuration({ timings }: { timings: StepTimingRow[] }) {
  const data = aggregate(timings);
  if (data.length === 0) return <ChartCard title="Scan time by source"><ChartEmpty>No scan timing data in this time range.</ChartEmpty></ChartCard>;
  return (
    <ChartCard title="Scan time by source">
      <Table
        variant="embedded"
        contentDensity="compact"
        wrapLines
        items={data}
        trackBy="source"
        ariaLabels={{ tableLabel: "Scan time by source" }}
        columnDefinitions={[
          {
            id: "source",
            header: "Source",
            width: 100,
            cell: (row) => row.source,
          },
          {
            id: "duration",
            header: "Average",
            width: 100,
            cell: (row) => `${row.seconds.toFixed(1)}s`,
          },
        ]}
      />
    </ChartCard>
  );
}
