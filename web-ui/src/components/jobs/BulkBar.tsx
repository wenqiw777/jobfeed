import { useBulkTransition, type TransitionStatus } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/use-toast";

interface BulkBarProps {
  selectedIds: string[];
  /** True total matching the current filters (the API's `total`). */
  total: number;
  /** Rows actually loaded in the current response — select-all's real reach. */
  loadedCount: number;
  /** Pending-JD mode: ONLY the forced Ignore (dismiss-as-junk). Un-scored
   * pending rows are status 'new' (sole legal move: new→scored), so the
   * queue's Shortlist/Skip would 409 there — and Ignore must force (§15). */
  ignoreOnly: boolean;
  onSelectPage: () => void;
  onSelectAllMatching: () => void;
  onClear: () => void;
  /** Fired after a bulk action lands, so the page re-seeds selection. */
  onBulkCleared: () => void;
}

interface BulkAction {
  label: string;
  to: TransitionStatus;
  force: boolean;
}

const QUEUE_ACTIONS: BulkAction[] = [
  { label: "Shortlist", to: "shortlisted", force: false },
  { label: "Skip", to: "archived", force: false },
];

const PENDING_JD_ACTIONS: BulkAction[] = [{ label: "Ignore", to: "ignored", force: true }];

/** Appears while rows are checkbox-selected; runs the bulk endpoint. */
export function BulkBar({
  selectedIds,
  total,
  loadedCount,
  ignoreOnly,
  onSelectPage,
  onSelectAllMatching,
  onClear,
  onBulkCleared,
}: BulkBarProps) {
  const bulk = useBulkTransition();

  if (selectedIds.length === 0) {
    return null;
  }

  const actions = ignoreOnly ? PENDING_JD_ACTIONS : QUEUE_ACTIONS;

  const run = ({ to, force }: BulkAction) => {
    bulk.mutate(
      { items: selectedIds.map((id) => ({ id, to })), force },
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
          onClick={() => run(action)}
        >
          {action.label}
        </Button>
      ))}
      <span className="mx-1 h-4 w-px bg-accent-border" aria-hidden="true" />
      <Button variant="ghost" size="sm" disabled={bulk.isPending} onClick={onSelectPage}>
        Select page
      </Button>
      {/* Select-all only reaches the loaded response. At triage scale that
          IS the matching set (one page, plan D10) — but when the total
          extends past the page, the label must not overstate. */}
      <Button variant="ghost" size="sm" disabled={bulk.isPending} onClick={onSelectAllMatching}>
        {total > loadedCount
          ? `Select all ${loadedCount} loaded`
          : `Select all ${total} matching`}
      </Button>
      <Button variant="ghost" size="sm" disabled={bulk.isPending} onClick={onClear}>
        Clear
      </Button>
    </div>
  );
}
