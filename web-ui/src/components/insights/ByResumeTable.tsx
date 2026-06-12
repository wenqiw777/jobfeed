import type { InsightsOverviewResponse } from "@/api/queries";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";

type ByResume = InsightsOverviewResponse["applications"]["by_resume"];

const COLUMNS = ["sent", "responses", "interviews", "offers", "rejections"] as const;

/**
 * Per-resume-variant outcomes for the window, busiest variant first.
 * Applications recorded without a variant ride under the empty-string
 * key — labeled "unknown" here.
 */
export function ByResumeTable({ byResume }: { byResume: ByResume }) {
  const rows = Object.entries(byResume).sort(([, a], [, b]) => b.sent - a.sent);
  if (rows.length === 0) {
    return (
      <ChartCard title="By resume">
        <ChartEmpty>
          No applications in this window — record one with a variant and outcomes track here.
        </ChartEmpty>
      </ChartCard>
    );
  }
  return (
    <ChartCard title="By resume">
      <table className="w-full text-compact">
        <thead>
          <tr className="border-b border-border text-left">
            <th className="py-1.5 pr-2 text-label font-medium uppercase tracking-wide text-mute">
              Variant
            </th>
            {COLUMNS.map((column) => (
              <th
                key={column}
                className="py-1.5 pl-2 text-right text-label font-medium uppercase tracking-wide text-mute"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([variant, stats]) => (
            <tr key={variant} className="border-b border-hairline">
              <td className="py-1.5 pr-2 font-medium text-ink">
                {variant === "" ? "unknown" : variant}
              </td>
              {COLUMNS.map((column) => (
                <td key={column} className="py-1.5 pl-2 text-right font-mono text-ink-2">
                  {stats[column]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </ChartCard>
  );
}
