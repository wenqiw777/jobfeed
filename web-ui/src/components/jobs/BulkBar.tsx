import Button from "@cloudscape-design/components/button";
import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import SpaceBetween from "@cloudscape-design/components/space-between";

import { useBulkTransition, type TransitionStatus } from "@/api/queries";
import { toast } from "@/components/ui/use-toast";

interface BulkBarProps {
  currentDecision: "results" | "wait" | "applied" | "ignored";
  selectedIds: string[];
  /** True total matching the current filters (the API's `total`). */
  total: number;
  /** False while the first-page fast path is still resolving the exact total. */
  totalIsExact: boolean;
  isSelectingAll: boolean;
  onSelectPage: () => void;
  onSelectAllMatching: () => void;
  onClear: () => void;
  /** Receives failures so successful rows can leave the active list while
   * failed rows remain selected for retry. */
  onBulkResult: (failedIds: string[]) => void;
}

interface BulkAction {
  decision: "wait" | "ignored";
  label: string;
  to: TransitionStatus;
}

const ACTIONS: BulkAction[] = [
  { decision: "wait", label: "Move selected to Wait", to: "shortlisted" },
  { decision: "ignored", label: "Ignore selected", to: "ignored" },
];

/** Appears while rows are checkbox-selected; runs the bulk endpoint. */
export function BulkBar({
  currentDecision,
  selectedIds,
  total,
  totalIsExact,
  isSelectingAll,
  onSelectPage,
  onSelectAllMatching,
  onClear,
  onBulkResult,
}: BulkBarProps) {
  const bulk = useBulkTransition();

  if (selectedIds.length === 0) {
    return null;
  }

  const run = ({ to }: BulkAction) => {
    bulk.mutate(
      { items: selectedIds.map((id) => ({ id, to })) },
      {
        onSuccess: (result) => {
          const failedIds = result.failed.map((failure) => failure.id);
          toast({
            variant: failedIds.length > 0 ? "destructive" : "default",
            title: failedIds.length > 0
              ? "Some selected jobs could not be updated"
              : "Selected jobs updated",
            description:
              `${result.succeeded} succeeded · ${result.skipped} skipped · ` +
              `${failedIds.length} failed · ${result.cascaded} cascaded` +
              (failedIds.length > 0 ? ` · Failed IDs: ${failedIds.join(", ")}` : ""),
          });
          onBulkResult(failedIds);
        },
        onError: (error) => {
          toast({
            variant: "destructive",
            title: "Selected jobs could not be updated",
            description: error.message,
          });
        },
      },
    );
  };

  return (
    <div role="toolbar" aria-label="Bulk actions">
      <Container>
        <SpaceBetween size="xs">
          <SpaceBetween direction="horizontal" size="xs" alignItems="center">
            <Box variant="strong">{selectedIds.length} selected</Box>
            <div role="group" aria-label="Decision actions">
              <SpaceBetween direction="horizontal" size="xs">
                {ACTIONS.filter(
                  (action) => action.decision !== currentDecision,
                ).map((action) => (
                  <Button
                    key={action.to}
                    wrapText={false}
                    disabled={bulk.isPending || isSelectingAll}
                    onClick={() => run(action)}
                  >
                    {action.label}
                  </Button>
                ))}
              </SpaceBetween>
            </div>
          </SpaceBetween>
          <div role="group" aria-label="Selection controls">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                wrapText={false}
                disabled={bulk.isPending || isSelectingAll}
                onClick={onSelectPage}
              >
                Select this page
              </Button>
              <Button
                variant="link"
                wrapText={false}
                loading={isSelectingAll}
                disabled={bulk.isPending || !totalIsExact}
                onClick={onSelectAllMatching}
              >
                {totalIsExact
                  ? `Select all ${total} ${total === 1 ? "result" : "results"}`
                  : "Loading all results"}
              </Button>
              <Button
                variant="link"
                wrapText={false}
                disabled={bulk.isPending || isSelectingAll}
                onClick={onClear}
              >
                Clear selection
              </Button>
            </SpaceBetween>
          </div>
        </SpaceBetween>
      </Container>
    </div>
  );
}
