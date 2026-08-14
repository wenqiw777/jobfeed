import PieChart from "@cloudscape-design/components/pie-chart";

import type { FunnelRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

export function GatePassFail({ funnel }: { funnel: FunnelRow[] }) {
  if (funnel.length === 0) {
    return (
      <ChartCard title="Local filter results">
        <ChartEmpty>No local filter data in this time range.</ChartEmpty>
      </ChartCard>
    );
  }

  const data = [
    { result: "Passed", count: funnel.reduce((sum, row) => sum + row.after_gate, 0) },
    {
      result: "Excluded",
      count: funnel.reduce(
        (sum, row) => sum + Math.max(0, row.after_filter - row.after_gate),
        0,
      ),
    },
  ];
  const total = data.reduce((sum, row) => sum + row.count, 0);
  const passed = data[0]?.count ?? 0;
  const passRate = total === 0 ? 0 : Math.round((passed / total) * 100);
  return (
    <ChartCard title="Local filter results">
      <PieChart
        ariaLabel="Local filter results"
        ariaDescription="Total jobs passed or excluded by the local filter."
        variant="donut"
        size="small"
        hideFilter
        innerMetricValue={`${passRate}%`}
        data={data.map((row) => ({ title: row.result, value: row.count }))}
        segmentDescription={(segment) => `${segment.value.toLocaleString()} jobs`}
      />
    </ChartCard>
  );
}
