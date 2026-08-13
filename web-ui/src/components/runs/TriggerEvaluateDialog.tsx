/**
 * Trigger Evaluate: dialog with stage + limit → POST /api/runs/evaluate.
 *
 * The evaluate trigger has parameters (stage, limit) that benefit from a
 * form, so this uses the Dialog primitive rather than a dropdown.
 */
import { useState } from "react";
import { FlaskConical } from "lucide-react";

import { useTriggerEvaluate } from "@/api/queries";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { toast } from "@/components/ui/use-toast";

type Stage = "a" | "b" | "both";

export function TriggerEvaluateButton() {
  const [open, setOpen] = useState(false);
  const [stage, setStage] = useState<Stage>("both");
  const [limitText, setLimitText] = useState("");
  const trigger = useTriggerEvaluate();

  const limit = limitText.trim() === "" ? null : Number(limitText);
  const isLimitInvalid = limit !== null && (Number.isNaN(limit) || limit < 1);

  function submit(): void {
    if (isLimitInvalid) return;

    trigger.mutate(
      { stage, limit },
      {
        onSuccess: () => {
          setOpen(false);
          setStage("both");
          setLimitText("");
          toast({ title: `Evaluate started (stage ${stage})` });
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 409) {
            toast({
              title: "Evaluate already running",
              description: "Wait for the current evaluation to finish.",
              variant: "destructive",
            });
          } else {
            toast({
              title: "Evaluate failed",
              description: err instanceof Error ? err.message : String(err),
              variant: "destructive",
            });
          }
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <FlaskConical className="h-3.5 w-3.5" />
          Evaluate
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Trigger Evaluate</DialogTitle>
          <DialogDescription>
            Score unrated jobs through the LLM evaluation pipeline.
          </DialogDescription>
        </DialogHeader>

        <div className="mt-3 flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-label text-mute">Stage</span>
            <select
              value={stage}
              onChange={(event) => setStage(event.target.value as Stage)}
              aria-label="Stage"
              className="h-8 w-full rounded-control border border-border-strong bg-surface px-2.5 text-body-sm text-ink focus:outline-none focus:ring-2 focus:ring-accent"
            >
              <option value="both">Both (A + B)</option>
              <option value="a">Stage A only</option>
              <option value="b">Stage B only</option>
            </select>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-label text-mute">Limit (optional)</span>
            <Input
              type="number"
              min={1}
              placeholder="all"
              value={limitText}
              onChange={(e) => setLimitText(e.target.value)}
              aria-label="Limit"
              aria-invalid={isLimitInvalid}
            />
            {isLimitInvalid && (
              <p role="alert" className="text-micro text-danger">
                Limit must be a number of at least 1.
              </p>
            )}
          </label>
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost" size="sm">
              Cancel
            </Button>
          </DialogClose>
          <Button
            variant="primary"
            size="sm"
            disabled={trigger.isPending || isLimitInvalid}
            onClick={submit}
          >
            {trigger.isPending ? "Starting…" : "Start evaluate"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
