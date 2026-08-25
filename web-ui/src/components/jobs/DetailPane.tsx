import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";

import { useJobDetail, type TransitionStatus } from "@/api/queries";
import { EvaluationSections, TwinsLine } from "@/components/jobs/DetailSections";
import { VerdictPill } from "@/components/jobs/VerdictPill";
import { formatEstimatedPostedDate, formatRelativeAge } from "@/lib/dates";

export type UserDecision = Extract<
  TransitionStatus,
  "applied" | "shortlisted" | "ignored" | "scored"
>;
export type DecisionView = "results" | "wait" | "applied" | "ignored";

interface DetailPaneProps {
  jobId: string | null;
  decisionView?: DecisionView;
  isDeciding?: boolean;
  onDecide?: (to: UserDecision) => void;
  emptyHint?: React.ReactNode;
}

/** Persistent Cloudscape detail pane for the active result. */
export function DetailPane({
  jobId,
  decisionView,
  isDeciding = false,
  onDecide,
  emptyHint = "Select a row to review its evidence and decide.",
}: DetailPaneProps) {
  const detail = useJobDetail(jobId);

  if (jobId === null) return <Box padding="l">{emptyHint}</Box>;
  if (detail.isPending) {
    return <Box padding="l" textAlign="center"><Spinner size="large" /></Box>;
  }
  if (detail.isError) return <Alert type="error">{detail.error.message}</Alert>;

  const { job, evaluation, status, twins } = detail.data;

  return (
    <SpaceBetween size="m">
      <Header variant="h3" description={job.title}>{job.company}</Header>
      <KeyValuePairs
        columns={3}
        items={[
          { label: "Location", value: job.location || "—" },
          { label: "Source", value: job.platform },
          {
            label: "Posted",
            value: job.posted_at === null ? (
              <SpaceBetween size="xxs">
                <Box>{formatEstimatedPostedDate(job.discovered_at)}</Box>
                <Box variant="small" color="text-body-secondary">
                  Estimated from date added
                </Box>
              </SpaceBetween>
            ) : formatRelativeAge(job.posted_at),
          },
          { label: "Decision", value: visibleDecision(status.decision) },
          { label: "Match score", value: evaluation.match_score ?? "—" },
          { label: "ATS visibility", value: evaluation.ats_visibility_score ?? "—" },
          { label: "Eligibility", value: evaluation.eligibility_status ?? "—" },
          {
            label: "Evaluator",
            value: evaluation.evaluator_version ?? evaluation.model ?? "—",
          },
        ]}
      />
      <SpaceBetween direction="horizontal" size="xs">
        <VerdictPill
          verdict={evaluation.match_tier}
          status={null}
        />
        <Link href={job.url} external externalIconAriaLabel="Opens in a new tab">
          Open posting
        </Link>
      </SpaceBetween>
      <TwinsLine twins={twins} />
      {decisionView !== undefined && (
        <DecisionActions
          currentView={decisionView}
          isDeciding={isDeciding}
          onDecide={onDecide}
        />
      )}
      <EvaluationSections evaluation={evaluation} />
    </SpaceBetween>
  );
}

function DecisionActions({
  currentView,
  isDeciding,
  onDecide,
}: {
  currentView: DecisionView;
  isDeciding: boolean;
  onDecide?: (to: UserDecision) => void;
}) {
  const actions: { view: DecisionView; label: string; status: UserDecision }[] = [
    { view: "results", label: "Move to Results", status: "scored" },
    { view: "applied", label: "Mark as applied", status: "applied" },
    { view: "wait", label: "Move to Wait", status: "shortlisted" },
    { view: "ignored", label: "Ignore", status: "ignored" },
  ];
  return (
    <SpaceBetween direction="horizontal" size="xs">
      {actions.filter((action) => action.view !== currentView).map((action) => (
        <Button
          key={action.view}
          variant={action.view === "applied" ? "primary" : "normal"}
          disabled={isDeciding}
          onClick={() => onDecide?.(action.status)}
        >
          {action.label}
        </Button>
      ))}
    </SpaceBetween>
  );
}

function visibleDecision(decision: DecisionView | null): string {
  if (decision === "results") return "Ready for decision";
  if (decision === "wait") return "Wait";
  if (decision === "applied") return "Applied";
  if (decision === "ignored") return "Ignored";
  return "—";
}
