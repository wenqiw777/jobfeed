import { useState } from "react";
import Button from "@cloudscape-design/components/button";
import Textarea from "@cloudscape-design/components/textarea";

import { usePasteJd } from "@/api/queries";
import { toast } from "@/components/ui/use-toast";

/**
 * Pending-JD work queue card: paste the JD body by hand, the store
 * assesses its quality, and the row returns to the scored flow (§15).
 */
export function JdPasteCard({ jobId, url }: { jobId: string; url: string | null }) {
  const [text, setText] = useState("");
  const paste = usePasteJd();

  const submit = () => {
    const trimmed = text.trim();
    if (trimmed === "" || paste.isPending) {
      return;
    }
    paste.mutate(
      { id: jobId, text: trimmed },
      {
        onSuccess: (result) => {
          setText("");
          toast({
            title: "JD saved",
            description: `Assessed quality: ${result.jd_quality ?? "unknown"}`,
          });
        },
        onError: (error) => {
          toast({ variant: "destructive", title: "JD paste failed", description: error.message });
        },
      },
    );
  };

  return (
    <section aria-label="Paste JD" className="border-b border-hairline px-4 py-3">
      <h3 className="text-label uppercase tracking-wide text-mute">JD missing — paste manually</h3>
      <p className="mt-1 text-micro text-mute">
        Open the posting, copy the JD body, and paste it here; the evaluator picks it up on
        the next pass.
      </p>
      {url !== null && (
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="mt-1 inline-block text-micro text-accent hover:underline"
        >
          open posting ↗
        </a>
      )}
      <Textarea
        value={text}
        onChange={({ detail }) => setText(detail.value)}
        placeholder="Paste JD body here…"
        rows={6}
        disabled={paste.isPending}
        ariaLabel="JD text"
      />
      <div className="mt-2">
        <Button
          variant="primary"
          disabled={paste.isPending || text.trim() === ""}
          onClick={submit}
        >
          {paste.isPending ? "Saving…" : "Save JD"}
        </Button>
      </div>
    </section>
  );
}
