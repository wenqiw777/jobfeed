/**
 * Trigger Scan: dropdown source picker → POST /api/runs/scan.
 *
 * Uses the existing DropdownMenu primitive (non-modal, no dialog
 * needed for a single-click action).  A 409 surfaces a toast instead
 * of a dialog — the user just needs to wait for the current scan.
 */
import { useState } from "react";
import { Play } from "lucide-react";

import { useTriggerScan } from "@/api/queries";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toast } from "@/components/ui/use-toast";

const SOURCES = ["all", "ats", "indeed", "linkedin-guest", "speedyapply"] as const;

export function TriggerScanButton() {
  const [open, setOpen] = useState(false);
  const trigger = useTriggerScan();

  function fire(source: string): void {
    setOpen(false);
    trigger.mutate(
      { source },
      {
        onSuccess: () => {
          toast({ title: `Scan started (${source})` });
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 409) {
            toast({
              title: "Scan already running",
              description: "Wait for the current scan to finish.",
              variant: "destructive",
            });
          } else {
            toast({
              title: "Scan failed",
              description: err instanceof Error ? err.message : String(err),
              variant: "destructive",
            });
          }
        },
      },
    );
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button size="sm" disabled={trigger.isPending}>
          <Play className="h-3.5 w-3.5" />
          {trigger.isPending ? "Starting…" : "Scan"}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>Source</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {SOURCES.map((src) => (
          <DropdownMenuItem key={src} onSelect={() => fire(src)}>
            {src}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
