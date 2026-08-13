import { useRef, useState } from "react";
import Button from "@cloudscape-design/components/button";

import {
  useAddInterview,
  useCompleteInterview,
  useInterviews,
  type InterviewRound,
} from "@/api/queries";
import { SectionLabel } from "@/components/jobs/DetailSections";
import { Input } from "@/components/ui/input";
import { toast } from "@/components/ui/use-toast";
import { dateTimeInputToIso } from "@/lib/dates";

/**
 * Interview rounds for an interviewing job: list, add (label + optional
 * scheduled time), complete with notes. Mounted by the Pipeline zone via
 * DetailPane's extraSections seam.
 */
export function InterviewPanel({ jobId }: { jobId: string }) {
  const interviews = useInterviews(jobId);
  const rounds = interviews.data?.interviews ?? [];

  return (
    <section className="border-b border-hairline px-4 py-3" aria-label="Interviews">
      <SectionLabel>Interviews</SectionLabel>
      {interviews.isError && (
        <p className="text-body-sm text-danger">{interviews.error.message}</p>
      )}
      {rounds.length > 0 && (
        <ul className="mb-2 flex flex-col gap-2">
          {rounds.map((round) => (
            <RoundItem key={round.round_index} jobId={jobId} round={round} />
          ))}
        </ul>
      )}
      <AddRoundForm jobId={jobId} />
    </section>
  );
}

function RoundItem({ jobId, round }: { jobId: string; round: InterviewRound }) {
  return (
    <li className="text-body-sm">
      <p className="flex flex-wrap items-center gap-x-1.5">
        <span className="font-mono text-micro text-mute">#{round.round_index}</span>
        <span className="font-medium text-ink">{round.label}</span>
        {round.scheduled_at !== null && (
          <span className="font-mono text-micro text-mute">
            {new Date(round.scheduled_at).toLocaleString()}
          </span>
        )}
        {round.completed_at !== null && (
          <span className="rounded-pill bg-apply-bg px-2 py-px text-micro font-medium text-apply">
            done
          </span>
        )}
      </p>
      {round.completed_at !== null ? (
        round.notes !== null &&
        round.notes !== "" && (
          <p className="mt-0.5 whitespace-pre-wrap text-body-sm text-ink-2">{round.notes}</p>
        )
      ) : (
        <CompleteForm jobId={jobId} roundIndex={round.round_index} />
      )}
    </li>
  );
}

function CompleteForm({ jobId, roundIndex }: { jobId: string; roundIndex: number }) {
  const [notes, setNotes] = useState("");
  const complete = useCompleteInterview();

  const submit = () => {
    if (complete.isPending) {
      return;
    }
    const trimmed = notes.trim();
    complete.mutate(
      { id: jobId, roundIndex, notes: trimmed === "" ? null : trimmed },
      {
        onError: (error) =>
          toast({ variant: "destructive", title: "Complete failed", description: error.message }),
      },
    );
  };

  return (
    <div className="mt-1 flex items-start gap-1.5">
      <textarea
        value={notes}
        onChange={(event) => setNotes(event.target.value)}
        rows={1}
        placeholder="How did it go?"
        aria-label={`Notes for round ${roundIndex}`}
        className="w-full resize-y rounded-control border border-border-strong bg-surface px-2.5 py-1 text-body-sm text-ink placeholder:text-mute focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      />
      <Button disabled={complete.isPending} onClick={submit}>
        Mark done
      </Button>
    </div>
  );
}

function AddRoundForm({ jobId }: { jobId: string }) {
  const [label, setLabel] = useState("");
  const scheduledRef = useRef<HTMLInputElement>(null);
  const add = useAddInterview();

  const submit = () => {
    const trimmed = label.trim();
    if (trimmed === "" || add.isPending) {
      return;
    }
    add.mutate(
      {
        id: jobId,
        label: trimmed,
        scheduledAt: dateTimeInputToIso(scheduledRef.current?.value ?? ""),
      },
      {
        onSuccess: () => {
          setLabel("");
          if (scheduledRef.current !== null) {
            scheduledRef.current.value = "";
          }
        },
        onError: (error) =>
          toast({ variant: "destructive", title: "Add round failed", description: error.message }),
      },
    );
  };

  return (
    <form
      className="flex flex-wrap items-center gap-1.5"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <Input
        value={label}
        onChange={(event) => setLabel(event.target.value)}
        placeholder="Round label…"
        aria-label="Round label"
        className="h-7 w-36"
      />
      <input
        ref={scheduledRef}
        type="datetime-local"
        aria-label="Scheduled time"
        className="h-7 rounded-control border border-border-strong bg-surface px-2 font-mono text-micro text-ink focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      />
      <Button formAction="submit" disabled={add.isPending || label.trim() === ""}>
        Add round
      </Button>
    </form>
  );
}
