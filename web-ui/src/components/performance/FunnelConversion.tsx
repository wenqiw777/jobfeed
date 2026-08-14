import ProgressBar from "@cloudscape-design/components/progress-bar";
import SpaceBetween from "@cloudscape-design/components/space-between";

import type { FunnelRow } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

function aggregate(funnel: FunnelRow[]) {
  if (funnel.length === 0) return [];
  return [
    { step: "Candidates", count: funnel.reduce((sum, row) => sum + row.total_candidates, 0) },
    { step: "Passed local filter", count: funnel.reduce((sum, row) => sum + row.after_gate, 0) },
    { step: "Evaluated", count: funnel.reduce((sum, row) => sum + row.scored, 0) },
  ];
}

export function FunnelConversion({ funnel }: { funnel: FunnelRow[] }) {
  const data = aggregate(funnel);
  if (data.length === 0) return <ChartCard title="Evaluation conversion"><ChartEmpty>No evaluation funnel data in this time range.</ChartEmpty></ChartCard>;
  const candidates = data[0]?.count ?? 0;
  return (
    <ChartCard title="Evaluation conversion">
      <SpaceBetween size="m">
        {data.map((row) => {
          const value = candidates === 0 ? 0 : (row.count / candidates) * 100;
          return (
            <ProgressBar
              key={row.step}
              ariaLabel={`${row.step}: ${row.count.toLocaleString()} jobs`}
              label={row.step}
              description={`${row.count.toLocaleString()} jobs`}
              additionalInfo={`${Math.round(value)}% of candidates`}
              value={value}
            />
          );
        })}
      </SpaceBetween>
    </ChartCard>
  );
}
