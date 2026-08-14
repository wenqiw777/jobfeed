import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import StatusIndicator from "@cloudscape-design/components/status-indicator";

import type { PerformanceOverviewResponse } from "@/api/queries";

function formatDuration(ms: number) {
  if (ms < 1_000) return `${Math.round(ms)}ms`;

  const seconds = ms / 1_000;
  if (seconds < 86_400) return `${seconds.toFixed(1)}s`;

  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (hours < 24) return `${hours}h ${remainingMinutes}m`;

  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h ${remainingMinutes}m`;
}
function formatCost(usd: number) { return `$${usd.toFixed(2)}`; }
function formatPct(rate: number) { return `${(rate * 100).toFixed(1)}%`; }

/** Delta label; cost and error increases are negative operational signals. */
function DeltaArrow({ delta, goodDirection }: { delta: number | null; goodDirection: "down" | "neutral" }) {
  if (delta === null) return null;
  const isUp = delta > 0;
  const abs = Math.abs(delta) * 100;
  const pct = abs >= 1 ? `${Math.round(abs)}%` : `${abs.toFixed(1)}%`;
  const type = goodDirection === "down" ? (isUp ? "error" : "success") : "info";
  return <StatusIndicator type={type}>{isUp ? "Increased" : "Decreased"} {pct}</StatusIndicator>;
}

/** Cloudscape KPI summary for operational performance. */
export function KpiCards({ overview }: { overview: PerformanceOverviewResponse }) {
  const items = [
    { label: "Average scan time", value: formatDuration(overview.avg_scan_duration_ms), delta: overview.scan_duration_delta, goodDirection: "neutral" as const },
    { label: "Average evaluation time", value: formatDuration(overview.avg_eval_duration_ms), delta: overview.eval_duration_delta, goodDirection: "neutral" as const },
    { label: "Model cost", value: formatCost(overview.total_llm_cost_usd), delta: overview.cost_delta, goodDirection: "down" as const },
    { label: "Error rate", value: formatPct(overview.error_rate), delta: overview.error_rate_delta, goodDirection: "down" as const },
  ];
  return (
    <section aria-label="Performance summary">
      <Container header={<Header variant="h2">Performance summary</Header>}>
        <ColumnLayout columns={4} minColumnWidth={160} borders="vertical">
          {items.map(({ label, value, delta, goodDirection }) => (
            <div key={label}>
              <Box variant="awsui-key-label">{label}</Box>
              <Box variant="awsui-value-large" display="block" fontWeight="bold">{value}</Box>
              <DeltaArrow delta={delta} goodDirection={goodDirection} />
            </div>
          ))}
        </ColumnLayout>
      </Container>
    </section>
  );
}
