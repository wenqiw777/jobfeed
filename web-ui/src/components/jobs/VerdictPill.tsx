import Badge from "@cloudscape-design/components/badge";
import type { BadgeProps } from "@cloudscape-design/components/badge";

export type DisplayVerdict =
  | "strong_match"
  | "possible_match"
  | "weak_match"
  | "ineligible"
  | "apply"
  | "consider"
  | "skip"
  | "error"
  | "unscored";

export function displayVerdict(
  verdict: string | null,
  status: string | null,
): DisplayVerdict {
  if (status === "error") return "error";
  if (
    verdict === "strong_match"
    || verdict === "possible_match"
    || verdict === "weak_match"
    || verdict === "ineligible"
    || verdict === "apply"
    || verdict === "consider"
    || verdict === "skip"
  ) return verdict;
  return "unscored";
}

const LABELS: Record<DisplayVerdict, string> = {
  strong_match: "Strong match",
  possible_match: "Possible match",
  weak_match: "Weak match",
  ineligible: "Ineligible",
  apply: "Apply",
  consider: "Consider",
  skip: "Skip",
  error: "Evaluation error",
  unscored: "Not evaluated",
};

const COLORS: Record<DisplayVerdict, NonNullable<BadgeProps["color"]>> = {
  strong_match: "green",
  possible_match: "blue",
  weak_match: "grey",
  ineligible: "red",
  apply: "green",
  consider: "blue",
  skip: "grey",
  error: "red",
  unscored: "grey",
};

/** Cloudscape badge for one normalized evaluation verdict. */
export function VerdictPill({
  verdict,
  status,
}: {
  verdict: string | null;
  status: string | null;
}) {
  const display = displayVerdict(verdict, status);
  return <Badge color={COLORS[display]}>{LABELS[display]}</Badge>;
}
