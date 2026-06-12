import { useState } from "react";

import { useRemoveCompany, type CompanyOut } from "@/api/queries";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/components/ui/use-toast";
import { cn } from "@/lib/utils";

const GRID_COLS = "grid grid-cols-[minmax(0,2fr)_7rem_6rem_minmax(0,1fr)_5.5rem] items-center gap-2";

/**
 * Tracked-companies table: slug (mono) / vendor / discover-failure count
 * (consider tint when > 0) / removed badge. Removing is destructive —
 * scans stop covering the company — so it sits behind the danger dialog
 * (DESIGN.md: dialogs are for destructive confirmation only).
 */
export function CompaniesTable({ companies }: { companies: CompanyOut[] }) {
  // The dialog target is a slug snapshot: a refetch can drop the row
  // while the dialog is up, but the confirm still names what was asked.
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);
  const remove = useRemoveCompany();

  const confirmRemove = () => {
    if (removeTarget === null || remove.isPending) {
      return;
    }
    remove.mutate(
      { slug: removeTarget },
      {
        onSuccess: () => {
          toast({ title: `Stopped tracking ${removeTarget}` });
          setRemoveTarget(null);
        },
        onError: (error) =>
          toast({ variant: "destructive", title: "Remove failed", description: error.message }),
      },
    );
  };

  return (
    <div>
      <div
        aria-hidden="true"
        className={cn(GRID_COLS, "border-b border-border px-3 py-1.5 text-label uppercase tracking-wide text-mute")}
      >
        <span>Slug</span>
        <span>Vendor</span>
        <span className="text-right">Failures</span>
        <span />
        <span />
      </div>
      <ul aria-label="Tracked companies">
        {companies.map((company) => (
          <CompanyRow key={company.slug} company={company} onRemove={setRemoveTarget} />
        ))}
      </ul>
      <Dialog
        open={removeTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRemoveTarget(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Stop tracking {removeTarget}?</DialogTitle>
            <DialogDescription>
              Scans will skip this company from now on. Jobs already discovered stay in
              Library; re-adding the slug resumes tracking.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button size="sm" onClick={() => setRemoveTarget(null)}>
              Cancel
            </Button>
            <Button variant="danger" size="sm" disabled={remove.isPending} onClick={confirmRemove}>
              {remove.isPending ? "Removing…" : "Remove"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function CompanyRow({
  company,
  onRemove,
}: {
  company: CompanyOut;
  onRemove: (slug: string) => void;
}) {
  return (
    <li
      data-testid={`company-row-${company.slug}`}
      className={cn(GRID_COLS, "border-b border-hairline px-3 py-1 text-compact")}
    >
      <span className="truncate font-mono text-ink">{company.slug}</span>
      <span className="text-ink-2">{company.vendor ?? "—"}</span>
      <span className="text-right">
        <FailureCount count={company.consecutive_discover_failures} />
      </span>
      <span>
        {company.removed && (
          <span className="rounded-pill bg-skip-bg px-1.5 py-px text-micro font-medium text-skip">
            removed
          </span>
        )}
      </span>
      <span className="text-right">
        {!company.removed && (
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Remove ${company.slug}`}
            className="text-mute hover:text-danger"
            onClick={() => onRemove(company.slug)}
          >
            Remove
          </Button>
        )}
      </span>
    </li>
  );
}

/** Consecutive discover failures; consider-family tint once non-zero. */
function FailureCount({ count }: { count: number }) {
  if (count === 0) {
    return <span className="font-mono text-micro text-mute">0</span>;
  }
  return (
    <span className="inline-flex rounded-pill bg-consider-bg px-1.5 py-px font-mono text-micro font-medium text-consider">
      {count}
    </span>
  );
}
