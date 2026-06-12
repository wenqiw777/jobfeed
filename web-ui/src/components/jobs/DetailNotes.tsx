import { useState } from "react";

import { useFollowup, useNote } from "@/api/queries";
import { SectionLabel } from "@/components/jobs/DetailSections";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/use-toast";
import { dateInputToIso, followupAtFromNow } from "@/lib/dates";

/** Existing notes + an append box. data-kbd-target wires the `n` key. */
export function NotesSection({ jobId, notes }: { jobId: string; notes: string | null }) {
  const [text, setText] = useState("");
  const note = useNote();

  const save = () => {
    const trimmed = text.trim();
    if (trimmed === "" || note.isPending) {
      return;
    }
    note.mutate(
      { id: jobId, text: trimmed },
      {
        onSuccess: () => setText(""),
        onError: (error) =>
          toast({ variant: "destructive", title: "Note failed", description: error.message }),
      },
    );
  };

  return (
    <section className="border-b border-hairline px-4 py-3">
      <SectionLabel>Notes</SectionLabel>
      {notes !== null && notes !== "" && (
        <p className="mb-2 whitespace-pre-wrap text-body-sm text-ink-2">{notes}</p>
      )}
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        rows={2}
        placeholder="Add a note…"
        aria-label="Add a note"
        data-kbd-target="note"
        className="w-full resize-y rounded-control border border-border-strong bg-surface px-2.5 py-1.5 text-body-sm text-ink placeholder:text-mute focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      />
      <div className="mt-1.5">
        <Button size="sm" disabled={note.isPending || text.trim() === ""} onClick={save}>
          Save note
        </Button>
      </div>
    </section>
  );
}

const PRESETS: { label: string; days: number }[] = [
  { label: "7d", days: 7 },
  { label: "2w", days: 14 },
];

/** Follow-up presets + custom date. data-kbd-target wires the `f` key. */
export function FollowupSection({ jobId, current }: { jobId: string; current: string | null }) {
  const [customDate, setCustomDate] = useState("");
  const followup = useFollowup();

  const submit = (at: string) => {
    followup.mutate(
      { id: jobId, at },
      {
        onError: (error) =>
          toast({ variant: "destructive", title: "Follow-up failed", description: error.message }),
      },
    );
  };

  const submitCustom = () => {
    const iso = dateInputToIso(customDate);
    if (iso !== null) {
      submit(iso);
      setCustomDate("");
    }
  };

  return (
    <section className="px-4 py-3">
      <SectionLabel>Follow up</SectionLabel>
      <div className="flex flex-wrap items-center gap-1.5">
        {PRESETS.map((preset) => (
          <Button
            key={preset.label}
            size="sm"
            disabled={followup.isPending}
            onClick={() => submit(followupAtFromNow(preset.days))}
          >
            {preset.label}
          </Button>
        ))}
        <input
          type="date"
          value={customDate}
          onChange={(event) => setCustomDate(event.target.value)}
          onBlur={submitCustom}
          aria-label="Custom follow-up date"
          data-kbd-target="followup"
          className="h-7 rounded-control border border-border-strong bg-surface px-2 font-mono text-micro text-ink focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        />
        {current !== null && (
          <span className="font-mono text-micro text-mute">
            {/* Local date, not the UTC slice — a 9:00 local follow-up can
                land on the previous/next UTC day. */}
            due {new Date(current).toLocaleDateString()}
          </span>
        )}
      </div>
    </section>
  );
}
