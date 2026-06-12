import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";

import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

/**
 * Status → palette token. Recharts needs concrete colors; `rgb(var(--x))`
 * strings resolve through the CSS variables, so a future theme recolors
 * the slices too. Muted inks for pre-decision and terminal states, the
 * accent family for the active middle, verdict hues for outcome states.
 */
const STATUS_COLORS: Record<string, string> = {
  new: "rgb(var(--faint))",
  scored: "rgb(var(--skip))",
  shortlisted: "rgb(var(--accent))",
  awaiting_referral: "rgb(var(--accent-border))",
  applied: "rgb(var(--apply))",
  interviewing: "rgb(var(--consider))",
  offer: "rgb(var(--accent-hover))",
  rejected: "rgb(var(--danger))",
  ghosted: "rgb(var(--mute))",
  archived: "rgb(var(--ink-2))",
  ignored: "rgb(var(--border-strong))",
};

export function statusColor(status: string): string {
  return STATUS_COLORS[status] ?? "rgb(var(--mute))";
}

const INITIAL_DIMENSION = { width: 600, height: 200 };

/**
 * Workflow status distribution as a donut. Verdict-side groupings
 * (below_threshold, unscored) never appear here — they are evaluation
 * display states, not statuses. The HTML legend carries the counts so
 * they read (and test) without SVG layout.
 */
export function StatusDonut({ distribution }: { distribution: Record<string, number> }) {
  const slices = Object.entries(distribution)
    .filter(([, count]) => count > 0)
    .sort(([, a], [, b]) => b - a)
    .map(([status, count]) => ({ name: status, value: count }));

  if (slices.length === 0) {
    return (
      <ChartCard title="Statuses">
        <ChartEmpty>No tracked statuses yet — triage decisions land here.</ChartEmpty>
      </ChartCard>
    );
  }
  return (
    <ChartCard title="Statuses">
      <div className="h-52" data-testid="status-donut">
        {/* initialDimension: a real ResizeObserver corrects it on the
            first frame; environments without one (tests) still render. */}
        <ResponsiveContainer width="100%" height="100%" initialDimension={INITIAL_DIMENSION}>
          <PieChart>
            <Pie
              data={slices}
              dataKey="value"
              nameKey="name"
              innerRadius="55%"
              outerRadius="85%"
              stroke="rgb(var(--surface))"
              strokeWidth={1}
              isAnimationActive={false}
            >
              {slices.map((slice) => (
                <Cell key={slice.name} fill={statusColor(slice.name)} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {slices.map((slice) => (
          <li key={slice.name} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="h-2 w-2 rounded-pill"
              style={{ backgroundColor: statusColor(slice.name) }}
            />
            <span className="text-micro text-mute">{slice.name}</span>
            <span className="font-mono text-micro text-ink-2">{slice.value}</span>
          </li>
        ))}
      </ul>
    </ChartCard>
  );
}
