import LineChart from "@cloudscape-design/components/line-chart";

import type { LLMDailyStatsRow } from "@/api/queries";
import { CHART_I18N, countDomain } from "@/components/insights/chartProps";
import { ChartCard, ChartEmpty } from "@/components/insights/ChartCard";
import { dayTick } from "@/lib/dates";

const compactTokenFormatter = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function tokenTick(value: number): string {
  return compactTokenFormatter.format(value);
}

function exactTokens(value: number): string {
  return `${Math.round(value).toLocaleString("en-US")} tokens`;
}

export function DailyTokens({ stats }: { stats: LLMDailyStatsRow[] }) {
  const byDay = new Map<string, { weightedTokens: number; calls: number }>();
  for (const row of stats) {
    const calls = Number.isFinite(row.call_count) && row.call_count > 0 ? row.call_count : 1;
    const value = byDay.get(row.day) ?? { weightedTokens: 0, calls: 0 };
    value.weightedTokens += (row.avg_input_tokens + row.avg_output_tokens) * calls;
    value.calls += calls;
    byDay.set(row.day, value);
  }
  const data = [...byDay].map(([day, value]) => ({ day, tokens: value.weightedTokens / value.calls }));
  if (data.length === 0) return <ChartCard title="Daily average tokens per model call"><ChartEmpty>No token data in this time range.</ChartEmpty></ChartCard>;
  return <ChartCard title="Daily average tokens per model call"><LineChart
    ariaLabel="Daily average tokens per model call" ariaDescription="Daily average input and output tokens per model call."
    i18nStrings={CHART_I18N} height={100} hideFilter hideLegend xScaleType="categorical" xDomain={data.map((row) => row.day)}
    yDomain={countDomain(data.map((row) => row.tokens))} xTickFormatter={dayTick} yTitle="Tokens"
    yTickFormatter={tokenTick}
    series={[{
      title: "Average tokens",
      type: "line",
      valueFormatter: exactTokens,
      data: data.map((row) => ({ x: row.day, y: row.tokens })),
    }]}
  /></ChartCard>;
}
