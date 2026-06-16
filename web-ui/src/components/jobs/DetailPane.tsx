import { useJobDetail, type TransitionStatus } from "@/api/queries";
import { JdPasteCard } from "@/components/jobs/JdPasteCard";
import { FollowupSection, NotesSection } from "@/components/jobs/DetailNotes";
import { EvaluationSections, TwinsLine } from "@/components/jobs/DetailSections";
import { VerdictPill } from "@/components/jobs/VerdictPill";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatRelativeAge } from "@/lib/dates";

export type DecideStatus = Extract<TransitionStatus, "shortlisted" | "archived" | "ignored">;

interface DetailPaneProps {
  jobId: string | null;
  /** Capability flags, not zone names — Library reuses the pane (T11). */
  showJdPaste: boolean;
  /** Pending-JD dismiss-as-junk. Independent of the decide trio: an
   * un-scored posting can't be meaningfully applied/shortlisted/skipped,
   * so pending_jd shows ONLY paste-JD + Ignore. */
  showIgnore: boolean;
  /** Triage decide actions (Apply/Shortlist/Skip). Pipeline turns them
   * off: its rows are post-decision; status moves happen through
   * interviews and restore instead. */
  showDecide?: boolean;
  isDeciding?: boolean;
  onDecide?: (to: DecideStatus) => void;
  onOpenApply?: () => void;
  /** Zone-specific sections rendered after the header — the seam that
   * keeps the pane zone-agnostic (Pipeline mounts InterviewPanel and the
   * restore card here based on the selected row's status). */
  extraSections?: React.ReactNode;
  /** Nothing-selected message; defaults to the triage keyboard hint. */
  emptyHint?: React.ReactNode;
}

/** Persistent right pane: detail aggregation for the active row. */
export function DetailPane({
  jobId,
  showJdPaste,
  showIgnore,
  showDecide = true,
  isDeciding = false,
  onDecide,
  onOpenApply,
  extraSections,
  emptyHint = "Select a row — or move with j/k and decide with a/h/s.",
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
          {status.status !== null && <span className="font-mono">[{status.status}]</span>}
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
        {(showDecide || showIgnore) && (
          <div className="mt-2.5 flex items-center gap-1.5">
            {showDecide && (
              <>
                <Button variant="primary" size="sm" disabled={isDeciding} onClick={onOpenApply}>
                  Apply
                </Button>
                <Button size="sm" disabled={isDeciding} onClick={() => onDecide?.("shortlisted")}>
                  Shortlist
                </Button>
                <Button size="sm" disabled={isDeciding} onClick={() => onDecide?.("archived")}>
                  Skip
                </Button>
              </>
            )}
            {showIgnore && (
              <Button size="sm" disabled={isDeciding} onClick={() => onDecide?.("ignored")}>
                Ignore
              </Button>
            )}
          </div>
        )}
      </header>
      {showJdPaste && <JdPasteCard jobId={job.id} url={job.url} />}
      {extraSections}
      <EvaluationSections evaluation={evaluation} />
      <NotesSection jobId={job.id} notes={status.notes} />
      <FollowupSection jobId={job.id} current={status.next_followup_at} />
    </div>
  );
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
