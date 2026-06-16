import { useState } from "react";

import { useApply, type JobSummary } from "@/api/queries";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { toast } from "@/components/ui/use-toast";

interface ApplyDialogProps {
  job: JobSummary | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Fired on success so triage collapses the row and advances. */
  onApplied: (id: string) => void;
}

/** Record-an-application form: optional file snapshots + audit fields. */
export function ApplyDialog({ job, open, onOpenChange, onApplied }: ApplyDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            Apply — {job === null ? "" : `${job.company} · ${job.title}`}
          </DialogTitle>
          <DialogDescription>
            Records the application with resume snapshots; nothing is submitted anywhere.
          </DialogDescription>
        </DialogHeader>
        {/* Keyed on the job: closing (job -> null) or retargeting remounts a
            blank form, so cancelled fields and file inputs never ride into
            another job's application audit. */}
        <ApplyForm
          key={job?.id ?? "closed"}
          job={job}
          onOpenChange={onOpenChange}
          onApplied={onApplied}
        />
      </DialogContent>
    </Dialog>
  );
}

interface ApplyFormProps {
  job: JobSummary | null;
  onOpenChange: (open: boolean) => void;
  onApplied: (id: string) => void;
}

function ApplyForm({ job, onOpenChange, onApplied }: ApplyFormProps) {
  const [tailored, setTailored] = useState<File | null>(null);
  const [coverLetter, setCoverLetter] = useState<File | null>(null);
  const [variant, setVariant] = useState("");
  const [method, setMethod] = useState("");
  const [notes, setNotes] = useState("");
  const apply = useApply();

  const submit = () => {
    if (job === null || apply.isPending) {
      return;
    }
    // Multipart per D8: files ride as uploads, fields as form values;
    // empty values stay off the wire (the API treats them as absent).
    const form = new FormData();
    if (tailored !== null) form.append("tailored", tailored);
    if (coverLetter !== null) form.append("cover_letter", coverLetter);
    if (variant.trim() !== "") form.append("variant", variant.trim());
    if (method.trim() !== "") form.append("method", method.trim());
    if (notes.trim() !== "") form.append("notes", notes.trim());
    apply.mutate(
      { id: job.id, form },
      {
        onSuccess: (result) => {
          toast({
            title: result.applied ? "Application recorded" : "Already applied — no changes",
            description: result.reapply_notice ?? undefined,
          });
          onOpenChange(false);
          onApplied(job.id);
        },
        onError: (error) => {
          toast({ variant: "destructive", title: "Apply failed", description: error.message });
        },
      },
    );
  };

  return (
    <>
      <div className="mt-3 flex flex-col gap-2.5">
        <FileField label="Tailored resume" onFile={setTailored} />
        <FileField label="Cover letter" onFile={setCoverLetter} />
        <label className="flex flex-col gap-1 text-label text-ink-2">
          Resume variant
          <Input value={variant} onChange={(e) => setVariant(e.target.value)} placeholder="e.g. backend" />
        </label>
        <label className="flex flex-col gap-1 text-label text-ink-2">
          Method
          <Input value={method} onChange={(e) => setMethod(e.target.value)} placeholder="e.g. referral, portal" />
        </label>
        <label className="flex flex-col gap-1 text-label text-ink-2">
          Notes
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            className="w-full resize-y rounded-control border border-border-strong bg-surface px-2.5 py-1.5 text-body-sm text-ink placeholder:text-mute focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          />
        </label>
      </div>
      <DialogFooter>
        <Button onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" disabled={apply.isPending || job === null} onClick={submit}>
          {apply.isPending ? "Recording…" : "Record application"}
        </Button>
      </DialogFooter>
    </>
  );
}

function FileField({ label, onFile }: { label: string; onFile: (file: File | null) => void }) {
  return (
    <label className="flex flex-col gap-1 text-label text-ink-2">
      {label}
      <input
        type="file"
        onChange={(event) => onFile(event.target.files?.[0] ?? null)}
        className="text-body-sm text-ink-2 file:mr-2 file:rounded-control file:border file:border-border-strong file:bg-surface file:px-2.5 file:py-1 file:text-body-sm file:text-ink"
      />
    </label>
  );
}
