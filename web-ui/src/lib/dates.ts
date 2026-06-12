/**
 * Compact relative-age formatting for the mono age column (e.g. "3d",
 * "12h"). `now` is injectable so tests pin a deterministic clock.
 */

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

/** Ages beyond this render as an absolute M/D/YY date. */
const DAY_CUTOFF = 30;

export function formatRelativeAge(iso: string | null, now: Date = new Date()): string {
  if (iso === null) {
    return "—";
  }
  const then = new Date(iso);
  const diffMs = now.getTime() - then.getTime();
  if (Number.isNaN(diffMs)) {
    return "—";
  }
  // Future timestamps (clock skew) collapse into "<1h" instead of negatives.
  if (diffMs < HOUR_MS) {
    return "<1h";
  }
  if (diffMs < DAY_MS) {
    return `${Math.round(diffMs / HOUR_MS)}h`;
  }
  // Count local calendar-day boundaries crossed, not raw 24h blocks, so two
  // postings from the same day always show the same "Xd".
  const nowMidnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const thenMidnight = new Date(then.getFullYear(), then.getMonth(), then.getDate());
  const days = Math.round((nowMidnight.getTime() - thenMidnight.getTime()) / DAY_MS);
  if (days <= DAY_CUTOFF) {
    return `${days}d`;
  }
  const yy = String(then.getFullYear()).slice(-2);
  return `${then.getMonth() + 1}/${then.getDate()}/${yy}`;
}

/** ISO timestamp `days` from now — the followup preset payload (D12). */
export function followupAtFromNow(days: number, now: Date = new Date()): string {
  return new Date(now.getTime() + days * DAY_MS).toISOString();
}

/**
 * `<input type="date">` value (YYYY-MM-DD) -> ISO timestamp at 09:00
 * LOCAL time. The API requires an offset-carrying datetime (naive -> 422);
 * toISOString always emits UTC `Z`, satisfying that.
 */
export function dateInputToIso(value: string): string | null {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) {
    return null;
  }
  return new Date(year, month - 1, day, 9, 0, 0).toISOString();
}
