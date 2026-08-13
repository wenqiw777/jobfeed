import { useState } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FileUpload from "@cloudscape-design/components/file-upload";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Textarea from "@cloudscape-design/components/textarea";

import { useApply, type JobSummary } from "@/api/queries";
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
  // Cloudscape keeps a hidden dialog landmark mounted when `visible` is false.
  // Do not mount it at all so global queue shortcuts are suspended only while
  // the application form is actually open.
  if (!open) {
    return null;
  }
  return (
    <Modal
      visible={open}
      onDismiss={() => onOpenChange(false)}
      closeAriaLabel="Close"
      size="medium"
      header={`Apply — ${job === null ? "" : `${job.company} · ${job.title}`}`}
    >
      {/* Keyed on the job: closing (job -> null) or retargeting remounts a
          blank form, so cancelled fields and file inputs never ride into
          another job's application audit. */}
      <ApplyForm
        key={job?.id ?? "closed"}
        job={job}
        onOpenChange={onOpenChange}
        onApplied={onApplied}
      />
    </Modal>
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
    <div data-testid="cloudscape-apply-form">
      <SpaceBetween size="m">
        <Box color="text-body-secondary">
          Records the application with resume snapshots; nothing is submitted anywhere.
        </Box>
        <FileField label="Tailored resume" file={tailored} onFile={setTailored} />
        <FileField label="Cover letter" file={coverLetter} onFile={setCoverLetter} />
        <FormField label="Resume variant">
          <Input value={variant} onChange={({ detail }) => setVariant(detail.value)} placeholder="e.g. backend" />
        </FormField>
        <FormField label="Method">
          <Input value={method} onChange={({ detail }) => setMethod(detail.value)} placeholder="e.g. referral, portal" />
        </FormField>
        <FormField label="Notes">
          <Textarea value={notes} onChange={({ detail }) => setNotes(detail.value)} rows={3} />
        </FormField>
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button variant="primary" disabled={apply.isPending || job === null} onClick={submit}>
              {apply.isPending ? "Recording…" : "Record application"}
            </Button>
          </SpaceBetween>
        </Box>
      </SpaceBetween>
    </div>
  );
}

function FileField({ label, file, onFile }: { label: string; file: File | null; onFile: (file: File | null) => void }) {
  return (
    <FormField label={label}>
      <FileUpload
        value={file === null ? [] : [file]}
        onChange={({ detail }) => onFile(detail.value[0] ?? null)}
        multiple={false}
        i18nStrings={{
          uploadButtonText: () => "Choose file",
          dropzoneText: () => "Drop file here",
          removeFileAriaLabel: (_index, fileName) => `Remove ${fileName}`,
        }}
      />
    </FormField>
  );
}
