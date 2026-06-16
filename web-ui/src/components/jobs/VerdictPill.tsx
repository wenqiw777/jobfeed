import { cn } from "@/lib/utils";

/**
 * Verdict trio plus two derived display states: below_threshold (not a
 * stored verdict — derived from stage_b_status, plan A2) and unscored.
 */
export type DisplayVerdict = "apply" | "consider" | "skip" | "below_threshold" | "unscored";

export function displayVerdict(
  verdict: string | null,
  stageBStatus: string | null,
): DisplayVerdict {
  if (stageBStatus === "skipped_below_threshold") {
    return "below_threshold";
  }
  if (verdict === "apply" || verdict === "consider" || verdict === "skip") {
    return verdict;
  }
  return "unscored";
}

// Tinted bg + same-hue dark text (DESIGN.md: never gray text on color).
// below_threshold shares the skip family; unscored is faint-only meta.
const STYLES: Record<DisplayVerdict, string> = {
  apply: "bg-apply-bg text-apply",
  consider: "bg-consider-bg text-consider",
  skip: "bg-skip-bg text-skip",
  below_threshold: "bg-skip-bg text-skip",
  unscored: "bg-bg text-faint",
};

const LABELS: Record<DisplayVerdict, string> = {
  apply: "apply",
  consider: "consider",
  skip: "skip",
  // Two words so it never reads as the LLM "skip" verdict.
  below_threshold: "below threshold",
  unscored: "unscored",
};

export function VerdictPill({
  verdict,
  stageBStatus,
}: {
  verdict: string | null;
  stageBStatus: string | null;
}) {
  const display = displayVerdict(verdict, stageBStatus);
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center whitespace-nowrap rounded-pill px-2 py-px text-micro font-medium",
        STYLES[display],
      )}
    >
      {LABELS[display]}
    </span>
  );
}
