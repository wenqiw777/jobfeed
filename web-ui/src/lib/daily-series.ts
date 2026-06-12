/**
 * Zero-fill for the Insights daily series. The API sends sparse UTC day
 * buckets (only days having data appear); the timeline chart needs a
 * continuous axis, so missing days are filled with zeros client-side.
 */

const DAY_MS = 24 * 60 * 60 * 1000;

/** Structural twin of InsightsDayEntry — keeps lib/ free of api imports. */
export interface DailyPoint {
  /** UTC day, YYYY-MM-DD. */
  day: string;
  discovered: number;
  evaluated: number;
  applied: number;
}

function utcDayKey(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/**
 * Expand `daily` to one entry per UTC day over the trailing `windowDays`
 * ending today, oldest first. Days absent from the payload become zero
 * rows; payload days outside the window are dropped. The server window
 * is timestamp-based (col >= now() - N days), so the payload can span
 * N+1 UTC day buckets — the N-day axis deliberately drops that partial
 * oldest bucket, and it relies on the client clock being roughly in
 * sync with the server's. `now` is injectable so tests pin a
 * deterministic clock.
 */
export function zeroFillDaily(
  daily: readonly DailyPoint[],
  windowDays: number,
  now: Date = new Date(),
): DailyPoint[] {
  const byDay = new Map(daily.map((entry) => [entry.day, entry]));
  const todayMs = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const filled: DailyPoint[] = [];
  for (let back = windowDays - 1; back >= 0; back -= 1) {
    const day = utcDayKey(todayMs - back * DAY_MS);
    filled.push(byDay.get(day) ?? { day, discovered: 0, evaluated: 0, applied: 0 });
  }
  return filled;
}
