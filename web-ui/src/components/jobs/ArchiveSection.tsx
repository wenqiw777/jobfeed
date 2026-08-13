import { useJobTransition } from "@/api/queries";
import { SectionLabel } from "@/components/jobs/DetailSections";
import { toast } from "@/components/ui/use-toast";

/**
 * Detail card for abandoning an active pipeline job — applied and
 * interviewing, the two active states the transition graph now allows to
 * reach archived directly (non-forced). Mounted in the Pipeline detail
 * pane via the extraSections seam, mirroring RestoreSection's single-action
 * shape. Archiving is a normal graph edge here, so the mutation never sets
 * force.
 */
export function ArchiveSection({
  jobId,
  status,
  onArchived,
}: {
  jobId: string;
  status: "applied" | "interviewing";
  /** Fires after a successful archive — hosts whose row snapshot goes
   * stale close themselves here. Optional: the Pipeline pane keys off the
   * live row, which the invalidated list refetch moves out of the active
   * groups. */
  onArchived?: () => void;
}) {
  const transition = useJobTransition();

  const submit = () => {
    transition.mutate(
      { id: jobId, to: "archived" },
      {
        onSuccess: () => {
          toast({ title: "Archived" });
          onArchived?.();
        },
        onError: (error) =>
          toast({ variant: "destructive", title: "Archive failed", description: error.message }),
      },
    );
  };

  return (
    <section className="border-b border-hairline px-4 py-3">
      <SectionLabel>{status === "applied" ? "Applied" : "Interviewing"}</SectionLabel>
      <p className="mb-1.5 text-body-sm text-mute">
        Abandon this application — Archive moves it out of the active pipeline.
      </p>
      <Button disabled={transition.isPending} onClick={submit}>
        Archive
      </Button>
    </section>
  );
}
import Button from "@cloudscape-design/components/button";
