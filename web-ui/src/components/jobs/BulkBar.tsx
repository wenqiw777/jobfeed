import { useBulkTransition, type TransitionStatus } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/use-toast";

interface BulkBarProps {
  selectedIds: string[];
  /** True total matching the current filters (the API's `total`). */
  total: number;
  /** Ignore is the pending-JD flavor of skip (§15) — queue hides it. */
  showIgnore: boolean;
  onSelectPage: () => void;
  onSelectAllMatching: () => void;
  onClear: () => void;
  /** Fired after a bulk action lands, so the page re-seeds selection. */
  onBulkCleared: () => void;
}

const QUEUE_ACTIONS: { label: string; to: TransitionStatus }[] = [
  { label: "Shortlist", to: "shortlisted" },
  { label: "Skip", to: "archived" },
];

/** Appears while rows are checkbox-selected; runs the bulk endpoint. */
export function BulkBar({
  selectedIds,
  total,
  showIgnore,
  onSelectPage,
  onSelectAllMatching,
  onClear,
  onBulkCleared,
}: BulkBarProps) {
  const bulk = useBulkTransition();

  if (selectedIds.length === 0) {
    return null;
  }

  const actions = showIgnore
    ? [...QUEUE_ACTIONS, { label: "Ignore", to: "ignored" as TransitionStatus }]
    : QUEUE_ACTIONS;

  const run = (to: TransitionStatus) => {
    bulk.mutate(
      selectedIds.map((id) => ({ id, to })),
      {
        onSuccess: (result) => {
          // All four counters, always — the cascade count is the twin story.
          toast({
            title: "Bulk update",
            description:
              `${result.succeeded} succeeded · ${result.skipped} skipped · ` +
              `${result.failed.length} failed · ${result.cascaded} cascaded`,
          });
          onBulkCleared();
        },
        onError: (error) => {
          toast({ variant: "destructive", title: "Bulk update failed", description: error.message });
        },
      },
    );
  };

  return (
    <div
      role="toolbar"
      aria-label="Bulk actions"
      className="flex items-center gap-2 border-b border-border bg-accent-bg px-3 py-1.5"
    >
      <span className="text-body-sm font-medium text-accent">
        {selectedIds.length} selected
      </span>
      <span className="flex-1" />
      {actions.map((action) => (
        <Button
          key={action.to}
          size="sm"
          disabled={bulk.isPending}
          onClick={() => run(action.to)}
        >
          {action.label}
        </Button>
      ))}
      <span className="mx-1 h-4 w-px bg-accent-border" aria-hidden="true" />
      <Button variant="ghost" size="sm" disabled={bulk.isPending} onClick={onSelectPage}>
        Select page
      </Button>
      {/* The current response IS the matching set at triage scale (one
          page, plan D10); a dedicated all-matching fetch can come later. */}
      <Button variant="ghost" size="sm" disabled={bulk.isPending} onClick={onSelectAllMatching}>
        Select all {total} matching
      </Button>
      <Button variant="ghost" size="sm" disabled={bulk.isPending} onClick={onClear}>
        Clear
      </Button>
    </div>
  );
}
