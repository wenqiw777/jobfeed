import type { InsightsOverviewResponse } from "@/api/queries";

/**
 * The funnel's headline numbers. Totals are ALL-TIME (backend contract);
 * only the applications figure is windowed — each cell says which.
 */
export function KpiCards({ overview }: { overview: InsightsOverviewResponse }) {
  const { totals, applications, window_days: windowDays } = overview;
  const items = [
    { label: "Discovered", value: totals.jobs, meta: "all-time" },
    { label: "Gate passed", value: totals.ml_gate_passed, meta: "all-time" },
    { label: "Evaluated", value: totals.evaluated, meta: "all-time" },
    { label: "Applied", value: totals.applied, meta: "all-time" },
    {
      label: "Applications",
      value: applications.applied_count,
      meta: `last ${windowDays}d`,
    },
  ];
  return (
    <section
      aria-label="Key numbers"
      className="grid grid-cols-2 gap-px overflow-hidden rounded-panel border border-border bg-border sm:grid-cols-5"
    >
      {items.map(({ label, value, meta }) => (
        <div key={label} className="bg-surface px-4 py-3">
          <p className="text-label uppercase tracking-wide text-mute">{label}</p>
          <p className="mt-0.5 font-mono text-h2 text-ink">{value.toLocaleString()}</p>
          <p className="text-micro text-mute">{meta}</p>
        </div>
      ))}
    </section>
  );
}
