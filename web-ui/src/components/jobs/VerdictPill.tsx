import Badge from "@cloudscape-design/components/badge";
import type { BadgeProps } from "@cloudscape-design/components/badge";

export type DisplayVerdict = "apply" | "consider" | "skip" | "below_threshold" | "unscored";

export function displayVerdict(
  verdict: string | null,
  stageBStatus: string | null,
): DisplayVerdict {
  if (stageBStatus === "skipped_below_threshold") return "below_threshold";
  if (verdict === "apply" || verdict === "consider" || verdict === "skip") return verdict;
  return "unscored";
}

const LABELS: Record<DisplayVerdict, string> = {
  apply: "Apply",
  consider: "Consider",
  skip: "Skip",
  below_threshold: "Below threshold",
  unscored: "Not evaluated",
};

const COLORS: Record<DisplayVerdict, NonNullable<BadgeProps["color"]>> = {
  apply: "green",
  consider: "blue",
  skip: "grey",
  below_threshold: "grey",
  unscored: "grey",
};

/** Cloudscape badge for one normalized evaluation verdict. */
export function VerdictPill({
  verdict,
  stageBStatus,
}: {
  verdict: string | null;
  stageBStatus: string | null;
}) {
  const display = displayVerdict(verdict, stageBStatus);
  return <Badge color={COLORS[display]}>{LABELS[display]}</Badge>;
}
