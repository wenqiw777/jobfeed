import type { PerformanceOverviewResponse } from "@/api/queries";

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatCost(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

function formatPct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

/** Delta arrow with directional coloring. For cost and errors, down is
 * good (green) and up is bad (red). Duration is neutral (just show it).
 * The backend sends raw ratios ((cur − prev) / prev, e.g. -0.08 for an
 * 8% drop), so scale to percent before formatting. */
function DeltaArrow({
  delta,
  goodDirection,
}: {
  delta: number | null;
  goodDirection: "down" | "neutral";
}) {
  if (delta === null) return null;
  const isUp = delta > 0;
  const arrow = isUp ? "↑" : "↓";
  const abs = Math.abs(delta) * 100;
  const pct = abs >= 1 ? `${Math.round(abs)}%` : `${abs.toFixed(1)}%`;
  let colorClass = "text-mute";
  if (goodDirection === "down") {
    colorClass = isUp ? "text-danger" : "text-apply";
  }
  return (
    <span className={`font-mono text-micro ${colorClass}`}>
      {arrow}{pct}
    </span>
  );
}

export function KpiCards({ overview }: { overview: PerformanceOverviewResponse }) {
  const items = [
    {
      label: "Avg scan",
      value: formatDuration(overview.avg_scan_duration_ms),
      delta: overview.scan_duration_delta,
      goodDirection: "neutral" as const,
    },
    {
      label: "Avg eval",
      value: formatDuration(overview.avg_eval_duration_ms),
      delta: overview.eval_duration_delta,
      goodDirection: "neutral" as const,
    },
    {
      label: "LLM cost",
      value: formatCost(overview.total_llm_cost_usd),
      delta: overview.cost_delta,
      goodDirection: "down" as const,
    },
    {
      label: "Error rate",
      value: formatPct(overview.error_rate),
      delta: overview.error_rate_delta,
      goodDirection: "down" as const,
    },
  ];
  return (
    <section
      aria-label="Performance KPIs"
      className="grid grid-cols-2 gap-px overflow-hidden rounded-panel border border-border bg-border sm:grid-cols-4"
    >
      {items.map(({ label, value, delta, goodDirection }) => (
        <div key={label} className="bg-surface px-4 py-3">
          <p className="text-label uppercase tracking-wide text-mute">{label}</p>
          <p className="mt-0.5 font-mono text-h2 text-ink">{value}</p>
          <DeltaArrow delta={delta} goodDirection={goodDirection} />
        </div>
      ))}
    </section>
  );
}
