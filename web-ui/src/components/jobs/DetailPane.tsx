import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import { useState } from "react";

import { useJobDetail, useRealJobDetail, useSourceAuditDetail, type TransitionStatus } from "@/api/queries";
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
  realJob?: boolean;
  decisionView?: DecisionView;
  isDeciding?: boolean;
  onDecide?: (to: UserDecision) => void;
  emptyHint?: React.ReactNode;
}

/** Persistent Cloudscape detail pane for the active result. */
export function DetailPane({
  jobId,
  realJob = false,
  decisionView,
  isDeciding = false,
  onDecide,
  emptyHint = "Select a row to review its evidence and decide.",
}: DetailPaneProps) {
  const sourceDetail = useJobDetail(jobId, !realJob);
  const canonicalDetail = useRealJobDetail(jobId, realJob);
  const detail = realJob ? canonicalDetail : sourceDetail;

  if (jobId === null) return <Box padding="l">{emptyHint}</Box>;
  if (detail.isPending) {
    return <Box padding="l" textAlign="center"><Spinner size="large" /></Box>;
  }
  if (detail.isError) return <Alert type="error">{detail.error.message}</Alert>;

  const { job, evaluation, status } = detail.data;
  const reviewState = realJob && canonicalDetail.data !== undefined
    ? canonicalDetail.data.identity_review_state : "clear";
  const held = reviewState !== "clear";
  const staleReason = detail.data.evaluation_stale_reason;
  const staleScore = realJob && canonicalDetail.data !== undefined
    ? canonicalDetail.data.stale_stage_a_score : null;
  const stageA = held ? null : evaluation.stage_a;
  const stageB = held ? null : evaluation.stage_b;

  return (
    <SpaceBetween size="m">
      <Header variant="h3" description={job.title}>{job.company}</Header>
      {held && <Alert type="warning" header="Needs review">
        {reviewMessage(reviewState)} No current canonical score is shown until this job is reviewed.
      </Alert>}
      {staleReason && <Alert type="warning" header="Old score · re-evaluation pending">
        {staleReason === "legacy_policy_unknown"
          ? "The old score has no recorded model and policy version."
          : "The scoring model or policy changed."}
        {staleScore !== null && staleScore !== undefined
          ? ` Previous quick score: ${staleScore}.` : ""}
        {" "}Source audit remains available for historical source scores.
      </Alert>}
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
          { label: "Quick score", value: stageA?.score ?? "—" },
          { label: "Detailed fit score", value: stageB?.fit_score ?? "—" },
        ]}
      />
      <SpaceBetween direction="horizontal" size="xs">
        <VerdictPill
          verdict={stageB?.verdict ?? null}
          stageBStatus={held ? null : evaluation.stage_b_status}
        />
        <Link href={job.url} external externalIconAriaLabel="Opens in a new tab">
          Open posting
        </Link>
      </SpaceBetween>
      {realJob && canonicalDetail.data !== undefined ? (
        <SpaceBetween size="xxs">
          <Box variant="strong">
            Seen on {canonicalDetail.data.sources.length} {canonicalDetail.data.sources.length === 1 ? "source" : "sources"}
          </Box>
          {canonicalDetail.data.sources.map((source) => (
            <SpaceBetween key={source.job_id} direction="horizontal" size="xs">
              <Link href={source.url} external externalIconAriaLabel="Opens in a new tab">
                {source.platform} · {source.title}
              </Link>
              {source.apply_url && (
                <Link href={source.apply_url} external externalIconAriaLabel="Opens in a new tab">
                  Apply link
                </Link>
              )}
              <SourceAudit jobId={source.job_id} />
            </SpaceBetween>
          ))}
        </SpaceBetween>
      ) : sourceDetail.data !== undefined ? <TwinsLine twins={sourceDetail.data.twins} /> : null}
      {decisionView !== undefined && (
        <DecisionActions
          currentView={decisionView}
          isDeciding={isDeciding}
          onDecide={onDecide}
        />
      )}
      {!held && <EvaluationSections evaluation={evaluation} />}
    </SpaceBetween>
  );
}

function SourceAudit({ jobId }: { jobId: string }) {
  const [open, setOpen] = useState(false);
  const audit = useSourceAuditDetail(jobId, open);
  return (
    <SpaceBetween size="xxs">
      <Button variant="inline-link" onClick={() => setOpen((value) => !value)}>
        {open ? "Hide source audit" : "View source audit"}
      </Button>
      {open && audit.isPending && <Spinner size="normal" />}
      {open && audit.isError && <Alert type="error">Source audit unavailable</Alert>}
      {open && audit.data && <Box variant="small">
        Source audit score: {audit.data.evaluation.stage_b?.fit_score
          ?? audit.data.evaluation.stage_a?.score ?? "none"}
        {audit.data.evaluation.stage_b?.verdict
          ? ` · ${audit.data.evaluation.stage_b.verdict}` : ""}
        . Historical source evaluation only.
      </Box>}
    </SpaceBetween>
  );
}

function reviewMessage(state: string): string {
  if (state === "evaluation_conflict" || state === "evaluation_input_conflict"
    || state === "input_conflict") return "Source evaluations or inputs conflict.";
  if (state === "evaluation_input_missing" || state === "input_missing") {
    return "The canonical evaluation input is missing.";
  }
  return `Identity review state: ${state}.`;
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
