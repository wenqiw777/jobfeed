import { useJobDetail, type TransitionStatus } from "@/api/queries";
import { EvaluationSections, TwinsLine } from "@/components/jobs/DetailSections";
import { VerdictPill } from "@/components/jobs/VerdictPill";
import { Skeleton } from "@/components/ui/skeleton";
import { formatRelativeAge } from "@/lib/dates";

export type UserDecision = Extract<TransitionStatus, "applied" | "shortlisted" | "ignored">;

interface DetailPaneProps {
  jobId: string | null;
  /** Results expose exactly the three user decisions. */
  showDecide?: boolean;
  isDeciding?: boolean;
  onDecide?: (to: UserDecision) => void;
  /** Nothing-selected message. */
  emptyHint?: React.ReactNode;
}

/** Persistent right pane: detail aggregation for the active row. */
export function DetailPane({
  jobId,
  showDecide = true,
  isDeciding = false,
  onDecide,
  emptyHint = "Select a row to review its evidence and decide.",
}: DetailPaneProps) {
  const detail = useJobDetail(jobId);

  if (jobId === null) {
    return <Empty>{emptyHint}</Empty>;
  }
  if (detail.isPending) {
    return <PaneSkeleton />;
  }
  if (detail.isError) {
    return <Empty>{detail.error.message}</Empty>;
  }

  const { job, evaluation, status, twins } = detail.data;
  const stageA = evaluation.stage_a;
  const stageB = evaluation.stage_b;

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <header className="border-b border-border px-4 py-3">
        <h2 className="text-h3 text-ink">{job.company}</h2>
        <p className="text-body-sm text-ink-2">{job.title}</p>
        <p className="mt-1 flex flex-wrap items-center gap-x-1.5 text-micro text-mute">
          {job.location !== "" && <span>{job.location}</span>}
          <span className="font-mono">{job.platform}</span>
          <span className="font-mono">{formatRelativeAge(job.posted_at ?? job.discovered_at)}</span>
          {status.status !== null && <span>{visibleDecision(status.status)}</span>}
          <a
            href={job.url}
            target="_blank"
            rel="noreferrer"
            className="text-accent hover:underline"
          >
            open posting ↗
          </a>
        </p>
        <TwinsLine twins={twins} />
        <div className="mt-2.5 flex items-center gap-4">
          <Score label="Stage A" value={stageA?.score ?? null} />
          <Score label="Fit" value={stageB?.fit_score ?? null} />
          <VerdictPill
            verdict={stageB?.verdict ?? null}
            stageBStatus={evaluation.stage_b_status}
          />
        </div>
        {showDecide && (
          <div className="mt-2.5 flex items-center gap-1.5">
            <Button variant="primary" disabled={isDeciding} onClick={() => onDecide?.("applied")}>
              Applied
            </Button>
            <Button disabled={isDeciding} onClick={() => onDecide?.("shortlisted")}>
              Wait
            </Button>
            <Button disabled={isDeciding} onClick={() => onDecide?.("ignored")}>
              Ignore
            </Button>
          </div>
        )}
      </header>
      <EvaluationSections evaluation={evaluation} />
    </div>
  );
}

function visibleDecision(status: string): string {
  if (["shortlisted", "awaiting_referral"].includes(status)) return "Wait";
  if (["applied", "interviewing", "offer", "rejected", "ghosted"].includes(status)) {
    return "Applied";
  }
  if (["ignored", "archived"].includes(status)) return "Ignored";
  return "Ready for decision";
}

function Score({ label, value }: { label: string; value: number | null }) {
  return (
    <span className="flex items-baseline gap-1">
      <span className="text-micro uppercase tracking-wide text-mute">{label}</span>
      <span className="font-mono text-body-sm font-medium text-ink">{value ?? "—"}</span>
    </span>
  );
}

function PaneSkeleton() {
  return (
    <div className="flex flex-col gap-3 px-4 py-3" aria-label="Loading detail">
      <Skeleton className="h-5 w-2/5" />
      <Skeleton className="h-4 w-3/5" />
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid h-full place-items-center px-6 text-center text-body-sm text-mute">
      {children}
    </div>
  );
}
import Button from "@cloudscape-design/components/button";
