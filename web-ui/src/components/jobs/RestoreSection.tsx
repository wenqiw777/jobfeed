import { useRestore } from "@/api/queries";
import { SectionLabel } from "@/components/jobs/DetailSections";
import { toast } from "@/components/ui/use-toast";

/**
 * Detail card for restorable rows — ghosted and archived, the two
 * statuses POST /restore accepts (ignored is terminal). Shared by the
 * Pipeline detail pane and the Library drawer via the extraSections
 * seam. The restore target comes from status history server-side; the
 * client never recomputes it.
 */
export function RestoreSection({
  jobId,
  status,
  onRestored,
}: {
  jobId: string;
  status: "ghosted" | "archived";
  /** Fires after a successful restore — hosts whose row snapshot goes
   * stale (the Library drawer) close themselves here. Optional: the
   * Pipeline pane keys off the live row, which simply leaves "ghosted". */
  onRestored?: () => void;
}) {
  const restore = useRestore();

  const submit = () => {
    restore.mutate(
      { id: jobId },
      {
        onSuccess: (data) => {
          toast({ title: `Restored to ${data.status}` });
          onRestored?.();
        },
        onError: (error) =>
          toast({ variant: "destructive", title: "Restore failed", description: error.message }),
      },
    );
  };

  return (
    <section className="border-b border-hairline px-4 py-3">
      <SectionLabel>{status === "ghosted" ? "Ghosted" : "Archived"}</SectionLabel>
      <p className="mb-1.5 text-body-sm text-mute">
        {status === "ghosted" && "No response in a while. "}
        Restore puts it back where it was (the server picks the last non-terminal status from
        history).
      </p>
      <Button disabled={restore.isPending} onClick={submit}>
        Restore
      </Button>
    </section>
  );
}
import Button from "@cloudscape-design/components/button";
